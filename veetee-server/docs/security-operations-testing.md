# Bảo mật, vận hành và kiểm thử

## Trạng thái upstream

Source tham khảo hữu ích cho nghiên cứu nhưng không nên đưa thẳng lên production mà
không threat model và hardening. CI quan sát chủ yếu build Docker image; Python core
không có unit/integration suite rõ ràng, Java có test nhưng Maven mặc định skip test.

## Xác thực và danh tính thiết bị

WebSocket dùng `Authorization`, `Device-Id`, `Client-Id`. Utility tham khảo tạo token:

- Outer JWT HS256.
- Inner payload có `device_id` và expiry, mã hóa AES-GCM.
- AES key derive từ auth secret bằng PBKDF2 và fixed salt.
- Token hết hạn sau một giờ.

Fixed salt và một shared symmetric secret không phải thiết kế identity tối ưu cho fleet.
Veetee nên xem xét credential riêng từng device, rotation/revocation, bind flow, key
storage trên device và ràng buộc token với audience/issuer/session.

## Trust boundaries

| Boundary | Mối nguy chính |
| --- | --- |
| Device -> gateway | Device giả, replay, oversized frame, protocol confusion |
| Gateway -> AI provider | Prompt/tool injection, data leak, cost abuse |
| Runtime -> manager API | Service secret leak, config tampering, lateral movement |
| Web/mobile -> manager API | Broken access control, tenant escape, token theft |
| OTA -> device | Firmware giả, downgrade, rollout sai |
| Plugin/MCP tool | Command injection, SSRF, quyền quá rộng |

## Kiểm soát tối thiểu

- TLS/WSS/MQTTS mọi nơi; không chấp nhận credential qua plaintext network.
- Giới hạn frame, JSON depth, upload size, audio rate và concurrent connection.
- Schema validation trước khi dispatch handler/tool.
- RBAC/tenant ownership ở service layer, không chỉ ẩn nút trên UI.
- Allowlist tool theo agent/device/user; confirmation cho hành động vật lý nhạy cảm.
- Signed OTA, anti-rollback, phased rollout và kill switch.
- Secret manager, rotation, redaction và không commit key mẫu dùng được.
- Audit event cho login, bind, config, tool call, OTA và admin command.

## Vận hành và observability

Metric nên có:

- Active/accepted/rejected connection và lý do disconnect.
- Audio ingress/egress bytes, packet drop và queue depth.
- VAD utterance duration, ASR/LLM/TTS latency percentile.
- Provider error/rate-limit/timeout và token/audio usage.
- Tool call count, duration, error và authorization denial.
- Event loop lag, thread pool saturation, memory và CPU/GPU.
- OTA check/download/success/rollback theo firmware/board cohort.

Log cần có correlation `session_id`, device ID đã hash/redact và request ID. Không log
raw token, API key, Wi-Fi/MQTT secret, audio hay transcript nếu chưa có privacy policy.

## Scale và resilience

- WebSocket cần sticky session hoặc session state nằm trọn trong một worker.
- Redis/database không nên nằm trên hot audio path nếu không có timeout/cache.
- Shared local model cần có concurrency limiter và admission control.
- Graceful shutdown dừng nhận connection mới, chờ/cancel stream, flush metric và đóng
  provider theo deadline.
- Manager API outage không nên làm rơi tất cả session đang chạy nếu cache còn hợp lệ.
- Provider outage cần fallback có policy; không loop retry làm tăng chi phí.

## Chiến lược kiểm thử Veetee

| Lớp | Kiểm thử |
| --- | --- |
| Protocol | Golden JSON/binary vectors, malformed/oversized/fuzz, version compatibility |
| Session | Hello, bind, listen, abort, reconnect, timeout và cleanup |
| Audio | Opus fixtures, VAD boundaries, sample-rate conversion, backpressure |
| Provider | Contract tests bằng fake server, timeout/retry/cancel/error mapping |
| Conversation | Deterministic fake ASR/LLM/TTS, tool routing và session isolation |
| API | OpenAPI contract, auth/RBAC/tenant, validation và idempotency |
| OTA | Signature, downgrade, power loss, staged rollout và rollback |
| Load | Nhiều WebSocket, slow client, provider saturation và event-loop lag |
| E2E | Firmware simulator -> server -> fake AI -> Opus response |

## Lệnh upstream để tham khảo

```bash
# Python runtime, không phải test suite
python app.py

# Java: cần override cấu hình skipTests của pom khi muốn chạy test
mvn test -DskipTests=false

# Web console
npm run test:unit
npm run test:snapshot
npm run check:i18n

# Mobile console
pnpm type-check
pnpm lint
pnpm test:snapshot
```

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/utils/auth.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/websocket_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/manager-api/pom.xml`
- `../references/xiaozhi-esp32-server/.github/workflows/`
- `../references/xiaozhi-esp32-server/docs/Deployment.md`
