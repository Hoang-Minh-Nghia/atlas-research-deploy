"""Dịch vụ Telegram luôn chạy: nhận nút Duyệt / Viết lại / Bỏ và yêu cầu sửa bằng reply.

    python -m news_agent.bot
"""
from __future__ import annotations

import json
import logging
import sys
import time
from html import escape

from . import config, db, pipeline, publisher, telegram
from .scraper import SourceArticle

log = logging.getLogger("news_agent.bot")


def _allowed(user: dict) -> bool:
    return int(user.get("id", 0)) in config.TELEGRAM_ALLOWED_USER_IDS


def _in_review_chat(chat_id) -> bool:
    return str(chat_id) == str(config.TELEGRAM_CHAT_ID)


def _rewrite(draft: dict, feedback: str = "") -> None:
    data = json.loads(draft["data"])
    if not db.set_status(draft["id"], "superseded", expect="pending"):
        telegram.send_message(f"Nháp #{draft['id']} không còn chờ duyệt.")
        return
    pipeline.discard(draft)
    telegram.send_message(f"🔁 Đang viết lại nháp #{draft['id']}…")
    try:
        pipeline.create_and_send(SourceArticle(**data["source"]), feedback)
    except Exception as e:  # noqa: BLE001
        log.exception("Viết lại thất bại")
        telegram.send_message(f"⚠️ Viết lại nháp #{draft['id']} thất bại: {escape(str(e))[:3000]}")


def handle_callback(cb: dict) -> None:
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not _allowed(cb.get("from", {})) or not _in_review_chat(chat_id):
        telegram.answer_callback(cb["id"], "Bạn không có quyền duyệt bài.")
        log.warning("Bỏ qua callback từ user %s", cb.get("from", {}).get("id"))
        return
    try:
        action, raw_id = cb.get("data", "").split(":", 1)
        draft = db.get_draft(int(raw_id))
    except ValueError:
        telegram.answer_callback(cb["id"], "Dữ liệu nút không hợp lệ.")
        return
    if not draft or draft["status"] != "pending":
        telegram.answer_callback(cb["id"], "Nháp này đã được xử lý.")
        telegram.clear_keyboard(chat_id, msg.get("message_id"))
        return
    telegram.clear_keyboard(chat_id, msg.get("message_id"))

    if action == "approve":
        if not db.set_status(draft["id"], "publishing", expect="pending"):
            telegram.answer_callback(cb["id"], "Nháp này đã được xử lý.")
            return
        telegram.answer_callback(cb["id"], "Đang đăng bài…")
        try:
            url = publisher.publish(json.loads(draft["data"]))
        except Exception as e:  # noqa: BLE001
            log.exception("Đăng bài thất bại")
            db.set_status(draft["id"], "pending")
            telegram.send_message(
                f"⚠️ Đăng nháp #{draft['id']} thất bại: {escape(str(e))[:3000]}",
                reply_markup=telegram.review_keyboard(draft["id"]),
            )
            return
        db.set_status(draft["id"], "published")
        telegram.send_message(f"✅ Đã đăng: {escape(draft['title'])}\n{url}\n(Cloudflare Pages cập nhật sau 1–2 phút)")
    elif action == "rewrite":
        telegram.answer_callback(cb["id"], "Đang viết lại…")
        _rewrite(draft)
    elif action == "reject":
        if db.set_status(draft["id"], "rejected", expect="pending"):
            pipeline.discard(draft)
        telegram.answer_callback(cb["id"], "Đã bỏ bài.")
        telegram.send_message(f"❌ Đã bỏ nháp #{draft['id']}.")
    else:
        telegram.answer_callback(cb["id"], "Thao tác không hợp lệ.")


def handle_message(msg: dict) -> None:
    """Reply vào tin có nút của một nháp = yêu cầu viết lại kèm góp ý."""
    reply = msg.get("reply_to_message")
    if not reply or not msg.get("text") or not _in_review_chat(msg["chat"]["id"]) or not _allowed(msg.get("from", {})):
        return
    with db.connect() as c:
        row = c.execute(
            "SELECT * FROM drafts WHERE tg_message_id=? AND status='pending'", (reply["message_id"],)
        ).fetchone()
    if not row:
        telegram.send_message("Không tìm thấy nháp đang chờ duyệt cho tin này.", reply_to=msg["message_id"])
        return
    draft = dict(row)
    telegram.clear_keyboard(msg["chat"]["id"], reply["message_id"])
    _rewrite(draft, msg["text"].strip())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENROUTER_API_KEY")
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        raise SystemExit("Thiếu TELEGRAM_ALLOWED_USER_IDS — không ai duyệt được bài.")
    offset = int(db.get_kv("tg_offset", "0") or 0)
    log.info("Bot duyệt bài đang chạy (chat %s)", config.TELEGRAM_CHAT_ID)
    while True:
        try:
            updates = telegram.get_updates(offset)
        except Exception as e:  # noqa: BLE001 — mạng chập chờn: nghỉ rồi thử lại
            log.warning("getUpdates lỗi: %s", e)
            time.sleep(10)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            db.set_kv("tg_offset", str(offset))  # lưu trước khi xử lý để không xử lý lặp khi khởi động lại
            try:
                if "callback_query" in upd:
                    handle_callback(upd["callback_query"])
                elif "message" in upd:
                    handle_message(upd["message"])
            except Exception:  # noqa: BLE001
                log.exception("Lỗi xử lý update %s", upd.get("update_id"))


if __name__ == "__main__":
    sys.exit(main())
