"""Tạo ảnh minh hoạ, lưu JPG 1600×900.

Hai backend (IMAGE_BACKEND):
* ``comfy`` (mặc định) — GPU máy nhà: gọi ``lib/images.py`` của studio YouTube
  (ComfyUI + FLUX.2 klein 4B GGUF, 4 bước, CFG 1). Chi phí $0.
  Quy tắc của "Họa sĩ GPU": card 6 GB chỉ làm một việc GPU một lúc → nếu ComfyUI đã
  chạy sẵn (studio đang dùng) thì nhường, bỏ lượt (trừ khi COMFY_SHARED=1); tự bật
  ComfyUI khi cần và tắt ngay khi xong để giải phóng GPU.
* ``openrouter`` — model ảnh trả phí trên OpenRouter.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import random
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import requests
from PIL import Image

from . import config, llm

log = logging.getLogger(__name__)
SIZE = (1600, 900)


class GpuBusy(RuntimeError):
    """GPU đang được việc khác dùng — thử lại ở lượt sau."""


def style_prompt(scene: str) -> str:
    base = (config.PROMPTS_DIR / "image.md").read_text(encoding="utf-8").strip()
    return f"{scene.strip().rstrip('.')}. {base}"


def _fit_16_9(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    target = SIZE[0] / SIZE[1]
    if w / h > target:  # quá rộng → cắt hai bên
        nw = int(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:  # quá cao → cắt trên dưới
        nh = int(w / target)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize(SIZE, Image.LANCZOS)


def _save_jpg(raw: bytes, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _fit_16_9(raw).save(out_path, "JPEG", quality=88, optimize=True, progressive=True)


# ---------------------------------------------------------------------------
# Backend ComfyUI (GPU)
# ---------------------------------------------------------------------------
def comfy_running() -> bool:
    try:
        return requests.get(f"{config.COMFY_URL}/system_stats", timeout=3).ok
    except requests.RequestException:
        return False


def _images_py(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
    script = config.STUDIO_LIB_DIR / "images.py"
    if not script.is_file():
        raise RuntimeError(f"Không thấy {script} — đặt STUDIO_LIB_DIR trỏ tới thư mục lib/ của studio")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", COMFY_URL=config.COMFY_URL)
    res = subprocess.run(
        [config.STUDIO_PYTHON, str(script), *args],
        cwd=str(config.STUDIO_LIB_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout or config.COMFY_TIMEOUT,
    )
    if res.returncode != 0:
        raise RuntimeError(f"images.py {args[0]} lỗi: {(res.stderr or res.stdout).strip()[-800:]}")
    return res


def _port_of(url: str) -> int:
    m = re.search(r":(\d+)(?:/|$)", url.split("//", 1)[-1])
    return int(m.group(1)) if m else 80


def stop_comfy() -> None:
    """Tắt tiến trình đang nghe cổng ComfyUI (giải phóng GPU cho render video)."""
    port = _port_of(config.COMFY_URL)
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=30).stdout
            pids = {
                line.split()[-1]
                for line in out.splitlines()
                if "LISTENING" in line and re.search(rf"[:.]{port}\s", line)
            }
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, timeout=30)
        else:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=30)
        log.info("Đã tắt ComfyUI (cổng %d)", port)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Không tắt được ComfyUI: %s", e)


def _comfy_generate(scene: str, out_path: Path, seed: int) -> None:
    w, h = config.COMFY_SIZE
    with tempfile.TemporaryDirectory(prefix="atlas-img-") as tmp:
        png = Path(tmp) / "out.png"  # file mới tinh: images.py bỏ qua ảnh đã tồn tại
        _images_py("one", style_prompt(scene), str(png), "--w", str(w), "--h", str(h), "--seed", str(seed))
        if not png.is_file():
            raise RuntimeError("images.py chạy xong nhưng không có file ảnh")
        _save_jpg(png.read_bytes(), out_path)


# ---------------------------------------------------------------------------
# Backend OpenRouter
# ---------------------------------------------------------------------------
def _decode(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _openrouter_generate(scene: str, out_path: Path, seed: int) -> None:
    payload = {
        "model": config.OPENROUTER_IMAGE_MODEL,
        "messages": [{"role": "user", "content": style_prompt(scene)}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "16:9"},
        "seed": seed,
    }
    last = None
    for attempt in range(2):
        try:
            data = llm.post(payload, timeout=300)
            url = (data["choices"][0]["message"].get("images") or [])[0]["image_url"]["url"]
            _save_jpg(_decode(url), out_path)
            return
        except llm.FatalLLMError:
            raise
        except (KeyError, IndexError, OSError, ValueError, RuntimeError, requests.RequestException) as e:
            last = e
            log.warning("Model ảnh OpenRouter lỗi (lần %d): %s", attempt + 1, e)
    raise RuntimeError(f"Không tạo được ảnh qua OpenRouter: {last}")


# ---------------------------------------------------------------------------
# API dùng chung
# ---------------------------------------------------------------------------
class _State:
    backend = config.IMAGE_BACKEND


@contextmanager
def session():
    """Bao một lô ảnh. Với GPU: kiểm tra GPU rảnh, bật ComfyUI, xong thì tắt.

    Ném GpuBusy nếu GPU đang bận và không có dự phòng.
    """
    _State.backend = config.IMAGE_BACKEND
    if config.IMAGE_BACKEND != "comfy":
        yield
        return
    started = False
    try:
        if comfy_running():
            if not config.COMFY_SHARED:
                raise GpuBusy("ComfyUI đang chạy (GPU đang được studio dùng)")
        else:
            log.info("Bật ComfyUI…")
            _images_py("start", timeout=300)
            started = True
            if not comfy_running():
                raise RuntimeError("Đã chạy images.py start nhưng ComfyUI không phản hồi")
    except (GpuBusy, RuntimeError, OSError, subprocess.SubprocessError) as e:
        if config.IMAGE_FALLBACK == "openrouter":
            log.warning("GPU không dùng được (%s) — chuyển sang OpenRouter", e)
            _State.backend = "openrouter"
            yield
            return
        if isinstance(e, GpuBusy):
            raise
        raise GpuBusy(f"Không bật được ComfyUI: {e}") from e
    try:
        yield
    finally:
        if started:
            stop_comfy()


def new_seed() -> int:
    return random.randint(1, 2**31 - 1)


def generate(scene: str, out_path: Path, seed: int | None = None) -> int:
    """Tạo một ảnh (phải gọi bên trong session()). Trả về seed đã dùng."""
    seed = seed or new_seed()
    if _State.backend == "comfy":
        try:
            try:
                _comfy_generate(scene, out_path, seed)
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                log.warning("Ảnh GPU lỗi (%s) — đổi seed thử lại", e)
                seed = new_seed()
                _comfy_generate(scene, out_path, seed)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            if config.IMAGE_FALLBACK != "openrouter":
                raise RuntimeError(f"Tạo ảnh GPU lỗi: {e}") from e
            log.warning("Ảnh GPU lỗi (%s) — thử OpenRouter", e)
            _openrouter_generate(scene, out_path, seed)
    else:
        _openrouter_generate(scene, out_path, seed)
    return seed
