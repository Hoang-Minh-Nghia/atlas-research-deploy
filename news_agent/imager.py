"""Tạo ảnh minh hoạ bằng mô hình ảnh trên OpenRouter, lưu JPG 1600×900."""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import requests
from PIL import Image

from . import config
from .rewriter import openrouter_chat

log = logging.getLogger(__name__)
SIZE = (1600, 900)


def _decode(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


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


def generate(scene_prompt: str, out_path: Path) -> Path:
    base = (config.PROMPTS_DIR / "image.md").read_text(encoding="utf-8").strip()
    payload = {
        "model": config.OPENROUTER_IMAGE_MODEL,
        "messages": [{"role": "user", "content": f"{base}\n{scene_prompt.strip()}"}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "16:9"},
    }
    last = None
    for attempt in range(2):
        data = openrouter_chat(payload, timeout=300)
        try:
            images = data["choices"][0]["message"].get("images") or []
            url = images[0]["image_url"]["url"]
            img = _fit_16_9(_decode(url))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, "JPEG", quality=85, optimize=True, progressive=True)
            return out_path
        except (KeyError, IndexError, OSError, ValueError, requests.RequestException) as e:
            last = e
            log.warning("Mô hình ảnh không trả ảnh (lần %d): %s", attempt + 1, e)
    raise RuntimeError(f"Không tạo được ảnh: {last}")
