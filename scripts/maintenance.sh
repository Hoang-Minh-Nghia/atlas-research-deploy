#!/usr/bin/env bash
# =============================================================================
# ATLAS RESEARCH — maintenance.sh
# Script bảo trì & xử lý sự cố: dọn lock file kẹt, kill process treo,
# reset quyền www-data. Chạy với sudo.
#
# setup.sh cài script này thành /usr/local/sbin/atlas-maintenance (chỉ root sửa được).
# Cách dùng:
#   sudo atlas-maintenance clean-locks       # dọn lock file kẹt trong /var/tmp
#   sudo atlas-maintenance kill-zombies      # rà soát & xử lý PHP-FPM/Nginx zombie
#   sudo atlas-maintenance fix-permissions   # reset chown/chmod chuẩn
#   sudo atlas-maintenance all               # chạy tuần tự cả 3 bước trên
# =============================================================================
set -uo pipefail

DOMAIN="atlasresearch.blog"
WEB_ROOT="/var/www/${DOMAIN}"
WEB_USER="www-data"
WEB_GROUP="www-data"
LOCK_DIR="/var/tmp"
# Ngưỡng RAM (%) coi là bất thường cho 1 tiến trình PHP-FPM/Nginx đơn lẻ
RAM_THRESHOLD_PERCENT=25

log()  { echo -e "\e[1;32m[maintenance]\e[0m $*"; }
warn() { echo -e "\e[1;33m[maintenance][warn]\e[0m $*"; }
die()  { echo -e "\e[1;31m[maintenance][error]\e[0m $*" >&2; exit 1; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        die "Cần quyền root. Chạy lại với: sudo $0 $*"
    fi
}

# Tìm service PHP-FPM thực tế (php8.1-fpm / php8.2-fpm / php8.3-fpm ...)
detect_php_fpm_service() {
    systemctl list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '{print $1}' \
        | grep -E '^php[0-9.]+-fpm\.service$' \
        | sort -V | tail -n1 \
        | sed 's/\.service$//' || true
}

# =============================================================================
# 1. DỌN DẸP LOCK FILE KẸT TRONG /var/tmp
#    Xóa các file .lock không được tiến trình nào đang giữ (không có PID
#    tương ứng còn sống), hoặc đã "già" hơn 24h — dấu hiệu tiến trình treo
#    để lại mà không dọn.
# =============================================================================
clean_locks() {
    log "Đang quét lock file trong ${LOCK_DIR} ..."
    local found=0

    while IFS= read -r -d '' lockfile; do
        found=1
        # Nếu lock file chứa PID, kiểm tra tiến trình đó còn sống không
        local pid
        pid=$(grep -Eo '[0-9]+' "$lockfile" 2>/dev/null | head -n1 || true)

        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            log "  Giữ lại: $lockfile (PID $pid vẫn đang chạy)"
            continue
        fi

        # File cũ hơn 24h (1440 phút) mà không map được tới tiến trình sống -> xóa
        if [[ -z "$(find "$lockfile" -mmin -1440 2>/dev/null)" ]]; then
            log "  Xóa lock file kẹt: $lockfile"
            rm -f "$lockfile"
        else
            log "  Bỏ qua (còn mới, chưa đủ 24h): $lockfile"
        fi
    done < <(find "${LOCK_DIR}" -maxdepth 1 -type f -name "*.lock" -print0 2>/dev/null)

    if [[ $found -eq 0 ]]; then
        log "Không tìm thấy lock file nào trong ${LOCK_DIR}."
    fi

    log "Hoàn tất dọn dẹp lock file."
}

# =============================================================================
# 2. QUÉT & XỬ LÝ PHP-FPM / NGINX ZOMBIE / TREO
#    - Liệt kê tiến trình ở trạng thái zombie (Z)
#    - Liệt kê tiến trình PHP-FPM/Nginx chiếm RAM bất thường
#    - Dùng killall -9 để cưỡng chế khi cần (chỉ khi có xác nhận hoặc --force)
# =============================================================================
kill_zombies() {
    local force="${1:-}"

    log "Đang quét tiến trình zombie (trạng thái Z) ..."
    local zombies
    zombies=$(ps -eo pid,ppid,stat,comm | awk '$3 ~ /^Z/')
    if [[ -n "$zombies" ]]; then
        warn "Phát hiện tiến trình zombie:"
        echo "$zombies"
        warn "Zombie process không thể kill trực tiếp — cần xử lý tiến trình cha (PPID ở trên)."
    else
        log "Không có tiến trình zombie."
    fi

    log "Đang quét PHP-FPM / Nginx chiếm RAM bất thường (> ${RAM_THRESHOLD_PERCENT}%) ..."
    local heavy
    heavy=$(ps -eo pid,comm,%mem --sort=-%mem | awk -v t="${RAM_THRESHOLD_PERCENT}" '$3+0 > t && ($2 ~ /php-fpm/ || $2 ~ /nginx/)')

    if [[ -z "$heavy" ]]; then
        log "Không có tiến trình PHP-FPM/Nginx nào vượt ngưỡng RAM."
        return 0
    fi

    warn "Các tiến trình chiếm RAM cao:"
    echo "$heavy"

    if [[ "$force" != "--force" ]]; then
        warn "Chạy lại với 'kill-zombies --force' để cưỡng chế kill các tiến trình PHP-FPM/Nginx nói trên."
        return 0
    fi

    local php_service
    php_service="$(detect_php_fpm_service)"

    warn "Đang cưỡng chế kết thúc tiến trình php-fpm/nginx bất thường (killall -9) ..."
    # Chỉ áp dụng cho các service chuyên dụng, KHÔNG kill toàn bộ 'php' để tránh
    # ảnh hưởng các site khác trên cùng server nếu có.
    if [[ -n "$php_service" ]]; then
        # Tên service php8.3-fpm  ->  tên tiến trình php-fpm8.3
        local php_version="${php_service#php}"
        killall -9 "php-fpm${php_version%-fpm}" 2>/dev/null || true
    fi
    killall -9 php-fpm 2>/dev/null || true
    killall -9 nginx   2>/dev/null || true

    log "Đã gửi tín hiệu SIGKILL. Khởi động lại service ..."
    if [[ -n "$php_service" ]]; then
        systemctl restart "$php_service" && log "Đã khởi động lại ${php_service}."
    else
        warn "Không tìm thấy service PHP-FPM nào (php*-fpm), hãy khởi động lại thủ công."
    fi
    systemctl restart nginx
    log "Đã khởi động lại Nginx."
}

# =============================================================================
# 3. RESET PHÂN QUYỀN CHUẨN www-data:www-data
#    Fix dứt điểm lỗi Permission denied (errno=13) khi ghi log/cache mới.
# =============================================================================
fix_permissions() {
    log "Đang reset quyền sở hữu về ${WEB_USER}:${WEB_GROUP} cho ${WEB_ROOT} ..."
    chown -R "${WEB_USER}:${WEB_GROUP}" "${WEB_ROOT}"

    log "Đang reset quyền thư mục (755) / file (644) ..."
    find "${WEB_ROOT}" -type d -exec chmod 755 {} +
    find "${WEB_ROOT}" -type f -exec chmod 644 {} +

    # Thư mục cần quyền ghi (log, cache, uploads): thư mục 775 / file 664
    # (không dùng chmod -R 775 để tránh gắn quyền thực thi cho mọi file)
    for dir in "${WEB_ROOT}/logs" "${WEB_ROOT}/wordpress/wp-content"; do
        if [[ -d "$dir" ]]; then
            find "$dir" -type d -exec chmod 775 {} +
            find "$dir" -type f -exec chmod 664 {} +
            log "  Đã cấp quyền ghi cho: $dir"
        fi
    done

    # wp-config.php chứa mật khẩu database -> chỉ owner + group đọc được
    if [[ -f "${WEB_ROOT}/wordpress/wp-config.php" ]]; then
        chmod 640 "${WEB_ROOT}/wordpress/wp-config.php"
    fi

    log "Hoàn tất reset phân quyền. errno=13 (Permission denied) sẽ được khắc phục."
}

# =============================================================================
# MAIN
# =============================================================================
require_root "$@"

CMD="${1:-}"
case "$CMD" in
    clean-locks)
        clean_locks
        ;;
    kill-zombies)
        kill_zombies "${2:-}"
        ;;
    fix-permissions)
        fix_permissions
        ;;
    all)
        clean_locks
        kill_zombies "${2:-}"
        fix_permissions
        ;;
    *)
        echo "Cách dùng: sudo $0 {clean-locks|kill-zombies [--force]|fix-permissions|all [--force]}"
        exit 1
        ;;
esac
