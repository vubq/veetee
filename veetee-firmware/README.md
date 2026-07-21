# veetee-firmware

Source repo firmware của robot Veetee, mục tiêu đầu tiên là ESP32-S3 N16R8 + ST7789 + INMP441 + MAX98357A.

## Scope V1

- ESP-IDF 6.0.2/C++17.
- Một board factory `veetee-s3-n16r8`.
- Wi-Fi station + AP captive portal fallback.
- OTA/bootstrap, activation code 6 số và signed A/B OTA.
- WebSocket Xiaozhi-compatible V1, Opus, auto conversation, assistant gate, wake/abort và optional manual/PTT compatibility.
- Hai cách wake: button và ESP-SR activation wake word; interrupt profile chạy khi AI đang xử lý và best-effort khi đang phát tiếng tới khi AEC gate pass.
- Session timeout/closing grace nhận từ signed agent config; firmware tự recover nếu server treo.
- Signed resource bundle cho wake/model/assets dùng inactive slot, verify rồi activate; executable/runtime vẫn chỉ qua A/B firmware OTA.
- Device MCP `initialize`, `tools/list`, `tools/call`.
- Locale mặc định `vi-VN`, fallback `en-US`.

Wake phrase sản phẩm là `Hey VeeTee`, đọc “hây vi ti”. Firmware không so transcript
với phrase này; Web/API lưu profile/model ID và metadata pronunciation để train,
benchmark rồi phân phối model pack đã ký. ESP-SR `2.4.7` chưa có model tiếng Việt
hoặc `Hey VeeTee` built-in đã validate. Build dev hiện dùng `Hi ESP` chỉ để bring-up
đường PCM/task/event trên board và không được dùng làm capability production.

## Source layout mục tiêu

```text
main/
├── app/
├── audio/
├── board/
├── display/
├── mcp/
├── network/
├── ota/
├── config/
├── resources/
├── settings/
└── transport/
boards/veetee-s3-n16r8/
partitions/
tests/
```

## Spec bắt buộc

- `../docs/03-firmware-spec.md`
- `../docs/04-protocol-compatibility.md`
- `../docs/05-realtime-conversation.md`
- `../docs/09-testing-security-operations.md`

Không copy cả `references/xiaozhi-esp32/main`. Chỉ mang code hoặc pattern sau khi xác định owner module, license notice và test tương ứng.

## Build và hardware bring-up

```bash
source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
cd veetee-firmware
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

Host test cho state machine không phụ thuộc ESP-IDF:

```bash
cmake -S tests -B build/host-tests
cmake --build build/host-tests
ctest --test-dir build/host-tests --output-on-failure
```

Firmware bring-up hiện hiển thị color bars, phát tone ngắn, log thống kê PCM16
của mic và đưa button event qua application queue. Kích thước/offset/mirror màn
hình và INMP441 slot được đổi bằng `idf.py menuconfig`; giá trị mặc định vẫn là
baseline provisional cho tới khi kiểm tra trực tiếp trên phần cứng.

ESP-SR bring-up được bật bởi `CONFIG_VEETEE_ESP_SR_BRINGUP`. Build tạo
`build/srmodels/srmodels.bin`, kiểm tra không vượt partition và `idf.py flash` ghi
nó vào `resource_0`. Một I2S reader fan-out frame PCM 20 ms vào queue hữu hạn;
WakeNet chạy ở task riêng, drop frame cũ khi nghẽn và generation-check khi đổi
activation/interrupt role. Đây là layout development; resource updater production
phải stage/verify/activate slot A/B theo signed manifest trước khi hot-reload.
Board smoke đã xác nhận model load, contract 16 kHz mono/chunk 512, boot tone và
mic chạy liên tục hơn 45 giây không panic/watchdog. Wake-to-WebSocket vật lý còn
cần provision Wi-Fi và bind code 6 số để state machine vào `idle`; captive portal
không bật detector vì wake không được phép bỏ qua activation.

Khi chưa có cấu hình, firmware phát AP `Veetee-XXXX` và captive portal tại
`http://192.168.4.1`. Portal scan SSID, nhận Wi-Fi, bootstrap URL LAN, locale và
wake profile ID; password không được ghi log. Nếu station không lấy được IP trong
60 giây, firmware tự quay lại portal. Settings có schema version trong NVS và
`client_id` UUID bền vững.

## Bootstrap và activation hiện tại

- Sau khi nhận IP, firmware POST system report tới bootstrap URL đã provision;
  route native `/veetee/ota/` và alias `/xiaozhi/ota/` do server cung cấp.
- Request gửi hardware/client/model/firmware/locale headers; token và challenge
  không được ghi log. HTTP(S) redirect bị từ chối để credential không bị chuyển
  sang host ngoài bootstrap trust.
- URL provisioning chỉ chấp nhận HTTP(S) có host hợp lệ, không userinfo, query hay
  fragment. WebSocket URL trả về chỉ chấp nhận `ws://` hoặc `wss://` cùng policy.
- Code 6 số và challenge được lưu trong một activation record NVS v2 có version +
  CRC. Khi activate thành công, cùng record được thay atomically bằng device ID,
  scoped token, WebSocket URL và config version.
- Ticket pending được refresh ngay sau reboot và mỗi 30 giây; ticket hết TTL được
  server thay bằng code mới thay vì firmware poll challenge cũ vô hạn.
- Mã được vẽ bằng renderer số local, không chứa câu semantic hard-code. Sau bind,
  màn hình xóa code và chuyển sang trạng thái standby.

Authenticated bootstrap cũng nhận optional config/resource desired state. Firmware
đã có task bounded để pull manifest tối đa 32 KiB, từ chối redirect, verify strict
schema/board/flash/PSRAM/slot/SemVer/resource ABI/runtime, security epoch và
detached Ed25519 trước khi báo `manifest verified`. Verifier dùng Monocypher 4.0.3
và restricted JCS integer-only; fixture Node/host dùng chung development public
key. Đây chưa phải resource updater hoàn chỉnh: payload Range/resume, streaming
SHA-256, inactive-slot write, journal, atomic activation, health check và rollback
là milestone kế tiếp.

Hardware E2E từ portal tới bind vẫn cần nhập Wi-Fi thật trên điện thoại; firmware
không đọc password Wi-Fi đã lưu trên máy phát triển.

## WebSocket handshake hiện tại

- Transport chạy trên task riêng; callback chỉ assemble frame có bound và post
  command, không stop/destroy client từ callback.
- Header dùng Bearer device token, protocol version, hardware MAC `Device-Id` và
  UUID bền vững `Client-Id`; token/session không được log.
- Firmware gửi device hello fixture 16 kHz/mono/Opus 60 ms, chờ server hello tối đa
  10 giây rồi mới gửi `listen:start(mode=auto)` với source `button` hoặc
  `wake_word` qua cùng một code path.
- JSON text fragment được assemble với giới hạn 8 KiB. Hello sai transport,
  session/audio profile hoặc event có `session_id` khác sẽ đóng session.
- Button/interrupt dùng chung đường `abort`; long press gửi `listen:stop` rồi đóng
  channel. Generation mới làm event của connection cũ trở thành stale.
- Binary Opus upload/download và `tts:start/stop` playback gate là lát audio kế
  tiếp; transport hiện chỉ hoàn tất handshake/control lifecycle.
