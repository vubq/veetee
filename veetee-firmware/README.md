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

Hardware E2E từ portal tới bind vẫn cần nhập Wi-Fi thật trên điện thoại; firmware
không đọc password Wi-Fi đã lưu trên máy phát triển.
