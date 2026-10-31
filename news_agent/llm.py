"""Cổng OpenRouter cho vai trò viết lại — theo bộ rewrite_openrouter_kit (Social_product).

Bài học áp dụng:
* Chất lượng lọc trước (danh mục đã kiểm định), giá chọn sau (model rẻ nhất theo bảng giá).
* Bảng giá cache 24h; mất mạng dùng cache cũ; chưa có cache dùng thứ tự danh mục.
* Không chốt model lúc import — xếp hạng lại mỗi lượt gọi.
* Lỗi thì thử model kế tiếp, NHƯNG 401/402/403 (khoá sai, hết tiền) thì dừng ngay.
* LUÔN tắt reasoning: model suy luận tính token "thinking" vào max_tokens → content rỗng.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import requests

from . import config

log = logging.getLogger(__name__)

CHAT_URL = config.OPENROUTER_URL
MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICE_TTL_SECONDS = 24 * 3600
#: Token ước tính cho một lượt viết lại bài tin (vào, ra) — để quy giá về $/lượt.
USAGE_ESTIMATE = (3000, 2500)
FATAL_STATUS = frozenset({401, 402, 403})


@dataclass(frozen=True)
class ModelChoice:
    model: str
    note: str


#: Danh mục mặc định lấy từ Social_product (đo trên bài tiếng Anh ngắn). Bài của Atlas là
#: tiếng Việt dài + JSON: nên chạy thử --dry-run vài bài rồi chỉnh LLM_REWRITE_CATALOG.
DEFAULT_CATALOG: tuple[ModelChoice, ...] = (
    ModelChoice("inclusionai/ling-3.0-flash", "điểm cao, rẻ nhất (Social_product)"),
    ModelChoice("mistralai/mistral-small-3.2-24b-instruct", "ổn định, dự phòng"),
    ModelChoice("openai/gpt-4o-mini", "chất lượng tốt nhưng đắt gấp ~7"),
)


class FatalLLMError(RuntimeError):
    """Lỗi đổi model không cứu được (khoá sai, thiếu quyền, hết tiền)."""


def catalog() -> list[str]:
    return config.LLM_REWRITE_CATALOG or [m.model for m in DEFAULT_CATALOG]


def _price_cache():
    return config.STATE_DIR / "openrouter_prices.json"


def fetch_prices(timeout: float = 8.0) -> dict[str, dict[str, float]]:
    r = requests.get(MODELS_URL, timeout=timeout, headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    out: dict[str, dict[str, float]] = {}
    for item in r.json().get("data") or []:
        pricing = item.get("pricing") or {}
        try:
            out[str(item.get("id") or "")] = {
                "prompt": float(pricing.get("prompt") or 0) * 1e6,
                "completion": float(pricing.get("completion") or 0) * 1e6,
            }
        except (TypeError, ValueError):
            continue
    return out


def prices() -> dict[str, dict[str, float]]:
    """Cache còn hạn → dùng; hết hạn → hỏi mới; hỏi lỗi → cache cũ; không có gì → {}."""
    cached: dict[str, dict[str, float]] = {}
    fetched_at = 0.0
    path = _price_cache()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            cached, fetched_at = data.get("prices") or {}, float(data.get("fetched_at") or 0)
    except (OSError, ValueError, TypeError) as e:
        log.warning("Cache giá hỏng, bỏ qua: %s", e)
    if cached and time.time() - fetched_at < PRICE_TTL_SECONDS:
        return cached
    try:
        table = fetch_prices()
    except (requests.RequestException, ValueError) as e:
        log.warning("Không lấy được bảng giá OpenRouter (%s) — %s", e, "dùng cache cũ" if cached else "dùng thứ tự danh mục")
        return cached
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": time.time(), "prices": table}) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("Không ghi được cache giá: %s", e)
    return table


def cost_per_call(price: dict[str, float]) -> float:
    tin, tout = USAGE_ESTIMATE
    return (tin * price.get("prompt", 0.0) + tout * price.get("completion", 0.0)) / 1e6


def ranked_models() -> list[str]:
    """Thứ tự thử: model ép bằng env → danh mục theo giá tăng dần → phần còn lại của danh mục."""
    order: list[str] = []
    if config.LLM_REWRITE_MODEL:
        order.append(config.LLM_REWRITE_MODEL)
    table = prices()
    cat = catalog()
    by_price = sorted((m for m in cat if m in table), key=lambda m: cost_per_call(table[m]))
    for m in [*by_price, *cat]:  # model đã bị gỡ khỏi OpenRouter tự rơi xuống cuối
        if m not in order:
            order.append(m)
    return order


def _headers() -> dict:
    config.require("OPENROUTER_API_KEY")
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": "Atlas Research News Agent",
    }


def post(payload: dict, timeout: int = 180) -> dict:
    """Một lời gọi thô tới OpenRouter. 401/402/403 → FatalLLMError."""
    r = requests.post(CHAT_URL, headers=_headers(), json=payload, timeout=timeout)
    if r.status_code in FATAL_STATUS:
        raise FatalLLMError(f"OpenRouter {r.status_code}: {r.text[:300]} (kiểm tra khoá / số dư)")
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code}: {r.text[:300]}")
    data = r.json()
    if "error" in data:
        code = (data["error"] or {}).get("code")
        if code in FATAL_STATUS:
            raise FatalLLMError(f"OpenRouter lỗi: {data['error']}")
        raise RuntimeError(f"OpenRouter lỗi: {data['error']}")
    return data


def chat(messages: list[dict], validate=None, **kwargs) -> tuple[str, object]:
    """Gọi chat completion có dự phòng model.

    validate(content) → kết quả đã kiểm tra, hoặc ném ValueError để chuyển sang model kế tiếp.
    Trả về (model đã dùng, kết quả).
    """
    candidates = ranked_models()
    last: Exception | None = None
    for model in candidates:
        payload = {
            "model": model,
            "messages": messages,
            "reasoning": {"enabled": False},
            "max_tokens": config.LLM_MAX_TOKENS,
            **kwargs,
        }
        try:
            data = post(payload)
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                raise ValueError("model trả nội dung rỗng")
            result = validate(content) if validate else content
            log.info("Viết lại: dùng model %s", model)
            return model, result
        except FatalLLMError:
            raise
        except (requests.RequestException, RuntimeError, ValueError, KeyError, IndexError) as e:
            last = e
            log.warning("Model %s lỗi (%s) — thử model kế tiếp", model, e)
    raise RuntimeError(f"Mọi model trong danh mục đều lỗi: {last}")
