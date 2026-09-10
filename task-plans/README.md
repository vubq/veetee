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

- [Groq API trực tiếp: multi-key quota-aware routing](2026-09-10-groq-direct-quota-aware-routing.md) — `IN_PROGRESS` (2026-09-10): provider/router/ledger/config xong + chạy live (chat/tool/recall đúng, hết traffic OmniRoute); unit 207 xanh. Còn: failover thật, metrics endpoint, harness fixes, deadline propagation, benchmark/hardware đường mới, A/B gpt-oss-20b đang thử (nhanh tương đương, 1 mẫu đọc giờ sai).

- [AI semantics, persona lớn, tool/memory và pipeline dưới 1 giây](2026-09-09-ai-persona-tools-memory-latency.md) — `PARTIAL`, execution có evidence nhiều đợt (2026-09-09/10): code M0–M7 + unit 155 xanh, live API/WS/E2E model thật, smoke 20 mẫu, hardware acoustic wake/QA/idle/re-wake; corpus 200, cert 100-attempt, SLA, interrupt/AEC còn `PENDING`.
- [Rà soát và hợp nhất tài liệu dự án](2026-09-09-hop-nhat-tai-lieu-du-an.md) — `COMPLETED`, execution `COMPLETED`: D01–D14 đã được hợp nhất vào owner docs, `docs/PLAN.md` thành historical stub, review/task-plans được bảo toàn và link/diff/inventory validation đã pass.

Plan runtime ở trên đã thực thi nhiều đợt và đang `PARTIAL` (xem Execution status trong file); trong phạm vi đã nêu, yêu cầu AI mới thay chỉ dẫn direct clock/template fallback/explicit-only matcher và giới hạn cứng đúng 2 rounds. Plan tài liệu đã `COMPLETED` ngày 2026-09-09 và chỉ thay đổi docs/index/status, không triển khai các gap runtime A01–A12.

## Tài liệu sau migration

- `veetee-server/docs/ARCHITECTURE.md` là nơi mô tả kiến trúc hiện hành.
- `veetee-server/docs/TESTING.md` là nơi mô tả loại test, metric và quality gate.
- Các task plan vẫn được giữ để theo dõi execution, evidence và lịch sử.

Giữ nguyên các file plan cũ, lịch sử checklist và evidence. Các mô tả test/runtime bên dưới thuộc lần cập nhật trước, không chứng nhận source hiện tại đã hết hardcode: audit 2026-09-09 còn tìm thấy direct clock và literal receipt fallback. Khi thực thi cập nhật đúng plan bằng bằng chứng mới, không xóa/gộp mất handoff cũ.

## Plan đã lập

- [Hội thoại do AI quyết định, bỏ hardcode ý định và câu trả lời](2026-09-08-hoi-thoai-ai-khong-hardcode.md) — `PARTIAL`: implementation server-side + unit 155 xanh (2026-09-10); audit xác nhận 0 matcher lên lời user; live remember→recall đúng, abort + follow-up sạch; corpus ≥200, SLA và ESP32 đầy đủ vẫn `PENDING`.
- [Ổn định và tối ưu pipeline sau review server](2026-09-08-on-dinh-va-toi-uu-pipeline.md) — `PARTIAL`: các lỗi semantic/hardcode phát hiện từ review đã được chuyển sang plan AI và sửa ở working tree; runtime/SLA/phần cứng còn thiếu.
- [Pipeline dưới 1 giây: Intent, Memory và Function Calling](2026-09-08-pipeline-600ms-intent-memory-tools.md) — `PARTIAL`: đã có implementation và regression phía server; corpus/runtime, SLA và phần cứng còn thiếu. Plan review mới tiếp nối việc sửa lỗi và nghiệm thu.
- [Hội thoại tự nhiên: wake cache, lời chào, idle timeout và thoát](2026-09-08-hoi-thoai-tu-nhien-khong-sua-fw.md) — `PARTIAL`: hướng exact-match/allowlist trong plan này đã bị thay bằng AI-first + deterministic idle end (xem plan AI-không-hardcode và follow-up 2026-09-10); phần server mới + hardware wake/QA/idle/re-wake đã có evidence, runtime profile 20 lượt và checklist ESP32 đầy đủ còn `PENDING`.
- [Server tương thích FW nguyên bản](2026-09-08-server-khong-sua-fw.md) — `PARTIAL`: phần server + regression đã hoàn thành; E2E runtime PASSED 2026-09-10; hardware đã có wake/QA/idle/re-wake trên board build Kconfig từ baseline (không sửa logic source), interrupt 20×20 và đo loa vật lý còn `PENDING`.

## Ghi chú được giữ để truy vết

- [AI first semantics rules](2026-09-09-ai-first-semantics-rules.md) — note định hướng lịch sử, không phải plan theo template và không có nghiệm thu implementation. Giữ nguyên file; áp dụng [rule gốc](../AGENTS.md) và plan tiếp nối theo yêu cầu hiện hành.
