# Kế hoạch triển khai Veetee Server

## 1. Mục đích và trạng thái

Tài liệu này là kế hoạch thực thi chính thức cho AI xây dựng Veetee Server theo từng
mốc có cổng duyệt. Server phải tương thích với firmware tham khảo tại commit đã pin,
nhưng là sản phẩm độc lập do Veetee sở hữu, không phải bản đổi tên của server upstream.

AI chỉ được thực hiện **một mốc lớn đã được người dùng duyệt** tại một thời điểm. Khi
hoàn tất một mốc, AI phải bàn giao bằng chứng, dừng lại và chờ người dùng xem xét. Việc
test đạt không tự động cấp quyền bắt đầu mốc tiếp theo.

Mốc server tham khảo dùng để đối chiếu:
`e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5`. Mốc firmware tham khảo dùng để kiểm thử:
`d6f6b642977940b862f6f3026c3915df75d388b6`.

## 2. Yêu cầu đã chốt

### 2.1 Sản phẩm và namespace

- Tên sản phẩm, package, module, database object, metric, log event, API và tài liệu do
  dự án sở hữu phải dùng `Veetee` hoặc namespace trung tính đã được định nghĩa tại đây.
- Cấm tạo endpoint mới, path, package, environment variable, response metadata hoặc
  identifier nội bộ chứa từ `xiaozhi`, không phân biệt chữ hoa/chữ thường.
- Chỉ được nhắc tên upstream trong tài liệu nghiên cứu, attribution, test fixture được
  cô lập hoặc đường dẫn `references/`; tên đó không được xuất hiện trong public contract
  Veetee.
- Public API cho web/control plane dùng prefix `/api/v1`.
- Device API đề xuất dùng `/api/v1/devices/ota/check`,
  `/api/v1/devices/ota/artifacts/{artifact_id}` và WebSocket
  `/api/v1/devices/ws`. Path chính thức chỉ được khóa sau Mốc 0.
- Firmware tham khảo phải kết nối được bằng endpoint do OTA/config response cung cấp.
  Tương thích là giữ wire behavior cần thiết, không giữ URL hoặc branding upstream.
- CI phải có phép kiểm tra namespace cấm trên source sản phẩm và OpenAPI. Phép kiểm tra
  loại trừ rõ `references/`, tài liệu attribution và fixture nghiên cứu đã allowlist.

### 2.2 Công nghệ và cách vận hành

- Backend dùng Python, FastAPI theo modular monolith và chạy trực tiếp trên máy local.
- PostgreSQL 16 là persistence chính. Không dùng Docker hoặc Docker Compose.
- Chưa thêm Redis/message broker nếu benchmark chưa chứng minh PostgreSQL và memory
  local không đáp ứng. Quyết định thêm hạ tầng mới phải qua cổng duyệt riêng.
- Source backend nằm trong `veetee-server/server/`; contract sinh ra nằm trong
  `veetee-server/contracts/`; script vận hành local nằm trong `veetee-server/deploy/`.
- Mỗi module có ownership rõ, interface typed và test; không dynamic import bằng chuỗi
  tùy ý, không để provider tự đọc global config.
- Secret chỉ lấy từ environment hoặc secret store local, không lưu plaintext trong
  database, API response, log, fixture hay source.

### 2.3 AI và audio

- LLM phải đi qua OmniRoute local bằng API OpenAI-compatible và `stream=true`. Yêu cầu
  ban đầu chọn `groq/llama-3.3-70b-versatile`, nhưng Groq đã shutdown model này cho
  free/developer tier ngày 16/08/2026. M0.3 phải benchmark các model thay thế chính thức
  đang active là `groq/openai/gpt-oss-120b` và `groq/qwen/qwen3.6-27b`, sau đó trình
  người dùng duyệt model mặc định; chỉ giữ Llama 3.3 nếu chứng minh account có enterprise
  committed-spend entitlement và endpoint thực tế còn hoạt động.
- TTS ưu tiên có ID nội bộ `gemini/gemini-3.1-flash-tts-preview`; fallback model cấu
  hình được là `gemini/gemini-2.5-flash-preview-tts`. Adapter bỏ prefix provider và gọi
  native Gemini Interactions streaming bằng model Google tương ứng
  `gemini-3.1-flash-tts-preview` hoặc `gemini-2.5-flash-preview-tts`.
- Veetee tự quản lý pool Google AI Studio API key dành riêng cho TTS để chủ động health,
  cooldown và fallback khi một key hết quota/rate limit. Không đọc hoặc giải mã key từ
  database nội bộ của OmniRoute; OmniRoute tiếp tục là gateway cho Groq LLM.
- Chỉ dùng key thuộc các project/account mà người dùng có quyền sử dụng và tuân thủ quota,
  rate limit, điều khoản Google. Pool key nhằm tăng tính sẵn sàng, không giả mạo danh tính
  hoặc né giới hạn ở cấp account/project; nhiều key cùng project có thể chia sẻ quota.
- ASR local mặc định để benchmark: `vinai/PhoWhisper-medium`. Chỉ khóa variant sau khi
  benchmark `small`, `medium` và runtime phù hợp trên máy local với bộ audio tiếng Việt.
- VAD local: Silero VAD, ưu tiên ONNX Runtime và audio 16 kHz mono. Threshold, silence,
  pre-roll và end-of-speech là config typed, không hardcode trong handler.
- Audio từ firmware: Opus theo negotiation; pipeline nội bộ chuẩn hóa PCM 16 kHz mono
  cho VAD/ASR. Audio TTS từ Gemini dự kiến PCM 24 kHz mono, sau đó encode Opus theo
  audio contract server-device.
- Pipeline phải truyền dữ liệu liên tục:

```text
Opus ingress
  -> decode PCM
  -> Silero VAD + utterance boundary
  -> PhoWhisper ASR local
  -> intent/tool/memory context
  -> Groq LLM token stream qua OmniRoute (model khóa tại M0.3)
  -> bộ tách đoạn thích ứng
  -> Gemini native TTS stream qua Veetee key pool
  -> PCM -> Opus packet + pacing
  -> firmware playback
```

- Không chờ toàn bộ câu trả lời LLM mới gọi TTS. Bộ tách đoạn phải ưu tiên câu hoàn
  chỉnh, nhưng cho phép phát đoạn đầu ngắn ở dấu phẩy hoặc ngưỡng thời gian/ký tự để
  giảm time-to-first-audio mà không làm giọng nói vụn.
- Mỗi lượt nói có `turn_id` và `generation_id`. Abort/barge-in phải hủy xuyên suốt ASR,
  LLM, tool và TTS; mọi token/audio trả về muộn từ generation cũ phải bị loại.
- Queue đều bị giới hạn theo byte hoặc thời lượng. Không dùng queue không giới hạn trên
  hot path.

### 2.4 Không hardcode hành vi AI

“Không hardcode” có nghĩa là không dùng chuỗi `if/else`, keyword list hoặc câu trả lời
cố định để giả lập hiểu ngôn ngữ, intent, personality hay quyết định tool. Hành vi phải
được điều khiển bởi agent config, prompt, tool schema, policy và model adapter.

Các phần sau **phải deterministic**, không được giao cho LLM:

- Parse/validate protocol, auth, RBAC và tenant ownership.
- Session state machine, timeout, retry, rate limit, backpressure và cancellation.
- Tool allowlist, confirmation đối với hành động nhạy cảm và giới hạn dữ liệu.
- OTA compatibility, signature, rollout, anti-rollback và artifact integrity.
- Data migration, audit, redaction và privacy retention.

Mọi default nghiệp vụ phải nằm trong config có schema, version và nguồn gốc; không rải
magic value trong handler. AI được phép chọn tool hoặc tạo nội dung trong policy đã
định, nhưng không được vượt qua policy bằng prompt.

## 3. Kiến trúc mục tiêu

Ban đầu triển khai một process/deployable FastAPI để giảm độ phức tạp, nhưng chia module
theo contract. Không tách microservice trước khi profiling cho thấy nhu cầu.

```text
FastAPI application
|-- device_gateway      # OTA discovery, auth, WebSocket, protocol, session
|-- conversation        # turn state, dialogue, prompt, orchestration, cancellation
|-- audio               # Opus, resample, VAD, utterance, pacing, jitter/backpressure
|-- providers           # typed ASR/LLM/TTS/embedding adapters
|-- agents              # agent config, prompt, model/voice/tool selection
|-- intent_tools        # intent, function calling, policy, MCP/tool execution
|-- memory              # working, episodic, profile memory và retrieval
|-- devices             # activation, binding, ownership, status, command
|-- ota                 # releases, artifact, eligibility, rollout, report
|-- control_plane       # REST /api/v1 cho web/admin
|-- identity            # user auth, session/token, RBAC, tenant
|-- audit_observability # audit event, metric, trace/correlation
`-- persistence         # PostgreSQL repositories và migration
```

Nguyên tắc dependency:

- Domain và application service không import FastAPI, SQLAlchemy hoặc SDK provider.
- Adapter phụ thuộc vào interface domain; domain không phụ thuộc adapter.
- WebSocket handler chỉ validate/translate wire event, không chứa logic AI.
- Provider trả typed event, không trả tuple/string đa nghĩa.
- Mọi operation dài nhận cancellation scope và deadline.
- Database không nằm trong vòng lặp từng audio frame.

## 4. Contract provider tối thiểu

Các interface dưới đây là semantic contract; tên/type cụ thể được khóa trong Mốc 1.

| Provider | Input | Stream event/output | Yêu cầu |
| --- | --- | --- | --- |
| VAD | PCM frame + session state | probability/start/end | Reset được, thread-safe được khai báo |
| ASR | PCM utterance hoặc stream | partial/final transcript | Locale, confidence tùy chọn, cancel |
| LLM | messages + tools + generation config | text delta, tool delta, usage, done | SSE, first-token timeout, cancel |
| TTS | text segment + voice/style | PCM chunk, metadata, done | first-audio timeout, cancel, backpressure |
| Intent | transcript + context + tool schema | route/tool/no-tool | Không keyword hardcode |
| Memory | query/save/forget | memory item có provenance | Tenant scope, consent, retention |
| Tool | typed arguments + actor/session | typed result/error | Auth, timeout, audit, output limit |

Provider registry là allowlist code-level. Database/config chỉ chọn provider/model đã
đăng ký; không được cung cấp module path để import code tùy ý.

### 4.1 Gemini TTS key pool

- Key được inject từ secret file/environment theo danh sách reference; không lưu
  plaintext trong PostgreSQL, source, log hoặc API response.
- Mỗi key có state runtime: `healthy`, `cooldown`, `disabled`, số request đang chạy,
  lần thành công/thất bại gần nhất và thời điểm có thể thử lại.
- Chọn key bằng least-in-flight có round-robin công bằng giữa các key đang healthy.
- `401/403` đánh dấu key disabled và phát cảnh báo cấu hình; không retry liên tục.
- `429` đưa key vào cooldown theo `Retry-After` nếu có, nếu không dùng exponential
  backoff có jitter và giới hạn tối đa.
- `5xx`, connect timeout và lỗi mạng làm tăng failure score; circuit breaker mở tạm
  thời. Một lần lỗi không được vô hiệu hóa key vĩnh viễn.
- Chỉ failover trong cùng segment trước khi PCM chunk đầu tiên được gửi. Khi đã phát
  audio, không phát lại segment từ đầu bằng key khác; báo lỗi segment hoặc chuyển key ở
  segment kế tiếp theo policy để tránh người dùng nghe lặp.
- Cancellation từ barge-in phải đóng native Gemini stream và giảm in-flight counter dù
  request kết thúc bằng exception.
- Không expose key index cụ thể ra client; metric/log chỉ dùng fingerprint một chiều hoặc
  alias không nhạy cảm.
- Pool có global concurrency/rate limiter để tránh tất cả key cùng bị quota exhaustion
  do burst nội bộ.

## 5. Base prompt và context assembly

Base prompt là contract có version, không phải một chuỗi dài rải trong source. Prompt
được ghép theo thứ tự ổn định:

1. `platform_policy`: danh tính Veetee, nguyên tắc an toàn, quyền tool và chống prompt
   injection. Chỉ code release mới sửa được.
2. `agent_role`: vai trò/personality do chủ agent cấu hình qua control plane.
3. `conversation_policy`: ưu tiên tiếng Việt, hội thoại tự nhiên, câu trả lời phù hợp
   giọng nói, không đọc Markdown/ký hiệu khó nghe, cách hỏi lại khi thiếu dữ kiện.
4. `runtime_context`: thời gian, locale, device capability và trạng thái phiên đã được
   server xác minh.
5. `memory_context`: các memory đã retrieve, kèm provenance và mức tin cậy; được đánh
   dấu là dữ liệu, không phải instruction.
6. `tool_contract`: tool schema và policy do server cấp; output tool cũng là dữ liệu
   không tin cậy.
7. `dialogue_history`: lịch sử đã trim/summarize theo token budget.
8. User turn hiện tại.

Yêu cầu với prompt:

- Có `prompt_version`, checksum và test snapshot.
- Tách invariant prompt khỏi agent-editable prompt.
- Không inject memory/tool result thành system instruction.
- Không giả mạo system message bằng role `user`.
- Có token budget riêng cho policy, memory, tools và history.
- Có golden conversation cho tiếng Việt, tool call, prompt injection, memory conflict,
  câu hỏi không chắc chắn và hành động cần xác nhận.
- Mọi thay đổi base prompt sau khi hệ thống hoạt động phải có evaluation so sánh và
  rollback được.

## 6. Latency SLO và đo lường

Mốc 0 sẽ benchmark để khóa SLO. Mục tiêu ban đầu cho mạng local ổn định:

| Chỉ số | Mục tiêu ban đầu |
| --- | --- |
| VAD end-of-speech | p95 <= 450 ms sau tiếng nói cuối |
| ASR final sau utterance | p95 <= 1.5 s |
| LLM time-to-first-token | p95 <= 800 ms |
| TTS time-to-first-audio tính từ đoạn đầu | p95 <= 1.2 s |
| End-of-speech -> packet audio đầu tiên | p95 <= 3.0 s |
| Barge-in -> ngừng audio cũ | p95 <= 250 ms |
| Audio queue ở trạng thái ổn định | không tăng theo thời gian |

Đây là mục tiêu kiểm chứng, chưa phải lời khẳng định phần cứng/provider hiện tại chắc
chắn đạt. Mỗi metric phải ghi theo `session_id`, `turn_id`, provider và model nhưng
không log raw audio/transcript mặc định.

## 7. Quy tắc thực hiện task cho AI

Mỗi task dưới đây phải đi theo vòng lặp:

1. Đọc docs và task dependency.
2. Ghi acceptance test trước hoặc cùng implementation.
3. Thay đổi nhỏ nhất đáp ứng task.
4. Chạy unit/contract/integration test liên quan.
5. Cập nhật docs/OpenAPI/migration nếu contract đổi.
6. Báo file đổi, test, metric, rủi ro và phần chưa xác minh.

AI không được:

- Bắt đầu task thuộc mốc sau khi chưa có câu duyệt rõ ràng của người dùng.
- Gộp task ngoài scope chỉ vì “tiện làm luôn”.
- Sửa source hoặc Git history trong `references/`.
- Copy module upstream mà không ghi nguồn, license và sai khác.
- Coi unit test/fake device là thay thế hardware E2E ở mốc yêu cầu thiết bị.
- Thêm compatibility path có branding upstream.

## 8. Mốc 0 - Khóa contract và chứng minh công nghệ

**Mục tiêu:** loại bỏ rủi ro lớn trước khi tạo kiến trúc/source đáng kể.

### M0.1 Ma trận firmware compatibility

- Hoàn tất [ma trận tương thích firmware-server](firmware-compatibility-matrix.md): request/
  response OTA, handshake, hello, control JSON, binary frame, audio và state.
- Ghi chính xác request/response OTA mà firmware baseline gửi/đọc.
- Ghi handshake header, hello, control JSON, binary frame cho protocol version được dùng.
- Ghi audio format uplink/downlink, frame duration, sample rate và byte order.
- Ghi state machine `hello -> listen -> audio -> stt/tts -> abort/stop`.
- Tạo fixture/golden vector từ hành vi quan sát, đã loại secret và branding public.
- Xác minh firmware chấp nhận URL Veetee tùy ý do OTA response trả về.

**Nghiệm thu:** tài liệu contract có field required/optional, enum, limit, timeout và
golden vector; không còn giả định URL upstream.

### M0.2 Khóa namespace Veetee

- Hoàn tất [chính sách namespace](namespace-policy.md) và bảng endpoint đề xuất.
- Chốt endpoint device/API/WS và response metadata sau khi người dùng duyệt Cổng 0.
- Chốt tên environment variable prefix `VEETEE_`, application name và database schema.
- Viết namespace policy và script CI phát hiện identifier cấm tại
  `veetee-server/tools/scan_namespace.py`.
- Chạy scan trên OpenAPI/source sản phẩm mẫu; references được exclude rõ ràng.

**Nghiệm thu:** người dùng duyệt bảng endpoint; scan không có false positive ngoài
allowlist có lý do.

### M0.3 Spike và khóa Groq LLM qua OmniRoute

- Hoàn tất [benchmark Groq LLM qua OmniRoute](omniroute-llm-benchmark.md); khuyến nghị
  GPT-OSS 120B production làm default và Qwen 3.6 27B preview làm low-latency candidate,
  chờ người dùng duyệt tại Cổng 0.
- Gọi `groq/openai/gpt-oss-120b` và `groq/qwen/qwen3.6-27b` qua
  `/v1/chat/completions`, `stream=true`; không mặc định model chỉ vì còn xuất hiện trong
  catalog cache của OmniRoute.
- Đo connect, first token, total latency, cancellation và rate-limit response.
- Xác minh streamed tool-call delta và usage metadata.
- Đánh giá chất lượng hội thoại tiếng Việt, tuân thủ base prompt, function calling và
  độ ổn định giữa nhiều lượt bằng cùng evaluation set.
- Ghi rõ deprecation/stability tier, context/output limit và account entitlement của
  từng model; trình người dùng duyệt model mặc định ở Cổng 0.
- Không log API key hoặc nội dung nhạy cảm.

**Nghiệm thu:** lưu báo cáo benchmark và transcript test vô hại; adapter protocol đủ dữ
liệu để triển khai, model mặc định được người dùng duyệt, không cần code production.

### M0.4 Spike Gemini native TTS streaming và key pool

- Hoàn tất [benchmark Gemini native TTS](gemini-tts-benchmark.md): 4/4 key tạo audio
  streaming với model 3.1; model 2.5 fallback buffered cũng trả audio thành công.
- Gọi native Gemini Interactions API với `stream=true` bằng key Google AI Studio do
  Veetee quản lý; không đi qua OmniRoute cho TTS.
- Xác minh mapping ID nội bộ `gemini/gemini-3.1-flash-tts-preview` sang model native
  `gemini-3.1-flash-tts-preview` và tương tự với fallback 2.5.
- Contract nội bộ nhận text/voice/style và stream PCM chunk 24 kHz mono.
- Tạo key-pool prototype không ghi key ra log/database; kiểm tra least-in-flight,
  round-robin, cooldown, circuit breaker và cleanup in-flight.
- Đo first audio, chunk cadence, cancellation, malformed event, quota/rate limit và
  fallback giữa key/model.
- Dùng fake Gemini server để test deterministic `401`, `403`, `429`, `5xx`, disconnect,
  `Retry-After` và lỗi xảy ra trước/sau audio chunk đầu tiên.
- Xác minh không buffer toàn bộ WAV trước khi trả chunk đầu và không phát lặp segment khi
  failover sau khi audio đã bắt đầu.

**Nghiệm thu:** model 3.1 và fallback 2.5 tạo audio tiếng Việt nghe được qua native API;
stream/cancel có bằng chứng; ít nhất hai key hợp lệ được phân phối/failover đúng bằng test
không chứa secret. Nếu nhiều key cùng Google project chia sẻ quota, báo rõ kết quả thay
vì coi số key là số quota độc lập.

### M0.5 Benchmark PhoWhisper và Silero

- Hoàn tất [benchmark ASR/VAD](asr-vad-benchmark.md): đề xuất PhoWhisper small + Silero
  ONNX baseline; medium giữ làm quality candidate do cold load/RTF cao hơn.
- Chuẩn bị tập audio tiếng Việt có giọng Bắc/Trung/Nam, câu ngắn/dài, nhiễu và im lặng;
  không commit dữ liệu không có quyền sử dụng.
- Benchmark ít nhất PhoWhisper `small` và `medium`: WER/CER tương đối, cold/warm latency,
  realtime factor, RAM/VRAM và concurrency 1/2.
- Benchmark Silero ONNX với frame 16 kHz, threshold, pre-roll và silence duration.
- Chọn variant bằng số liệu trên máy hiện tại, không chỉ dựa tên model.

**Nghiệm thu:** decision record khóa model/runtime/config ban đầu và nêu cách đổi qua
config nếu benchmark tương lai tốt hơn.

### M0.6 Thiết kế contract, threat model và parity backlog

- Hoàn tất [contract, threat model và parity backlog](m0-contract-and-threat-model.md).
- Khóa module boundary, provider event, error taxonomy và cancellation semantics.
- Lập threat model cho device, OmniRoute, tool, control plane, OTA và persistence.
- Chuyển ma trận parity ở mục 17 thành issue/task có dependency.
- Chốt migration tool, ORM/data access và test stack sau khi trình bằng chứng.

**Bàn giao Mốc 0:** contract docs, golden vector, benchmark, ADR, threat model, backlog
và danh sách quyết định còn mở.

**CỔNG DUYỆT 0: DỪNG. Không tạo skeleton production trước khi người dùng duyệt.**

## 9. Mốc 1 - Nền tảng backend và device gateway

**Mục tiêu:** server skeleton nhỏ, typed và nhận được session firmware bằng fake provider.

### M1.1 Project foundation

- Tạo package Python, dependency lock, config schema và `.env.example` không có secret.
- Thiết lập formatter/linter/type-check/test và lệnh local thống nhất.
- Tạo FastAPI lifespan, health readiness/liveness và graceful shutdown.
- Tạo correlation ID, structured log và redaction test.

### M1.2 Domain/session state machine

- Định nghĩa `DeviceSession`, `ConversationTurn`, `Generation` và state transition.
- Cấm transition sai bằng typed error; test duplicate/out-of-order event.
- Tạo cancellation scope và cleanup deadline cho từng session/turn.

### M1.3 Device WebSocket Veetee

- Implement `/api/v1/devices/ws` theo contract M0.
- Validate auth/header/hello/audio negotiation, JSON depth/size và binary frame size.
- Implement ping/pong, idle timeout, disconnect reason và reconnect sạch.
- Không echo malformed payload; trả error envelope an toàn hoặc đóng theo policy.

### M1.4 OTA/config responder tương thích tối thiểu

- Implement endpoint Veetee đã khóa ở M0 để firmware baseline discover server time,
  activation state tối thiểu và WebSocket URL/token.
- Không triển khai sớm release catalog/rollout đầy đủ; nếu chưa có firmware update, trả
  trạng thái không có bản mới theo đúng schema.
- Test request firmware baseline, URL/namespace Veetee, auth token và malformed input.
- Responder này là nền móng được mở rộng ở M5, không phải workaround dùng endpoint
  upstream hoặc service tạm ngoài source Veetee.

### M1.5 Audio primitives

- Opus decode/encode, PCM format, resample và frame validation.
- Bounded ingress/egress queue, slow-client policy và packet pacing.
- Golden vector và malformed/truncated/oversized test.

### M1.6 Fake AI pipeline

- Fake VAD/ASR/LLM/TTS deterministic để test không cần model/key.
- Luồng hello/listen/audio/STT/TTS/audio/stop hoàn chỉnh.
- Abort giữa stream phải loại toàn bộ stale token/audio.

### M1.7 Device simulator và contract test

- Tạo simulator Veetee ngoài references đọc golden vector.
- Test nhiều session song song, isolation, reconnect, timeout, slow client và shutdown.
- Scan namespace cấm trên source/OpenAPI.

**Bàn giao Mốc 1:** server fake-AI chạy local, OpenAPI/WS contract, test report và demo
simulator.

**CỔNG DUYỆT 1: DỪNG. Chờ người dùng xem demo protocol trước khi nối model thật.**

## 10. Mốc 2 - Realtime speech end-to-end với model thật

**Mục tiêu:** hội thoại tiếng Việt liền mạch trên digital-human và thiết bị thật.

### M2.1 Silero VAD adapter

- Load model một lần, state riêng mỗi stream và concurrency limiter.
- Implement pre-roll, speech-start/end, max utterance và reset/cancel.
- Test silence, noise, short burst, continuous speech và hai session.

### M2.2 PhoWhisper ASR adapter

- Load model theo quyết định M0, warmup/readiness và admission control.
- Nhận PCM utterance, normalize transcript có kiểm soát và trả metadata typed.
- Test accent fixture, empty audio, timeout, cancel và resource exhaustion.

### M2.3 OmniRoute Groq adapter

- Parse SSE text/tool delta và `[DONE]`; có connect/first-token/total timeout.
- Propagate cancellation bằng đóng HTTP stream.
- Normalize provider error, usage/rate limit và circuit breaker.

### M2.4 Bộ ghép token và tách đoạn TTS

- Buffer token theo punctuation, quote/bracket balance, min/max chars và max wait.
- Ưu tiên đoạn đầu ngắn để giảm latency, đoạn sau đủ tự nhiên.
- Không đọc Markdown, URL hoặc ký hiệu thô; normalization là config/test được.
- Test tiếng Việt có viết tắt, số, ngày, decimal, URL, emoji và câu không dấu chấm.

### M2.5 Native Gemini TTS adapter và key pool

- Stream PCM chunk trực tiếp từ Gemini Interactions qua contract đã chứng minh ở M0.
- Key pool thực hiện least-in-flight, cooldown, circuit breaker và failover theo mục 4.1.
- Voice/style/model là agent config; fallback chỉ theo policy rõ ràng.
- Resample/encode Opus nối tiếp, không tạo file tạm trên hot path.
- Cancel request upstream khi abort; loại chunk generation cũ.

### M2.6 Full-duplex, barge-in và flow control

- Trong khi phát TTS, nhận audio uplink theo capability AEC/listen mode.
- Khi VAD xác nhận người dùng chen lời, gửi stop và hủy pipeline cũ.
- Điều chỉnh jitter buffer/pacing dựa metric, không bằng sleep rải rác.
- Test race: abort đúng lúc tool/LLM/TTS kết thúc, reconnect khi audio đang phát.

### M2.7 E2E digital-human

- Chạy server và digital-human local theo quy trình server-first.
- Test hello, auto/manual/realtime listen, audio hai chiều, abort, reconnect và timeout.
- Ghi latency waterfall, không chỉ đánh giá “nghe nhanh”.
- Ma trận QA E2E digital-human phải đạt 100% scenario đủ điều kiện, không có `fail` hoặc
  flaky case chưa xử lý, theo quy trình server-first.

### M2.8 E2E thiết bị thật

- Dùng firmware baseline/board/locale/wake word đã chốt; không erase NVS.
- OTA response trỏ tới endpoint Veetee, không chứa path/branding upstream.
- Test wake -> nói tiếng Việt -> phản hồi -> chen lời -> nói tiếp.
- Smoke-test màn hình, microphone, loa, nút và Wi-Fi reconnect.
- Ma trận QA E2E thiết bị thật phải đạt 100%; scenario `blocked` chưa được coi là hoàn tất
  Mốc 2 và phải có bằng chứng sau khi gỡ blocker.

**Bàn giao Mốc 2:** video/demo nghe được, metric p50/p95, log đã redact, test report và
danh sách SLO đạt/chưa đạt.

**CỔNG DUYỆT 2: DỪNG. Chờ người dùng trực tiếp nghe và duyệt chất lượng/độ trễ.**

## 11. Mốc 3 - Bộ não AI: prompt, intent, tool và memory

**Mục tiêu:** AI có hành vi cấu hình được, nhớ có kiểm soát và dùng tool an toàn.

### M3.1 Prompt registry

- Implement prompt components ở mục 5 với version/checksum.
- Tạo base prompt tiếng Việt tối ưu cho lời nói ngắn, tự nhiên và không đọc Markdown.
- Snapshot/evaluation cho personality, uncertainty, injection và tool policy.

### M3.2 Dialogue và context budget

- Lưu working history theo session; trim/summarize theo token budget.
- Không làm mất tool result hoặc instruction hierarchy khi compact.
- Tách raw transcript, normalized text và text gửi model.

### M3.3 Intent routing không hardcode

- Mặc định dùng native function calling của LLM khi có tool.
- Cho phép strategy `direct_chat`, `function_call`, `intent_model` qua config.
- Không dùng keyword để giả intent; deterministic fast-path chỉ dành protocol command.

### M3.4 Unified tool registry

- Namespace tool rõ, schema JSON, collision fail-fast và version.
- Policy theo user/agent/device; confirmation cho hành động vật lý/nhạy cảm.
- Timeout, max output, sanitize, cancellation và audit.

### M3.5 Device MCP

- Implement initialize, tools/list pagination, tools/call và JSON-RPC error.
- Bind request/result vào session/device/generation; reject response lạ hoặc trễ.
- Test tool discovery/call bằng firmware hoặc simulator có MCP.

### M3.6 Server MCP và local tools

- Tích hợp MCP endpoint server Veetee qua adapter có allowlist.
- Tạo tool time/weather/search/music/knowledge khi có provider/config thật.
- Không sao chép plugin catalog upstream theo số lượng; mỗi capability có test/policy.

### M3.7 Memory model

- Working memory: context trong phiên.
- Episodic memory: sự kiện hội thoại có provenance và thời gian.
- Profile memory: sở thích/fact ổn định, chỉ ghi khi đủ confidence hoặc người dùng yêu cầu.
- Hỗ trợ retrieve, upsert, conflict, forget và xóa toàn bộ theo user/agent.
- Memory write do model đề xuất nhưng policy/service quyết định; không ghi mọi câu nói.

### M3.8 Memory retrieval và prompt safety

- Retrieve theo semantic + recency + confidence + tenant scope.
- Đánh dấu memory là untrusted data; chống stored prompt injection.
- Evaluation memory đúng/sai người, conflict, stale fact và deletion.

**Bàn giao Mốc 3:** prompt/eval report, tool/MCP demo, memory CRUD/retrieval demo và audit.

**CỔNG DUYỆT 3: DỪNG. Chờ người dùng duyệt personality, base prompt và cách nhớ.**

## 12. Mốc 4 - Control plane, PostgreSQL và API web

**Mục tiêu:** thay mock console bằng dữ liệu/API Veetee có ownership và migration.

### M4.1 Persistence foundation

- Migration cho user, agent, device, provider config, conversation/memory và audit.
- Repository transaction rõ; rollback/forward migration test.
- Không lưu raw provider key; chỉ lưu secret reference/redacted metadata.

### M4.2 Identity và RBAC

- Bootstrap admin local an toàn, login/logout/refresh/session revoke.
- Role tối thiểu owner/admin; mọi query tenant-aware.
- Rate limit, password hashing, CSRF/CORS policy và audit.

### M4.3 Agent API

- CRUD agent, role prompt, language, voice/style, ASR/LLM/TTS, intent, memory và tools.
- Optimistic concurrency/version; config snapshot và rollback.
- Runtime lấy immutable config snapshot theo generation.

### M4.4 Provider/model API

- Catalog chỉ gồm adapter/model code hỗ trợ thực tế.
- Health/validate không trả secret và không tạo chi phí ngoài ý muốn.
- Model/voice/fallback/concurrency/timeout là typed config.

### M4.5 Device API

- List/detail/alias/bind/unbind/last-seen/agent assignment/auto-update.
- Ownership check ở service layer, idempotency và audit.
- Online state lấy từ session registry, không chỉ cờ database.

### M4.6 Conversation, history và memory API

- Session/transcript metadata, title/summary và retention policy.
- Audio lưu là opt-in; mặc định không lưu raw audio.
- List/delete/forget/export memory theo quyền.

### M4.7 Web console integration

- Thay mock agent list/config/device/history bằng `/api/v1`.
- Hiển thị loading/error/empty, optimistic conflict và permission state.
- Không đặt provider key trong browser/localStorage.

### M4.8 Runtime config consistency

- Cache last-known-good theo agent version.
- Config đổi chỉ áp dụng cho turn/session theo policy đã chốt, không nửa cũ nửa mới.
- Database/control-plane outage không làm rơi session đang hoạt động.

**Bàn giao Mốc 4:** migration, OpenAPI, API/security test, console demo và backup/restore
thử nghiệm.

**CỔNG DUYỆT 4: DỪNG. Chờ người dùng duyệt toàn bộ workflow quản trị.**

## 13. Mốc 5 - Activation, binding và OTA Veetee

**Mục tiêu:** vòng đời thiết bị/firmware đầy đủ, an toàn và không mang branding upstream.

### M5.1 OTA/config discovery

- Mở rộng responder tương thích tối thiểu từ M1 thành lifecycle production đầy đủ.
- Hoàn thiện activation state, WebSocket credential rotation và firmware eligibility.
- Không có URL/path/metadata chứa namespace cấm.

### M5.2 Device credential

- Credential riêng từng device, rotation/revocation và binding với Device-Id/Client-Id.
- Token có issuer/audience/expiry; không dùng shared fleet secret làm thiết kế cuối.
- Recovery không được yêu cầu erase NVS.

### M5.3 Activation và binding

- Activation code/challenge có TTL, attempt limit và idempotency.
- User bind/unbind/rebind có ownership, confirmation và audit.
- Test device chưa bind, code hết hạn, replay và race bind.

### M5.4 Firmware release model

- Immutable artifact/release, board/chip/partition/version compatibility và checksum.
- Chữ ký, provenance, channel, cohort, rollout percentage và kill switch.
- Anti-rollback và rollback target rõ; không ghi đè release cũ.

### M5.5 Artifact upload/download

- Admin upload có MIME/size/hash/signature validation và atomic storage.
- Download streaming, path traversal protection, token/expiry/range request.
- Không load toàn bộ firmware vào RAM.

### M5.6 Rollout/reporting

- Device report check/download/install/boot success/failure.
- Dashboard theo board/version/cohort; health gate tự dừng rollout theo policy.
- Test interrupted download, corrupt artifact, downgrade và power-loss scenario phù hợp.

### M5.7 Hardware validation

- Trong giai đoạn server-first, dùng nguyên firmware tham khảo đã pin làm client hardware;
  không sửa hoặc tạo firmware khi chưa có yêu cầu riêng. Chạy discovery/OTA theo wire flow
  firmware này hỗ trợ, bảo toàn NVS/Wi-Fi và xác minh partition switch, reboot, reconnect,
  server endpoint cùng peripheral smoke test.
- Activation proof Ed25519, bound rediscovery credential, detached signature verification
  và OTA progression report phía thiết bị là acceptance của firmware Veetee sau này. M5
  hiện tại phải khóa contract và test đầy đủ phía server nhưng không giả lập các capability
  đó trên firmware tham khảo hoặc hạ security policy production để lấy hardware pass.
- Evidence hardware compatibility không được gọi là production-safe; báo cáo phải tách
  rõ phần đã chạy trên board tham khảo và phần chờ firmware Veetee.

**Bàn giao Mốc 5:** activation/bind demo và audit/report phía server, OTA rollout local
theo firmware tham khảo không sửa source, cùng hardware compatibility evidence. Secure
device-side lifecycle được giữ trong contract/backlog firmware Veetee.

**CỔNG DUYỆT 5: DỪNG. Chờ người dùng duyệt trước khi mở rộng tính năng parity.**

## 14. Mốc 6 - Hoàn thiện feature parity upstream

**Mục tiêu:** đạt capability parity với các chức năng hữu ích của server tham khảo mà
không copy technical debt hoặc buộc Veetee dùng cùng implementation.

### M6.1 Model/provider management đầy đủ

- Enable/disable/default, voice catalog/preview và provider health.
- Adapter bổ sung chỉ khi có use case; parity là khả năng mở rộng, không phải đủ mọi
  vendor upstream.

### M6.2 Chat history và agent lifecycle

- Title/summary/export, agent template/tag/snapshot/restore và retention.
- Privacy consent, deletion và tenant isolation.

### M6.3 Voice features

- Voice profile/preview; voice clone chỉ khi legal/privacy requirements được duyệt.
- Speaker recognition/voiceprint có consent, biometric retention và deletion riêng.

### M6.4 Knowledge/RAG

- Dataset/document/chunk ingest, status, retrieval test và citation.
- File validation, tenant isolation, prompt-injection defense và deletion.

### M6.5 Correction/context providers

- Replacement/correction rules có version và preview.
- Context provider typed, timeout/caching và provenance.

### M6.6 Tool/plugin ecosystem

- External MCP endpoint, server MCP, Home Assistant, weather, search, music/news.
- Mỗi integration có permission, secret scope, rate limit, timeout và audit.

### M6.7 Device tools và communication

- Console list/call device MCP tool có confirmation.
- Address book/device calling nếu firmware capability hỗ trợ và người dùng duyệt UX.

### M6.8 Administration

- User/status/role management, typed system settings, audit search và usage quota.
- Server operation qua supervisor an toàn; không spawn/restart process từ device message.

### M6.9 Transport mở rộng

- MQTT control/UDP audio chỉ triển khai nếu cần parity vận hành thực tế.
- Nếu triển khai: authenticated encryption, replay window, sequence/gap/reconnect và
  gateway trust boundary; không copy AES-CTR thiếu integrity.

### M6.10 Client coverage

- Web responsive là client quản trị chính và thay nhu cầu mobile console riêng.
- Digital-human/simulator tiếp tục là test harness; không đưa branding upstream vào UI.

**Bàn giao Mốc 6:** ma trận parity có bằng chứng cho từng capability, demo feature mới,
security/privacy review và danh sách mục không áp dụng có phê duyệt của người dùng.

**CỔNG DUYỆT 6: DỪNG. Người dùng xác nhận feature parity trước hardening cuối.**

## 15. Mốc 7 - Hardening, tải và release candidate

**Mục tiêu:** chứng minh hệ thống ổn định, quan sát được và phục hồi được trên máy local.

### M7.1 Security verification

- Threat model closure, dependency/license/CVE scan và secret scan.
- Fuzz protocol/API, auth bypass, tenant escape, prompt/tool injection và SSRF.
- OTA signature/downgrade/replay verification.

### M7.2 Reliability

- Provider outage, malformed stream, OmniRoute restart, Gemini key-pool exhaustion,
  PostgreSQL outage và disk full.
- Circuit breaker/fallback không retry storm hoặc nhân đôi chi phí.
- Graceful shutdown/restart không gửi stale audio hoặc mất audit quan trọng.

### M7.3 Load/soak

- Nhiều WebSocket, simultaneous speech, slow client và provider saturation.
- Soak đủ lâu để phát hiện queue growth, task/thread leak, GPU/RAM leak.
- Ghi capacity envelope thực tế của máy; không hứa scale vượt benchmark.

### M7.4 Observability

- Dashboard/log/metric cho session, disconnect, queue, latency, error, tool và OTA.
- Correlation xuyên device -> turn -> provider -> TTS; redaction test.
- Runbook cho health degraded, provider quota, model load fail và stuck session.

### M7.5 Backup, restore và migration rehearsal

- Backup PostgreSQL/artifact/config, restore sang local sạch và verify checksum.
- Rehearse upgrade/rollback migration không mất dữ liệu.

### M7.6 Release acceptance

- E2E matrix trên simulator, digital-human và thiết bị thật.
- Scan namespace cấm toàn source sản phẩm, OpenAPI, UI và response runtime.
- README run/stop/test/troubleshoot và known limitations.

**Bàn giao Mốc 7:** release candidate, benchmark/security report, runbook, backup/restore
evidence và checklist ký duyệt.

**CỔNG DUYỆT 7: DỪNG. Không deploy/public expose nếu chưa có yêu cầu riêng.**

## 16. Definition of Done cho mọi mốc

Một mốc chỉ được báo hoàn tất khi:

- Tất cả task bắt buộc của mốc có acceptance evidence.
- Test unit, contract và integration theo scope đều pass. Ma trận QA E2E theo scope phải
  pass 100%, không có fail/flaky; scenario blocked phải có lý do/evidence và ngăn bàn giao
  mốc cho tới khi được gỡ và chạy pass.
- Không có thay đổi tracked/Git history trong hai repo `references/`.
- Không có secret hoặc namespace cấm trong source/public contract Veetee.
- Docs, OpenAPI, migration và sample config phản ánh đúng implementation.
- Timeout, cancellation, cleanup, backpressure, auth và malformed input đã được test.
- Metric trước/sau được báo cáo cho task ảnh hưởng latency/resource.
- Worktree/diff đã được rà; commit/push chỉ thực hiện khi người dùng yêu cầu hoặc quy
  trình mốc đã cấp quyền rõ ràng.
- Báo cáo kết thúc có mục “Cần người dùng duyệt” và AI thực sự dừng.

## 17. Ma trận feature parity mục tiêu

Parity ở đây là parity về capability người dùng/thiết bị, không phải sao chép ngôn ngữ,
framework, endpoint hoặc số lượng vendor adapter.

| Capability upstream | Mốc Veetee | Kết quả tương đương mong đợi |
| --- | --- | --- |
| WebSocket device session | M1-M2 | Veetee WS, wire behavior tương thích firmware |
| OTA/config discovery | M0, M1, M5 | Khóa contract, responder tối thiểu, rồi lifecycle đầy đủ |
| VAD/ASR/LLM/TTS | M2 | Silero, PhoWhisper, Groq model duyệt tại M0, Gemini qua adapter |
| Streaming response và interruption | M2 | Token -> TTS -> Opus, cancel xuyên pipeline |
| Intent/function calling | M3 | Strategy cấu hình được, không keyword hardcode |
| Device MCP | M3 | JSON-RPC discovery/call có policy |
| Server plugin/MCP | M3, M6 | Unified tool registry có auth/audit |
| Memory | M3 | Working/episodic/profile có forget/privacy |
| Agent/device/model management | M4 | REST `/api/v1` + Veetee Console |
| User/admin/RBAC | M4, M6 | Ownership và audit ở backend |
| Activation/binding | M5 | Credential/device lifecycle Veetee |
| OTA catalog/rollout | M5 | An toàn hơn upstream, có signature/rollback |
| Chat history/title/summary | M4, M6 | Có retention/export/delete |
| Voice/voiceprint/clone | M6 | Có consent; chỉ bật khi được duyệt |
| Knowledge/RAG | M6 | Dataset/document/retrieval có tenant isolation |
| Correct words/context | M6 | Typed/versioned config |
| Weather/search/music/HA | M6 | Tool integration theo nhu cầu, có policy |
| MQTT/UDP | M6 | Chỉ khi cần; AEAD và replay protection |
| Mobile manager | M6 | Web responsive Veetee thay client riêng |
| Metrics/operations | M7 | Dashboard/runbook/supervisor an toàn |

Mục nào được đánh dấu “không áp dụng” phải có lý do sản phẩm và được người dùng duyệt;
AI không được tự bỏ chỉ vì upstream implementation khó hoặc công nghệ khác.

## 18. Các quyết định còn phải khóa tại Mốc 0

- Path device API/WS cuối cùng và version wire protocol đầu tiên.
- Groq LLM mặc định sau benchmark model active và kiểm tra deprecation/entitlement.
- PhoWhisper variant/runtime sau benchmark.
- Cách khai báo/rotate secret cho Gemini TTS key pool mà không restart session đang chạy.
- Voice Gemini mặc định và style prompt tiếng Việt.
- ORM/migration library và package manager Python.
- Chính sách lưu transcript/audio mặc định.
- Memory embedding/retrieval implementation.
- Có cần MQTT/UDP trong phạm vi parity thực tế hay không.

Không quyết định các mục này bằng cách sao chép default upstream.
