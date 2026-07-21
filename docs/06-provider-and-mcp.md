# Provider registry và MCP

## 1. Provider ports

Core chỉ phụ thuộc port. Adapter chịu trách nhiệm SDK, auth, retry và mapping event.

```python
class AsrProvider(Protocol):
    capabilities: AsrCapabilities
    async def start(self, request: AsrRequest) -> AsrStream: ...

class TtsProvider(Protocol):
    capabilities: TtsCapabilities
    async def synthesize(self, request: TtsRequest) -> AsyncIterator[AudioChunk]: ...

class LlmProvider(Protocol):
    capabilities: LlmCapabilities
    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatEvent]: ...

class RealtimeProvider(Protocol):
    async def connect(self, request: RealtimeRequest) -> RealtimeSession: ...

class InputAdmissionProvider(Protocol):
    async def evaluate(self, request: AdmissionRequest) -> AdmissionDecision: ...

class IntentPlanner(Protocol):
    async def plan(self, request: IntentRequest) -> ConversationPlan: ...
```

Mỗi adapter khai báo capability để planner chọn được đường chạy:

```json
{
  "id": "azure-vi",
  "kind": "tts",
  "streaming": true,
  "locales": ["vi-VN", "en-US"],
  "interruptible": true,
  "audioFormats": ["pcm_s16le", "opus"],
  "functionCalling": false,
  "health": "healthy"
}
```

## 2. Provider plan cho Vietnamese-first

MVP không cần port toàn bộ adapter Xiaozhi. Ưu tiên:

### VAD

- `silero-local`: default, chạy server; model cache cục bộ.
- `server-vad`: provider-native nếu realtime API đã có VAD.

### Input admission

- `signal-gate`: integrity/SNR/clipping/frame-loss/self-playback features;
- `speech-admission`: model xác định input có thể tạo turn, không phân nhánh theo tên nguồn âm cụ thể;
- `target-speaker`: optional/opt-in speaker relevance, không mặc định lưu voiceprint;
- `semantic-gate`: structured admission decision + dialogue act/plan theo schema của `docs/05-realtime-conversation.md`.

Admission adapter phải trả confidence/reason code/feature version. LLM/MCP chỉ chạy sau decision `accepted`; VAD hoặc ASR có text chưa đủ để coi là user request.

### ASR

- `sherpa-onnx-zipformer-vi`: primary Vietnamese streaming/chunk provider, model
  `zipformer-vi-30m-int8`.
- `chunkformer-ctc-vie`: quality fallback, model `chunkformer-ctc-large-vie`, chỉ
  re-decode sau VAD final khi confidence/ổn định/semantic quality thấp.
- `whisper-local` hoặc `faster-whisper`: optional evaluation/dev provider, không phải
  baseline V1 nếu Zipformer đã được freeze.
- `openai-compatible-asr`: adapter cho endpoint tương thích khi cần so sánh hoặc
  fallback có credential.

Không chạy Zipformer và ChunkFormer đồng thời trên mọi lượt. Registry phải trả
`confidence`, transcript stability, `is_final`, latency và model version để
`InputAdmissionGate` quyết định có re-decode. ChunkFormer được thực thi trong cùng
`turn_id`/deadline/cancellation scope; nếu runtime không có streaming capability,
registry phải khai báo `streaming=false` để planner không hứa first-response realtime.

### LLM

- `openai-compatible-9router`: baseline local/dev; instance hiện tại `v0.5.40`,
  endpoint `http://127.0.0.1:20128/v1`, model smoke-test `cx/gpt-5.4-mini`.
  Endpoint/model/secret reference cấu hình trong Manager. Chỉ enable production sau
  conformance test cho streaming, structured output, tool calling, cancellation,
  concurrency và usage metadata. Voice profile mặc định dùng
  `reasoning_effort=none`; không đưa reasoning content vào TTS.
- `openai-compatible`: adapter chung cho OpenAI Platform, DeepSeek, Qwen, GLM,
  OpenRouter và self-hosted gateway.
- `gemini`: adapter native khi cần multimodal/live.
- `ollama`: local/dev only, không nên default production.

ChatGPT Plus/Codex subscription login không được coi là OpenAI Platform API key.
Không đưa token phiên Codex, cookie hoặc `~/.codex/auth.json` vào provider secret
hoặc firmware. 9router phải có credential/app token riêng và contract được phép sử
dụng; nếu không, đổi binding sang API key chính thức hoặc self-hosted model mà không
đổi `LlmProvider`.

Source 9Router `v0.5.40` hiện đánh dấu Codex OAuth provider là deprecated/risk notice;
đây là lý do binding này chỉ là dev/LAN candidate. Nếu 9Router bind `0.0.0.0` và
`REQUIRE_API_KEY=false`, phải bật key hoặc bind loopback trước khi cho máy khác trong
LAN gọi.

### TTS

- `vieneu-local`: primary local `vi-VN`, model `vieneu-tts-v3-turbo`; capability
  `streaming` phải probe, có sentence chunk fallback nếu chỉ batch.
- `azure-neural`: có voice `vi-VN`, streaming và SSML.
- `google-cloud`: locale/voice matrix rõ.
- `openai-tts`: fallback khi cần một API chung.
- `viet-provider`: FPT/Vbee/Zalo/Viettel adapter theo credential thực tế.
- `local-vits`: offline fallback; phải benchmark chất lượng tiếng Việt trước khi bật.

V1 privacy profile mặc định chỉ bật `vieneu-local`; cloud TTS là adapter optional,
không tự động fallback nếu tenant chưa cho phép chuyển audio/text ra ngoài.

### Memory

- `none`: mặc định cho thiết bị mới.
- `short-local`: SQLite/Postgres summary với retention rõ.
- `vector`: chỉ bật khi có use case và consent; không đẩy transcript riêng tư vô hạn.

## 3. Registry và health

Provider registry cần:

- `provider_type`, `adapter_id`, `config_schema_version`;
- encrypted secret reference, không lưu raw key trong agent JSON;
- capability, locale, cost class, priority, fallback chain;
- health check chủ động và circuit breaker;
- per-tenant quota/rate limit;
- test-call trong manager web với redacted result;
- semantic version của adapter.

Fallback policy V1:

```text
VAD vi-VN: silero-local -> provider-native-vad (chỉ nếu cùng session contract)
ASR vi-VN: sherpa-onnx-zipformer-vi -> chunkformer-ctc-vie (low confidence) -> ask_again
LLM: openai-compatible-9router -> openai-compatible-backup/self-hosted -> error
TTS vi-VN: vieneu-local -> cached_system_audio/text-only error
```

ChunkFormer là quality re-decode chứ không phải retry mù. Chỉ chạy khi còn deadline
và transcript mới có cơ hội cải thiện; không chạy lại sau `abort`.

Không retry mù các request đã bị user abort; retry chỉ khi provider error retryable và còn deadline.

Conversation timeout và provider deadline là config độc lập. Registry khai báo deadline tối đa cho admission, ASR, planner, LLM, TTS và MCP; `TurnArbiter` hủy cả chain khi button/interrupt profile phát abort.

## 4. MCP trên firmware

Giữ JSON-RPC flow của Xiaozhi:

1. `initialize` nhận client capability/vision URL.
2. `tools/list` default chỉ trả regular tools.
3. `tools/list` với `withUserTools=true` trả thêm privileged tools.
4. `tools/call` validate type/range rồi schedule callback trên main task.
5. Pagination theo cursor, giới hạn payload.

Ngôn ngữ tự nhiên không được map sang tool bằng exact-string rule. Model/intent planner chọn tool từ description + JSON Schema + session context; policy engine deterministic kiểm tra permission và arguments trước khi gửi `tools/call`.

Tool không được gọi chỉ vì ASR transcript chứa một động từ giống tên tool. Điều kiện gọi tool gồm admission accepted, intent confidence, tool availability, permission, argument schema và side-effect policy.

### Tool policy V1

Mỗi tool có một safety class deterministic:

- `read_only`: đọc trạng thái, không side effect;
- `reversible`: thay đổi có thể hoàn tác hoặc giới hạn rõ;
- `disruptive`: làm gián đoạn phiên, reboot, đổi audio/network behavior;
- `destructive`: factory reset, credential, firmware/resource mutation.

Model chỉ được tự gọi `read_only` và các `reversible` đã được agent policy cho phép. `disruptive`/`destructive` cần explicit confirmation hoặc user-only role, trừ khi agent policy có một exception được audit.

Regular AI-callable:

- `self.get_device_status`;
- `self.audio_speaker.set_volume`;
- `self.screen.set_brightness`;
- `self.robot.set_expression`;
- board-specific actuator tools.

User-only:

- `self.get_system_info`;
- `self.reboot`;
- `self.upgrade_firmware`;
- `self.screen.snapshot`;
- `self.assets.reconcile_desired_version`.

`self.assets.set_download_url` chỉ được giữ trong Xiaozhi compatibility/dev mode. Native Veetee không nhận arbitrary URL từ MCP; nó chỉ nhận `artifactId`, `manifestId` hoặc desired resource version đã được Manager publish và firmware tự verify signature/allowlist/ABI.

Tool names dùng namespace `self.<domain>.<action>`. Mô tả phải nói rõ side effect, range và đơn vị. Không cho LLM tự gọi firmware upgrade, network credential, factory reset hoặc actuator nguy hiểm nếu policy chưa explicit.

Firmware hard-code implementation và safe range của capability vật lý, nhưng không hard-code các câu người dùng phải nói để gọi capability đó. Ví dụ mọi cách diễn đạt “nói nhỏ thôi”, “giảm loa xuống” hoặc “bé quá” đều do AI hiểu theo context; firmware chỉ nhận lệnh có schema như `{"volume": 35}` và validate range.

## 5. Server tool broker

Server hợp nhất bốn nguồn tool thành một catalog có policy:

```text
device-mcp      -> tool proxy qua session
remote-mcp      -> MCP endpoint đã allowlist
server-plugin   -> Python/HTTP function có timeout
native-function -> core safe functions
```

Tool call record phải có `tenant_id`, `agent_id`, `device_id`, `session_id`, `turn_id`, `tool_name`, args hash, result status, duration và actor (`model`, `user`, `system`). Raw secret trong args phải redact.

Tool call thuộc cùng cancellation scope với turn. Khi button hoặc interrupt profile phát `abort`, broker phải:

1. dừng gửi thêm tool request nếu chưa dispatch;
2. cancel request đang chạy nếu adapter hỗ trợ;
3. đánh dấu result đến trễ là stale theo `turn_id`/generation;
4. không để stale result kích hoạt TTS hoặc tool tiếp theo;
5. với side effect không thể rollback, ghi audit rõ trạng thái `completed_after_abort`.

## 6. MCP security

- Allowlist remote endpoint và URL scheme; cấm SSRF vào metadata/private network.
- JSON schema validate cả request và response.
- Timeout mặc định 5-30 giây theo tool; cancellation propagate.
- User-only tool cần explicit user action hoặc role.
- Device tool catalog cache theo firmware version; invalidate khi `initialize`/reconnect.
- Không cho model tự tạo tool name hoặc URL tùy ý.
