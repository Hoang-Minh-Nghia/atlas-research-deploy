"""Viết lại bài nguồn qua OpenRouter, trả về JSON bài viết + prompt ảnh."""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

import requests

from . import config
from .scraper import SourceArticle

log = logging.getLogger(__name__)


def openrouter_chat(payload: dict, timeout: int = 180) -> dict:
    config.require("OPENROUTER_API_KEY")
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": "Atlas Research News Agent",
    }
    last = None
    for attempt in range(3):
        try:
            r = requests.post(config.OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"OpenRouter lỗi: {data['error']}")
            return data
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last = e
            log.warning("OpenRouter thất bại (lần %d): %s", attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"OpenRouter không phản hồi: {last}")


def slugify(text: str, max_words: int = 8) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:max_words]) or "bai-viet"


def _extract_json(content: str) -> dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Mô hình không trả về JSON")
    return json.loads(content[start : end + 1])


def _validate(d: dict) -> dict:
    for key in ("title", "lead", "sections", "hero_image"):
        if not d.get(key):
            raise ValueError(f"JSON thiếu trường '{key}'")
    d["sections"] = [
        {"heading": str(s.get("heading", "")).strip(), "paragraphs": [str(p).strip() for p in s.get("paragraphs", []) if str(p).strip()]}
        for s in d["sections"]
        if isinstance(s, dict)
    ]
    d["sections"] = [s for s in d["sections"] if s["paragraphs"]]
    if not d["sections"]:
        raise ValueError("Bài viết lại không có đoạn văn nào")
    d["slug"] = slugify(d.get("slug") or d["title"])
    d.setdefault("category", "Thị trường")
    d.setdefault("excerpt", d["lead"])
    d["description"] = (d.get("description") or d["lead"])[:200]
    d["figures"] = [f for f in d.get("figures", []) if isinstance(f, dict) and f.get("prompt")][: config.FIGURES_PER_ARTICLE]
    return d


def rewrite(src: SourceArticle, feedback: str = "") -> dict:
    system = (config.PROMPTS_DIR / "rewrite.md").read_text(encoding="utf-8").replace(
        "{{FIGURES}}", str(config.FIGURES_PER_ARTICLE)
    )
    user = f"BÀI NGUỒN ({config.SOURCE_NAME}, {src.url}):\n\n{src.as_text()}"
    if feedback:
        user += f"\n\nYÊU CẦU THÊM: {feedback}"
    payload = {
        "model": config.OPENROUTER_TEXT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
    }
    last = None
    for _ in range(2):
        data = openrouter_chat(payload)
        try:
            return _validate(_extract_json(data["choices"][0]["message"]["content"] or ""))
        except (ValueError, KeyError, IndexError, json.JSONDecodeError) as e:
            last = e
            log.warning("JSON viết lại không hợp lệ, thử lại: %s", e)
    raise RuntimeError(f"Không nhận được JSON hợp lệ từ mô hình: {last}")
