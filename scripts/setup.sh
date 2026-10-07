#!/usr/bin/env bash
# =============================================================================
# ATLAS RESEARCH — setup.sh
# Tạo cấu trúc thư mục, triển khai Affiliate Hub, phân quyền chuẩn,
# cài WP-CLI, cài script bảo trì và nạp cấu hình Nginx.
# Chạy với quyền root/sudo trên server Linux (Ubuntu/Debian, LEMP stack):
#   sudo bash scripts/setup.sh
#
# Có thể chạy lại nhiều lần (idempotent). Lần đầu (chưa có chứng chỉ SSL),
# script cài cấu hình Nginx tạm chỉ có HTTP để xin chứng chỉ; sau khi chạy
# certbot, chạy lại script này để chuyển sang cấu hình HTTPS đầy đủ.
# =============================================================================
set -euo pipefail

# ---- Cấu hình chung (chỉnh lại nếu domain/user khác) ----
DOMAIN="atlasresearch.blog"
WEB_ROOT="/var/www/${DOMAIN}"
WEB_USER="www-data"
WEB_GROUP="www-data"
MAINT_BIN="/usr/local/sbin/atlas-maintenance"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "${SCRIPT_DIR}")"
HUB_SRC="${PKG_DIR}/affiliate_hub"
CERT_FILE="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"

log()  { echo -e "\e[1;32m[setup]\e[0m $*"; }
warn() { echo -e "\e[1;33m[setup][warn]\e[0m $*"; }
die()  { echo -e "\e[1;31m[setup][error]\e[0m $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Vui lòng chạy script này với quyền root (sudo bash scripts/setup.sh)."
fi

# =============================================================================
# 1. KIỂM TRA CÁC THÀNH PHẦN PHỤ THUỘC
# =============================================================================
log "Kiểm tra các thành phần phụ thuộc ..."
MISSING=()
command -v nginx  >/dev/null 2>&1 || MISSING+=("nginx")
command -v curl   >/dev/null 2>&1 || MISSING+=("curl")
command -v unzip  >/dev/null 2>&1 || MISSING+=("unzip")
command -v mysql  >/dev/null 2>&1 || MISSING+=("mysql-server (hoặc mariadb-server)")
compgen -G "/usr/sbin/php-fpm*" >/dev/null || MISSING+=("php-fpm php-mysql")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Chưa cài: ${MISSING[*]}"
    warn "Cài bằng: sudo apt update && sudo apt install -y nginx php-fpm php-mysql mysql-server curl unzip"
    command -v nginx >/dev/null 2>&1 || die "Thiếu Nginx — không thể tiếp tục."
else
    log "Đủ các thành phần cơ bản."
fi

# =============================================================================
# 2. TẠO CẤU TRÚC THƯ MỤC
# =============================================================================
log "Tạo cấu trúc thư mục tại ${WEB_ROOT} ..."
mkdir -p "${WEB_ROOT}/wordpress/.well-known/acme-challenge"
mkdir -p "${WEB_ROOT}/affiliate_hub/"{css,js,img/partners}
mkdir -p "${WEB_ROOT}/logs"
touch "${WEB_ROOT}/logs/access.log" "${WEB_ROOT}/logs/error.log"

# =============================================================================
# 3. TRIỂN KHAI AFFILIATE HUB TĨNH (/doi-tac/)
# =============================================================================
if [[ -f "${HUB_SRC}/index.html" ]]; then
    log "Sao chép Affiliate Hub từ ${HUB_SRC} ..."
    cp -a "${HUB_SRC}/." "${WEB_ROOT}/affiliate_hub/"
else
    warn "Không thấy ${HUB_SRC}/index.html — bỏ qua bước triển khai Affiliate Hub."
fi

# =============================================================================
# 4. PHÂN QUYỀN CHUẨN CHO www-data
# =============================================================================
log "Phân quyền sở hữu ${WEB_USER}:${WEB_GROUP}, thư mục 755 / file 644 ..."
chown -R "${WEB_USER}:${WEB_GROUP}" "${WEB_ROOT}"
find "${WEB_ROOT}" -type d -exec chmod 755 {} +
find "${WEB_ROOT}" -type f -exec chmod 644 {} +
# Thư mục log cần quyền ghi cho nhóm www-data
find "${WEB_ROOT}/logs" -type d -exec chmod 775 {} +
find "${WEB_ROOT}/logs" -type f -exec chmod 664 {} +

# =============================================================================
# 5. CÀI WP-CLI (nếu chưa có)
# =============================================================================
if command -v wp >/dev/null 2>&1; then
    log "WP-CLI đã có: $(wp --version --allow-root 2>/dev/null || true)"
else
    log "Đang tải WP-CLI ..."
    curl -fsSL -o /tmp/wp-cli.phar https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
    install -m 755 /tmp/wp-cli.phar /usr/local/bin/wp
    rm -f /tmp/wp-cli.phar
    if wp --version --allow-root >/dev/null 2>&1; then
        log "WP-CLI đã cài xong: $(wp --version --allow-root)"
    else
        warn "Đã cài WP-CLI nhưng chưa chạy được — cần cài php-cli (sudo apt install -y php-cli)."
    fi
fi

# =============================================================================
# 6. CÀI SCRIPT BẢO TRÌ (chỉ root được sửa — an toàn khi chạy bằng cron)
#    Không chạy cron root từ thư mục web: www-data có quyền ghi ở đó.
# =============================================================================
if [[ -f "${SCRIPT_DIR}/maintenance.sh" ]]; then
    install -m 750 -o root -g root "${SCRIPT_DIR}/maintenance.sh" "${MAINT_BIN}"
    log "Đã cài script bảo trì: ${MAINT_BIN}"
fi

# =============================================================================
# 7. NẠP CẤU HÌNH NGINX
# =============================================================================
NGINX_SRC="${PKG_DIR}/nginx/atlasresearch.blog.conf"
NGINX_DST="/etc/nginx/sites-available/${DOMAIN}"
NGINX_LINK="/etc/nginx/sites-enabled/${DOMAIN}"

# Socket PHP-FPM thực tế (php8.1 / 8.2 / 8.3 ... tuỳ bản phân phối)
PHP_SOCK=""
for sock in /run/php/php*-fpm.sock; do
    [[ -S "${sock}" ]] && PHP_SOCK="${sock}"
done
if [[ -z "${PHP_SOCK}" ]]; then
    PHP_SOCK="/run/php/php8.2-fpm.sock"
    warn "Không tìm thấy socket PHP-FPM đang chạy — tạm dùng ${PHP_SOCK}."
fi

TMP_CONF="$(mktemp)"
trap 'rm -f "${TMP_CONF}"' EXIT

if [[ -f "${CERT_FILE}" ]]; then
    [[ -f "${NGINX_SRC}" ]] || die "Không tìm thấy ${NGINX_SRC}."
    log "Đã có chứng chỉ SSL -> cài cấu hình HTTPS đầy đủ (PHP-FPM: ${PHP_SOCK}) ..."
    sed -e "s#atlasresearch\.blog#${DOMAIN}#g" \
        -e "s#unix:/run/php/php[0-9.]*-fpm\.sock#unix:${PHP_SOCK}#g" \
        "${NGINX_SRC}" > "${TMP_CONF}"
    MODE="https"
else
    warn "Chưa có chứng chỉ SSL -> cài cấu hình TẠM (chỉ HTTP) để xin chứng chỉ."
    cat > "${TMP_CONF}" <<NGINX
# Cấu hình TẠM do setup.sh tạo — chỉ dùng để xin chứng chỉ Let's Encrypt.
# Sau khi có chứng chỉ, chạy lại setup.sh để thay bằng cấu hình HTTPS đầy đủ.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};
    root ${WEB_ROOT}/wordpress;

    location ^~ /.well-known/acme-challenge/ {
        default_type text/plain;
    }

    location / {
        return 503;
    }
}
NGINX
    MODE="bootstrap"
fi

# Sao lưu cấu hình cũ để khôi phục nếu bản mới lỗi cú pháp
BACKUP=""
if [[ -f "${NGINX_DST}" ]]; then
    BACKUP="${NGINX_DST}.bak.$(date +%Y%m%d%H%M%S)"
    cp -a "${NGINX_DST}" "${BACKUP}"
fi

install -m 644 "${TMP_CONF}" "${NGINX_DST}"
ln -sf "${NGINX_DST}" "${NGINX_LINK}"

log "Kiểm tra cú pháp Nginx ..."
if nginx -t; then
    systemctl reload nginx
    log "Nginx đã reload thành công."
    [[ -n "${BACKUP}" ]] && rm -f "${BACKUP}"
else
    if [[ -n "${BACKUP}" ]]; then
        mv -f "${BACKUP}" "${NGINX_DST}"
        warn "Đã khôi phục cấu hình Nginx trước đó."
    else
        rm -f "${NGINX_LINK}"
    fi
    die "Cấu hình Nginx có lỗi cú pháp, vui lòng kiểm tra lại."
fi

# =============================================================================
# HOÀN TẤT
# =============================================================================
log "✅ Setup hoàn tất. Web root: ${WEB_ROOT}"
log "   - Khối động (WordPress):     ${WEB_ROOT}/wordpress"
log "   - Khối tĩnh (Affiliate Hub): ${WEB_ROOT}/affiliate_hub"
log "   - Log riêng dự án:           ${WEB_ROOT}/logs"

if [[ "${MODE}" == "bootstrap" ]]; then
    echo
    log "BƯỚC TIẾP THEO — xin chứng chỉ SSL rồi chạy lại setup.sh:"
    echo "    sudo apt install -y certbot"
    echo "    sudo certbot certonly --webroot -w ${WEB_ROOT}/wordpress \\"
    echo "         -d ${DOMAIN} -d www.${DOMAIN} \\"
    echo "         --deploy-hook \"systemctl reload nginx\""
    echo "    sudo bash ${SCRIPT_DIR}/setup.sh"
fi
