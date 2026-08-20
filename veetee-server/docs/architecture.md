# Tổng quan kiến trúc server tham khảo

## Thành phần

```text
ESP32 / test client
  -> WebSocket trực tiếp hoặc MQTT gateway
  -> Python xiaozhi-server
       -> VAD -> ASR -> intent/LLM -> tool/MCP -> TTS
        -> HTTP OTA và vision
        -> tùy chọn gọi manager-api

Web/mobile console
  -> Java manager-api
       -> MySQL
       -> Redis
        -> cấu hình agent/device/model/OTA
```

| Thành phần | Vai trò quan sát được |
| --- | --- |
| `xiaozhi-server` | Đường realtime, session thiết bị, AI pipeline và streaming audio |
| `manager-api` | Quản trị user, device, agent, model, OTA và cấu hình tập trung |
| `manager-web` | Console Vue 2 cho trình duyệt |
| `manager-mobile` | Console Vue 3/uni-app cho H5/app/mini-program |
| `digital-human` | Test/browser client và wake-word runtime, không bắt buộc production |

## Hai chế độ vận hành

### Tối giản

Chỉ chạy Python realtime server. Cấu hình đọc từ YAML local, không cần MySQL/Redis.
HTTP server Python tự cung cấp OTA discovery/download đơn giản và vision endpoint.

### Đầy đủ

Python realtime server gọi Java manager API để lấy cấu hình chung và cấu hình riêng theo
device/agent. Java API dùng MySQL, Redis và phục vụ web/mobile console. Cách này tăng
khả năng quản trị nhưng thêm coupling, failure mode và yêu cầu bảo mật nội bộ.

## Entry point

Python `app.py`:

1. Kiểm tra FFmpeg.
2. Load config và auth key.
3. Khởi động GC manager.
4. Chạy WebSocket server và HTTP server đồng thời.
5. Bắt SIGINT/SIGTERM, hủy task và cleanup.

Spring Boot `AdminApplication.java` là entry point manager API. Web và mobile có entry
point riêng (`src/main.js`, `src/main.ts`) và chỉ giao tiếp qua HTTP API.

## Session boundary

`WebSocketServer` giữ provider có thể dùng chung và tạo `ConnectionHandler` cho mỗi
device. `ConnectionHandler` sở hữu state riêng:

- `session_id`, socket, header, device ID và IP.
- Trạng thái bind, listen, speaking, abort và AEC.
- Audio buffer, VAD window, ASR queue và speaker identity.
- Dialogue, prompt, memory và agent config.
- TTS sentence, IoT descriptors, MCP client và tool handler.
- Timeout task, executor và reporting queue.

Provider local nặng có thể dùng chung để tiết kiệm bộ nhớ; provider có stream/session
state phải được tạo riêng. Boundary này cần được ghi rõ khi viết provider mới.

## Concurrency

- `asyncio` xử lý socket và orchestration I/O.
- `ThreadPoolExecutor`/thread xử lý SDK hoặc model blocking.
- Queue nối audio/reporting với worker.
- Cleanup memory/title được đẩy sang daemon thread trong implementation tham khảo.

Cần tránh gọi blocking model/API trong event loop. Thread không được sửa session state
mà không có cơ chế đồng bộ; khi scale nhiều process, state trong memory không còn dùng
chung.

## Boundary đề xuất cho Veetee

Source tham khảo gợi ý bốn boundary nghiệp vụ, nhưng Veetee cần quyết định lại:

| Boundary | Trách nhiệm |
| --- | --- |
| Device gateway | Auth, protocol, session, backpressure và reconnect |
| Conversation engine | VAD/ASR, dialogue, LLM, intent, tool và TTS |
| Control plane | User/device/agent/model/config/OTA management |
| Client applications | Web/mobile UX, không chứa business secret |

Không bắt buộc mỗi boundary là một service riêng. Ban đầu có thể cùng một deployable
nhưng cần tách hợp đồng và ownership để dễ test và scale sau này.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/app.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/websocket_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/manager-api/pom.xml`
- `../references/xiaozhi-esp32-server/main/README_en.md`
