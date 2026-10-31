#!/usr/bin/env bash
# =============================================================================
# ATLAS RESEARCH — news-agent-install.sh
# Cài agent tin tức tự động (news_agent/) lên server:
#   - mỗi giờ quét trang tag VnExpress, viết lại bài + tạo ảnh qua OpenRouter,
#     gửi bản nháp lên Telegram;
#   - bot Telegram luôn chạy, bấm "Duyệt" thì commit + push bài lên GitHub
#     (Cloudflare Pages tự deploy).
# Chạy với quyền root:
#   sudo bash scripts/news-agent-install.sh
#
# Idempotent. Lần đầu script tạo deploy key và file cấu hình rồi dừng lại để
# bạn (1) thêm deploy key vào GitHub, (2) điền /etc/atlas-news.env;
# sau đó chạy lại script để clone repo và bật dịch vụ.
# =============================================================================
set -euo pipefail

AGENT_USER="atlas-news"
APP_DIR="/opt/atlas-news"              # bản clone riêng của agent (agent tự reset --hard)
STATE_DIR="/var/lib/atlas-news"        # SQLite, bản nháp, venv, SSH key
VENV_DIR="${STATE_DIR}/venv"
ENV_FILE="/etc/atlas-news.env"
REPO_SSH="${REPO_SSH:-git@github.com:Hoang-Minh-Nghia/atlas-research-deploy.git}"
GIT_BRANCH_DEFAULT="main"

log()  { echo -e "\e[1;32m[news-agent]\e[0m $*"; }
warn() { echo -e "\e[1;33m[news-agent][warn]\e[0m $*"; }
die()  { echo -e "\e[1;31m[news-agent][error]\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Vui lòng chạy với quyền root (sudo bash scripts/news-agent-install.sh)."

as_agent() { runuser -u "${AGENT_USER}" -- "$@"; }

# =============================================================================
# 1. GÓI PHỤ THUỘC
# =============================================================================
MISSING=()
command -v git     >/dev/null 2>&1 || MISSING+=("git")
command -v python3 >/dev/null 2>&1 || MISSING+=("python3")
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || MISSING+=("python3-venv")
command -v ssh     >/dev/null 2>&1 || MISSING+=("openssh-client")
if ((${#MISSING[@]})); then
    log "Cài gói còn thiếu: ${MISSING[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || die "Cần Python >= 3.10 (Ubuntu 22.04 trở lên)."

# =============================================================================
# 2. USER HỆ THỐNG + THƯ MỤC (không dùng www-data, không chạy bằng root)
# =============================================================================
if ! id "${AGENT_USER}" >/dev/null 2>&1; then
    log "Tạo user hệ thống ${AGENT_USER}"
    useradd --system --home-dir "${STATE_DIR}" --shell /usr/sbin/nologin "${AGENT_USER}"
fi
install -d -m 750 -o "${AGENT_USER}" -g "${AGENT_USER}" "${STATE_DIR}" "${STATE_DIR}/drafts"
install -d -m 755 -o "${AGENT_USER}" -g "${AGENT_USER}" "${APP_DIR}"
install -d -m 700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${STATE_DIR}/.ssh"

# =============================================================================
# 3. DEPLOY KEY SSH (quyền ghi vào repo GitHub)
# =============================================================================
KEY="${STATE_DIR}/.ssh/id_ed25519"
if [[ ! -f "${KEY}" ]]; then
    log "Tạo deploy key SSH cho agent"
    as_agent ssh-keygen -q -t ed25519 -N "" -C "atlas-news-agent@$(hostname)" -f "${KEY}"
fi
cat > "${STATE_DIR}/.ssh/config" <<EOF
Host github.com
    IdentityFile ${KEY}
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
chown "${AGENT_USER}:${AGENT_USER}" "${STATE_DIR}/.ssh/config"
chmod 600 "${STATE_DIR}/.ssh/config"

# =============================================================================
# 4. FILE CẤU HÌNH (bí mật — chỉ root sửa, agent đọc)
# =============================================================================
if [[ ! -f "${ENV_FILE}" ]]; then
    log "Tạo ${ENV_FILE} (cần điền giá trị thật)"
    cat > "${ENV_FILE}" <<EOF
# Cấu hình agent tin tức — sửa xong chạy lại: sudo bash scripts/news-agent-install.sh
OPENROUTER_API_KEY=__CHANGE_ME__
# Model viết bài và model tạo ảnh trên OpenRouter (đổi theo model đang dùng ở dự án khác)
OPENROUTER_TEXT_MODEL=anthropic/claude-sonnet-4.5
OPENROUTER_IMAGE_MODEL=google/gemini-2.5-flash-image
FIGURES_PER_ARTICLE=1

# Bot tạo qua @BotFather; CHAT_ID là chat/nhóm nhận bản nháp;
# ALLOWED_USER_IDS là id Telegram (số) của người được bấm Duyệt, cách nhau bằng dấu phẩy.
TELEGRAM_BOT_TOKEN=__CHANGE_ME__
TELEGRAM_CHAT_ID=__CHANGE_ME__
TELEGRAM_ALLOWED_USER_IDS=__CHANGE_ME__

SOURCE_URL=https://vnexpress.net/tag/tai-san-so-1064801
MAX_PER_RUN=3
DRAFT_TTL_DAYS=7
GIT_BRANCH=${GIT_BRANCH_DEFAULT}
SITE_URL=https://atlas-research-vn.pages.dev
REPO_DIR=${APP_DIR}
STATE_DIR=${STATE_DIR}
EOF
fi
chown "root:${AGENT_USER}" "${ENV_FILE}"
chmod 640 "${ENV_FILE}"

# =============================================================================
# 5. CLONE REPO (cần deploy key đã được thêm trên GitHub)
# =============================================================================
if [[ ! -d "${APP_DIR}/.git" ]]; then
    log "Clone ${REPO_SSH} vào ${APP_DIR}"
    if ! as_agent env HOME="${STATE_DIR}" git clone -q "${REPO_SSH}" "${APP_DIR}"; then
        warn "Chưa clone được repo. Thêm public key dưới đây vào GitHub:"
        warn "  Repo → Settings → Deploy keys → Add deploy key → tick 'Allow write access'"
        echo
        cat "${KEY}.pub"
        echo
        die "Thêm key xong, chạy lại: sudo bash scripts/news-agent-install.sh"
    fi
fi
BRANCH="$(sed -n 's/^GIT_BRANCH=//p' "${ENV_FILE}" | tail -n1)"
BRANCH="${BRANCH:-${GIT_BRANCH_DEFAULT}}"
as_agent env HOME="${STATE_DIR}" git -C "${APP_DIR}" fetch -q origin "${BRANCH}"
as_agent env HOME="${STATE_DIR}" git -C "${APP_DIR}" checkout -q -B "${BRANCH}" "origin/${BRANCH}"
as_agent git -C "${APP_DIR}" config user.name  "Atlas News Bot"
as_agent git -C "${APP_DIR}" config user.email "news-bot@atlas-research.invalid"
[[ -d "${APP_DIR}/news_agent" ]] \
    || die "Nhánh ${BRANCH} chưa có thư mục news_agent/ — merge code agent vào nhánh này trước."

# =============================================================================
# 6. VIRTUALENV
# =============================================================================
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "Tạo virtualenv ${VENV_DIR}"
    as_agent python3 -m venv "${VENV_DIR}"
fi
as_agent "${VENV_DIR}/bin/pip" install -q --upgrade pip
as_agent "${VENV_DIR}/bin/pip" install -q -r "${APP_DIR}/news_agent/requirements.txt"

# =============================================================================
# 7. SYSTEMD
# =============================================================================
COMMON="User=${AGENT_USER}
Group=${AGENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=HOME=${STATE_DIR} PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR} ${STATE_DIR}"

cat > /etc/systemd/system/atlas-news-scan.service <<EOF
[Unit]
Description=Atlas Research — quét tin mới và gửi bản nháp lên Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
${COMMON}
ExecStart=${VENV_DIR}/bin/python -m news_agent.scan
TimeoutStartSec=45min
EOF

cat > /etc/systemd/system/atlas-news-scan.timer <<EOF
[Unit]
Description=Atlas Research — quét tin mỗi 60 phút

[Timer]
OnCalendar=hourly
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/atlas-news-bot.service <<EOF
[Unit]
Description=Atlas Research — bot Telegram duyệt bài
After=network-online.target
Wants=network-online.target

[Service]
${COMMON}
ExecStart=${VENV_DIR}/bin/python -m news_agent.bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

# =============================================================================
# 8. BẬT DỊCH VỤ (chỉ khi cấu hình đã điền đủ)
# =============================================================================
if grep -q '__CHANGE_ME' "${ENV_FILE}"; then
    warn "${ENV_FILE} còn giá trị __CHANGE_ME__ — chưa bật dịch vụ."
    warn "Điền xong chạy lại: sudo bash scripts/news-agent-install.sh"
    exit 0
fi
systemctl enable --now atlas-news-bot.service atlas-news-scan.timer
systemctl restart atlas-news-bot.service
log "Đã bật bot duyệt bài và lịch quét mỗi giờ."
log "Chạy thử ngay:   sudo systemctl start atlas-news-scan"
log "Xem log bot:     sudo journalctl -u atlas-news-bot -f"
log "Xem log quét:    sudo journalctl -u atlas-news-scan -n 100"
