# Hướng dẫn release và rollback cho phiên bản v0.1.0

Tài liệu này dùng để lưu lại trạng thái hiện tại của dự án như một release chính thức `v0.1.0`, để sau này có thể rollback khi cần.

## 1) Tạo release v0.1.0 cho bản hiện tại

Trước khi tạo release, hãy chắc chắn repo đang ở trạng thái ổn định và đã test kỹ.

```bash
cd /Users/apple/Documents/Workspace/atlas-research-deploy

git status

git add .
git commit -m "Release v0.1.0"

git tag -a v0.1.0 -m "Release v0.1.0"

git push origin main --tags
```

Nếu branch không phải `main`, thay bằng branch đang dùng, ví dụ:

```bash
git push origin master --tags
```

### Backup nhanh cho bản deploy hiện tại

Để rollback dễ hơn, nên backup cả source code lẫn dữ liệu deploy trên server:

```bash
sudo tar -czf /tmp/atlasresearch-v0.1.0-backup-$(date +%F-%H%M%S).tar.gz \
  /var/www/atlasresearch.blog
```

Nếu cần backup cả database WordPress:

```bash
mysqldump -u root -p atlasresearch > /tmp/atlasresearch-db-v0.1.0.sql
```

---

## 2) Làm gì nếu cần rollback về v0.1.0

### Cách A: rollback bằng git tag

Nếu dự án đang có lỗi ở version mới hơn và muốn quay lại trạng thái `v0.1.0`:

```bash
cd /Users/apple/Documents/Workspace/atlas-research-deploy

git checkout v0.1.0
```

Nếu đang deploy từ repo trên server, sau đó sync lại code về server:

```bash
sudo rsync -av --delete /Users/apple/Documents/Workspace/atlas-research-deploy/ /var/www/atlasresearch.blog/
```

Sau đó reload service nếu cần:

```bash
sudo systemctl reload nginx
sudo systemctl reload php8.2-fpm
```

> Nếu hệ thống chạy trên PHP version khác, thay `php8.2-fpm` bằng phiên bản tương ứng trên server.

### Cách B: rollback bằng file backup

Nếu bạn đã backup source từ server hoặc branch trước đó:

```bash
sudo rm -rf /var/www/atlasresearch.blog
sudo tar -xzf /tmp/atlasresearch-v0.1.0-backup-2026-10-29-120000.tar.gz -C /
```

Hoặc nếu backup được extract vào thư mục khác, copy lại vào `/var/www/atlasresearch.blog` rồi reload Nginx.

---

## 3) Rollback dữ liệu WordPress nếu cần

Nếu lỗi liên quan đến database hoặc nội dung WordPress:

```bash
mysql -u root -p
CREATE DATABASE atlasresearch;
USE atlasresearch;
SOURCE /tmp/atlasresearch-db-v0.1.0.sql;
```

Nếu WordPress đang chạy với config chứa DB name khác, kiểm tra lại file:

```bash
cat /var/www/atlasresearch.blog/wordpress/wp-config.php
```

Sau đó reload PHP/Nginx:

```bash
sudo systemctl reload nginx
sudo systemctl reload php8.2-fpm
```

---

## 4) Checklist rollback nhanh

Khi cần rollback, làm theo thứ tự sau:

1. Dừng hoặc tạm thời ngưng deploy mới.
2. Backup trạng thái hiện tại trước khi đổi.
3. Quay về tag `v0.1.0` hoặc khôi phục package backup.
4. Copy lại code lên server.
5. Restore DB nếu cần.
6. Reload Nginx/PHP-FPM.
7. Kiểm tra lại:
   - `https://atlasresearch.blog/`
   - `https://atlasresearch.blog/doi-tac/`
   - WordPress admin
   - quyền file/folder

---

## 5) Ghi chú quan trọng

- Luôn lưu `git tag v0.1.0` để có điểm rollback rõ ràng.
- Không nên rollback bằng cách chỉ đổi code mà bỏ qua database, vì WordPress có thể chưa đồng bộ phiên bản dữ liệu.
- Nếu site đang chạy production, nên test trên staging trước khi rollback trực tiếp trên server chính.
- Nếu deploy dùng CI/CD / hosting khác, hãy thay lệnh `rsync` bằng lệnh deploy tương ứng của hệ thống đó.

---

## 6) Mẫu lệnh tổng hợp

```bash
cd /Users/apple/Documents/Workspace/atlas-research-deploy
git checkout v0.1.0
sudo rsync -av --delete /Users/apple/Documents/Workspace/atlas-research-deploy/ /var/www/atlasresearch.blog/
sudo systemctl reload nginx
sudo systemctl reload php8.2-fpm
```

Nếu bạn muốn có một ví dụ lệnh rollback hoàn chỉnh dùng cho dự án này, hãy chạy theo checklist trên và lưu lại output của từng lần deploy để dễ đối chiếu sau này.
