"""Đưa bản nháp đã duyệt vào repo, commit và push."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from contextlib import contextmanager

from . import builder, config
from .scraper import now_iso

log = logging.getLogger(__name__)


@contextmanager
def repo_lock():
    """Khoá file đa nền tảng (fcntl trên Linux, msvcrt trên Windows)."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOCK_PATH, "a+") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK chỉ thử ~10 giây rồi báo lỗi
                    time.sleep(1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def git(*args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(config.REPO_DIR), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} lỗi: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def sync_repo() -> None:
    """Đồng bộ bản clone riêng của agent với nhánh trên remote (bỏ mọi thay đổi cục bộ)."""
    git("fetch", "--quiet", config.GIT_REMOTE, config.GIT_BRANCH)
    git("checkout", "--quiet", "-B", config.GIT_BRANCH, f"{config.GIT_REMOTE}/{config.GIT_BRANCH}")
    git("reset", "--quiet", "--hard", f"{config.GIT_REMOTE}/{config.GIT_BRANCH}")


def publish(article: dict) -> str:
    """Trả về URL bài trên site."""
    slug = article["slug"]
    src_dir = config.DRAFTS_DIR / slug
    if not (src_dir / "thumb.jpg").is_file():
        raise RuntimeError(f"Không còn ảnh nháp trong {src_dir}")
    with repo_lock():
        sync_repo()
        dest = config.NEWS_DIR / slug
        if dest.exists():
            raise RuntimeError(f"Thư mục news/{slug}/ đã tồn tại trong repo")
        try:
            article = dict(article, published=now_iso())  # giờ đăng = lúc duyệt
            dest.mkdir(parents=True)
            for img in src_dir.glob("*.jpg"):
                shutil.copy2(img, dest / img.name)
            (dest / "index.html").write_text(builder.render_article(article), encoding="utf-8")

            index = config.NEWS_INDEX.read_text(encoding="utf-8")
            config.NEWS_INDEX.write_text(builder.insert_story(index, builder.render_story(article)), encoding="utf-8")
            sitemap = config.SITEMAP.read_text(encoding="utf-8")
            config.SITEMAP.write_text(builder.add_sitemap_url(sitemap, slug, article["published"][:10]), encoding="utf-8")

            git("add", "--", str(dest), str(config.NEWS_INDEX), str(config.SITEMAP))
            git(
                "-c", "user.name=Atlas News Bot", "-c", "user.email=news-bot@atlas-research.invalid",
                "commit", "--quiet", "-m", f"Tin tức: {article['title']}\n\nNguồn tham khảo: {article['source_url']}",
            )
            for attempt in range(4):
                try:
                    git("push", "--quiet", config.GIT_REMOTE, f"HEAD:{config.GIT_BRANCH}")
                    break
                except RuntimeError as e:
                    if attempt == 3:
                        raise
                    log.warning("Push thất bại, thử lại: %s", e)
                    time.sleep(2 ** (attempt + 1))
                    git("pull", "--quiet", "--rebase", config.GIT_REMOTE, config.GIT_BRANCH)
        except Exception:
            # Trả repo về trạng thái remote để lần sau làm lại sạch sẽ.
            try:
                sync_repo()
                shutil.rmtree(dest, ignore_errors=True)
            except RuntimeError as e2:
                log.error("Không khôi phục được repo: %s", e2)
            raise
    shutil.rmtree(src_dir, ignore_errors=True)
    return f"{config.SITE_URL}/news/{slug}/"
