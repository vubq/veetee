# Roadmap và kế hoạch triển khai

Thứ tự dưới đây được tối ưu để AI có thể code từng lát dọc, luôn có artifact chạy được. Không bắt đầu bằng việc port toàn bộ Xiaozhi.

Trạng thái implementation ngày 2026-07-22: Phase 0-3 đã có source/build/test và
hardware bring-up; blank-flash AP -> Wi-Fi -> bind, resource A/B signed rollout và
reported-state đã chạy trên board. Phase 4 cascade local đã pass host wire E2E cho
Silero -> Zipformer -> semantic gate/9Router -> VieNeu -> Opus, cancellation,
inactivity và semantic reject. Phase 6 MCP đã pass firmware/voice/Manager end-to-end
trên host. Phase 5 có auth, pairing, agent/provider config, desired/reported state,
Manager Web theo prototype với live MCP, Realtime Lab dùng event metadata thật và
catalog/wake rollout cơ bản. Custom `Hey VeeTee`, AEC, canary pause/resume và
soak phần cứng vẫn phải qua benchmark trên thiết bị thật.

## Phase 0 - Freeze contract và board (1-2 ngày)

### Việc cần làm

- Chốt pin map thật từ schematic/đồng hồ đo.
- Chụp ảnh board + wiring, xác nhận INMP441 slot và MAX98357A gain.
- Tạo protocol fixtures từ `docs/04-protocol-compatibility.md`.
- Quyết định license/NOTICE cho code reuse.
- Chốt profile LAN không domain và URL env.
- Chốt device-edge/gateway ownership cho port 8003; không để bootstrap/artifact route mơ hồ giữa manager-api và storage.
- Freeze exact repository/commit/license/runtime của Silero VAD, Zipformer
  Vietnamese 30M INT8, ChunkFormer-CTC-Large-Vie và VieNeu-TTS v3 Turbo.
- Deployment single-node đã chốt; ghi CPU/RAM/VRAM/GPU, concurrency mục tiêu và
  memory budget riêng cho từng model worker.
- Probe 9router: base URL/auth, Chat Completions hay Responses, SSE, structured
  output, tool calling, cancellation, usage và concurrency. Không dùng token phiên
  Codex trực tiếp làm app credential.
- Chốt canonical `/veetee/...` routes, không ship branded reference alias; freeze resource ABI, ESP-SR model-pack format và partition budget sau size probe.
- Crypto spike JCS + detached Ed25519 đã pass trên host và ESP-IDF bằng
  Monocypher 4.0.3; giữ signed test vector chạy chung ở Node và firmware.

### Đầu ra

- `veetee-firmware/boards/veetee-s3-n16r8/README.md`.
- `veetee-server/packages/contracts/fixtures/*.json`.
- `veetee-server/packages/contracts/fixtures/config/provider-baseline-v1.json` và
  model/provider capability report.
- ADR hardware/license/deployment.

### Gate

Không còn pin “tham chiếu” chưa xác nhận, không có secret thật trong repo và mọi
model/provider có owner, license, checksum/version cùng capability probe report.

## Phase 1 - Firmware skeleton + hardware smoke (3-5 ngày)

### Việc cần làm

- ESP-IDF project, board factory duy nhất, executable A/B; resource A/B chỉ freeze sau size probe.
- Boot log, LED, button, ST7789 test pattern.
- I2S RX record 5 giây và TX tone/sine.
- NVS settings schema/migration/factory reset.

### Đầu ra

- Flash được bằng `idf.py`/release script.
- Màn hình, mic, loa pass test standalone.

### Gate

Không có watchdog reset; heap/PSRAM report ổn định 10 phút.

## Phase 2 - Wi-Fi + bootstrap + activation (3-5 ngày)

### Việc cần làm

- AP captive portal + station connect.
- 60 giây timeout fallback AP.
- OTA POST nhận server time/websocket/activation.
- Bootstrap nhận optional config/resource manifest; device desired/reported state và ETag reconcile.
- Manager API tạo/bind code 6 số CSPRNG + TTL.
- Firmware hiển thị/đọc code và activate.

### Đầu ra

- Demo từ blank flash -> AP -> LAN bootstrap -> bind manager -> idle.

### Gate

Sai password, server down, code hết hạn và bind trùng đều có UX/alert rõ. Config drift phải hiển thị; config/artifact download không nằm trên voice WebSocket.

## Phase 3 - WebSocket compatibility + audio (5-8 ngày)

### Việc cần làm

- Protocol-neutral API, WebSocket v1 raw Opus.
- Hello timeout/session id/headers.
- Device `listen`, `abort`; server `stt/tts/llm`; compatibility reason `wake_word_detected` và native reason codes.
- Audio service queue, Opus, resample.
- Button wake và activation wake word cùng phát `listen:start(mode=auto)` qua một command path.
- Host parser tests + Wi-Fi integration test.

### Đầu ra

- Nói một câu tiếng Việt qua LAN và nghe phản hồi TTS giả lập.

### Gate

- p95 assistant-gate/wake-to-first-frame <150 ms local.
- abort không phát audio cũ.

## Phase 4 - Voice-server cascade MVP (5-8 ngày)

### Việc cần làm

- Session/turn arbiter/cancellation.
- Mock provider cho input admission/VAD/ASR/intent/LLM/TTS.
- Provider registry + config snapshot.
- `silero-local` VAD/endpoint worker.
- Sherpa-ONNX Zipformer Vietnamese 30M INT8 primary streaming ASR.
- ChunkFormer-CTC-Large-Vie quality re-decode trong cùng turn, chỉ chạy khi
  confidence/ổn định thấp và còn deadline.
- VieNeu-TTS v3 Turbo local adapter; probe true streaming, nếu batch thì dùng
  Vietnamese sentence chunker và khai báo capability đúng.
- `openai-compatible-9router` LLM adapter cho dev; pass SSE/structured output/tool
  calling/cancellation conformance, có backup binding.
- Admission gate tổng quát: chỉ input hợp lệ/có chủ đích/hướng tới robot mới tạo AI/MCP turn.
- ESP-SR activation/interrupt profile, semantic exit và fallback/error response.
- `first_input_timeout`, `between_turns_timeout`, closing grace và provider deadlines.

### Đầu ra

- Realtime cascade với `mode=auto`: button hoặc wake word gọi robot dậy; admission gate chấp nhận request; VAD tự kết thúc lượt; AI/MCP xử lý; inactivity goodbye đưa robot về standby.

### Gate

- Input không hợp lệ không gọi LLM/MCP; provider/tool timeout, abort và partial stream không treo session.
- Zipformer đạt WER/CER và p95 latency gate; ChunkFormer chỉ được bật nếu re-decode
  cải thiện quality có ý nghĩa mà không phá `total_turn_deadline`.
- VieNeu đạt first-audio/RTF/pronunciation/cancel/license gate trên server thật.
- 9router bị hủy thì không còn token/tool/TTS stale; nếu contract fail, cùng flow
  chạy được bằng official API hoặc self-hosted compatible adapter.

## Phase 5 - Manager web MVP (4-6 ngày)

### Việc cần làm

- Auth + tenant/agent CRUD.
- Device pairing bằng code 6 số.
- Provider credential/catalog/health.
- Agent locale/prompt/conversation policy.
- Wake profile/resource library, artifact validation, signed manifest và device desired/reported state.
- Resource bundle composer kiểm tra flash/PSRAM/capability trước publish.
- Realtime lab event timeline.

### Đầu ra

- Prototype được chuyển thành Vue 3, API thật và responsive nhưng giữ nguyên visual,
  layout, token, typography, breakpoint và interaction đã được duyệt.

### Gate

User role không thấy secret/tool privileged; audit mutation có request id. Publish config/resource không đồng nghĩa đã apply; canary/reconcile/apply error phải hiển thị.

Realtime event ingestion đã đạt gate host: service-authenticated batch, UUID
idempotency, tenant scope derive từ device, retention 1-30 ngày, queue voice-server
không chặn và UI không còn phát event mô phỏng. First-audio hiện tính từ metadata
`stt.final -> tts.start`; abort-to-silence chính xác vẫn chờ playback ACK/hardware.

Resource control plane đã đạt gate host: signed artifact registration, immutable
artifact/wake publish, activation/interrupt profile tách riêng, stable benchmark gate,
tenant audit và explicit-device rollout. Desired state không được coi là active;
rollout chỉ chuyển terminal khi reported resource phase tương ứng. Upload object-store,
percentage rollout, pause/resume và rollback command vẫn thuộc Phase 8 hardening.

## Phase 6 - MCP + device tools (3-5 ngày)

### Việc cần làm

- Firmware `initialize/tools/list/tools/call`.
- Tool schema/range/pagination.
- Voice server device MCP proxy + model tool calling sau admission/intent gate.
- Manager MCP inspector và explicit confirmation cho user-only.
- Cancel/stale-result/audit cho tool đang chạy khi button/interrupt profile abort.

### Đầu ra

- “Đặt âm lượng 55”, “đổi biểu cảm”, “lấy device status” chạy end-to-end.

### Gate

Tool call unauthorized/timeout/malformed được từ chối an toàn.

## Phase 7 - Advanced voice barge-in/AEC + realtime provider (sau MVP)

### Việc cần làm

- Button interrupt hard guarantee; không biến button thành nút gửi câu hỏi.
- Benchmark semantic/free-form voice interrupt ngoài local interrupt profile V1.
- Server AEC timestamp profile v2 benchmark.
- ESP AFE/hardware AEC nếu cần full-duplex.
- Realtime provider adapter và event mapping.

### Gate

Chỉ bật `mode=realtime` khi ERLE/false VAD/latency đạt tiêu chí trong `docs/05-realtime-conversation.md`.

## Phase 8 - Production hardening

- MQTT+UDP gateway nếu connection scale cần.
- Signed OTA rollout/canary/rollback.
- Ed25519 release signer/key rotation, artifact provenance/SBOM/license và revocation.
- Power-loss/resource-slot recovery, resumable download và desired/reported drift alerts.
- OTel traces, SLO/alerts, backup/restore.
- Secret manager, TLS, domain (khi thực sự cần public).
- Vietnamese benchmark + locale expansion.

## Backlog ưu tiên

### P0

- Board bring-up, AP fallback, activation, WebSocket, Opus, auto conversation và assistant gate.
- Button wake, activation wake word, local interrupt profile ở standby/thinking, input admission và inactivity timeout. Speaking voice interrupt chỉ best-effort trước AEC gate.
- Silero VAD + Zipformer Vietnamese INT8 primary, ChunkFormer quality fallback,
  VieNeu-TTS v3 Turbo local và OpenAI-compatible 9router adapter (sau conformance
  gate).
- Manager pairing/agent/provider config.
- Dynamic config, ESP-SR model/assets bundle đã ký và desired/reported device state.

### P1

- MCP regular tools, fallback chains, OTA signed.
- Signed resource bundle rollout, A/B resource slot/rollback và custom wake model build job.
- Conversation dialogue acts (follow-up/confirmation/correction), absolute session ceiling và playback stale-frame race suite.
- Realtime lab, transcript redaction, metrics.

### P2

- MQTT+UDP, AEC full duplex, camera/vision, voiceprint, memory/vector, mobile manager, alternative wake runtime.

## Quy tắc giao task cho AI

Mỗi task phải ghi:

```text
Context: file/spec/contract liên quan
Goal: một behavior quan sát được
Constraints: wire/API/hardware không đổi
Artifacts: file code + test + docs/changelog
Validation: command và hardware scenario
Out of scope: những gì không được đụng tới
```

Không giao task kiểu “port toàn bộ Xiaozhi”. Hãy giao lát dọc như “Implement WebSocket hello + raw Opus fixture và test malformed payload”.

Mọi task conversation phải ghi rõ `mode=auto` là mặc định: speech/VAD tự tạo turn AI. `manual/PTT` chỉ được thêm dưới compatibility/accessibility flag.
