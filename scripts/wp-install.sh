#!/usr/bin/env bash
# =============================================================================
# ATLAS RESEARCH — wp-install.sh
# Tự động hóa cài đặt WordPress core + database + plugin thiết yếu bằng WP-CLI.
# Yêu cầu: đã chạy setup.sh (WP-CLI, Nginx) và đã có MySQL/MariaDB.
#
# Chạy với quyền root. Truyền mật khẩu qua biến môi trường để không phải
# lưu mật khẩu trong file:
#   sudo DB_PASS='...' ADMIN_PASS='...' bash scripts/wp-install.sh
#
# Script có thể chạy lại an toàn: bước nào đã xong sẽ được bỏ qua.
# =============================================================================
set -euo pipefail

# ---- Cấu hình — có thể ghi đè bằng biến môi trường ----
DOMAIN="${DOMAIN:-atlasresearch.blog}"
WP_PATH="${WP_PATH:-/var/www/${DOMAIN}/wordpress}"
SITE_TITLE="${SITE_TITLE:-Atlas Research}"
SITE_TAGLINE="${SITE_TAGLINE:-Góc kiến thức & hệ thống liên kết đa sàn}"
SITE_URL="${SITE_URL:-https://${DOMAIN}}"

DB_NAME="${DB_NAME:-atlas_research_db}"
DB_USER="${DB_USER:-atlas_research_user}"
DB_PASS="${DB_PASS:-__CHANGE_ME_STRONG_PASSWORD__}"
DB_HOST="${DB_HOST:-localhost}"

ADMIN_USER="${ADMIN_USER:-atlas_admin}"
ADMIN_PASS="${ADMIN_PASS:-__CHANGE_ME_STRONG_PASSWORD__}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@${DOMAIN}}"

WEB_USER="www-data"
WEB_GROUP="www-data"

log()  { echo -e "\e[1;32m[wp-install]\e[0m $*"; }
warn() { echo -e "\e[1;33m[wp-install][warn]\e[0m $*"; }
die()  { echo -e "\e[1;31m[wp-install][error]\e[0m $*" >&2; exit 1; }
wpc()  { wp --path="${WP_PATH}" --allow-root "$@"; }

[[ $EUID -eq 0 ]] || die "Cần quyền root: sudo DB_PASS='...' ADMIN_PASS='...' bash $0"
command -v wp    >/dev/null 2>&1 || die "WP-CLI chưa được cài. Chạy setup.sh trước."
command -v mysql >/dev/null 2>&1 || die "MySQL/MariaDB client chưa được cài."

for var in DB_PASS ADMIN_PASS; do
    value="${!var}"
    if [[ "${value}" == __CHANGE_ME* ]]; then
        die "Chưa đặt ${var}. Ví dụ: sudo DB_PASS='MatKhauManh#1' ADMIN_PASS='MatKhauManh#2' bash $0"
    fi
    if [[ ${#value} -lt 12 ]]; then
        die "${var} quá ngắn — dùng mật khẩu tối thiểu 12 ký tự."
    fi
done
unset value

mkdir -p "${WP_PATH}"

# =============================================================================
# 1. TẠO DATABASE + USER MYSQL
#    Ubuntu/Debian mặc định cho root đăng nhập qua socket (không cần mật khẩu);
#    nếu không được, script sẽ hỏi mật khẩu root MySQL.
# =============================================================================
if mysql -u root -e "SELECT 1" >/dev/null 2>&1; then
    MYSQL_ROOT=(mysql -u root)
else
    MYSQL_ROOT=(mysql -u root -p)
fi

DB_PASS_SQL="${DB_PASS//\\/\\\\}"
DB_PASS_SQL="${DB_PASS_SQL//\'/\\\'}"

log "Tạo database '${DB_NAME}' và user '${DB_USER}' (nếu chưa có) ..."
"${MYSQL_ROOT[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'${DB_HOST}' IDENTIFIED BY '${DB_PASS_SQL}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'${DB_HOST}';
FLUSH PRIVILEGES;
SQL

# =============================================================================
# 2. TẢI MÃ NGUỒN WORDPRESS CORE
# =============================================================================
if [[ -f "${WP_PATH}/wp-load.php" ]]; then
    log "WordPress core đã có tại ${WP_PATH} — bỏ qua bước tải."
else
    log "Tải WordPress core (tiếng Việt) vào ${WP_PATH} ..."
    wpc core download --locale=vi
fi

# =============================================================================
# 3. TẠO wp-config.php
# =============================================================================
if [[ -f "${WP_PATH}/wp-config.php" ]]; then
    log "wp-config.php đã tồn tại — giữ nguyên."
else
    log "Tạo wp-config.php ..."
    wpc config create \
        --dbname="${DB_NAME}" \
        --dbuser="${DB_USER}" \
        --dbpass="${DB_PASS}" \
        --dbhost="${DB_HOST}" \
        --dbcharset=utf8mb4 \
        --locale=vi
    # Ghi file trực tiếp, không xin FTP khi cài plugin qua wp-admin
    wpc config set FS_METHOD direct
    # Không cho sửa code theme/plugin trong wp-admin (giảm thiệt hại nếu lộ tài khoản)
    wpc config set DISALLOW_FILE_EDIT true --raw
fi

# =============================================================================
# 4. CÀI ĐẶT WORDPRESS
# =============================================================================
if wpc core is-installed >/dev/null 2>&1; then
    log "WordPress đã được cài — bỏ qua bước install."
else
    log "Cài đặt WordPress ..."
    wpc core install \
        --url="${SITE_URL}" \
        --title="${SITE_TITLE}" \
        --admin_user="${ADMIN_USER}" \
        --admin_password="${ADMIN_PASS}" \
        --admin_email="${ADMIN_EMAIL}" \
        --skip-email
fi

# =============================================================================
# 5. THIẾT LẬP CƠ BẢN: ngôn ngữ, múi giờ, permalink chuẩn SEO
# =============================================================================
log "Thiết lập ngôn ngữ, múi giờ, mô tả site ..."
wpc language core install vi --activate >/dev/null 2>&1 || warn "Không kích hoạt được gói tiếng Việt (bỏ qua)."
wpc option update timezone_string "Asia/Ho_Chi_Minh"
wpc option update date_format "d/m/Y"
wpc option update blogdescription "${SITE_TAGLINE}"

log "Thiết lập permalink /%postname%/ ..."
wpc rewrite structure '/%postname%/'
wpc rewrite flush

# =============================================================================
# 6. CÀI ĐẶT PLUGIN THIẾT YẾU
# =============================================================================
install_plugin() {
    if wpc plugin is-installed "$1"; then
        wpc plugin activate "$1" >/dev/null 2>&1 || true
        log "Plugin $1 đã có — đảm bảo đã kích hoạt."
    else
        log "Cài đặt & kích hoạt $1 ..."
        wpc plugin install "$1" --activate
    fi
}

install_plugin seo-by-rank-math
# Lưu ý: trên Nginx, LiteSpeed Cache chỉ dùng được phần tối ưu CSS/JS/ảnh;
# page cache của plugin này chỉ hoạt động trên máy chủ LiteSpeed.
install_plugin litespeed-cache
install_plugin redis-cache
# Bật Redis object cache (cần: sudo apt install -y redis-server php-redis)
wpc redis enable >/dev/null 2>&1 || warn "Chưa bật được Redis — cần cài & chạy redis-server + php-redis trước."

log "Dọn plugin mặc định không cần thiết ..."
wpc plugin delete hello akismet >/dev/null 2>&1 || true

# =============================================================================
# 7. FIX QUYỀN SAU KHI CÀI (tránh lỗi ghi file khi cập nhật/cache)
# =============================================================================
log "Reset quyền ${WEB_USER} cho thư mục WordPress ..."
chown -R "${WEB_USER}:${WEB_GROUP}" "${WP_PATH}"
find "${WP_PATH}" -type d -exec chmod 755 {} +
find "${WP_PATH}" -type f -exec chmod 644 {} +
find "${WP_PATH}/wp-content" -type d -exec chmod 775 {} +
find "${WP_PATH}/wp-content" -type f -exec chmod 664 {} +
chmod 640 "${WP_PATH}/wp-config.php"

log "✅ Hoàn tất cài đặt WordPress tại ${SITE_URL}"
log "   Đăng nhập: ${SITE_URL}/wp-admin  (user: ${ADMIN_USER})"
