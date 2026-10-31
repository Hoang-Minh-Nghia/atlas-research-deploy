"""Gọi Telegram Bot API (không cần thư viện bot)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)


def _url(method: str) -> str:
    config.require("TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def call(method: str, files: dict | None = None, http_timeout: int = 60, **params) -> dict:
    data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    r = requests.post(_url(method), data=data, files=files, timeout=http_timeout)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} lỗi: {r.status_code} {body.get('description', r.text[:200])}")
    return body["result"]


def send_message(text: str, reply_markup: dict | None = None, reply_to: int | None = None, chat_id: str | None = None) -> dict:
    return call(
        "sendMessage",
        chat_id=chat_id or config.TELEGRAM_CHAT_ID,
        text=text[:4096],
        parse_mode="HTML",
        disable_web_page_preview="true",
        reply_markup=reply_markup,
        reply_to_message_id=reply_to,
    )


def send_photo(path: Path, caption: str) -> dict:
    with path.open("rb") as f:
        return call("sendPhoto", files={"photo": f}, chat_id=config.TELEGRAM_CHAT_ID, caption=caption[:1024], parse_mode="HTML")


def send_document(path: Path, caption: str = "", reply_to: int | None = None) -> dict:
    with path.open("rb") as f:
        return call(
            "sendDocument",
            files={"document": (path.name, f)},
            http_timeout=120,
            chat_id=config.TELEGRAM_CHAT_ID,
            caption=caption[:1024],
            parse_mode="HTML",
            reply_to_message_id=reply_to,
        )


def review_keyboard(draft_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Duyệt", "callback_data": f"approve:{draft_id}"},
                {"text": "🔁 Viết lại", "callback_data": f"rewrite:{draft_id}"},
                {"text": "❌ Bỏ", "callback_data": f"reject:{draft_id}"},
            ]
        ]
    }


def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
    except RuntimeError as e:  # callback quá hạn — không quan trọng
        log.info("%s", e)


def clear_keyboard(chat_id, message_id: int) -> None:
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": []})
    except RuntimeError as e:
        log.info("%s", e)


def get_updates(offset: int, timeout: int = 50) -> list[dict]:
    return call(
        "getUpdates",
        http_timeout=timeout + 15,
        offset=offset,
        timeout=timeout,
        allowed_updates=["callback_query", "message"],
    )
