# MyBike'nCar – ghi chú cho AI

Chủ nhà nói tiếng Việt, không phải lập trình viên. Trả lời tiếng Việt, ngắn gọn; mỗi lần sửa phải báo tên phiên bản.

## Cấu trúc
- `index.html` – toàn bộ app (một tệp). `APP_VERSION` là số phiên bản duy nhất; máy chủ đọc nó để báo phiên bản.
- `sw.js` – cache offline. **Mỗi lần đổi app phải đổi `VERSION` trong sw.js giống `APP_VERSION`**, nếu không iPhone không nhận bản mới.
- `server/app.py` – FastAPI + SQLite. API: `/api/info`, `/api/state`, `PUT|DELETE /api/{parkings|maint}/{id}`, `POST /api/photos`, `GET /api/photos/{id}/{thumb|full}`, `DELETE /api/photos/{id}`, `/api/backup`, `/api/import`, `GET|POST /api/update`.
- `server/update.sh` – git pull + `docker compose up -d --build`, chờ healthy, lỗi thì quay về bản cũ (`.bad_commit`). Ghi trạng thái `data/requests/update.status`.
- `server/install.sh` – cài lần đầu: thư mục, systemd `vmbikencar-update.path` (nghe `data/requests/update.req`), container, `tailscale serve --https=8446 8096`.
- `docker-compose.yml` – container `vm_bikencar`, cổng 8096, gắn `../data`, `../backup`.

## Đồng bộ
iPhone giữ đủ dữ liệu trong IndexedDB. Mọi thay đổi vào `outbox`, đẩy lên máy chủ khi có mạng, rồi kéo `/api/state` về (máy chủ là bản chuẩn; bản ghi còn trong outbox thì giữ bản trên máy). Ảnh chụp ở máy khác lấy từ `/api/photos/…`, service worker cache lại để xem offline. Trên GitHub Pages (`*.github.io`) không có máy chủ nên chỉ lưu trên máy.

## Hệ thống OMV
Máy `192.168.50.231`, Tailscale `100.78.213.85`. DAS = `/srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c`. Thư mục app `DAS/VM_bikencar/{web,data,backup}`.

## Nhật ký phiên bản
- **v1.0.0** (01/11/2026) – Chạy trên OMV: máy chủ FastAPI + SQLite, đồng bộ offline-first, nút Cập nhật ngay trong Cài đặt, sao lưu/khôi phục qua máy chủ.
- 4 (27/10/2026) – Bỏ thông báo sau khi chụp ảnh nhanh.
- 3 (27/10/2026) – Tự cập nhật khi mở app, hiện số phiên bản.
- 2 (27/10/2026) – Dọn sạch ảnh có xác nhận; tách ảnh đỗ xe và ảnh phiếu bảo dưỡng.
- 1 (27/10/2026) – Bản đầu, chỉ lưu trên iPhone (GitHub Pages).
