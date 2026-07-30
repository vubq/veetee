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

- `openai-compatible-cliproxyapi`: baseline local/dev hiện hành, endpoint
  `http://127.0.0.1:8317/v1`, model `gpt-5.6-terra`. Agent chọn binding này tường
  minh; OAuth/upstream credential do CLIProxyAPI quản lý, còn Veetee chỉ giữ client
  key của gateway trong trusted local config/encrypted provider secret.
- `openai-compatible-9router`: adapter local/dev tùy chọn đang tạm dừng; instance
  lịch sử `v0.5.40`,
  endpoint `http://127.0.0.1:20128/v1`, default đã benchmark `cx/gpt-5.6-terra`.
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

V1 privacy profile mặc định chỉ bật `vieneu-local`; TTS external không nằm trong
runtime mặc định và không được tự động chèn vào fallback chain.

### Memory

- `none`: mặc định cho thiết bị mới.
- `short-local`: Manager-backed recent messages và structured facts bounded; bắt buộc
  consent, retention/expiry, delete/edit API và scope theo authenticated device + agent.
- `vector`: chỉ bật khi có use case và consent; không đẩy transcript riêng tư vô hạn.

Voice load `short-local` một lần ở session boundary và ghi completed turn qua bounded
async queue. Fact candidate do structured planner đề xuất bằng `category/key/value/
confidence/expires_in_days`, nhưng runtime chỉ persist sau completed assistant output và
Manager vẫn áp policy/idempotency. Memory luôn là untrusted data context, không được chèn
vào system prompt hoặc dùng làm quyền gọi tool.

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
LLM mặc định: openai-compatible-cliproxyapi -> error
LLM opt-in sau publish tường minh: primary -> configured fallback(s) -> error
TTS vi-VN: vieneu-local -> cached_system_audio/text-only error
```

Registry hỗ trợ fallback chain và binding Groq độc lập, nhưng seed/local agent hiện
không tự đưa Groq vào chain. Thêm fallback là một thay đổi agent config có chủ đích,
không phải behavior mặc định khi CLIProxyAPI lỗi hoặc hết quota.

ChunkFormer là quality re-decode chứ không phải retry mù. Chỉ chạy khi còn deadline
và transcript mới có cơ hội cải thiện; không chạy lại sau `abort`.

Không retry mù các request đã bị user abort; retry chỉ khi provider error retryable và còn deadline.

Conversation timeout và provider deadline là config độc lập. Registry khai báo deadline
tối đa cho admission, ASR, planner, TTS và MCP. `llmSeconds` riêng của prose stream là
idle deadline cho first token/khoảng cách giữa hai event và được làm mới khi stream có
tiến triển; nó không phải giới hạn tổng độ dài hoặc thời lượng câu trả lời.
`maxCompletionTokens`/`max_tokens` vẫn là bound bắt buộc cho từng provider request,
không phải duration cap. Adapter phải giữ `finish_reason`; `length|max_tokens` là
incomplete, phần TTS đã phát được drain rồi runtime báo `llm_output_truncated` và không
commit partial context/memory. Generated output vượt một request dùng resumable
segment/cursor; file/source text dài stream bounded qua sentence chunker -> TTS với
offset checkpoint, không tăng token ceiling hoặc load toàn bộ source vào RAM.
`ttsFirstAudioSeconds` chỉ áp trước PCM đầu tiên của toàn speech turn;
`ttsSeconds`/`ttsStreamIdleSeconds` là synthesis idle deadline giữa các audio chunk
và cho first audio của những batch sau. Thời gian chờ hàng đợi và tổng thời lượng câu
trả lời không bị tính vào deadline này. VieNeu giữ tuần tự trọn một speech turn thay
vì xen kẽ sentence request của nhiều phiên. Request được tạo ở sentence boundary:
ONNX gom các câu ngắn tới natural cap 160 ký tự và chỉ emergency-split output thiếu
dấu câu ở 256; native giữ 72/72 vì vẫn batch-only. Playback rate dùng đúng agent
config; adapter chỉ đo realtime speed ceiling và cảnh báo starvation thay vì tự hạ
tốc độ, vì config đã publish phải phản ánh đúng điều người dùng nghe.
`TurnArbiter` vẫn hủy cả chain khi button/interrupt profile phát abort.

Source hiện publish `providerChains` tường minh theo `kind + locale`; mỗi chain chứa
1 primary và tối đa 3 fallback theo đúng thứ tự đã cấu hình. Publish bị từ chối nếu
provider sai tenant/kind, bị disable, không support locale hoặc thiếu capability bắt
buộc. Snapshot chỉ chứa metadata/reference, không chứa credential. Voice-server dùng
service token để resolve secret cho session mới và cache theo immutable config version.

LLM failover hiện đã chạy trong cùng `OperationContext`: circuit mở sau 3 lỗi liên
tiếp, thử half-open sau 30 giây, chỉ fallback với timeout/network/HTTP retryable và
chỉ trước output đầu tiên. Sau khi đã có token user-visible, lỗi được trả về turn hiện
tại thay vì nối câu trả lời từ model khác. `abort`/deadline không bao giờ fallback.
HTTP `429` vẫn được phép fallback nếu chain đã cấu hình nhưng không làm mở circuit:
rate limit là budget theo model/account có cửa sổ phục hồi riêng, không chứng minh
endpoint bị hỏng. Nhờ vậy provider được thử lại ở lượt sau ngay khi quota hồi thay vì
bị che bởi `ProviderChainUnavailable`.

Nút Test runtime của LLM tạo một completion tối thiểu bằng đúng model và secret đã
cấu hình. Chỉ `/models` không đủ chứng minh inference còn quota hoặc model route dùng
được. Phép đo này có thể tiêu tốn một lượng token rất nhỏ; `healthLatencyMs` là tổng
thời gian readiness inference, vẫn không thay thế benchmark time-to-first-token.
Latency hội thoại phải đọc từ `conversation_llm_first_token` và `tts.first_audio`.
ASR chain đã có contract/config nhưng ChunkFormer vẫn chỉ được bật sau benchmark
conditional re-decode; source không chạy hai ASR song song trên mọi utterance.

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

Source hiện đã có registry `native-function` đầu tiên dùng chung cho phiên ESP32 và
Realtime Lab:

- `context.get_time` trả ngày, giờ, thứ, IANA timezone và UTC offset; mặc định dùng
  timezone thiết bị đã report, có thể yêu cầu một IANA timezone hợp lệ khác;
- `context.get_session` chỉ trả agent version, locale, interaction mode và timezone
  không nhạy cảm; không trả tenant/device identifier, token hoặc secret;
- catalog server được merge động với device/simulated MCP, từ chối trùng tên và
  giới hạn tổng cộng 128 tool;
- arguments luôn qua Draft 2020-12 JSON Schema, result bounded theo kiểu dữ liệu và
  call dùng cùng `OperationContext`/generation với turn.

Hai tool này bổ sung dữ liệu realtime khi model cần truy vấn chính xác; current
date/time trong agent prompt vẫn được render theo từng turn. Chúng không gọi mạng,
không đi qua Manager API trong hot path và không mở remote MCP/HTTP context source.

Remote MCP discovery snapshot được resolve từ Manager tại session/config boundary bằng
service token. Ngay trước mỗi `tools/call`, Voice resolve lại cùng agent/device/config
trong turn deadline để áp dụng disable/secret clear/rotation như kill-switch; không poll
theo audio frame và không re-discover metadata remote. Endpoint URL, auth header và secret
chỉ sống trong Voice process, không đi vào catalog/prompt/log. Voice V1 hỗ trợ MCP
Streamable HTTP với JSON hoặc SSE response
cho `initialize`, paginated `tools/list` và `tools/call`; enum SSE legacy độc lập chỉ dành
cho migration tương lai, còn Manager create và Voice đều fail-closed cho tới khi có
adapter conformance riêng.

Một tool chỉ trở thành AI-callable khi đồng thời nằm trong endpoint allowlist và immutable
agent assignment. Tool `disruptive`/`destructive` hoặc cần confirmation vẫn bị ẩn khỏi
planner hiện tại. Catalog merge ưu tiên native/device tool, loại remote name collision và
giữ tối đa 128 tool. Mọi arguments/output chạy Draft 2020-12 validation, result có byte
cap, timeout 5--30 giây và dùng cùng cancellation/generation của turn.

Metadata remote không có authority trong prompt: Voice bỏ description/title/example từ
server, chỉ giữ description deterministic và một JSON Schema structural subset có giới
hạn depth/node/property/enum. `anyOf` chỉ được dùng không lồng nhau, tối đa 4 nhánh tại
mỗi vị trí và 8 nhánh trong toàn schema; `$ref`, `oneOf`/`allOf`, `pattern`/regex và keyword
không support bị reject trước validator. Remote result được đặt trong
`untrusted_remote_tool_result`, escape delimiter và chỉ được dùng như dữ liệu, không phải
instruction.

Egress chỉ cho exact host đã publish, không follow redirect/proxy môi trường và resolve DNS
trước request. HTTP client kết nối thẳng tới IP đã validate trong khi giữ original
Host/TLS SNI; DNS không được resolve lại sau lúc gắn auth/body. Peer IP và DNS sau response
vẫn được kiểm tra defense-in-depth để phát hiện rebinding. `public_only` chỉ nhận global IP;
`private_allowlist` cho RFC1918/ULA/CGNAT đúng host nhưng loopback, link-local, multicast,
unspecified, reserved và cloud metadata luôn bị chặn. Voice và Manager cùng block explicit
IPv6 transition/documentation ranges (`::/96`, mapped, NAT64, `2001::/23`, `2002::/16`,
`3fff::/20`, `5f00::/16`, `100::/64`, `fec0::/10`) để không phụ thuộc khác biệt classifier
giữa runtime. Call audit chỉ gửi args SHA-256 cùng
scope/status/duration qua async bounded delivery, không gửi raw arguments/result/secret.

Tool call record phải có `tenant_id`, `agent_id`, `device_id`, `session_id`, `turn_id`, `tool_name`, args hash, result status, duration và actor (`model`, `user`, `system`). Raw secret trong args phải redact.

Tool call thuộc cùng cancellation scope với turn. Khi button hoặc interrupt profile phát `abort`, broker phải:

1. dừng gửi thêm tool request nếu chưa dispatch;
2. cancel request đang chạy nếu adapter hỗ trợ;
3. đánh dấu result đến trễ là stale theo `turn_id`/generation;
4. không để stale result kích hoạt TTS hoặc tool tiếp theo;
5. với side effect không thể rollback, ghi audit rõ trạng thái `completed_after_abort`.

## 6. MCP security

- Allowlist exact remote endpoint host và URL scheme; private LAN cần policy explicit;
  loopback/link-local/metadata luôn bị chặn.
- JSON schema validate cả request và response.
- Timeout mặc định 5-30 giây theo tool; cancellation propagate.
- User-only tool cần explicit user action hoặc role.
- Device tool catalog cache theo firmware version; invalidate khi `initialize`/reconnect.
- Không cho model tự tạo tool name hoặc URL tùy ý.
- Voice gọi lại internal resolver ngay trước mỗi Remote MCP `tools/call`; resolver chỉ
  trả endpoint đang enabled và credential vừa giải mã. Vì vậy disable/clear/rotate đã
  commit áp dụng ở call kế tiếp mà không đặt Manager vào audio-frame path. Call đã
  authorize và dispatch trước mutation có thể hoàn tất hoặc bị cancellation, sau đó vẫn
  phải audit đúng trạng thái; UI không mô tả thao tác này là hủy tức thì request in-flight.
- Fresh resolve phải khớp cached URL/transport/network/host/tool policy; chỉ secret value
  được phép rotate. Manager unavailable, resolver timeout, endpoint/tool missing hoặc
  immutable drift đều fail-closed trước khi auth/body được gửi tới remote.
- Khi auth header value đổi, Voice xóa remote MCP session id cũ và chạy lại
  `initialize` + `notifications/initialized` bằng fresh credential trong cùng turn
  deadline; catalog schema đã cache không bị discovery lại. Nhờ vậy session bind với
  credential cũ không được tái sử dụng sau rotation.
