# Veetee Console

Frontend quản trị Veetee dùng Vue 3, Vite và TypeScript. Đây là một ứng dụng responsive
duy nhất cho mobile và PC; không có bản giao diện riêng theo thiết bị.

## Chạy local

```bash
npm install
npm run dev
```

Mặc định Vite chạy tại `http://127.0.0.1:5173`. Không dùng Docker hoặc Docker Compose.

## Kiểm tra

```bash
npm run type-check
npm run build
npm run test:e2e
```

Suite Playwright chạy Chromium ở desktop 1440 x 900 và mobile 390 x 844. API được mock
ở HTTP boundary để kiểm tra deterministic các trạng thái thành công/lỗi; backend và
PostgreSQL được kiểm riêng trong test suite server. Test fail khi có lỗi Vue/browser,
`console.error` hoặc horizontal overflow trong scenario responsive.

## Phạm vi hiện tại

- Màn hình đăng nhập riêng; bearer token chỉ giữ trong memory. Đăng xuất luôn xóa token
  cục bộ trước, rồi mới gọi máy chủ để thu hồi phiên; nếu lời gọi thu hồi thất bại
  (lỗi mạng hoặc 5xx), console vẫn về màn hình đăng nhập nhưng hiển thị cảnh báo người dùng
  nên đăng nhập lại và thử đăng xuất khi kết nối ổn định.
- App shell, header, footer và trang danh sách trợ lý sau khi xác thực.
- Logo Veetee tự thiết kế dạng mặt robot vuông bo nhẹ, mắt sắc, xanh lục–cyan.
- Bộ component UI dùng chung: button, search, dropdown, select, switch, tab, dialog,
  table, badge, empty state và card.
- Agent create/rename/delete, conversation, device
  bind/unbind và OTA artifact/release/publish đều gọi API Veetee thật.
- Console chưa hiển thị workflow cấu hình agent runtime: backend lưu được profile nhưng
  realtime pipeline chưa áp immutable agent snapshot theo turn, nên không quảng bá control
  này như một chức năng đã hoạt động.
- Control chưa có backend không được hiển thị như chức năng khả dụng; các tab được giữ để
  định hướng chỉ xuất hiện ở trạng thái disabled và có nhãn `Sắp có`.

Giao diện tham chiếu bố cục và hành vi quan sát từ `https://xiaozhi.me/console/agents`,
nhưng dùng tài sản nhận diện Veetee riêng và không chứa mã nguồn/tài sản thương hiệu của
trang tham chiếu.
