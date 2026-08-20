# Ghi chú kỹ thuật firmware

## Mục đích

Thư mục này tổng hợp các ý tưởng và giao diện quan trọng từ source tham khảo
`../references/xiaozhi-esp32`. Đây là tài liệu nghiên cứu để hỗ trợ thiết kế firmware
Veetee, không phải đặc tả kiến trúc chính thức và không khẳng định Veetee sẽ kế thừa
toàn bộ cách triển khai của upstream.

Khi source tham khảo và tài liệu này khác nhau, source tham khảo là căn cứ cho hành vi
upstream. Khi Veetee có quyết định kiến trúc riêng, cần ghi quyết định đó trong tài liệu
Veetee và không sửa lại lịch sử khảo sát để biến nó thành đặc tả.

## Danh mục

| Tài liệu | Nội dung |
| --- | --- |
| [Tổng quan kiến trúc](architecture.md) | Thành phần, vòng đời, phân tách core và phần cứng |
| [Giao diện và phương thức](interfaces.md) | Các class, callback và hợp đồng quan trọng |
| [Âm thanh và trạng thái](audio-and-state.md) | Audio pipeline, task, queue, state machine và AEC |
| [Giao thức thiết bị-server](device-server-protocol.md) | WebSocket, MQTT/UDP, JSON, Opus và MCP |
| [Khởi tạo, OTA và cấu hình](provisioning-ota-config.md) | Network provisioning, activation, OTA, asset và NVS |

## Bản đồ source tham khảo

| Phạm vi | Vị trí upstream |
| --- | --- |
| Điều phối ứng dụng | `../references/xiaozhi-esp32/main/application.*` |
| Trạng thái thiết bị | `../references/xiaozhi-esp32/main/device_state*` |
| Audio | `../references/xiaozhi-esp32/main/audio/` |
| Giao thức mạng | `../references/xiaozhi-esp32/main/protocols/` |
| Board abstraction | `../references/xiaozhi-esp32/main/boards/common/` |
| Board cụ thể | `../references/xiaozhi-esp32/main/boards/` |
| MCP trên thiết bị | `../references/xiaozhi-esp32/main/mcp_server.*` |
| OTA, asset, NVS | `../references/xiaozhi-esp32/main/ota.*`, `assets.*`, `settings.*` |
| Tài liệu giao thức gốc | `../references/xiaozhi-esp32/docs/` |

## Nguyên tắc sử dụng

- Dùng tài liệu này để tìm nhanh điểm vào và hợp đồng cần đối chiếu.
- Kiểm tra source thực tế trước khi port một chi tiết nhạy cảm như wire format, bộ nhớ,
  ownership, timeout hoặc transition trạng thái.
- Không sửa code trong `references/` khi phát triển Veetee, trừ khi có yêu cầu rõ ràng.
- Các giá trị 16 kHz, 24 kHz, Opus 60 ms, timeout và kích thước queue là giá trị quan
  sát từ upstream hiện tại, chưa phải chuẩn bắt buộc của Veetee.
