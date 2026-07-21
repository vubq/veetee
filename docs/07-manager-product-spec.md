# Manager API và Manager Web

## 1. Vai trò

`manager-api` là control plane. Nó không relay audio và không được nằm trong đường nóng của phiên thoại. `manager-web` là console cho owner/operator, không phải UI trò chuyện chính của robot.

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
GET    /api/v1/providers/catalog
POST   /api/v1/providers/credentials
PATCH  /api/v1/providers/credentials/:id
POST   /api/v1/providers/credentials/:id/health
GET    /api/v1/providers/models?kind=llm&locale=vi-VN
```

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
POST           /api/v1/artifacts/uploads
GET            /api/v1/artifacts/:id
POST           /api/v1/artifacts/:id/validate
POST           /api/v1/resource-bundles
POST           /api/v1/resource-bundles/:id/publish
POST           /api/v1/resource-bundles/:id/rollout
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
POST /veetee/devices/:deviceId/reported-state
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
- Xem desired vs reported config/resource/firmware version và drift.
- Reconcile, xem apply journal/error và rollback version đã ký.
- Gửi user-only MCP command với confirmation.

### Agents

- Persona/prompt versioning.
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

### Realtime lab

- Chọn device/agent.
- Hiển thị timeline `listen:auto -> VAD final -> ASR -> planner/LLM -> TTS`.
- Hiển thị admission decision/reason code trước ASR/LLM, không gắn logic với tên nguồn âm thanh cụ thể.
- Xác nhận AI turn tự bắt đầu sau VAD final, không cần button submit.
- Nút interrupt/cancel.
- Raw event inspector đã redact.
- So sánh provider latency.

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
Manager API:  http://192.168.1.20:8002
Voice WS:     ws://192.168.1.20:8000/veetee/v1/
OTA:          http://192.168.1.20:8003/veetee/ota/
Artifacts:    http://192.168.1.20:8003/veetee/artifacts/
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
