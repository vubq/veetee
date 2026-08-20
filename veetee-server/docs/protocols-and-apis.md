# Giao thuc va API

## Device WebSocket

Endpoint mac dinh quan sat:

```text
ws://<host>:8000/xiaozhi/v1/
```

Handshake header gom `Authorization`, `Protocol-Version`, `Device-Id`, `Client-Id`.
Text frame la JSON control; binary frame la Opus hoac binary envelope theo protocol
version. Dac ta message chi tiet nam o `../../veetee-firmware/docs/device-server-protocol.md`.

Server nhan `hello`, `listen`, `abort`, `iot`, `mcp`, `server`, `ping`. Server gui
`hello`, `stt`, `llm`, `tts`, `mcp`, `system`, `alert` va binary audio. Hai dau phai
kiem tra `session_id` va feature negotiation.

## MQTT gateway

Python server co the nhan ket noi bridge qua WebSocket path co `?from=mqtt_gateway`.
Gateway chuyen MQTT control va UDP audio thanh hop dong noi bo cho `ConnectionHandler`.
Day la implementation detail upstream; Veetee can quyet dinh gateway la process rieng,
protocol noi bo nao va trust boundary o dau.

## Python HTTP service

Cong mac dinh quan sat la `8003`.

| Method | Path | Khi nao co | Vai tro |
| --- | --- | --- | --- |
| GET/POST/OPTIONS | `/xiaozhi/ota/` | Local mode | Tra WebSocket/OTA config cho device |
| GET/OPTIONS | `/xiaozhi/ota/download/{filename}` | Local mode | Chi download file trong `data/bin` |
| GET/POST/OPTIONS | `/mcp/vision/explain` | Luon dang ky | Nhan anh/cau hoi cho vision model |

Khi `read_config_from_api=true`, OTA route local khong duoc dang ky; control plane dam
nhan OTA/config. Download handler phai chong path traversal va gioi han file/content.
Vision endpoint can gioi han upload, MIME, timeout, token va chong SSRF neu goi URL ngoai.

## MCP device protocol

Server dong vai MCP client doi voi ESP32. Flow:

```text
device hello features.mcp=true
  -> server initialize
  -> device capabilities/serverInfo
  -> server tools/list (co pagination)
  -> LLM/backend chon tool
  -> server tools/call
  -> device result/error
```

MCP payload la JSON-RPC 2.0 boc trong message `type=mcp`. Tool invocation can duoc rang
buoc vao dung session/device va authorization policy. Khong de model tu dong goi
user-only tool nhu reboot/upgrade.

## Manager API

Spring Boot context quan sat la `/xiaozhi`, thuong o cong `8002`. OpenAPI/Knife4j co
the co tai `/xiaozhi/doc.html`. API day du rat lon; cac nhom quan trong:

| Nhom | Base path vi du | Vai tro |
| --- | --- | --- |
| Runtime config | `/config` | Server base, agent models, correction words |
| Authentication | `/user` | Login, register, info, password, public config |
| Device/agent | Theo controller `device`, `agent` | Bind va cau hinh tro ly |
| Model/provider | `/models`, `/models/provider` | CRUD provider/model/voice |
| OTA | Controller trong module OTA | Firmware, version va rollout |
| Voice | `/ttsVoice`, `/voiceClone`, `/voiceResource` | Timbre va clone voice |
| Knowledge | `/datasets` | Dataset/document cho RAG |
| Administration | `/admin/...` | User, role, parameter, dictionary, server action |

Ba API noi bo ma Python runtime phu thuoc truc tiep:

```text
POST /xiaozhi/config/server-base
POST /xiaozhi/config/agent-models
POST /xiaozhi/config/correct-words
```

Can doc `ConfigController.java` va DTO tuong ung de lay body/response chinh xac tai
commit dang pin. Khong nen coi bang tong hop nay la OpenAPI contract.

## API design neu xay Veetee

- Tach public device API, user API, admin API va service-to-service API.
- Version endpoint/path hoac media type.
- Dung OpenAPI sinh tu source va contract test cho client.
- Idempotency cho activation, bind, OTA report va command.
- Pagination/filter/sort nhat quan.
- Error envelope co machine code, message an toan va correlation ID.
- RBAC tenant-aware; admin action co audit log.
- Rate limit login, activation, upload va AI-cost endpoint.

## Source doi chieu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/http_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/api/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/manager-api/src/main/java/xiaozhi/modules/`
- `../references/xiaozhi-esp32-server/docs/mqtt-gateway-integration.md`
- `../references/xiaozhi-esp32-server/docs/mcp-endpoint-integration.md`
