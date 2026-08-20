# Giao thức và API

## Device WebSocket Veetee (Quyết định Veetee - M1.3)

Endpoint chính thức của Veetee Server:

```text
ws://<host>:<port>/api/v1/devices/ws
```

### Handshake HTTP Headers

- `Authorization: Bearer <token>` (Bắt buộc; so sánh constant-time với `VEETEE_DEVICE_GATEWAY_TOKEN`).
- `Protocol-Version: 1` (Bắt buộc; chỉ hỗ trợ version 1).
- `Device-Id: <string>` (Bắt buộc; non-empty, tối đa `VEETEE_ID_MAX_LENGTH=128`).
- `Client-Id: <string>` (Bắt buộc; non-empty, tối đa `VEETEE_ID_MAX_LENGTH=128`).

### Quy định Frame & Payload Limits

- `VEETEE_HELLO_TIMEOUT_SECONDS`: 10.0s (bắt buộc gửi frame text `hello` trong 10s đầu).
- `VEETEE_IDLE_TIMEOUT_SECONDS`: 60.0s (idle connection timeout).
- `VEETEE_PING_INTERVAL_SECONDS`: 20.0s.
- `VEETEE_PONG_TIMEOUT_SECONDS`: 10.0s.
- `VEETEE_JSON_MAX_BYTES`: 16384 bytes (16 KiB).
- `VEETEE_JSON_MAX_DEPTH`: 8.
- `VEETEE_BINARY_MAX_BYTES`: 65536 bytes (64 KiB).
- `VEETEE_CLEANUP_TIMEOUT_SECONDS`: 5.0s.

### Error Envelope Format

Mọi safe error envelope trả về từ server có cấu trúc:

```json
{
  "code": "veetee_invalid_input",
  "message": "Chi tiết lỗi an toàn (không lộ secret hay raw payload)",
  "session_id": "uuid-string-hoặc-null"
}
```

Các mã lỗi thuộc M0 taxonomy: `veetee_invalid_input`, `veetee_auth_failed`, `veetee_timeout`, `veetee_internal`.

### WebSocket Close Codes

- `1000`: Goodbye / normal closure.
- `1001`: Idle timeout.
- `1002`: Protocol confusion (ví dụ nhận binary frame trước hello).
- `1008`: Header/Auth failure, Hello timeout, schema violation, duplicate hello, session mismatch.
- `1009`: Message too big (vượt quá 16 KiB JSON hoặc 64 KiB binary).
- `1011`: Internal server error.
- `1012`: Service restart / graceful shutdown.

### Semantic JSON Control Frames (M1.3)

1. `hello`:
   - Device -> Server: `type: "hello"`, `version: 1`, `transport: "websocket"`, `audio_params: {format: "opus", sample_rate: 16000, channels: 1, frame_duration: 60}`. `features` là mapping boolean tùy chọn (tối đa 16 keys, key max 64 chars).
   - Server -> Device: `type: "hello"`, `transport: "websocket"`, `session_id: "<opaque UUID>"`, `audio_params: {format: "opus", sample_rate: 24000, channels: 1, frame_duration: 60}`.
2. `ping` / `pong`:
   - Device -> Server `ping` -> Server trả `{"type": "pong", "session_id": "..."}`.
   - Device -> Server `pong` -> Server cập nhật heartbeat.
3. `goodbye`:
   - Device -> Server `goodbye` -> Server trả `goodbye` và close websocket code 1000.
4. `abort`:
   - Device -> Server `abort` -> Idempotent abort active turn/generation.
5. `listen`:
   - Device -> Server `listen`: `state` (`start`, `stop`, `detect`), `mode` (`auto`, `manual`, `realtime`). Quản lý state machine `DeviceSession`.
6. `mcp` và unsupported frames:
   - Trả safe typed error envelope `veetee_invalid_input`. Tích hợp MCP pipeline hoãn lại M3.

### Binary Frames

- Binary trước hello: protocol error (close 1002).
- Binary sau hello: kiểm tra size <= 64 KiB và accept/drop an toàn. Audio decoding/Opus pipeline hoãn lại M1.5.

---

## OTA/config discovery Veetee (Quyết định Veetee - M1.4)

Endpoint thiết bị gọi để nhận server time, WebSocket URL/token và trạng thái firmware.

```text
GET  /api/v1/devices/ota/check   (fallback khi không có body system info)
POST /api/v1/devices/ota/check   (firmware baseline gửi system info JSON)
OPTIONS /api/v1/devices/ota/check (CORS preflight, credentials-free)
```

### Request

Header bắt buộc (có phân biệt hoa thường, so khớp không phân biệt case):

| Header | Yêu cầu |
| --- | --- |
| `Device-Id` | non-empty, <= `VEETEE_ID_MAX_LENGTH=128` |
| `Client-Id` | non-empty, <= `VEETEE_ID_MAX_LENGTH=128` |

Header tùy chọn có giới hạn:

| Header | Giới hạn |
| --- | --- |
| `User-Agent` | <= 256 chars |
| `Accept-Language` | <= 128 chars |
| `Content-Type` | POST có body phải là `application/json` (kèm charset chấp nhận) |

Body POST:

- Giới hạn kích thước trước khi parse: `VEETEE_JSON_MAX_BYTES=16384` (413 nếu vượt).
- JSON phải là object; depth tối đa `VEETEE_JSON_MAX_DEPTH=8`.
- Ràng buộc cấu trúc: tối đa 32 key mỗi object, key <= 64 chars, string value <= 256
  chars, array <= 32 phần tử; value phải là string/number/boolean/null/object/list một cấp.
- Body rỗng được chấp nhận (tương đương GET); body không phải JSON bị từ chối 400/415.

### Response 200

```json
{
  "server_time": {
    "timestamp": 1724150400000,
    "timezone_offset": 420
  },
  "websocket": {
    "url": "ws://<host>:<port>/api/v1/devices/ws",
    "token": "<gateway-token>",
    "version": 1
  },
  "firmware": {
    "version": "",
    "url": ""
  }
}
```

- `server_time.timestamp`: **epoch milliseconds** (baseline firmware chia 1000 để ra
  giây khi set clock; gửi giây sẽ làm clock thiết bị sai về ~1970).
- `server_time.timezone_offset`: offset phút so với UTC của server host.
- `websocket.url`: `VEETEE_DEVICE_WEBSOCKET_PUBLIC_URL` nếu cấu hình, ngược lại suy từ
  `VEETEE_HOST:VEETEE_PORT`. Bắt buộc `ws`/`wss`, không userinfo, không query/fragment.
- `websocket.token`: `VEETEE_DEVICE_GATEWAY_TOKEN`; chỉ xuất hiện ở response OTA này,
  không ghi vào log. Token rỗng khi chưa cấu hình.
- `firmware`: luôn là no-update ở M1.4 (`version` và `url` rỗng). Firmware baseline chỉ
  nâng cấp khi cả `version` lẫn `url` là string và version mới lớn hơn version hiện tại;
  version rỗng parse thành 0 component nên không bao giờ trigger update.
- Không có object `mqtt`, `activation` ở M1.4.

### Lỗi

| Status | Code | Ý nghĩa |
| --- | --- | --- |
| 400 | `veetee_invalid_input` | Header thiếu/sai, JSON sai cú pháp/không phải object, vượt depth/bounds |
| 413 | `veetee_payload_too_large` | Body vượt `VEETEE_JSON_MAX_BYTES` |
| 415 | `veetee_invalid_input` | Body có nhưng `Content-Type` không phải `application/json` |

Error envelope: `{"code": "veetee_*", "message": "...", "request_id": "..."}`. Mọi
response OTA (kể cả lỗi) có header `X-Veetee-Request-Id` và `Access-Control-Allow-Origin: *`.

### CORS

`OPTIONS` trả 204 với `Access-Control-Allow-Origin: *`, methods `GET, POST, OPTIONS`,
headers cho phép gồm `Device-Id, Client-Id, User-Agent, Accept-Language, Content-Type,
X-Veetee-Request-Id`. Không dùng `Access-Control-Allow-Credentials` (endpoint device
không dùng cookie/credential trình duyệt).

---

## Endpoint mặc định quan sát từ firmware tham khảo (Upstream reference)

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

## API design Veetee (đề xuất M0.2)

- Không dùng namespace, path, package hoặc response metadata chứa tên upstream. Firmware
  tham khảo phải nhận endpoint Veetee qua OTA/config discovery thay vì server giữ URL
  upstream. Bảng endpoint và identifier đầy đủ nằm trong
  [chính sách namespace](namespace-policy.md); các path đó vẫn chờ duyệt tại Cổng 0.
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
