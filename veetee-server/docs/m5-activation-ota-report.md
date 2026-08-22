# Báo cáo M5 activation, binding và OTA cơ bản

## Phạm vi

Backend triển khai đúng phạm vi M5 đã duyệt: wire activation hiện tại của firmware,
binding tenant-aware và OTA artifact/release local tối thiểu. Không triển khai credential
rotation, signature, channel/cohort, staged rollout hoặc reporting.

## Hành vi đã triển khai

- Activation code sáu chữ số và challenge ổn định tới TTL cấu hình được; poll Version1
  `{}` trả 202 trước bind và 200 sau bind.
- Bind xác thực user, kiểm ownership agent, global device identity, PostgreSQL advisory
  transaction lock, receipt hash có TTL cho replay/idempotency và quota theo user; audit
  nằm cùng transaction. Receipt cascade khi unbind và khi hết hạn không chặn tái sử dụng
  không gian code sáu chữ số.
- Unbind tenant-scoped tạo điều kiện cấp activation mới ở lần check tiếp theo.
- Device list lấy online state thật từ registry WebSocket in-process.
- Parser system-info đọc đúng nested `application`, `board`, `ota` và chip top-level của
  firmware hiện tại; metadata bind được dùng cho eligibility.
- Upload artifact raw bất biến theo stream, giới hạn size, SHA-256; release tenant-scoped
  compatible theo version/board/chip/partition/force và chỉ xuất hiện sau publish.
- Artifact download kiểm storage name/root/size và hash lại theo chunk trước khi stream;
  URL public bắt buộc cấu hình explicit khi bật persistence, không suy từ `Host`.

## Migration

`003_device_activation_ota.sql` giữ dữ liệu M4, thêm global uniqueness và fail toàn bộ
migration nếu dữ liệu cũ xung đột; không tự xóa bản ghi để chọn owner. Down migration từ
chối khi có dữ liệu M5 để tránh rollback mất dữ liệu.

Migration đã được chạy theo chuỗi `001 -> 002 -> 003 -> down -> 003` trong schema
PostgreSQL cô lập và chạy lại 003 idempotent. Trước khi áp lên database runtime, preflight
xác nhận không có duplicate global `device_id` và backup custom-format được tạo, kiểm tra
đọc được ngoài workspace. Runtime giữ nguyên dữ liệu M4 và ghi nhận migration 003.

## Browser E2E

Console được kiểm trên viewport mobile 390 x 844 với backend persistence thật:

- Login giữ token trong memory, tạo agent và tự bật thao tác thêm thiết bị.
- Nhập mã sáu số thật qua dialog tạo bind; card đổi `Thiết bị (0)` thành `Thiết bị (1)`
  sau response 200, không có success giả.
- Device dialog hiển thị đúng agent, metadata và last-seen sau OTA check.
- Confirmation dialog lồng chỉ lớp trên cùng nhận Escape và khôi phục focus về nút mở.
- Unbind thật trả 204, empty state xuất hiện và card trở lại `Thiết bị (0)`.
- Không có horizontal overflow; các request control plane quan sát được trả 200/201/204.
- Console OTA hỗ trợ upload raw binary, tạo release và publish có confirmation; control
  chưa có backend được ẩn hoặc disabled với nhãn `Sắp có`.

## Hardware E2E

Client là ESP32-S3 thật chạy nguyên firmware tham khảo 2.4.2 đã pin, board
`bread-compact-wifi-lcd`, LCD 240 x 280, locale `vi-VN`, wake model `wn9_hiesp`. Không
patch firmware, không erase flash/NVS và không thay Wi-Fi.

- First-contact tạo đúng một activation code sáu số/challenge; poll trả 202 trước bind.
- Mã được nhập qua Console; poll chuyển 200, check kế tiếp không còn activation và
  metadata board/chip/version/partition/last-seen được cập nhật.
- Hard reset giữ Wi-Fi/NVS; LCD, audio service và AFE wake-word khởi tạo.
- Wake phrase mở WebSocket thật; server accept `/api/v1/devices/ws` và registry quan sát
  device online. Một lượt tiếng Việt được phát làm peripheral/session smoke, nhưng không
  có conversation metadata mới nên không dùng lượt này làm bằng chứng transcript/TTS.
- Upload app image 2.64 MB khớp byte size/SHA-256; release force được publish cho tuple
  đang chạy. Board download, ghi `ota_1`, báo upgrade thành công, reboot, reconnect và
  tiếp tục chạy 2.4.2. Compatibility rollback lặp lại thành công về `ota_0`.
- Hardware E2E phát hiện firmware chỉ nhận `firmware.force` khi là JSON number. Backend
  ban đầu trả boolean nên board coi là không có update; serializer đã sửa thành `0/1` và
  có regression test exact wire type.

Raw serial log có định danh mạng, activation code, token và response tạm chỉ được giữ
trong `/tmp` khi chạy test rồi xóa; báo cáo không chứa các giá trị đó. Hai repo reference
giữ worktree sạch.

## Quality gates

- Ruff: pass.
- Mypy strict: pass trên 87 source files.
- Pytest với database `veetee_test`: 412 test pass.
- Namespace scan mặc định và `--all`: pass, references excluded bằng allowlist hẹp có test.
- Frontend type-check, production build và Playwright 24/24 desktop/mobile: pass.
- Lighthouse auth screen đúng URL Veetee: accessibility, best practices và SEO 100 trên
  desktop/mobile.
- `git diff --check`: pass.

## Giới hạn

- Online registry là state của một process; multi-worker cần registry dùng chung ở mốc
  scale/hardening.
- Artifact local chưa có chữ ký và download token; chỉ phù hợp môi trường local hiện tại.
- Firmware hiện tại không gửi progression report; trạng thái download/install được xác
  minh từ board và partition, chưa có dashboard rollout production.
- Full dialogue transcript/TTS không được kết luận từ smoke session M5; bằng chứng hội
  thoại đầy đủ vẫn thuộc gate realtime speech tương ứng.
