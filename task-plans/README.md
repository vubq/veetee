# Task Plans

Thư mục này dùng để chuyển giao công việc giữa một model mạnh dùng để **lập kế hoạch** và một model khác dùng để **thực thi**.

Workflow mong muốn:

```text
Model mạnh
  -> user yêu cầu "lên plan ..."
  -> rà repo
  -> tạo task-plans/YYYY-MM-DD-<task>.md

Model thực thi
  -> user yêu cầu "thực hiện plan <file>"
  -> đọc plan + kiểm tra lại source hiện tại
  -> implement + test
  -> cập nhật checklist/status ngay trong plan
```

Không dùng thư mục này cho các task thông thường khi user không yêu cầu lập plan.

Mẫu chuẩn: [`_TEMPLATE.md`](_TEMPLATE.md).

## Plan đã lập

- [Hội thoại tự nhiên: wake cache, lời chào, idle timeout và thoát](2026-09-08-hoi-thoai-tu-nhien-khong-sua-fw.md) — `PARTIAL`: phần server + unit/regression/WebSocket + browser/runtime smoke đã hoàn tất; runtime profile 20 lượt và kiểm thử ESP32 thật còn `PENDING`.
- [Server tương thích FW nguyên bản](2026-09-08-server-khong-sua-fw.md) — `PARTIAL`: phần server + regression đã hoàn thành; runtime/hardware trên ESP32 nguyên bản còn `PENDING`.
