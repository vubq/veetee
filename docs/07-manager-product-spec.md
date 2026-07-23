# Manager API và Manager Web

## 1. Vai trò

`manager-api` là control plane. Nó không relay audio và không được nằm trong đường nóng của phiên thoại. `manager-web` là console cho owner/operator, không phải UI trò chuyện chính của robot.

Baseline triển khai ngày 2026-07-22 đã có auth/tenant guard, pairing 6 số,
agent/provider config và immutable publish, desired/reported state, artifact edge,
live device MCP proxy có confirmation/audit, cùng Manager Web responsive giữ nguyên
prototype đã duyệt. Conversation timeout được validate ở Manager và clamp lại ở
voice-server; extension field được bảo toàn khi UI cập nhật draft. Realtime Lab đã
nhận event metadata thật theo batch idempotent, retention mặc định 7 ngày và không
lưu transcript/audio thô. Catalog artifact đã kiểm file immutable, SHA-256,
restricted JCS/Ed25519, board/ABI/license; wake profile activation và interrupt được
version riêng, rollout ghi desired state và chỉ hoàn tất theo reported state. Custom
`Hey VeeTee` vẫn chưa product-ready trước corpus benchmark thật. UI Pack V1 đã có
streaming upload vào quarantine, parser data-only, release signing, immutable
publish, explicit-device rollout và reported-state riêng cho `state.ui`.

Manager Web có thêm màn Operations read-only tại `#/operations`: audit trail
tenant-scoped đã redact, runtime LAN/Tailscale profile, privacy retention và
firmware inventory. Màn này không tạo firmware rollout khi API chưa có release
artifact đã ký; publish/apply vẫn được phân biệt rõ theo desired/reported state.

## 2. Data model lõi

Các bảng/aggregate nên bắt đầu nhỏ hơn Xiaozhi nhưng giữ đường mở rộng:

```text
tenant
  ├── user / role / permission
  ├── agent
  │     ├── agent_config_version
  │     ├── provider_binding
  │     ├── tool_policy
  │     ├── locale_profile
  │     └── conversation_policy
  ├── device
  │     ├── device_activation
  │     ├── device_status_snapshot
  │     ├── device_desired_state / device_reported_state
  │     └── device_agent_binding
  ├── provider_catalog / provider_credential
  ├── wake_profile / model_artifact
  ├── resource_bundle / artifact_manifest / artifact_rollout
  ├── firmware_artifact / firmware_rollout
  ├── mcp_endpoint / mcp_tool_snapshot
  └── audit_event
```

Transcript/audio retention là policy của tenant, không mặc định lưu vĩnh viễn. Dữ liệu nhạy cảm phải có `retention_until`, `redaction_status` và cơ chế xóa.

## 3. REST endpoint groups

### Auth

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/me
```

### Agent

```text
GET    /api/v1/agents
POST   /api/v1/agents
GET    /api/v1/agents/:id
PATCH  /api/v1/agents/:id
POST   /api/v1/agents/:id/publish
GET    /api/v1/agents/:id/config-versions
POST   /api/v1/agents/:id/test-provider
```

### Device + pairing

```text
GET    /api/v1/devices
POST   /api/v1/devices/activation/:code/bind
POST   /api/v1/devices/:id/unbind
GET    /api/v1/devices/:id/status
POST   /api/v1/devices/:id/command
GET    /api/v1/devices/:id/mcp/tools
POST   /api/v1/devices/:id/mcp/tools/:name/call
```

### Provider

```text
GET    /api/v1/providers
POST   /api/v1/providers
PATCH  /api/v1/providers/:id
POST   /api/v1/providers/:id/test
```

`PATCH` dùng `secretAction=keep|rotate|clear`; raw secret không xuất hiện trong
response/audit. Provider record có `priority`, `locales`, latency/error của lần probe
cuối, `failureCount` và `circuitState`. Probe OpenAI-compatible gọi đúng model binding,
không coi `/models` là bằng chứng model thực sự dùng được. Local provider chưa có
runtime reporter giữ `health=unknown` thay vì giả `healthy`.

Internal control plane có `POST /internal/v1/providers/resolve`, chỉ nhận service
token và danh sách provider ID của immutable agent snapshot. Endpoint này là secret
resolver cho voice-server single-node; không public cho Manager Web/firmware.

### OTA

```text
POST   /api/v1/firmware/artifacts
POST   /api/v1/firmware/releases
POST   /api/v1/firmware/releases/:id/rollout
GET    /api/v1/firmware/devices/:id
```

### Dynamic config, wake và resources

```text
GET/POST/PATCH /api/v1/wake-profiles
POST           /api/v1/wake-profiles/:id/publish
GET            /api/v1/artifacts
POST           /api/v1/artifacts/register
POST           /api/v1/artifacts/:id/publish
PATCH          /api/v1/artifacts/:id/benchmark
GET/POST/PATCH /api/v1/wake-profiles
POST           /api/v1/wake-profiles/:id/publish
GET/POST       /api/v1/resource-rollouts
GET            /api/v1/devices/:id/effective-config
POST           /api/v1/devices/:id/reconcile
GET            /api/v1/devices/:id/apply-events
```

### Voice-server internal

```text
GET    /internal/v1/agent-configs/:agentId?version=...
POST   /internal/v1/device-heartbeats
POST   /internal/v1/conversation-events/batch
```

Device-facing artifact/config endpoints không nằm trên voice hot path:

```text
GET  /veetee/config/v1/devices/:deviceId
GET  /veetee/artifacts/manifests/:manifestId
GET  /veetee/artifacts/:artifactId/content
PUT  /veetee/devices/:deviceId/reported-state
```

Internal endpoint dùng service token/mTLS, không expose ra internet.

## 4. Activation API behavior

`POST /api/v1/devices/activation/:code/bind` phải:

1. xác thực user/tenant/agent;
2. atomically consume Redis activation record;
3. kiểm tra MAC, challenge, TTL, attempt count;
4. từ chối device đã bind tenant khác;
5. tạo binding + audit event;
6. trả device summary và config version.

Không dùng code 6 số làm password lâu dài. Code chỉ là one-time pairing handle.
Bootstrap retry theo cùng hardware phải reuse ticket còn TTL. Activation challenge
là secret possession của device; token kết quả phải retry-idempotent và chỉ lưu hash
ở database. Device đã active phải xác thực Bearer token trước khi nhận config/WS URL;
không được cấp pairing code mới chỉ vì request thiếu hoặc sai token.

## 5. Manager Web information architecture

Visual/interaction direction đã được duyệt tại
`veetee-server/prototypes/manager-web/index.html`. Implementation Vue phải giữ
layout, typography, CSS tokens, responsive behavior và interaction hiện tại; không
redesign khi nối API. Provider name/model/metric trong prototype là fake data để
duyệt bố cục, không phải lựa chọn production.

### Overview

Hiển thị health của voice-server, provider, Redis/Postgres, số thiết bị online, p50/p95 first-audio và activation đang chờ.

### Devices

- Filter theo online/locale/agent/firmware.
- Pair device bằng code 6 số.
- Xem pin, Wi-Fi RSSI, firmware, last seen, active session.
- Device list trả `lastSeenAt` độc lập với nhãn status để UI không phải suy diễn thời điểm liên hệ từ desired/reported sequence.
- Xem desired vs reported config/resource/firmware version và drift.
- Reconcile, xem apply journal/error và rollback version đã ký.
- Gửi user-only MCP command với confirmation.

### Agents

- Persona/prompt versioning.
- Agent base prompt template tương tự `agent-base-prompt.txt`: template raw, biến allowlist,
  ngôn ngữ operator nhập, timezone, preview render và immutable snapshot.
- Personality preset dạng dữ liệu (bao gồm chính kiến/tranh luận) + custom override;
  preset không tạo nhánh semantic trong runtime.
- Locale + wake/exit profile.
- Activation wake profile và interrupt profile tách riêng, version theo locale/model.
- Conversation mode: cascade/realtime.
- Interaction mode: `auto` mặc định; manual/PTT chỉ là compatibility/accessibility option.
- Semantic intent examples và structured-output schema; không quản lý bằng danh sách exact phrase hard-code.
- Input admission policy: confidence, optional target-speaker, hỏi lại hay bỏ qua khi không chắc.
- `first_input_timeout`, `between_turns_timeout`, `closing_grace` và provider deadlines trong safe range.
- Fallback chain và tool policy.
- Publish immutable config version.

### Wake profiles và resource library

- Tách activation detector profile và interrupt detector profile; cả hai có thể tham chiếu cùng một ESP-SR model pack nhưng sensitivity/cooldown/allowed states version độc lập.
- Upload/chọn model đã build; custom wake training là build job riêng, không giả định nhập text là có model tốt.
- V1 chốt ESP-SR model pack/`srmodels.bin`; không cho upload runtime/operator/native code từ UI.
- Artifact library hiển thị kind, size, hash, signature, runtime ABI, license và scan status.
- Resource bundle composer kiểm tra flash/PSRAM budget và device capability trước publish.
- Rollout canary/percentage/pause/resume/rollback; publish không đồng nghĩa device đã apply.

Wake/model release hiện vẫn được tạo ngoài request path rồi catalog bằng directory
immutable `manifest.json + content.bin + .complete`. Riêng UI Pack V1 nhận binary
stream tại `POST /api/v1/ui-packs/uploads`, giới hạn 2 MiB trong lúc stream, ghi vào
quarantine, kiểm container/member/hash/data-only policy, ký manifest rồi mới rename
atomically vào local artifact store. Manager API không buffer toàn file trong RAM.
Object-store scoped upload, percentage rollout, pause/resume và operator rollback
command là hardening tiếp theo; rollout hiện chọn explicit device UUID để tránh mở
rộng ngoài ý muốn.

### Provider hub

- Catalog theo ASR/VAD/LLM/TTS/realtime/memory.
- Credential reference, không hiển thị secret sau khi lưu.
- Health/test request, latency và quota.
- Model/voice capability theo locale.
- Hiển thị baseline local: Silero VAD, Zipformer primary, ChunkFormer conditional
  fallback, VieNeu-TTS và 9router/OpenAI-compatible LLM binding.
- Hiển thị `local/external`, streaming/batch, CPU/GPU worker, model/version/license,
  fallback trigger, deadline, circuit state và p50/p95 benchmark.
- Không cho operator cấu hình “chạy cả hai ASR luôn” nếu không có evaluation profile;
  production profile dùng confidence/quality trigger có safe range.
- Hiển thị secret reference/health, không hiển thị raw credential; voice-server resolve credential qua secret service.
- Agent editor cấu hình primary/fallback riêng theo capability và locale; publish
  không tự gom toàn bộ provider enabled.

### Realtime lab

- Chọn immutable published agent và một trong ba input: Text, Audio Replay hoặc Live Mic.
- Text chỉ thay microphone và phát `vad.bypassed`/`asr.bypassed`; admission,
  planner/LLM, MCP, TTS và cancellation vẫn dùng provider/runtime thật.
- Audio Replay/Live Mic gửi PCM16 mono 16 kHz theo pacing realtime qua Silero VAD và
  Zipformer ASR thật; file replay tối đa 20 giây để bound session/resource.
- Chọn MCP `simulated`, `selected_device` hoặc `disabled`. Selected-device phải
  validate tenant ownership và yêu cầu device có voice session hoạt động.
- Hiển thị timeline `listen:auto -> VAD -> ASR -> admission -> planner/LLM/MCP -> TTS`.
- Hiển thị admission decision/reason code trước LLM/MCP, không gắn logic với tên nguồn âm thanh cụ thể.
- Xác nhận AI turn tự bắt đầu sau VAD final, không cần button submit.
- Nút interrupt dùng cùng generation/cancellation path với thiết bị; timeout có wake lại.
- Raw event inspector bounded, không persist transcript/audio và không đưa text vào metric label.
- Hiển thị latency speech-to-ASR, ASR-to-admission, admission-to-text,
  text-to-audio, end-to-audio và abort-to-silence.
- Gắn nhãn rõ `not measured` cho Opus device transport, AEC và loa vật lý; browser
  AEC/NS/AGC không được coi là acceptance của ESP32.

Manager API cấp phiên qua `POST /api/v1/lab/sessions`; agent phải đã publish, user
phải có role operator trở lên và bị giới hạn 12 phiên/phút. Response chứa JWT TTL
90 giây dùng một lần cùng URL `/veetee/lab/v1/`. Browser gửi token trong auth frame
đầu tiên; voice-server consume atomically qua internal service-authenticated API,
giới hạn mặc định bốn Lab session và kiểm Origin allowlist.

Voice-server chỉ gửi event bounded theo batch qua service-authenticated internal API.
Queue telemetry hữu hạn và không nằm trên hot path; lỗi Manager API không được làm
hỏng conversation session. Event UUID cho phép retry idempotent. Retention cấu hình
bằng `VEETEE_CONVERSATION_EVENT_RETENTION_DAYS` trong khoảng 1-30 ngày; payload mặc
định chỉ gồm locale, character count, confidence, admission/plan metadata, tool name,
cancellation, TTS lifecycle và bounded error code.

Đoạn event batch trên áp dụng cho telemetry từ thiết bị thật. Web Device Simulator
dùng WebSocket trực tiếp voice-server để UI thấy timeline của phiên hiện tại; nó
không relay audio qua Manager API và không ghi raw Lab session vào event store.

### MCP tools

- Tool catalog/pagination.
- Phân biệt AI-callable và user-only.
- Form theo JSON Schema.
- Audit từng call.
- Hiển thị safety class và confirmation requirement trước khi thực thi.

### Config, security và privacy

- Xem canonical config version, desired/reported drift và config diff.
- Chỉnh deadline theo stage trong safe range; UI không cho đặt timeout vô hạn.
- Chọn transcript/audio retention; raw audio mặc định tắt.
- Xem signing key id/security epoch và artifact provenance, không cho UI chạm private key.

## 6. Không có domain riêng

Dev UI và firmware bootstrap phải chạy được bằng IP/port:

```text
Manager Web:  http://192.168.1.20:8081
Manager API:  http://192.168.1.20:8001
Voice WS:     ws://192.168.1.20:8000/veetee/v1/
OTA:          http://192.168.1.20:8001/veetee/ota/
Artifacts:    http://192.168.1.20:8001/veetee/artifacts/
```

URL được lưu trong environment/config, không hard-code trong firmware. Khi cần truy cập từ ngoài LAN mà chưa mua domain:

- dùng Tailscale để kết nối private;
- hoặc Cloudflare Tunnel/ngrok cho staging tạm thời;
- không dùng tunnel miễn phí làm production SLA;
- khi public production, bật TLS qua tunnel/reverse proxy; domain là tùy chọn, không phải điều kiện của firmware.

## 7. Non-functional requirements

- API p95 <300 ms cho CRUD, không tính upload/health provider.
- List endpoint cursor pagination, không trả hàng nghìn tool/device một lần.
- Idempotency key cho bind, publish, OTA rollout.
- Desired/reported state và apply event phải có monotonic version/idempotency; stale report không được ghi đè state mới.
- Upload lớn dùng short-lived object-store URL; API không buffer binary artifact trong RAM.
- Artifact publish kiểm tra capability/size/hash/signature/runtime ABI/license và rollout scope.
- Audit mọi mutation có actor, before/after hash và request id.
- API error format thống nhất: `code`, `message`, `details`, `request_id`.
