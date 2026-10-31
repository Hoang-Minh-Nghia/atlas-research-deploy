"""Cấu hình đọc từ biến môi trường (systemd nạp /etc/atlas-news.env)."""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def _load_env_file(path: str) -> None:
    """Nạp file KEY=VALUE khi chạy tay (systemd đã nạp sẵn qua EnvironmentFile)."""
    p = Path(path)
    if not p.is_file():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip("'\"")
        os.environ.setdefault(key.strip(), val)


_load_env_file(os.environ.get("ATLAS_NEWS_ENV", "/etc/atlas-news.env"))


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


SOURCE_URL = _get("SOURCE_URL", "https://vnexpress.net/tag/tai-san-so-1064801")
SOURCE_NAME = _get("SOURCE_NAME", "VnExpress")
MAX_PER_RUN = int(_get("MAX_PER_RUN", "3"))
DRAFT_TTL_DAYS = int(_get("DRAFT_TTL_DAYS", "7"))
USER_AGENT = _get("USER_AGENT", "AtlasResearchNewsBot/1.0 (+https://atlas-research-vn.pages.dev/)")

OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_TEXT_MODEL = _get("OPENROUTER_TEXT_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_IMAGE_MODEL = _get("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image")
OPENROUTER_URL = _get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
FIGURES_PER_ARTICLE = int(_get("FIGURES_PER_ARTICLE", "1"))

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")
TELEGRAM_ALLOWED_USER_IDS = {
    int(x) for x in _get("TELEGRAM_ALLOWED_USER_IDS").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}

REPO_DIR = Path(_get("REPO_DIR", str(PKG_DIR.parent)))
GIT_BRANCH = _get("GIT_BRANCH", "main")
GIT_REMOTE = _get("GIT_REMOTE", "origin")
SITE_URL = _get("SITE_URL", "https://atlas-research-vn.pages.dev").rstrip("/")

STATE_DIR = Path(_get("STATE_DIR", "/var/lib/atlas-news"))
DRAFTS_DIR = STATE_DIR / "drafts"
DB_PATH = STATE_DIR / "state.sqlite3"
LOCK_PATH = STATE_DIR / "agent.lock"

TEMPLATE_PATH = REPO_DIR / "templates" / "news-article.html"
NEWS_DIR = REPO_DIR / "affiliate_hub" / "news"
NEWS_INDEX = NEWS_DIR / "index.html"
SITEMAP = REPO_DIR / "affiliate_hub" / "sitemap.xml"
PROMPTS_DIR = PKG_DIR / "prompts"


def require(*names: str) -> None:
    missing = [n for n in names if not globals().get(n) or str(globals().get(n)).startswith("__CHANGE_ME")]
    if missing:
        raise SystemExit("Thiếu cấu hình: " + ", ".join(missing) + " (xem /etc/atlas-news.env)")
