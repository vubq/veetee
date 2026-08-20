# Ghi chú kỹ thuật server

## Mục đích

Thư mục này tổng hợp các thành phần, phương thức và giao thức đang có trong source
tham khảo `../references/xiaozhi-esp32-server`. Tài liệu dùng để nghiên cứu và lập kế
hoạch cho Veetee; nó không định nghĩa kiến trúc chính thức của server Veetee.

Source tham khảo là monorepo gồm Python realtime server, Java management API, Vue web,
uni-app mobile và digital-human test client. Veetee không mặc định phải sử dụng tất cả
các thành phần hoặc cùng công nghệ.

## Danh mục

| Tài liệu | Nội dung |
| --- | --- |
| [Tổng quan kiến trúc](architecture.md) | Thành phần, boundary và deployment mode |
| [Realtime AI pipeline](realtime-ai-pipeline.md) | Connection, audio, VAD, ASR, LLM, tool và TTS |
| [Giao thức và API](protocols-and-apis.md) | Device WebSocket, HTTP/OTA/vision, MCP và manager API |
| [Provider và cấu hình](providers-and-configuration.md) | Plugin factory, selected modules và config precedence |
| [Bảo mật, vận hành và kiểm thử](security-operations-testing.md) | Auth, secret, scale, observability và test gap |
| [Kế hoạch triển khai Veetee Server](server-implementation-plan.md) | Kiến trúc mục tiêu, provider đã chốt, task và cổng duyệt từng mốc |

## Bản đồ source tham khảo

| Thành phần | Vị trí upstream |
| --- | --- |
| Python realtime server | `../references/xiaozhi-esp32-server/main/xiaozhi-server/` |
| Java management API | `../references/xiaozhi-esp32-server/main/manager-api/` |
| Web console | `../references/xiaozhi-esp32-server/main/manager-web/` |
| Mobile console | `../references/xiaozhi-esp32-server/main/manager-mobile/` |
| Browser test client | `../references/xiaozhi-esp32-server/main/digital-human/` |
| Deployment/integration docs | `../references/xiaozhi-esp32-server/docs/` |

## Cách đọc

- Dùng `architecture.md` để xác định subsystem nào cần tham khảo.
- Dùng `realtime-ai-pipeline.md` khi làm audio/session/AI orchestration.
- Dùng `protocols-and-apis.md` cùng tài liệu firmware khi thay đổi contract thiết bị.
- Dùng `providers-and-configuration.md` khi thêm model/provider.
- Dùng `server-implementation-plan.md` làm thứ tự thực thi chính thức; AI phải dừng ở
  cổng duyệt cuối mỗi mốc.
- Kiểm tra source tại commit đang pin trước khi triển khai wire format hoặc endpoint.
