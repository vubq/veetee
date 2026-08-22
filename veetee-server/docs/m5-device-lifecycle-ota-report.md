# Báo cáo Mốc 5: Device lifecycle và OTA

Trạng thái: hoàn tất acceptance server-first đã duyệt, sẵn sàng bàn giao tại Cổng duyệt 5.

## Đã triển khai

- Enrollment production bằng Ed25519 device proof; discovery chưa proof không lộ activation
  code. Activation challenge có TTL, attempt limit và chống replay.
- Binding/unbinding/recovery có ownership, idempotency, audit và credential riêng từng
  thiết bị. Bootstrap/recovery credential một lần không dùng được cho WebSocket, report
  hoặc download.
- WebSocket credential HMAC-SHA256 có issuer, audience, JTI, expiry, ràng buộc
  `Device-Id`/`Client-Id`, rotation và revocation persistent.
- Artifact/release immutable có target board/chip/partition, SHA-256, detached Ed25519
  signature, provenance và atomic filesystem storage. Server chỉ giữ public signing key.
- Rollout deterministic theo channel/cohort/percentage, pause/resume/kill, anti-rollback,
  rollback authorization theo rollout/cohort/device và automatic health gate.
- Artifact download streaming có signed token, expiry, single range, integrity recheck và
  path traversal protection. OTA report có progression validation, idempotency, quota,
  dedupe và retention policy explicit.
- Console có login in-memory, device bind/manage/unbind/recovery, OTA artifact/release/
  rollout/rollback và responsive layout theo visual system hiện có.

## Evidence software

- Backend: Ruff pass, mypy strict pass trên 91 source files và 427/427 pytest pass trên
  database riêng `veetee_test`. Warning duy nhất là `StarletteDeprecationWarning` từ
  `fastapi.testclient` về chuyển đổi `httpx`/`httpx2`.
- Frontend: `npm run type-check` và `npm run build` pass; bundle production khoảng
  162,16 kB JavaScript (54,63 kB gzip) và 27,69 kB CSS (6,18 kB gzip).
- Namespace scan và `git diff --check` pass. OpenAPI test khóa các device/OTA path M5 cốt
  lõi và không có namespace cấm.
- Hai repo `references` sạch; không có thay đổi tracked hoặc Git history do M5 tạo ra.

## Browser E2E local

E2E chạy trên backend riêng ở cổng 18081 với `veetee_test` và Console riêng ở cổng 5174;
không áp migration hoặc ghi fixture vào runtime database `veetee`.

- Admin login, navigation và bốn OTA list/summary API pass; không có console error từ
  ứng dụng.
- Upload firmware fixture qua browser trả 201 sau khi backend xác minh detached Ed25519
  signature; tạo release `9.0.0-e2e`, publish rollout và summary cập nhật đúng.
- Production enrollment proof được ký bằng private key tạm ngoài source. Console bind
  device vào agent trả 200; destructive unbind confirmation và API trả 200.
- Nested dialog pass: Escape chỉ đóng confirmation trên cùng, giữ dialog quản lý và body
  scroll lock.
- Viewport mobile 390x844 không có horizontal page overflow; các bảng OTA scroll trong
  region riêng và navigation OTA vẫn truy cập được.
- Một phép thử CORS loopback do test runner chủ động tạo hai console error trước khi thêm
  header CORS cho endpoint tạm; lỗi này không đến từ ứng dụng. Luồng ứng dụng trước phép
  thử có 0 console error. Endpoint, credential, key, artifact, log và file tạm đã được xóa.
- Cleanup trực tiếp release bị trigger immutable từ chối và transaction rollback đúng
  thiết kế. Full test suite sau đó reset database test bằng fixture `TRUNCATE ... CASCADE`
  và pass 427/427, xác nhận không còn state E2E ngoài quy trình test.

## Hardware evidence

Ngày 2026-08-22, board xuất hiện ổn định tại USB serial by-id, được `esptool` nhận là
ESP32-S3 revision 0.2, PSRAM 8 MB và flash 16 MB. Chỉ đọc chip ID, flash ID và partition
table `0x8000-0x8fff`; không đọc hoặc ghi NVS, không `erase-flash` và không thay Wi-Fi.
Partition table trên board khớp build 16 MB: NVS 16 KiB, `ota_0`/`ota_1` mỗi partition
4032 KiB và assets 8 MB.

Firmware tham khảo 2.4.2 đang chạy từ `ota_0`, ESP-IDF 6.0.2, board
`bread-compact-wifi-lcd`, locale `vi-VN`, LCD 240x280 và wake model `wn9_hiesp`. Boot
log xác nhận PSRAM, LCD, microphone/speaker simplex và AFE khởi tạo; thiết bị tự kết nối
lại Wi-Fi đã lưu, gọi discovery Veetee local và về idle với no-update. Không có credential
hoặc thông tin mạng định danh được lưu trong báo cáo.

Phát audio “Hi, ESP” từ máy ở intensity thấp đã kích hoạt wake word trước lượt OTA.
Board chuyển `idle -> connecting -> listening`, WebSocket handshake 40 ms và mã hóa 33
packet wake audio. Câu tiếng Việt đủ dài tạo phản hồi mẫu Veetee; board chuyển
`listening -> speaking -> listening`, server TTS local trả 200 và loa được bật/tắt đúng.
Reset khi socket đang mở làm socket cũ đóng; board boot lại từ `ota_0`, peripheral khởi
tạo, Wi-Fi/NVS được giữ và discovery chạy lại. Wake acoustic sau các lượt reboot chưa lặp
lại được trong ba input ở gain thấp dù AFE vẫn active; đây là giới hạn acoustic smoke,
không được đổi thành pass.

OTA compatibility one-shot dùng runtime harness tạm ngoài source, không sửa firmware và
không nới policy server production. Firmware nhận metadata `version`/`url`, stream image
2.768.096 byte, ghi `ota_1` tại `0x410000`, báo progress 14-100%, validate image, reboot,
tự nối lại Wi-Fi, discovery lại, chạy `ota_1` và mark image valid. Lượt compatibility
rollback stream cùng artifact từ `ota_1` về `ota_0` tại `0x20000`, validate, reboot,
reconnect, discovery no-update và mark valid. Final reboot xác nhận board ở `ota_0`,
PSRAM/LCD/audio/Wi-Fi hoạt động và runtime Veetee gốc đã được khôi phục readiness 200.
Harness, environment backup và raw log có thông tin mạng được xóa sau test.

## Giới hạn và backlog firmware

Firmware baseline không gửi `Activation-Nonce`/`Activation-Proof`, không gửi bearer khi
bound rediscovery, không gửi OTA progression report và không kiểm detached Ed25519
signature trước install. Vì vậy nó không thể làm client production cho M5 mà không hạ
security contract hoặc thêm firmware adaptation. Repo tham khảo là read-only và không
được patch; firmware Veetee chính thức chưa có source.

Theo phạm vi server-first đã được người dùng duyệt, M5.7 hiện yêu cầu hardware compatibility
bằng firmware tham khảo nguyên trạng; secure device-side lifecycle được chuyển sang backlog
firmware Veetee khi có yêu cầu phát triển firmware. Evidence trên đạt partition switch,
download/install/reboot/reconnect, compatibility rollback, bảo toàn NVS/Wi-Fi và peripheral
boot smoke. Không gọi kết quả này là production firmware validation.
