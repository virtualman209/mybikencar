# MyBike'nCar – ghi chú cho AI

Chủ nhà nói tiếng Việt, không phải lập trình viên. Trả lời tiếng Việt, ngắn gọn; mỗi lần sửa phải báo tên phiên bản.

## Cấu trúc
- `index.html` – toàn bộ app (một tệp). `APP_VERSION` là số phiên bản duy nhất; máy chủ đọc nó để báo phiên bản.
- `sw.js` – cache offline. **Mỗi lần đổi app phải đổi `VERSION` trong sw.js giống `APP_VERSION`**, nếu không iPhone không nhận bản mới.
- `server/app.py` – FastAPI + SQLite. API: `/api/info`, `/api/state`, `PUT|DELETE /api/{parkings|maint}/{id}`, `POST /api/photos`, `GET /api/photos/{id}/{thumb|full}`, `DELETE /api/photos/{id}`, `/api/backup`, `/api/import`, `GET|POST /api/update`, `GET|POST /api/mail`, `POST /api/mail/check`, `GET /api/fees/{id}/qr.png?tn=`, `/api/push/{key,subscribe,test}`. Kho đồng bộ: `parkings`, `maint`, `photos`, `fees` (phí, máy chủ tạo từ email, iPhone đánh dấu `paidAt`), `prefs` (`profile`: căn hộ, biển số).
- `server/feemail.py` – đọc Gmail qua IMAP (chỉ đọc, `BODY.PEEK`), nhận diện email báo phí của BQL Seasons Avenue (`parse_fee`), tạo chuỗi VietQR (`vietqr_payload`). Cấu hình Gmail ở `data/mail.json` (quyền 600, KHÔNG lên GitHub, không có trong bản sao lưu). Thông báo đẩy: khóa VAPID `data/vapid_private.pem`, danh sách iPhone `data/push.json`.
- `server/gmail.html` – trang hướng dẫn `/gmail` cài Apps Script (chép mã có khóa bí mật, dán URL /exec).
- `server/update.sh` – git pull + `docker compose up -d --build`, chờ healthy, lỗi thì quay về bản cũ (`.bad_commit`). Ghi trạng thái `data/requests/update.status`.
- `server/install.sh` – cài lần đầu (chạy lại thoải mái): thư mục, systemd `vmbikencar-update.path` (nghe `data/requests/update.req`), `vmbikencar-autoupdate.timer` (5 phút), container, `tailscale serve --https=8446 8096`.
- `docker-compose.yml` – container `vm_bikencar`, cổng 8096, gắn `../data`, `../backup`.

## Đồng bộ
iPhone giữ đủ dữ liệu trong IndexedDB. Mọi thay đổi vào `outbox`, đẩy lên máy chủ khi có mạng, rồi kéo `/api/state` về (máy chủ là bản chuẩn; bản ghi còn trong outbox thì giữ bản trên máy). Ảnh chụp ở máy khác lấy từ `/api/photos/…`, service worker cache lại để xem offline. Trên GitHub Pages (`*.github.io`) không có máy chủ nên chỉ lưu trên máy.

## Hệ thống OMV
Máy `192.168.50.231`, Tailscale `100.78.213.85`. DAS = `/srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c`. Thư mục app `DAS/VM_bikencar/{web,data,backup}`.

## Nhật ký phiên bản
- **v1.6.3** (02/11/2026) – Chặn chuột phải / chạm giữ (menu Sao chép, Mở trong tab mới…) trên nút, link và chỗ bấm được: `contextmenu` bị chặn khi bấm vào `a, button, [role=button], [onclick], label, summary, select, .btn` và ô nút / ô tick; `-webkit-touch-callout:none`. Chữ thường, ô nhập, ảnh (kể cả mã QR) vẫn giữ được. Trang `/gmail` cũng chặn phóng to + chuột phải.
- **v1.6.2** (01/11/2026) – Chặn phóng to/thu nhỏ: viewport `maximum-scale=1, user-scalable=no`, chặn `gesture*`, chạm 2 ngón và chạm đúp (iOS bỏ qua user-scalable nên phải chặn bằng JS), `touch-action:manipulation`.
- **v1.6.1** (01/11/2026) – Nội dung chuyển khoản chỉ gồm chữ không dấu, số, dấu cách (`cleanNote` ở app, cùng luật trong `vietqr_payload` ở máy chủ). Biển số bỏ hết `-` `.` (30A-123.45 → 30A12345); tháng 11/2026 → 11 2026.
- **v1.6.0** (01/11/2026) – Bỏ hẳn tính năng Phím tắt (lưu/xoá ảnh QR tự động) theo yêu cầu chủ nhà: quá rườm rà. Thanh toán quay về 2 bước: lưu mã QR vào Ảnh → mở Techcombank quét ảnh. Không làm lại tính năng này.
- **v1.5.0** (01/11/2026) – Trả nhanh: gộp còn 1 Phím tắt `QR phi xe` (Tìm ảnh album `QR phi xe` → Nếu có → Xoá ảnh → Lấy nội dung của URL (đầu vào) → Lưu vào album → Mở Techcombank); nút "đã chuyển khoản" không gọi Phím tắt nữa. Viền/nét vẽ giao diện mỏng còn khoảng 1/2.
- **v1.4.0** (01/11/2026) – Tự xoá ảnh QR: web app không xoá được ảnh trong Thư viện, nên dùng 2 Phím tắt iPhone do chủ nhà tự tạo: `QR phi xe` (nhận URL ảnh QR → lưu vào album `QR phi xe` → mở Techcombank) và `Xoa QR phi xe` (tìm ảnh trong album → xoá, iOS hỏi xác nhận). App gọi `shortcuts://run-shortcut`. Bật/tắt ở `prefs.profile.shortcuts`.
- **v1.3.1** (01/11/2026) – Sửa thanh toán: Techcombank KHÔNG nhận thông tin điền sẵn qua dl.vietqr.io (chỉ mở app). Trang thanh toán đổi thành 2 bước: (1) lưu mã QR vào Ảnh (ảnh QR tải sẵn để iOS cho mở bảng chia sẻ), (2) mở Techcombank → Quét QR → chọn ảnh.
- **v1.3.0** (01/11/2026) – Tự cập nhật: systemd `vmbikencar-autoupdate.timer` chạy `update.sh --auto` 5 phút/lần (im lặng khi không có gì mới, bỏ qua bản lỗi trong `.bad_commit`). Bản sao tạm của update.sh dùng `mktemp` để 2 lần chạy không giẫm nhau.
- **v1.2.0** (01/11/2026) – Kết nối Gmail không cần mật khẩu: Google Apps Script (`feemail.SCRIPT_CODE`, quyền `gmail.readonly`, web app /exec + khóa bí mật `scriptKey` trong `data/mail.json`), trang hướng dẫn `/gmail`. Mật khẩu ứng dụng vẫn dùng được (cách 2).
- **v1.1.0** (01/11/2026) – Phí gửi xe: máy chủ đọc Gmail 10 phút/lần, nhận email báo phí, thẻ phí trên trang chủ, trang thanh toán (chép STK/số tiền/nội dung, mã VietQR, nút mở Techcombank qua dl.vietqr.io – chưa xác nhận Techcombank hỗ trợ), đánh dấu đã trả, thông báo đẩy khi có phí mới.
- **v1.0.0** (01/11/2026) – Chạy trên OMV: máy chủ FastAPI + SQLite, đồng bộ offline-first, nút Cập nhật ngay trong Cài đặt, sao lưu/khôi phục qua máy chủ.
- 4 (27/10/2026) – Bỏ thông báo sau khi chụp ảnh nhanh.
- 3 (27/10/2026) – Tự cập nhật khi mở app, hiện số phiên bản.
- 2 (27/10/2026) – Dọn sạch ảnh có xác nhận; tách ảnh đỗ xe và ảnh phiếu bảo dưỡng.
- 1 (27/10/2026) – Bản đầu, chỉ lưu trên iPhone (GitHub Pages).
