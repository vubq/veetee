# Bao mat, van hanh va kiem thu

## Trang thai upstream

Source tham khao huu ich cho nghien cuu nhung khong nen dua thang len production ma
khong threat model va hardening. CI quan sat chu yeu build Docker image; Python core
khong co unit/integration suite ro rang, Java co test nhung Maven mac dinh skip test.

## Xac thuc va danh tinh thiet bi

WebSocket dung `Authorization`, `Device-Id`, `Client-Id`. Utility tham khao tao token:

- Outer JWT HS256.
- Inner payload co `device_id` va expiry, ma hoa AES-GCM.
- AES key derive tu auth secret bang PBKDF2 va fixed salt.
- Token het han sau mot gio.

Fixed salt va mot shared symmetric secret khong phai thiet ke identity toi uu cho fleet.
Veetee nen xem xet credential rieng tung device, rotation/revocation, bind flow, key
storage tren device va rang buoc token voi audience/issuer/session.

## Trust boundaries

| Boundary | Moi nguy chinh |
| --- | --- |
| Device -> gateway | Device gia, replay, oversized frame, protocol confusion |
| Gateway -> AI provider | Prompt/tool injection, data leak, cost abuse |
| Runtime -> manager API | Service secret leak, config tampering, lateral movement |
| Web/mobile -> manager API | Broken access control, tenant escape, token theft |
| OTA -> device | Firmware gia, downgrade, rollout sai |
| Plugin/MCP tool | Command injection, SSRF, quyen qua rong |

## Kiem soat toi thieu

- TLS/WSS/MQTTS moi noi; khong chap nhan credential qua plaintext network.
- Gioi han frame, JSON depth, upload size, audio rate va concurrent connection.
- Schema validation truoc khi dispatch handler/tool.
- RBAC/tenant ownership o service layer, khong chi an nut tren UI.
- Allowlist tool theo agent/device/user; confirmation cho hanh dong vat ly nhay cam.
- Signed OTA, anti-rollback, phased rollout va kill switch.
- Secret manager, rotation, redaction va khong commit key mau dung duoc.
- Audit event cho login, bind, config, tool call, OTA va admin command.

## Van hanh va observability

Metric nen co:

- Active/accepted/rejected connection va ly do disconnect.
- Audio ingress/egress bytes, packet drop va queue depth.
- VAD utterance duration, ASR/LLM/TTS latency percentile.
- Provider error/rate-limit/timeout va token/audio usage.
- Tool call count, duration, error va authorization denial.
- Event loop lag, thread pool saturation, memory va CPU/GPU.
- OTA check/download/success/rollback theo firmware/board cohort.

Log can co correlation `session_id`, device ID da hash/redact va request ID. Khong log
raw token, API key, Wi-Fi/MQTT secret, audio hay transcript neu chua co privacy policy.

## Scale va resilience

- WebSocket can sticky session hoac session state nam tron trong mot worker.
- Redis/database khong nen nam tren hot audio path neu khong co timeout/cache.
- Shared local model can co concurrency limiter va admission control.
- Graceful shutdown dung nhan connection moi, cho/cancel stream, flush metric va dong
  provider theo deadline.
- Manager API outage khong nen lam roi tat ca session dang chay neu cache con hop le.
- Provider outage can fallback co policy; khong loop retry lam tang chi phi.

## Chien luoc kiem thu Veetee

| Lop | Kiem thu |
| --- | --- |
| Protocol | Golden JSON/binary vectors, malformed/oversized/fuzz, version compatibility |
| Session | Hello, bind, listen, abort, reconnect, timeout va cleanup |
| Audio | Opus fixtures, VAD boundaries, sample-rate conversion, backpressure |
| Provider | Contract tests bang fake server, timeout/retry/cancel/error mapping |
| Conversation | Deterministic fake ASR/LLM/TTS, tool routing va session isolation |
| API | OpenAPI contract, auth/RBAC/tenant, validation va idempotency |
| OTA | Signature, downgrade, power loss, staged rollout va rollback |
| Load | Nhieu WebSocket, slow client, provider saturation va event-loop lag |
| E2E | Firmware simulator -> server -> fake AI -> Opus response |

## Lenh upstream de tham khao

```bash
# Python runtime, khong phai test suite
python app.py

# Java: can override cau hinh skipTests cua pom khi muon chay test
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

## Source doi chieu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/utils/auth.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/websocket_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/manager-api/pom.xml`
- `../references/xiaozhi-esp32-server/.github/workflows/`
- `../references/xiaozhi-esp32-server/docs/Deployment.md`
