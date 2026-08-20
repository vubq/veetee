# Khởi tạo mạng, activation, OTA và cấu hình

## Chuỗi activation tham khảo

```text
network connected
  -> background ActivationTask
  -> kiểm tra asset version
  -> Ota::CheckVersion
  -> xử lý server time / activation challenge / activation code
  -> Ota::Activate nếu cần
  -> chọn MQTT config hoặc WebSocket config
  -> kiểm tra firmware mới
  -> idle
```

`Ota` không chỉ download firmware; response của version endpoint còn có thể phân phối
transport config, server time, serial number và activation data. Đây là coupling quan
trọng cần tách rõ trong thiết kế Veetee: provisioning/config discovery và firmware OTA
có thể cần lifecycle, quyền và tần suất khác nhau.

## API `Ota` quan trọng

| Phương thức | Vai trò |
| --- | --- |
| `CheckVersion()` | Gọi endpoint, parse version/config/activation |
| `Activate()` | Hoàn tất challenge/activation |
| `HasNewVersion()` | Có firmware mới hay không |
| `HasMqttConfig()` / `HasWebsocketConfig()` | Transport config đã nhận |
| `HasActivationCode/Challenge()` | Trạng thái bind/activate |
| `StartUpgrade(callback)` | Download và flash version đã discover |
| `Upgrade(url, callback)` | Nâng cấp trực tiếp từ URL |
| `MarkCurrentVersionValid()` | Xác nhận image boot thành công |
| `GetCheckVersionUrl()` | Tạo URL version check |

Callback upgrade nhận phần trăm progress và tốc độ byte/giây. Image mới cần được xác
thực trước khi đánh dấu hợp lệ; production nên có secure boot, signed image, anti-
rollback và rollback khi boot health check thất bại.

## Network provisioning

Board phát event chung cho scanning, connecting, connected, disconnected và chế độ
cấu hình Wi-Fi. Cellular implementation có thêm no-SIM, registration denied, init
failure và timeout. Core không nên biết driver cụ thể.

Source tham khảo có BluFi và các board/network helper khác. Veetee cần chốt:

- Kênh provisioning: BLE, SoftAP, USB, app companion hay pre-provisioned.
- Cách bảo vệ credential khi pairing.
- Factory reset xóa gì và có làm đổi `Client-Id` hay không.
- Retry/backoff và UI khi mất mạng.

## Settings và NVS

`Settings` bọc NVS theo namespace/key và lưu chuỗi, integer, boolean. Key đã phát hành
là persistent API; đổi tên/key/type cần migration. Không nên lưu access token hoặc Wi-Fi
credential dạng plaintext nếu hardware có thể dùng flash encryption/NVS encryption.

Các nhóm data nên được phân loại riêng:

| Nhóm | Ví dụ | Policy đề xuất |
| --- | --- | --- |
| Identity | device ID, client ID, serial | Ổn định, có quy tắc reset rõ |
| Secret | Wi-Fi, token, MQTT password | Encrypt, không log |
| Runtime config | endpoint, volume, locale | Versioned schema, validate |
| OTA state | active/pending version, rollback | Atomic và chịu mất điện |
| Cache | asset metadata, temporary state | Có thể tái tạo/xóa |

## Asset partition

Upstream có partition riêng cho model/font/image/audio asset và kiểm tra version khi
khởi động. Asset có thể được download độc lập firmware. Cần kiểm tra kích thước partition,
hash, atomic switch và khả năng rollback; không ghi đè asset đang sử dụng.

## Checklist production

- HTTPS/TLS và pin/trust policy cho endpoint provisioning, activation và OTA.
- Chữ ký firmware/asset và kiểm tra hash trước khi switch partition.
- Power-loss test ở mỗi giai đoạn erase/write/activate.
- Rate limit activation và không hiện secret trong log/màn hình.
- Version schema cho response config và khả năng bỏ qua field mới.
- Recovery path khi endpoint trả config sai hoặc cả hai transport đều thất bại.
- Telemetry tối thiểu cho lý do rollback, download error và boot loop.

## Source đối chiếu

- `../references/xiaozhi-esp32/main/ota.h`
- `../references/xiaozhi-esp32/main/ota.cc`
- `../references/xiaozhi-esp32/main/settings.*`
- `../references/xiaozhi-esp32/main/assets.*`
- `../references/xiaozhi-esp32/main/application.cc`
- `../references/xiaozhi-esp32/docs/blufi.md`
