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

## Kế hoạch tiếp nối audit 2026-09-09

- [AI semantics, persona lớn, tool/memory và pipeline dưới 1 giây](2026-09-09-ai-persona-tools-memory-latency.md) — `PLANNED`, execution `NOT_STARTED`: M0–M7 xử lý direct clock/literal receipts, speech ordering, schema, persona API/UI và context budget, multi-round tool/history, retrieval/RAG seam, latency A/B và nghiệm thu model/ESP32 thật.
- [Rà soát và hợp nhất tài liệu dự án](2026-09-09-hop-nhat-tai-lieu-du-an.md) — `PLANNED`, execution `NOT_STARTED`: inventory D01–D14, nguồn tài liệu chính, mapping giữ/hợp nhất/stub và validation; bảo toàn toàn bộ task-plans và review lịch sử.

Hai file trên mới là kế hoạch, chưa phải implementation đã hoàn tất. Plan runtime tiếp nối các plan `PARTIAL` dưới đây; trong phạm vi đã nêu, yêu cầu AI mới thay chỉ dẫn direct clock/template fallback/explicit-only matcher và giới hạn cứng đúng 2 rounds. Không tự thực thi plan chỉ vì nó được liệt kê ở đây.

## Tài liệu sau migration

- `veetee-server/docs/ARCHITECTURE.md` là nơi mô tả kiến trúc hiện hành.
- `veetee-server/docs/TESTING.md` là nơi mô tả loại test, metric và quality gate.
- Các task plan vẫn được giữ để theo dõi execution, evidence và lịch sử.

Giữ nguyên các file plan cũ, lịch sử checklist và evidence. Các mô tả test/runtime bên dưới thuộc lần cập nhật trước, không chứng nhận source hiện tại đã hết hardcode: audit 2026-09-09 còn tìm thấy direct clock và literal receipt fallback. Khi thực thi cập nhật đúng plan bằng bằng chứng mới, không xóa/gộp mất handoff cũ.

## Plan đã lập

- [Hội thoại do AI quyết định, bỏ hardcode ý định và câu trả lời](2026-09-08-hoi-thoai-ai-khong-hardcode.md) — `PARTIAL`: implementation server-side + 127 regression tests đã xanh; không còn semantic keyword/regex matcher runtime cho end/memory/confirmation/language/tool selection. Runtime clock tool đã qua model thật với AI tự chọn tool; corpus ≥200, SLA và ESP32 vẫn `PENDING`.
- [Ổn định và tối ưu pipeline sau review server](2026-09-08-on-dinh-va-toi-uu-pipeline.md) — `PARTIAL`: các lỗi semantic/hardcode phát hiện từ review đã được chuyển sang plan AI và sửa ở working tree; runtime/SLA/phần cứng còn thiếu.
- [Pipeline dưới 1 giây: Intent, Memory và Function Calling](2026-09-08-pipeline-600ms-intent-memory-tools.md) — `PARTIAL`: đã có implementation và regression phía server; corpus/runtime, SLA và phần cứng còn thiếu. Plan review mới tiếp nối việc sửa lỗi và nghiệm thu.
- [Hội thoại tự nhiên: wake cache, lời chào, idle timeout và thoát](2026-09-08-hoi-thoai-tu-nhien-khong-sua-fw.md) — `PARTIAL`: phần server + unit/regression/WebSocket + browser/runtime smoke đã hoàn tất; runtime profile 20 lượt và kiểm thử ESP32 thật còn `PENDING`.
- [Server tương thích FW nguyên bản](2026-09-08-server-khong-sua-fw.md) — `PARTIAL`: phần server + regression đã hoàn thành; runtime/hardware trên ESP32 nguyên bản còn `PENDING`.

## Ghi chú được giữ để truy vết

- [AI first semantics rules](2026-09-09-ai-first-semantics-rules.md) — note định hướng lịch sử, không phải plan theo template và không có nghiệm thu implementation. Giữ nguyên file; áp dụng [rule gốc](../AGENTS.md) và plan tiếp nối theo yêu cầu hiện hành.
