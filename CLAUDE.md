# MyBike'nCar – ghi chú cho AI

Chủ nhà nói tiếng Việt, không phải lập trình viên. Trả lời tiếng Việt, ngắn gọn; mỗi lần sửa phải báo tên phiên bản.

## Cấu trúc
- `index.html` – toàn bộ app (một tệp). `APP_VERSION` là số phiên bản duy nhất; máy chủ đọc nó để báo phiên bản.
- `sw.js` – cache offline. **Mỗi lần đổi app phải đổi `VERSION` trong sw.js giống `APP_VERSION`**, nếu không iPhone không nhận bản mới.
- `server/app.py` – FastAPI + SQLite. API: `/api/info`, `/api/state`, `PUT|DELETE /api/{parkings|maint}/{id}`, `POST /api/photos`, `GET /api/photos/{id}/{thumb|full}`, `DELETE /api/photos/{id}`, `/api/backup`, `/api/import`, `GET|POST /api/update`, `GET|POST /api/mail`, `POST /api/mail/check`, `GET /api/fees/{id}/qr.png?tn=`, `/api/push/{key,subscribe,test}`. Kho đồng bộ: `parkings`, `maint`, `photos`, `fees` (phí, máy chủ tạo từ email, iPhone đánh dấu `paidAt`), `prefs` (`profile`: căn hộ, biển số).
- `server/feemail.py` – đọc Gmail qua IMAP (chỉ đọc, `BODY.PEEK`), nhận diện email báo phí của BQL Seasons Avenue (`parse_fee`), tạo chuỗi VietQR (`vietqr_payload`). Cấu hình Gmail ở `data/mail.json` (quyền 600, KHÔNG lên GitHub, không có trong bản sao lưu). Thông báo đẩy: khóa VAPID `data/vapid_private.pem`, danh sách iPhone `data/push.json`.
- `server/gmail.html` – trang hướng dẫn `/gmail` cài Apps Script (chép mã có khóa bí mật, dán URL /exec).
- `server/update.sh` – git pull + `docker compose up -d --build`, chờ healthy, lỗi thì quay về bản cũ (`.bad_commit`). Ghi trạng thái `data/requests/update.status`.
- `server/install.sh` – cài lần đầu: thư mục, systemd `vmbikencar-update.path` (nghe `data/requests/update.req`), container, `tailscale serve --https=8446 8096`.
- `docker-compose.yml` – container `vm_bikencar`, cổng 8096, gắn `../data`, `../backup`.

## Đồng bộ
iPhone giữ đủ dữ liệu trong IndexedDB. Mọi thay đổi vào `outbox`, đẩy lên máy chủ khi có mạng, rồi kéo `/api/state` về (máy chủ là bản chuẩn; bản ghi còn trong outbox thì giữ bản trên máy). Ảnh chụp ở máy khác lấy từ `/api/photos/…`, service worker cache lại để xem offline. Trên GitHub Pages (`*.github.io`) không có máy chủ nên chỉ lưu trên máy.

## Hệ thống OMV
Máy `192.168.50.231`, Tailscale `100.78.213.85`. DAS = `/srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c`. Thư mục app `DAS/VM_bikencar/{web,data,backup}`.

## Nhật ký phiên bản
- **v1.2.0** (01/11/2026) – Kết nối Gmail không cần mật khẩu: Google Apps Script (`feemail.SCRIPT_CODE`, quyền `gmail.readonly`, web app /exec + khóa bí mật `scriptKey` trong `data/mail.json`), trang hướng dẫn `/gmail`. Mật khẩu ứng dụng vẫn dùng được (cách 2).
- **v1.1.0** (01/11/2026) – Phí gửi xe: máy chủ đọc Gmail 10 phút/lần, nhận email báo phí, thẻ phí trên trang chủ, trang thanh toán (chép STK/số tiền/nội dung, mã VietQR, nút mở Techcombank qua dl.vietqr.io – chưa xác nhận Techcombank hỗ trợ), đánh dấu đã trả, thông báo đẩy khi có phí mới.
- **v1.0.0** (01/11/2026) – Chạy trên OMV: máy chủ FastAPI + SQLite, đồng bộ offline-first, nút Cập nhật ngay trong Cài đặt, sao lưu/khôi phục qua máy chủ.
- 4 (27/10/2026) – Bỏ thông báo sau khi chụp ảnh nhanh.
- 3 (27/10/2026) – Tự cập nhật khi mở app, hiện số phiên bản.
- 2 (27/10/2026) – Dọn sạch ảnh có xác nhận; tách ảnh đỗ xe và ảnh phiếu bảo dưỡng.
- 1 (27/10/2026) – Bản đầu, chỉ lưu trên iPhone (GitHub Pages).
