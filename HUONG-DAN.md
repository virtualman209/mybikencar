# MyBike'nCar trên máy chủ OMV nhà

Dữ liệu (SQLite) và ảnh nằm trên DAS. App trên iPhone giữ một bản để dùng khi mất sóng dưới hầm, có mạng lại thì tự đồng bộ lên OMV.

| | |
|---|---|
| Thư mục | `DAS/VM_bikencar/` gồm `web` (mã nguồn), `data` (`mybikencar.db` + `photos/`), `backup` (DB mỗi ngày, giữ 30 bản) |
| Container | `vm_bikencar` (compose `vm-bikencar`) |
| Cổng | `8096` http (trong nhà) · `8446` https qua Tailscale (để cài lên iPhone) |
| Cập nhật | Nút **Cập nhật ngay** trong ⚙︎ Cài đặt của app, hoặc lệnh SSH bên dưới |

## Cài lần đầu (SSH vào OMV bằng root, dán cả khối)

```bash
DAS=/srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c
git clone https://github.com/virtualman209/mybikencar $DAS/VM_bikencar/web
bash $DAS/VM_bikencar/web/server/install.sh
```

Cuối cùng script in ra địa chỉ dạng `https://vmminipc.<tên-tailnet>.ts.net:8446`.

## Cài lên iPhone

1. Bật Tailscale trên iPhone, mở địa chỉ `https://…ts.net:8446` bằng **Safari**.
2. Bấm **Chia sẻ → Thêm vào MH chính**.
3. Chuyển dữ liệu từ app cũ (bản GitHub Pages): mở app cũ → ⚙︎ → **Tạo bản sao lưu** → lưu vào Tệp. Mở app mới → ⚙︎ → **Khôi phục từ bản sao lưu** → chọn tệp đó. Xong thì có thể xoá app cũ.

Chấm nhỏ trên nút ⚙︎: xanh = đã đồng bộ, cam = có thay đổi chờ gửi, xám = chưa kết nối được OMV (dữ liệu vẫn lưu trên máy).

## Phí gửi xe (từ v1.1.0)

1. ⚙︎ → **Phí gửi xe** → **Thông tin căn hộ**: nhập số căn hộ, biển số (để điền nội dung chuyển khoản).
2. **Email báo phí (Gmail)**: nhập Gmail + **mật khẩu ứng dụng** (tạo ở myaccount.google.com/apppasswords, cần bật Xác minh 2 bước). Máy chủ đọc Gmail 10 phút một lần, chỉ đọc email của người gửi có chữ "seasons".
3. **Thông báo khi có phí mới**: bấm để bật (phải mở app từ màn hình chính).
4. Có phí mới thì trang chủ hiện thẻ đỏ. Bấm vào → chép STK / nội dung, hoặc **Mở Techcombank**, hoặc lưu mã VietQR rồi quét trong Techcombank. Chuyển xong bấm **Tôi đã chuyển khoản xong**.

## Lệnh SSH

| Việc | Lệnh |
|---|---|
| Cập nhật | `/srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c/VM_bikencar/web/server/update.sh` |
| Dựng lại kể cả khi không có gì mới | thêm `--force` vào lệnh trên |
| Xem nhật ký cập nhật | `tail -20 /srv/dev-disk-by-uuid-63bcf028-ccf8-4a65-a3f2-0510da8ead2c/VM_bikencar/web/update.log` |
| Xem nhật ký app | `docker logs --tail 50 vm_bikencar` |
| Khởi động lại | `docker restart vm_bikencar` |

Bản mới không chạy được thì `update.sh` tự quay về bản cũ và không thử lại bản lỗi đó (trừ khi dùng `--force`).

## Lưu ý

- Không có mật khẩu: chỉ mở được trong mạng nhà và qua Tailscale, router không mở cổng nào.
- Bản sao DB mỗi ngày nằm cùng ổ DAS (giống các web VM khác, rủi ro R-13). Ảnh nằm ở `data/photos`.
- Bản GitHub Pages (`virtualman209.github.io/mybikencar`) vẫn chạy nhưng chỉ lưu trên máy, không đồng bộ.
