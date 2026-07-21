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
