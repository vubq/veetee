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
```

## Phạm vi hiện tại

- App shell, header, footer và trang danh sách trợ lý.
- Logo Veetee tự thiết kế dạng mặt robot vuông bo nhẹ, mắt sắc, xanh lục–cyan.
- Bộ component UI dùng chung: button, search, dropdown, select, switch, tab, dialog,
  table, badge, empty state và card.
- Dữ liệu hiện tại chỉ là mock để kiểm thử UX/UI; chưa kết nối backend.

Giao diện tham chiếu bố cục và hành vi quan sát từ `https://xiaozhi.me/console/agents`,
nhưng dùng tài sản nhận diện Veetee riêng và không chứa mã nguồn/tài sản thương hiệu của
trang tham chiếu.
