# Giao thức và API

## Device WebSocket

Endpoint mặc định quan sát:

```text
ws://<host>:8000/xiaozhi/v1/
```

Handshake header gồm `Authorization`, `Protocol-Version`, `Device-Id`, `Client-Id`.
Text frame là JSON control; binary frame là Opus hoặc binary envelope theo protocol
version. Đặc tả message chi tiết nằm ở `../../veetee-firmware/docs/device-server-protocol.md`.

Server nhận `hello`, `listen`, `abort`, `iot`, `mcp`, `server`, `ping`. Server gửi
`hello`, `stt`, `llm`, `tts`, `mcp`, `system`, `alert` và binary audio. Hai đầu phải
kiểm tra `session_id` và feature negotiation.

## MQTT gateway

Python server có thể nhận kết nối bridge qua WebSocket path có `?from=mqtt_gateway`.
Gateway chuyển MQTT control và UDP audio thành hợp đồng nội bộ cho `ConnectionHandler`.
Đây là implementation detail upstream; Veetee cần quyết định gateway là process riêng,
protocol nội bộ nào và trust boundary ở đâu.

## Python HTTP service

Cổng mặc định quan sát là `8003`.

| Method | Path | Khi nào có | Vai trò |
| --- | --- | --- | --- |
| GET/POST/OPTIONS | `/xiaozhi/ota/` | Local mode | Trả WebSocket/OTA config cho device |
| GET/OPTIONS | `/xiaozhi/ota/download/{filename}` | Local mode | Chỉ download file trong `data/bin` |
| GET/POST/OPTIONS | `/mcp/vision/explain` | Luôn đăng ký | Nhận ảnh/câu hỏi cho vision model |

Khi `read_config_from_api=true`, OTA route local không được đăng ký; control plane đảm
nhận OTA/config. Download handler phải chống path traversal và giới hạn file/content.
Vision endpoint cần giới hạn upload, MIME, timeout, token và chống SSRF nếu gọi URL ngoài.

## MCP device protocol

Server đóng vai MCP client đối với ESP32. Flow:

```text
device hello features.mcp=true
  -> server initialize
  -> device capabilities/serverInfo
  -> server tools/list (có pagination)
  -> LLM/backend chọn tool
  -> server tools/call
  -> device result/error
```

MCP payload là JSON-RPC 2.0 bọc trong message `type=mcp`. Tool invocation cần được ràng
buộc vào đúng session/device và authorization policy. Không để model tự động gọi
user-only tool như reboot/upgrade.

## Manager API

Spring Boot context quan sát là `/xiaozhi`, thường ở cổng `8002`. OpenAPI/Knife4j có
thể có tại `/xiaozhi/doc.html`. API đầy đủ rất lớn; các nhóm quan trọng:

| Nhóm | Base path ví dụ | Vai trò |
| --- | --- | --- |
| Runtime config | `/config` | Server base, agent models, correction words |
| Authentication | `/user` | Login, register, info, password, public config |
| Device/agent | Theo controller `device`, `agent` | Bind và cấu hình trợ lý |
| Model/provider | `/models`, `/models/provider` | CRUD provider/model/voice |
| OTA | Controller trong module OTA | Firmware, version và rollout |
| Voice | `/ttsVoice`, `/voiceClone`, `/voiceResource` | Timbre và clone voice |
| Knowledge | `/datasets` | Dataset/document cho RAG |
| Administration | `/admin/...` | User, role, parameter, dictionary, server action |

Ba API nội bộ mà Python runtime phụ thuộc trực tiếp:

```text
POST /xiaozhi/config/server-base
POST /xiaozhi/config/agent-models
POST /xiaozhi/config/correct-words
```

Cần đọc `ConfigController.java` và DTO tương ứng để lấy body/response chính xác tại
commit đang pin. Không nên coi bảng tổng hợp này là OpenAPI contract.

## API design nếu xây Veetee

- Tách public device API, user API, admin API và service-to-service API.
- Version endpoint/path hoặc media type.
- Dùng OpenAPI sinh từ source và contract test cho client.
- Idempotency cho activation, bind, OTA report và command.
- Pagination/filter/sort nhất quán.
- Error envelope có machine code, message an toàn và correlation ID.
- RBAC tenant-aware; admin action có audit log.
- Rate limit login, activation, upload và AI-cost endpoint.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/http_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/api/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/manager-api/src/main/java/xiaozhi/modules/`
- `../references/xiaozhi-esp32-server/docs/mqtt-gateway-integration.md`
- `../references/xiaozhi-esp32-server/docs/mcp-endpoint-integration.md`
