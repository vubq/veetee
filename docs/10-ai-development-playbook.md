# Playbook giao việc cho AI

## 1. Nguyên tắc

- Luôn đọc spec/contract gần task trước khi sửa code.
- Tìm implementation tương tự trong `references/` nhưng không sửa reference.
- Viết behavior/test trước khi mở rộng provider/board matrix.
- Trước mỗi task, đọc `docs/13-decision-register.md`; không tự quyết item đang chờ xác nhận phần cứng/provider/UI/security.
- Giữ patch theo owning layer; không nhét board code vào application core.
- Không thay contract im lặng. Mọi thay đổi protocol/API/schema có migration và fixture.
- Mặc định conversation là `mode=auto`: VAD tự finalize và AI tự trả lời, không đợi lần bấm thứ hai.
- Button wake và activation wake word phải mở cùng một flow; button/interrupt profile phải hủy cùng cancellation scope.
- Admission gate tổng quát phải chấp nhận/từ chối input trước LLM/MCP; không hard-code tên nguồn âm thanh.
- Conversation inactivity timeout, provider deadline và MCP timeout phải được test độc lập.
- Contract device-facing dùng `snake_case`; manager DTO mapping không được làm đổi wire fixture.
- Dynamic config/resource task phải ghi rõ desired/reported version, signature/hash, capability/ABI, apply boundary và rollback.
- Provider/model task phải đọc `docs/14-model-and-provider-baseline.md`, ghi rõ
  streaming/batch, memory budget, license, fallback trigger và cancellation behavior.
- Không viết exact-string intent hoặc hard-code persona/provider/locale behavior; đọc `docs/11-ai-first-design-principles.md`.
- Báo rõ phần nào đã chạy tự động và phần nào cần board thật.

## 2. Template task

```markdown
# Task: <một outcome quan sát được>

Context:
- Spec: docs/...
- Contract fixture: ...
- Reference implementation: references/...

Goal:
- Khi ..., hệ thống phải ...

Constraints:
- Giữ ...
- Không thay ...
- Deadline/latency/memory ...

Deliverables:
- Code: ...
- Tests: ...
- Docs/ADR: ...

Validation:
- Command: ...
- Hardware scenario: ...

Out of scope:
- ...
```

## 3. Prompt mẫu theo component

### Firmware

```text
Use $veetee-development. Implement the ESP32-S3 WebSocket V1 hello handshake in
veetee-firmware. Follow docs/03-firmware-spec.md and
docs/04-protocol-compatibility.md. Add host tests for missing type, wrong transport,
hello timeout and malformed binary length. Do not implement MQTT or change board pins.
```

### Voice server

```text
Use $veetee-development. Add a cancellable TurnArbiter to voice-server. In auto mode,
VAD finalization must pass through a general input-admission and semantic gate before
starting LLM/MCP. Button wake and activation wake word must share one flow. Button or
interrupt-profile abort must invalidate all late ASR/LLM/TTS/MCP events and emit
tts.stop once. Add inactivity/closing-grace and race tests with fake providers. Keep
the Xiaozhi JSON event contract unchanged.
```

### Local model cascade

```text
Use $veetee-development. Implement the Vietnamese provider baseline from
docs/14-model-and-provider-baseline.md and
veetee-server/packages/contracts/fixtures/config/provider-baseline-v1.json.
Keep Silero VAD local, Zipformer INT8 as primary ASR, and run ChunkFormer only as
conditional same-turn re-decode. Add fake-provider conformance tests for confidence,
stability, deadline, cancellation and stale-result dropping. Do not run both ASR
models on every utterance, do not call LLM before admission, and do not put model
weights or provider secrets in firmware.
```

### Manager API

```text
Use $veetee-development. Implement one-time six-digit device activation with CSPRNG,
10-minute TTL, five attempts, atomic Redis consume, tenant checks and audit event.
Add OpenAPI DTOs and integration tests. No domain/TLS assumption in local defaults.
```

### Manager web

```text
Use $veetee-development. Convert the approved pairing prototype into Vue 3. Keep the
visual direction and responsive behavior. Bind to generated manager API types, validate
the six-digit form and cover success/expired/conflict flows with Playwright.
```

### Config/resource bundle

```text
Use $veetee-development. Implement device resource reconcile for
veetee-s3-n16r8. Follow docs/12-dynamic-config-and-artifacts.md. Add a signed
resource manifest fixture, capability/size/ABI validation, resumable download into
an inactive slot, atomic activation, power-loss recovery and desired/reported state
tests. Do not send binary over the voice WebSocket and do not load executable code
from the resource bundle.
```

## 4. Review checklist

### Firmware

- State mutation chỉ qua event/state machine.
- Callback không block; queue bounded.
- Parser validate length/null/type; cJSON ownership đúng.
- NVS key migration và OTA identity không bị đổi vô tình.
- Test cả reconnect, abort, wake word và playback drain.
- Test `accept_tts_audio`/generation gate để frame raw Opus cũ không phát sau abort.
- Test button wake và activation wake word hội tụ; interrupt profile dùng cùng abort path.
- Test auto turn finalization; manual/PTT không được trở thành default.

### Voice server

- Async path không chứa blocking SDK call chưa đưa vào executor.
- Task có cancellation/deadline; late result bị drop.
- Intent/model output có schema; không branch theo exact transcript string.
- Input admission không branch theo tên nguồn âm cụ thể; VAD/ASR text không tự động tạo LLM/MCP turn.
- Inactivity/provider/MCP deadlines độc lập và closing có thể bị wake/button cancel.
- Admission decision, dialogue act và plan là ba schema tách biệt; test follow-up/confirmation/correction.
- Config/artifact publish có immutable version, signature/hash/ABI/capability check và desired/reported apply evidence.
- Session state không dùng global mutable config.
- Zipformer/ChunkFormer fallback is quality-gated, not blind retry; VieNeu capability
  declares stream/batch; 9router adapter has a tested backup and cancel path.
- Secret/transcript redaction và metric label bounded.

### Manager API/web

- Tenant guard trên query/mutation.
- Activation/idempotency atomic.
- API schema và UI type cùng version.
- Privileged MCP/OTA có confirmation + audit.
- Locale text không hard-code trong component.

## 5. Khi nào cần ADR

Tạo ADR trước khi:

- thay wire protocol hoặc sample/frame profile mặc định;
- thay database/ORM/auth model;
- thêm public internet deployment/domain/TLS topology;
- bật full-duplex/AEC production;
- thêm board profile hoặc thay pin;
- thay retention/voiceprint/privacy policy.

## 6. Definition of Done chung

- Outcome chạy được hoặc có mock rõ ràng.
- Test failure trước fix và pass sau fix.
- Docs/contract được cập nhật nếu behavior đổi.
- Lint/typecheck/unit/integration phù hợp đều pass.
- Không đụng unrelated user changes.
- Handoff nêu test đã chạy, giới hạn và bước xác nhận board thật.
