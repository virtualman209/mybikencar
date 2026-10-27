# MyBike'nCar – cài lên iPhone (không cần App Store)

App là "web app" chạy offline. Chỉ cần đưa lên mạng **một lần** để iPhone tải về, sau đó dùng hoàn toàn offline.

## 1. Đưa lên GitHub Pages (miễn phí, làm 1 lần, ~5 phút)
1. Vào github.com, đăng nhập (chưa có thì tạo tài khoản).
2. Bấm **New repository**, đặt tên `mybikencar`, chọn **Public**, bấm **Create repository**.
3. Bấm **uploading an existing file**, kéo thả **toàn bộ file trong thư mục này** (index.html, sw.js, manifest.webmanifest, 3 file icon) → **Commit changes**.
4. Vào **Settings → Pages**, mục *Branch* chọn `main` → **Save**. Đợi 1–2 phút.
5. Địa chỉ app: `https://<tên-github-của-bạn>.github.io/mybikencar/`

## 2. Cài lên iPhone
1. Mở địa chỉ trên bằng **Safari**.
2. Bấm nút **Chia sẻ** → **Thêm vào MH chính** → **Thêm**.
3. Mở app từ biểu tượng trên màn hình chính **một lần khi có mạng**. Từ đó trở đi chạy offline được (kể cả dưới hầm gửi xe không có sóng).

## Lưu ý
- Dữ liệu & ảnh nằm trong app trên iPhone, không lên mạng, không vào Thư viện ảnh. GitHub chỉ chứa mã app, không chứa dữ liệu.
- Hãy dùng app từ biểu tượng màn hình chính (dữ liệu trong tab Safari là riêng, không dùng chung).
- **Xoá biểu tượng app = mất dữ liệu.** Thỉnh thoảng vào ⚙︎ Cài đặt → Tạo bản sao lưu → lưu vào app Tệp.
- Lần đầu chụp ảnh, iPhone sẽ hỏi quyền dùng camera → cho phép.
- Sửa app sau này: sửa file rồi tăng số `VERSION` trong `sw.js`, upload lại; app tự cập nhật ở lần mở tiếp theo khi có mạng.
