# Model và provider baseline

Tài liệu này ghi baseline AI được chọn cho Veetee V1. Nó bổ sung cho registry ở
`docs/06-provider-and-mcp.md`; không đưa SDK/model cụ thể vào firmware.

## 1. Quyết định V1

| Năng lực | Provider/model V1 | Nơi chạy | Vai trò |
|---|---|---|---|
| Wake/interrupt local | ESP-SR WakeNet/MultiNet model pack | ESP32-S3 | activation wake word và interrupt profile độ trễ thấp |
| VAD/endpoint | Silero VAD (`silero-local`) | voice-server cùng máy chạy AI | phát hiện speech/điểm kết thúc, không tự quyết định đó là yêu cầu |
| ASR nhanh | Sherpa-ONNX Zipformer Vietnamese 30M INT8 | voice-server local | đường chính, streaming/chunk để giảm latency |
| ASR chất lượng | ChunkFormer-CTC-Large-Vie | voice-server local | re-decode khi Zipformer không đủ tin cậy |
| LLM | `openai-compatible-cliproxyapi` | CLIProxyAPI local | development/default hiện hành, model/provider vẫn do Manager publish |
| LLM optional | `groq-cloud` | external OpenAI-compatible | binding có sẵn nhưng không nằm trong chain mặc định; chỉ publish theo yêu cầu sau |
| LLM paused | `openai-compatible-9router` | 9Router local | adapter tùy chọn/lịch sử; process `20128` đang tạm dừng |
| TTS tiếng Việt | VieNeu-TTS v3 Turbo | voice-server local | primary `vi-VN`, sentence/stream chunk tùy khả năng runtime |

Manager có binding độc lập `groq-cloud` (Groq OpenAI-compatible Chat Completions).
Binding này không tự trở thành fallback: local seed và agent mặc định chỉ publish
CLIProxyAPI; thêm Groq cần một agent-config publish tường minh.
Groq nhận cấu hình `serviceTier`, `maxCompletionTokens`, `temperature`, `topP`,
`reasoningEffort` và `parallelToolCalls`. TTS V1 chỉ dùng VieNeu local; secret
không nằm trong agent snapshot và không có đường gửi transcript ra dịch vụ ngoài.
Với model mặc định `llama-3.3-70b-versatile`, adapter không gửi
`reasoning_effort` hoặc metadata tùy biến vì Groq sẽ trả HTTP 400; semantic gate
dùng JSON Object Mode streaming và vẫn validate lại schema tại voice-server.
Groq Structured Outputs (`json_schema`) hiện không được dùng cho đường streaming
này. `streamProseResponse=true` giữ gate ở vai trò admission/planner và đưa prose
stream qua sentence chunker để TTS bắt đầu trước khi model hoàn thành toàn câu trả
lời.

ASR, VAD và TTS không chạy trên ESP32-S3. Firmware chỉ thu/phát audio, Opus,
wake/interrupt local, state machine và transport. Cách chia này phù hợp giới hạn
RAM/PSRAM/CPU của ESP32-S3 N16R8 và giữ provider có thể thay thế.

## 2. LLM gateways: CLIProxyAPI hiện hành và lịch sử 9Router

### 2.1 Phân biệt quyền truy cập

ChatGPT Plus/Codex và OpenAI Platform API là hai đường xác thực khác nhau. Tài liệu
Codex chính thức mô tả đăng nhập ChatGPT cho quyền sử dụng Codex và API key cho
usage-based API; API key được tính phí theo OpenAI Platform. Vì vậy không giả định
gói Plus tự động cấp API key, hạn mức API hoặc quyền dùng token phiên Codex cho
ứng dụng Veetee.

Nguồn tham khảo chính thức:

- [Codex authentication](https://learn.chatgpt.com/docs/auth#openai-authentication)
- [Chat Completions API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create)

Không đưa `~/.codex/auth.json`, cookie trình duyệt hoặc access token phiên cá nhân
vào firmware, database hay provider credential của Veetee. Codex access token (nếu
có trong workspace phù hợp) cũng không mặc nhiên là credential cho general OpenAI
API.

### 2.2 Điều kiện để dùng 9router

9router có thể được dùng làm LLM adapter nếu nó cung cấp endpoint OpenAI-compatible
và người dùng có quyền sử dụng cơ chế xác thực đó. V1 chỉ coi đây là provider
`development/experimental` cho tới khi smoke test và soak test đạt:

- `POST /v1/chat/completions` hoặc `/v1/responses`;
- streaming SSE (hoặc WebSocket event) ổn định;
- structured JSON output cho admission/planner;
- function/tool calling với `call_id`, arguments và tool result;
- timeout, client cancellation và không phát tiếp token sau abort;
- retry/error status rõ ràng, request id và usage metadata;
- concurrency tối thiểu bằng số session V1;
- auth/token không cần chia sẻ credential cá nhân cho thiết bị.

### 2.2.1 Kết quả kiểm tra instance của Veetee (2026-07-21)

Instance trong máy người dùng khớp với 9Router `v0.5.40` (package `9router-app`).
Tài liệu/repository chính thức của project:

- [9Router website](https://9router.com)
- [9Router GitHub](https://github.com/decolua/9router)
- [9Router README API reference](https://github.com/decolua/9router#-api-reference)
- [9Router architecture](https://github.com/decolua/9router/blob/master/docs/ARCHITECTURE.md)

Đã kiểm tra local, không ghi lại API key:

| Probe | Kết quả |
|---|---|
| `GET http://127.0.0.1:20128/api/health` | `200`, `{"ok":true}` |
| `GET http://127.0.0.1:20128/v1/models` | `200`, OpenAI model list |
| `POST /v1/chat/completions` | `200` JSON, model `cx/gpt-5.4-mini` |
| Chat streaming | `200` SSE, có delta text và terminal `finish_reason` |
| Forced function call | `200`, trả `tool_calls` + `call_id` + JSON arguments |
| `POST /v1/responses` | `200`, `status=completed`, output/usage hợp lệ |
| Model capability | `cx/gpt-5.4-mini` báo `tools=true`, `reasoning=true`, `contextWindow=400000` |

Probe lịch sử bằng chính `NineRouterLlmProvider` với `cx/gpt-5.4-mini` trên
loopback sau khi chuyển structured output sang SSE cho kết quả warm smoke: health
khoảng 9--11 ms, JSON planner khoảng 1.25 s, prose stream khoảng 1.23--1.50 s và
adapter cancellation khoảng 0.01--0.03 ms. `/v1/models` chỉ là catalog:
`cx/gpt-5.3-codex-spark` xuất hiện nhưng upstream ChatGPT account hiện trả
`400 not supported`, nên Manager health phải probe từng binding thực tế thay vì
coi model list là bằng chứng model dùng được.

### 2.2.2 Model dev/LAN hiện tại

Benchmark voice loop trên host V1 so sánh các model sẵn có qua cùng adapter,
`reasoning_effort=none`, cùng structured planner và VieNeu TTS:

| Model | Structured planner | Prose | Câu hỏi -> first audio | MCP -> first audio |
|---|---:|---:|---:|---:|
| `cx/gpt-5.4-mini` | 1.30--4.29 s | khoảng 1.50 s | 2.96--5.41 s | 3.99--4.99 s |
| `cx/gpt-5.6-terra` | khoảng 1.32 s | khoảng 1.85 s | 2.81--2.95 s | khoảng 4.48 s |
| `cx/gpt-5.6-luna` | khoảng 1.27 s | khoảng 2.44 s | chưa đủ mẫu | chưa đủ mẫu |

`cx/gpt-5.6-terra` từng được chọn làm default dev/LAN qua 9Router vì planner và câu hỏi
trực tiếp ổn định hơn trong các lượt đo tại thời điểm đó. Quyết định này đã được thay thế
ngày 2026-07-29 bởi CLIProxyAPI `gpt-5.6-terra`; đây vẫn không phải quyết định production:
Manager vẫn lưu model theo provider binding, giữ fallback adapter và phải benchmark
lại khi phiên bản 9Router, quota hoặc upstream model thay đổi. Structured planner
được prewarm khi voice-server khởi động.

Đo lại ngày 2026-07-22 cho thấy latency của provider Codex qua 9Router biến động
mạnh theo quota/upstream: `cx/gpt-5.6-terra` trả JSON probe khoảng 5.27 s và prose
khoảng 1.82 s; forced function schema của full conversation gate có lượt vượt quá
15 s. Trong cùng điều kiện, full gate dùng `response_format=json_object` mất khoảng
3.16 s; lượt Text Lab thật đạt admission khoảng 5.26 s và first audio khoảng 5.83 s.
`cx/gpt-5.4-mini`/`cx/gpt-5.6-luna` có lượt probe khoảng 21 s và
`cx/gpt-5.3-codex-spark` timeout, nên không thay default chỉ dựa trên catalog model.

Probe ngày 2026-07-24 xác nhận instance hiện tại hỗ trợ `response_format=json_schema`
strict cho full conversation gate. Vì vậy semantic gate dùng strict JSON Schema SSE rồi
validate lần hai bằng Draft 2020-12 tại voice-server. Provider boundary giữ mã lỗi
bounded và chỉ expose metadata (code, finish reason, output length, schema path), không
giữ raw output/transcript. Field tương thích bị model bỏ sót có thể được chuẩn hóa an
toàn từ chính structured fields trước lần validate cuối; không suy diễn exact phrase.
Tool name vẫn nằm trong enum catalog, arguments tiếp tục qua schema/policy MCP.
Nếu structured gate hỏng sau local signal admission, server dùng prose response không
có tool; nếu prose cũng hỏng, phát recovery response đã cấu hình và giữ session sống.
Forced function call vẫn nằm trong provider conformance suite nhưng không còn là
transport bắt buộc của gate. Baseline local dùng planner ceiling 15 s. Absolute
`total_turn` mặc định là `0` (tắt); `llmSeconds`/`ttsSeconds` là idle/progress deadline
được làm mới, không phải ceiling cho response đang tiếp tục sinh hoặc playback drain.

Live Manager probe ngày 2026-07-22 đã lấy API key active do chính 9Router quản lý,
rotate vào encrypted provider secret mà không ghi giá trị ra log/repo, rồi gọi đúng
`cx/gpt-5.6-terra` qua `/v1/chat/completions`: `healthy`, khoảng 1.20 giây,
`circuit=closed`. `/v1/models` không cần key trên instance này nhưng inference trả
`401` nếu thiếu key; vì vậy readiness/test phải dùng runtime secret resolver, không
dựa vào catalog public.

README/source của 9Router cũng xác nhận `/v1/chat/completions`, `/v1/models`, SSE,
API key và `REQUIRE_API_KEY`. Source có route `/v1/responses` và disconnect-aware
stream/AbortController. Tuy nhiên stream thực tế vừa kiểm tra kết thúc bằng terminal
chunk nhưng không gửi `[DONE]`; adapter Veetee phải coi `finish_reason` hoặc EOF sau
terminal event là kết thúc hợp lệ, không chờ `[DONE]` vô hạn.

### 2.2.3 Codex subscription trong 9Router

9Router không chỉ chuyển tiếp OpenAI Platform API. Provider `cx` trong source
`v0.5.40` dùng OAuth Codex và upstream `chatgpt.com/backend-api/codex/responses`,
với quota 5 giờ/tuần theo README. Source hiện còn đánh dấu provider Codex là
`deprecated` và có risk notice. Vì vậy:

- Có thể dùng model `cx/*` đã pass capability probe cho prototype cá nhân/LAN sau khi
  user đã tự đăng nhập trong 9Router.
- Không coi đây là production contract bền vững cho sản phẩm thương mại.
- Không đưa OAuth token/Codex refresh token vào Veetee; Veetee chỉ gọi local 9Router
  bằng API key riêng của 9Router.
- Giữ adapter `openai-compatible` để chuyển sang OpenAI Platform API key, provider
  trả phí khác hoặc self-hosted model mà không đổi conversation core.

`cx/gpt-5.4-mini` đã pass smoke tool call lịch sử; `cx/gpt-5.6-terra` đã pass
structured planner, direct response, MCP và cancellation loop hiện tại. Cả hai đều
không được coi là production contract trước khi đo tiếng Việt, quota reset và hành
vi khi quota/upstream hết. Với voice, ưu tiên model không phải `*-review`. Mọi lượt
hội thoại hiện cố định `reasoning_effort=none` để ưu tiên độ trễ và tính
dự đoán được. Cấu hình provider/agent không được tự nâng lên `low/medium/high`;
nếu sau này cần reasoning cho một workflow riêng thì phải bổ sung policy tách biệt,
không dùng ngầm trong hội thoại. Voice-server bỏ qua hoàn toàn
`reasoning_content`/`reasoning`, không lưu, không phát event cho client và tuyệt
đối không đưa vào TTS. Trạng thái giao thức `thinking` chỉ có nghĩa kỹ thuật là
đang chờ planner/provider/tool, UI hiển thị ngắn gọn là “Đang xử lý”.

### 2.2.4 API key và network policy

Phần này giữ policy lịch sử/opt-in cho adapter 9Router; process hiện đang tạm dừng và
không thuộc startup mặc định. Nếu bật lại rõ ràng, các ràng buộc dưới đây vẫn áp dụng.

Ảnh cấu hình ban đầu cho thấy `Require API key` tắt, nhưng smoke test mới nhất đã
trả `401 Missing API key` cho Chat Completions trong khi `/v1/models` vẫn public.
Voice-server đã pass full-loop test khi dùng key active lấy từ secret store local;
key không được in ra log hay ghi vào repo. Probe socket trước đó cho thấy process
listen trên `0.0.0.0:20128`, dù UI hiển thị endpoint local. Đây là policy bắt buộc:

1. Nếu voice-server và 9Router cùng máy: đổi 9Router bind về `127.0.0.1` nếu có thể;
   dùng `http://127.0.0.1:20128/v1` và không cần đưa key qua mạng.
2. Nếu voice-server ở máy khác: bật `REQUIRE_API_KEY=true`, tạo key riêng trong
   Dashboard, lưu trong secret/env của voice-server và giới hạn firewall chỉ IP máy
   voice-server.
3. Không cho ESP32 gọi 9Router trực tiếp; ESP32 chỉ gọi voice-server.
4. Không expose port `20128` ra Internet, tunnel công khai hoặc port-forward.
5. Không gửi API key vào chat, commit, fixture, log hoặc firmware. Chỉ gửi tên biến
   môi trường, ví dụ `VEETEE_9ROUTER_API_KEY`.

9Router local API key là credential tới gateway local, không phải OpenAI Platform
API key. Header chuẩn là `Authorization: Bearer <9router-key>`.

### 2.2.5 Topology Veetee V1 đã chốt

Tất cả backend, CLIProxyAPI, VAD, ASR và TTS chạy trên cùng một máy. Vì vậy:

- voice-server gọi `http://127.0.0.1:8317/v1` bằng client key riêng;
- Silero/Zipformer/ChunkFormer/VieNeu chạy như worker/process hoặc container riêng
  trên Docker private network/loopback;
- không mở port model worker và port `8317` cho ESP32/LAN/Tailscale;
- ESP32 chỉ gọi Voice WebSocket và Device Edge qua IP LAN;
- Manager Web/API chỉ mở các port đã định nghĩa cho operator;
- CLIProxyAPI hiện listen rộng trên host nên client-key authentication và firewall là
  bắt buộc dù Veetee chỉ gọi loopback. 9Router/`20128` tạm dừng.

Single-node là deployment profile V1, không phải coupling trong code. Provider ports,
queue và config vẫn giữ boundary để sau này tách GPU/model worker sang máy khác mà
không đổi firmware hoặc conversation core.

Veetee dùng port `LlmProvider`, nên topology là:

```text
voice-server -> openai-compatible adapter -> CLIProxyAPI (local primary)
                                         -> error/recovery (default chain end)
                                         -> Groq/official/self-hosted (optional,
                                            only after explicit publish)
```

CLIProxyAPI không được là dependency của firmware và không được hard-code vào
conversation core. Nếu gateway không còn contract/quota phù hợp, Manager đổi binding
sang official API hoặc model self-hosted tương thích; toàn bộ voice loop vẫn giữ nguyên.

### 2.3 LLM policy realtime

- Dùng streaming output và per-request `max_output_tokens` bounded phù hợp cho câu trả
  lời thoại; đây không phải duration cap. Generated output dài hơn một request dùng
  resumable segment/cursor, còn file/source text stream bounded trực tiếp qua sentence
  chunker -> TTS với offset checkpoint.
- Bảo toàn terminal `finish_reason`; `length|max_tokens` phải được báo
  `llm_output_truncated` sau khi partial TTS drain và không được commit vào completed
  context/memory.
- Tách planner/tool decision khỏi prose TTS; không phát chain-of-thought ra loa.
- Áp dụng `llmSeconds` như first-token/inter-event idle deadline được làm mới, không
  dùng nó làm absolute ceiling cho câu trả lời đang tiếp tục sinh hoặc TTS đang drain.
- Khi user abort, hủy request và tăng generation; token đến trễ bị drop.
- Không retry request đã abort. Chỉ retry lỗi retryable khi còn `total_turn_deadline`.
- Chọn model/temperature/context theo agent config; không đóng đinh tên model trong
  firmware hoặc code trung tâm.

### 2.4 CLIProxyAPI local

CLIProxyAPI `7.2.97` trên host hiện chạy cổng `8317`; Veetee gọi loopback qua
`http://127.0.0.1:8317/v1` bằng adapter
`openai-compatible-cliproxyapi`. Binding mặc định dùng `gpt-5.6-terra`, nhưng model
vẫn là dữ liệu Manager và có thể đổi độc lập với 9Router. Catalog tại thời điểm
kiểm tra gồm `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-luna` và `gpt-5.6-terra`.

Conformance ngày 2026-07-24 đã pass authenticated `/models`, Chat Completions SSE,
cancellation, JSON Object Mode, strict JSON Schema và forced tool call. Một số mẫu
ban đầu cho `gpt-5.6-terra` đạt structured JSON khoảng 1,34--3,19 giây và prose
hoàn tất khoảng 1,87 giây. Cùng probe đơn lẻ, 9Router đạt structured khoảng
1,46 giây và prose khoảng 2,15 giây. Số mẫu này chưa đủ để kết luận gateway nào
nhanh hơn.

Upstream account đầu tiên sau đó trả HTTP `429 usage_limit_reached`; Manager đã xác
nhận đúng `degraded/http_429` mà không tăng failure count hoặc mở circuit. Sau khi
người dùng đổi account ngày 2026-07-24, binding trở lại `healthy` và benchmark xen
kẽ năm lượt cho cùng model/prompt đạt:

| Gateway | Structured median/p95 | First token median/p95 | Prose total median/p95 |
|---|---:|---:|---:|
| 9Router | 1,54 / 3,06 giây | 1,25 / 1,91 giây | 2,05 / 2,08 giây |
| CLIProxyAPI | 1,66 / 2,62 giây | 2,00 / 3,18 giây | 2,15 / 3,30 giây |

Cả 20 request đều thành công. CLIProxyAPI có structured p95 tốt hơn trong mẫu nhỏ,
nhưng 9Router đưa first token ra sớm hơn khoảng 0,76 giây ở median và có prose p95
ổn định hơn khoảng 1,23 giây. Đây là bằng chứng lịch sử, không còn quyết định routing.
Từ 2026-07-29 CLIProxyAPI là default dev/LAN theo quyết định vận hành của người dùng;
9Router tạm dừng. Groq vẫn có binding riêng nhưng không nằm trong chain mặc định;
fallback chỉ được thêm bằng publish tường minh trong một task sau.

CLIProxyAPI/Codex không nhận `reasoning_effort=none` như 9Router: gateway ánh xạ nó
thành thinking budget 0 và full strict conversation schema trả HTTP 400. Adapter
CLIPROXYAPI vì vậy bỏ hẳn field reasoning khi policy là `none`; các mức reasoning
khác vẫn là cấu hình tường minh và phải qua capability probe trước khi dùng.
Codex strict JSON Schema còn yêu cầu mọi object phải đóng bằng
`additionalProperties: false` và khai báo toàn bộ property trong `required`. Schema
conversation có `tool_call.arguments` động nên adapter dùng JSON Object Mode cho
schema không đạt ràng buộc này, sau đó Voice Server vẫn chuẩn hóa và validate bằng
schema/tool policy gốc trước khi cho phép MCP. Schema đóng tương thích vẫn dùng strict
JSON Schema.

Lệnh benchmark tái lập là `npm run providers:benchmark:gateways`; client key
CLIProxyAPI phải được truyền qua `VEETEE_CLIPROXY_API_KEY`, không ghi vào repo hoặc
command log. Script đo structured latency, first-token, prose-total và gom lỗi bounded
thay vì dừng ở lỗi provider đầu tiên.

Soak Realtime Lab ngày 2026-07-29 xác nhận route hiện hành trên đường đầy đủ chứ không
chỉ bằng gateway probe. Ba lượt hội thoại thường tạo đúng sáu local POST (planner +
prose mỗi lượt) tới CLIProxyAPI `gpt-5.6-terra`, đều HTTP 200, không gọi Groq và không
có gap/turn error. Một lượt kể chuyện dài tạo 308,56 giây PCM, 323 frame, zero schedule
gap và terminal `tts.stop`; planner/prose đều đi qua CLIProxyAPI, trong khi port `20128`
không có listener. Lần thử đầu của prose chạm idle deadline ở 4,999 giây và được tính là
cycle fail; một retry được báo rõ mới pass. Provider deadline không được che bằng cách
tăng TTS thread, playback buffer hoặc âm thầm loại mẫu lỗi khỏi báo cáo.

CLIProxyAPI hiện listen trên mọi interface của host, dù Veetee chỉ gọi loopback.
Trước khi dùng ngoài môi trường local phải giữ client-key authentication, giới hạn
firewall hoặc đổi bind host về loopback. ESP32 và Manager Web không gọi trực tiếp
cổng `8317`; OAuth token/upstream credential không được sao chép vào Veetee.

## 3. ASR cascade tiếng Việt

### 3.1 Vì sao dùng cả hai

Không chạy hai model trên mọi utterance. Zipformer INT8 là model chính vì nhẹ hơn,
phù hợp streaming và phản hồi nhanh. ChunkFormer-Large là đường chất lượng để
re-decode có điều kiện; model lớn hơn không đồng nghĩa luôn tốt hơn trong nhiễu,
độ trễ hoặc phần cứng cụ thể.

```text
Opus -> PCM -> Silero VAD/endpoint
                  -> Zipformer vi INT8 (interim/final)
                  -> ASR quality/admission evaluator
                       ├─ accepted/stable -> planner/LLM
                       ├─ low confidence/unstable -> ChunkFormer re-decode
                       └─ invalid/timeout -> hỏi lại hoặc kết thúc theo policy
```

Các tín hiệu để kích hoạt re-decode (đều là config/model output, không phải
exact-string rule):

- confidence dưới ngưỡng theo locale/model version;
- transcript interim/final không ổn định hoặc có edit distance cao;
- quá nhiều token unknown, ký tự bất thường, số/tên riêng không hợp lệ;
- semantic planner trả `unclear` cho request có giá trị cao;
- người dùng yêu cầu “nói lại”, “nghe không đúng” hoặc sửa câu trước đó;
- profile yêu cầu chất lượng cao cho tool/action quan trọng.

Pinned Sherpa offline adapter chưa cung cấp confidence/stability đã hiệu chuẩn nên
hai field hiện là `null`, không phải `1.0` hay `0`. Preprocess/decode chạy trong
deadline/cancellation scope; native decode đã timeout vẫn giữ serialization lock
cho tới khi thread thật sự kết thúc để lượt sau không overlap trên recognizer dùng
chung. Telemetry tách queue/decode/total latency và ghi
`confidence_available=false`.

Re-decode phải dùng cùng `turn_id`, deadline và cancellation scope. Nếu ChunkFormer
không hỗ trợ streaming trên runtime đã chọn, chỉ dùng nó sau VAD final và không
quảng bá đó là first-response realtime. Khi server chưa đủ CPU/GPU/RAM, có thể bật
Zipformer-only để bring-up; adapter và metrics vẫn phải để sẵn cho ChunkFormer.

### 3.2 Không gọi LLM quá sớm

ASR có text không có nghĩa là user đang hỏi robot. `InputAdmissionGate` phải xem
signal quality, self-echo, target relevance, ASR confidence và session context trước
khi tạo planner/LLM/MCP turn. Audio TV, quạt, nhạc, người khác hoặc tiếng vọng chỉ
là các lớp dữ liệu benchmark; không tạo `if source == ...` trong product code.

## 4. VAD và xử lý audio

Silero VAD là VAD/endpoint model, không phải noise classifier, AEC hay semantic
relevance model. Baseline server dùng pipeline:

```text
PCM -> resample/format check -> optional denoise/AGC/AEC
    -> Silero speech probability -> endpointing
    -> quality features + Zipformer -> admission gate
```

ESP-SR AFE hoặc audio front-end trên ESP chỉ đảm nhiệm capture/wake path và các
feature mà board thật chứng minh được. Full-duplex voice barge-in vẫn phụ thuộc
far-end reference/AEC benchmark; button interrupt là guarantee V1.

Không lưu raw audio mặc định. Nếu bật dataset/eval, phải có consent, retention và
redaction rõ trong Manager.

## 5. TTS local và phát incremental

VieNeu-TTS v3 Turbo là primary `vi-VN` candidate. Voice server nhận token stream,
gom câu theo dấu câu tiếng Việt rồi synthesize từng chunk:

```text
LLM text delta -> sentence chunker -> VieNeu-TTS
              -> PCM -> Opus -> WebSocket -> MAX98357A
```

Nếu runtime VieNeu hỗ trợ streaming, phát audio ngay khi chunk đầu sẵn sàng. Nếu
chỉ hỗ trợ batch, sentence chunking vẫn cho UX incremental nhưng latency sẽ cao hơn;
không giả định “Turbo” tự động có streaming. Adapter phải có `cancel()` và trả
sample-rate/format rõ ràng.

Giọng production mặc định là Trúc Ly với tempo `1.0`. ONNX compatibility backend
dùng lead-in 16 acoustic frames; native C++ CPU vẫn là batch-only. VieNeu chỉ tạo
TTS request theo dấu kết câu để không tách một cụm như `khó khăn` thành hai
utterance. Nhiều câu ngắn được gom thành một natural batch tối đa 160 ký tự trên
ONNX hoặc 72 ký tự trên native, giảm phần im lặng và acoustic restart giữa các
request. Câu cuối không dấu được nhập vào batch khi còn vừa; output bệnh lý không
có dấu câu dùng emergency bound theo backend (ONNX 256, native 72 ký tự) và chỉ cắt
ở whitespace khi có thể. Tempo được áp dụng đúng theo agent config bằng WSOLA giữ cao
độ; runtime đo
`realtime_speed_ceiling` với headroom `1.15` và cảnh báo starvation thay vì tự đổi
tốc độ đã publish. Giao thức PCM/Opus 24 kHz với firmware không đổi.

Host baseline phải có `OPENBLAS_NUM_THREADS=1` trong process environment trước khi
Python/NumPy khởi tạo. `VEETEE_TTS_THREADS=2` chỉ giới hạn ONNX Runtime; nếu thiếu cap
OpenBLAS, NumPy có thể mở thêm worker, làm CPU/nhiệt tăng và TTS chậm hơn dù cấu hình
ONNX vẫn ghi hai threads. Giá trị checked-in, A/B dài và cách xác minh nằm trong
`docs/15-local-ai-runtime.md` và `docs/21-local-development-runbook.md`.

V1 không coi voice và style là hai tham số có thể ghép độc lập trong Manager. Mỗi entry
trong voice catalog quyết định đồng thời voice ID, `gender` và `style` nguồn đã được
benchmark từ reference/model pack; chọn voice đồng nghĩa Manager publish đúng metadata
của entry đó ở cả Agent và default của Provider. Các field riêng vẫn còn trong agent
và provider schema để đọc snapshot cũ. Với một voice legacy/custom không còn trong
catalog hiện tại, Manager giữ metadata đã lưu thay vì tự đoán hoặc âm thầm đổi cấu hình.

V1 chưa triển khai voice đa phong cách. Nếu một voice trong tương lai thực sự hỗ trợ
nhiều phong cách, provider capability, Manager UI và runtime phải cùng bổ sung contract
allowlist như `allowedStyles` và benchmark từng giá trị trước khi mở selector. Trong V1,
`style` nguồn từ voice catalog là giá trị duy nhất mà Manager cho publish với voice đó.

Mọi profile VieNeu dùng chung một inference lock của engine để không chạy đồng thời
trên model state dùng chung. Runtime cảnh báo `postprocess_rate_starvation_risk` từ
tempo `1.2` vì WSOLA có thể làm phụ âm và dấu tiếng Việt kém rõ, đồng thời giảm
playback headroom; `amplification_clipping_risk` áp dụng khi volume lớn hơn `1.0`.
Manager hiển thị cảnh báo trước khi publish nhưng không tự sửa desired config. Sau mỗi
synthesis, adapter log số sample/audio duration và clipping ratio thực tế đã redact.
Runtime giữ thêm turn-level reservation từ speech chunk đầu tới hết lượt. Cách này
tránh hai session thay nhau chiếm model sau từng câu; session đang chờ reservation
không tiêu thụ `ttsSeconds`. Sau khi nhận worker, `ttsSeconds` được làm mới theo
mỗi audio chunk để chỉ bắt provider thực sự đứng im, không giới hạn độ dài lời đáp.
Native C call đang chạy không thể bị kill an toàn: abort loại output theo generation
ngay, còn worker-owned lock giữ model tới khi call thoát để lượt sau không overlap.

Đã benchmark lại trên host V1 (Intel i5-10300H, 15 GiB RAM, GTX 1650 Ti 4 GiB)
bằng mười lượt fixed-seed có watermark. VieNeu ONNX INT8 CPU 2 threads đạt first
audio median/p95 khoảng 1,56/1,72 giây, RTF 1.148/1.205 và không có playback
starvation trong cả mười lượt. CUDA 12 với ONNX Runtime
GPU chậm hơn: 696/1,365 ms first audio và RTF 1.303/1.804 do nhiều đoạn graph phải
sao chép hoặc fallback qua CPU; GPU chỉ được dùng khoảng 4--10%. Zipformer INT8
decode 1,55 giây audio trong 38/44 ms median/p95 ở 2 threads. Vì vậy Zipformer giữ
ONNX INT8 CPU 2 threads và VieNeu ONNX là baseline portable lẫn profile hiện hành
trên host này. Native C++ CPU 4 threads chỉ là profile batch tùy chọn sau benchmark;
nó không tự fallback hoặc thay ONNX. Chi tiết, A/B batching bị rollback và live Lab
evidence nằm ở `docs/15-local-ai-runtime.md`.

Model TTS phải được benchmark về first-audio, real-time factor, CPU/RAM/VRAM,
phát âm tên riêng/số/ngày, chất lượng giọng, output sample rate, license và khả năng
hủy giữa chừng. Cache các câu hệ thống ngắn (goodbye, activation code, Wi-Fi lỗi)
để timeout/error vẫn phản hồi nhanh khi TTS đang bận.

V1 chỉ bật local-only. Không có cloud TTS fallback trong runtime baseline.

## 6. Provider config mẫu

Representation dưới đây là logical config của Manager; secret chỉ là reference.
Tên field có thể map sang `snake_case` ở device contract.

```json
{
  "locale": "vi-VN",
  "vad": {
    "adapter": "silero-local",
    "model": "silero_vad",
    "device": "cpu"
  },
  "asr": {
    "primary": {
      "adapter": "sherpa-onnx",
      "model": "zipformer-vi-30m-int8",
      "mode": "streaming"
    },
    "fallback": {
      "adapter": "chunkformer-ctc",
      "model": "chunkformer-ctc-large-vie",
      "trigger": "low_confidence_or_unstable"
    }
  },
  "llm": {
    "adapter": "openai-compatible-cliproxyapi",
    "base_url": "http://127.0.0.1:8317/v1",
    "model": "gpt-5.6-terra",
    "stream": true,
    "tool_calling": true,
    "reasoning_effort": "omitted_when_none",
    "reasoning_policy": "drop_from_tts"
  },
  "tts": {
    "adapter": "vieneu-local",
    "model": "vieneu-tts-v3-turbo",
    "locale": "vi-VN",
    "streaming": "probe_then_enable"
  }
}
```

Structured gate truyền thêm bằng chứng bounded của utterance (`wake_source`, thống kê
VAD/RMS/SNR/clipping, integrity và các capability chưa đo được để `null`) cùng transcript,
ASR metrics, agent snapshot và cửa sổ hội thoại. Những tín hiệu này hỗ trợ admission,
không thay thế ngữ cảnh: VAD không tự kết luận addressing, và LLM không được coi `null` là
số 0. Prose response sau planner/tool cũng nhận lại evidence, ASR metrics,
admission decision, dialogue plan, agent snapshot và context message count để không mất
ngữ cảnh giữa các stage. Raw PCM, provider secret và chain-of-thought không bao giờ vào
request TTS.

`base_url`, model id, thresholds và fallback chain là configuration version có
validation; không cho model/LLM tự sửa chúng.

Representation thật trong agent draft dùng `providerChains` với provider UUID thay
vì nhúng secret. Khi publish, Manager mở rộng UUID thành metadata immutable theo
thứ tự primary/fallback; voice-server resolve secret qua internal service token.
Manager Web cho phép rotate/clear secret nhưng không thể đọc lại secret cũ. Với
single-node hiện tại, endpoint nội bộ và provider URL phải giữ loopback hoặc HTTPS.

## 7. Benchmark và gate trước khi freeze

Tạo corpus có tiếng Việt tự nhiên, dấu, tên riêng, số/ngày/tiền, giọng trẻ/người
lớn, khoảng cách mic, self-TTS, media và nhiều loại noise. Báo cáo theo model,
hardware và quantization:

| Nhóm | Chỉ số bắt buộc |
|---|---|
| VAD/admission | false accept, false reject, endpoint delay, self-echo reject |
| ASR | WER/CER, số/tên riêng, first partial, final latency, real-time factor |
| LLM/gateway | first token p50/p95, stream gap, tool-call success, cancel latency |
| TTS | first audio, real-time factor, MOS/listener score, pronunciation, cancel latency |
| Hệ thống | p95 wake-to-first-frame, p95 user-stop-to-silence, CPU/RAM/VRAM, crash/timeout |

Mục tiêu cascade V1 kế thừa `docs/05-realtime-conversation.md`: p95 VAD-final ->
ASR-final <= 600 ms, ASR-final -> first LLM token <= 800 ms, first token -> first
TTS audio <= 700 ms và user-stop -> speaker silence <= 250 ms. Đây là gate đo trên
server/board thật, không phải cam kết khi chưa benchmark.

Freeze policy:

1. Zipformer được freeze làm primary nếu đạt latency/accuracy tối thiểu.
2. ChunkFormer chỉ bật fallback khi re-decode cải thiện transcript có ý nghĩa mà
   không phá `total_turn_deadline`.
3. VieNeu được freeze primary nếu first-audio/RTF/license đạt gate; nếu batch-only,
   ghi rõ sentence-level realtime trong capability.
4. CLIProxyAPI là default dev khi contract streaming/tool/cancel pass và mặc định kết
   thúc chain nếu lỗi. Groq/production adapter vẫn phải có health/circuit-breaker nhưng
   chỉ tham gia sau publish tường minh. 9Router chỉ opt-in khi người vận hành bật lại rõ
   ràng.

## 8. UI và vận hành

Giữ nguyên visual prototype ở `veetee-server/prototypes/manager-web/index.html`.
Các provider/model hiển thị trong prototype là fake data để duyệt layout, không phải
quyết định Azure/Whisper/gpt-4.1. Khi chuyển sang Vue, giữ DOM hierarchy, CSS tokens,
responsive breakpoint và interaction đã duyệt; chỉ thay data layer bằng Provider Hub
thật, thêm model benchmark/fallback/health và hiển thị rõ local-only hay external.

## 9. Câu hỏi còn cần xác nhận

- Máy chạy voice-server có CPU/RAM/VRAM và có GPU nào; dự kiến bao nhiêu session đồng
  thời?
- Exact repository/commit, runtime format và license của ba model local là gì?
- VieNeu-TTS v3 Turbo có streaming hay chỉ batch trên runtime bạn định dùng?
- V1 có chấp nhận Zipformer-only khi ChunkFormer benchmark chưa đạt hay không?
- Giới hạn latency/privacy nào quan trọng hơn nếu phải đánh đổi?
