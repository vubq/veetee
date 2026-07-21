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
| LLM | `openai-compatible-9router` | 9router local hoặc endpoint LAN | development/default candidate, có thể thay model/provider bằng Manager |
| TTS tiếng Việt | VieNeu-TTS v3 Turbo | voice-server local | primary `vi-VN`, sentence/stream chunk tùy khả năng runtime |

ASR, VAD và TTS không chạy trên ESP32-S3. Firmware chỉ thu/phát audio, Opus,
wake/interrupt local, state machine và transport. Cách chia này phù hợp giới hạn
RAM/PSRAM/CPU của ESP32-S3 N16R8 và giữ provider có thể thay thế.

## 2. LLM và 9router

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

`cx/gpt-5.6-terra` được chọn làm default dev/LAN vì planner và câu hỏi trực tiếp
ổn định hơn trong các lượt đo hiện tại. Đây không phải quyết định production:
Manager vẫn lưu model theo provider binding, giữ fallback adapter và phải benchmark
lại khi phiên bản 9Router, quota hoặc upstream model thay đổi. Structured planner
được prewarm khi voice-server khởi động; deadline 8 giây là safety ceiling, không
phải latency target.

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
vi khi quota/upstream hết. Với voice, ưu tiên model không phải `*-review`, đặt
`reasoning_effort=none` cho lượt bình thường và chỉ nâng lên `low` theo agent policy
khi request cần suy luận thêm. Reasoning event phải được giữ cho
planner/telemetry có kiểm soát nhưng tuyệt đối không đưa
`reasoning_content`/`reasoning` vào TTS.

### 2.2.4 API key và network policy

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

Tất cả backend, 9Router, VAD, ASR và TTS chạy trên cùng một máy. Vì vậy:

- voice-server gọi `http://127.0.0.1:20128/v1`;
- Silero/Zipformer/ChunkFormer/VieNeu chạy như worker/process hoặc container riêng
  trên Docker private network/loopback;
- không mở port model worker và port `20128` cho ESP32;
- ESP32 chỉ gọi Voice WebSocket và Device Edge qua IP LAN;
- Manager Web/API chỉ mở các port đã định nghĩa cho operator;
- nếu 9Router bind loopback thật, API key là defense-in-depth; nếu vẫn bind
  `0.0.0.0`, bắt buộc bật key và firewall dù mọi service hiện ở cùng máy.

Single-node là deployment profile V1, không phải coupling trong code. Provider ports,
queue và config vẫn giữ boundary để sau này tách GPU/model worker sang máy khác mà
không đổi firmware hoặc conversation core.

Veetee dùng port `LlmProvider`, nên topology là:

```text
voice-server -> openai-compatible adapter -> 9router (LAN)
                                         -> official OpenAI API (optional)
                                         -> self-hosted model (optional)
```

9router không được là dependency của firmware và không được hard-code vào
conversation core. Nếu 9router chỉ điều khiển một phiên Codex tương tác, không có
API contract ổn định hoặc không cho phép tích hợp này, V1 phải chuyển sang một
OpenAI Platform API key hoặc model self-hosted tương thích; toàn bộ voice loop vẫn
giữ nguyên.

### 2.3 LLM policy realtime

- Dùng streaming output, `max_output_tokens` thấp cho câu trả lời thoại và structured
  output cho planner/tool call.
- Tách planner/tool decision khỏi prose TTS; không phát chain-of-thought ra loa.
- Khi user abort, hủy request và tăng generation; token đến trễ bị drop.
- Không retry request đã abort. Chỉ retry lỗi retryable khi còn `total_turn_deadline`.
- Chọn model/temperature/context theo agent config; không đóng đinh tên model trong
  firmware hoặc code trung tâm.

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

Đã benchmark trên host V1 (Intel i5-10300H, 15 GiB RAM, GTX 1650 Ti 4 GiB; chưa có
CUDA toolkit): median ba lượt có watermark cho VieNeu ONNX INT8 đạt first audio
khoảng 304--347 ms và RTF 0.745--0.803 ở 6 threads; Zipformer INT8 decode 1.55
giây audio trong 31--37 ms ở 2 threads. VieNeu
native C++ CPU đạt RTF khoảng 0.75 cho batch hoàn chỉnh nhưng C ABI hiện chưa có
stream callback/cancellation. Vì vậy ONNX streaming vẫn là primary V1; native chỉ
là benchmark/opt-in worker cho tới khi bổ sung API streaming tương đương. Chi tiết
và lệnh tái lập nằm ở `docs/15-local-ai-runtime.md`.

Model TTS phải được benchmark về first-audio, real-time factor, CPU/RAM/VRAM,
phát âm tên riêng/số/ngày, chất lượng giọng, output sample rate, license và khả năng
hủy giữa chừng. Cache các câu hệ thống ngắn (goodbye, activation code, Wi-Fi lỗi)
để timeout/error vẫn phản hồi nhanh khi TTS đang bận.

V1 ưu tiên local-only. Cloud TTS có thể được đăng ký là adapter optional nhưng không
tự động bật và không làm lộ transcript/audio ra ngoài khi privacy profile không cho
phép.

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
    "adapter": "openai-compatible-9router",
    "base_url": "http://127.0.0.1:20128/v1",
    "model": "cx/gpt-5.6-terra",
    "stream": true,
    "tool_calling": true,
    "reasoning_effort": "none",
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

`base_url`, model id, thresholds và fallback chain là configuration version có
validation; không cho model/LLM tự sửa chúng.

## 7. Benchmark và gate trước khi freeze

Tạo corpus có tiếng Việt tự nhiên, dấu, tên riêng, số/ngày/tiền, giọng trẻ/người
lớn, khoảng cách mic, self-TTS, media và nhiều loại noise. Báo cáo theo model,
hardware và quantization:

| Nhóm | Chỉ số bắt buộc |
|---|---|
| VAD/admission | false accept, false reject, endpoint delay, self-echo reject |
| ASR | WER/CER, số/tên riêng, first partial, final latency, real-time factor |
| LLM/9router | first token p50/p95, stream gap, tool-call success, cancel latency |
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
4. 9router chỉ là default dev khi contract streaming/tool/cancel pass; production
   phải có adapter thay thế và health/circuit-breaker.

## 8. UI và vận hành

Giữ nguyên visual prototype ở `veetee-server/prototypes/manager-web/index.html`.
Các provider/model hiển thị trong prototype là fake data để duyệt layout, không phải
quyết định Azure/Whisper/gpt-4.1. Khi chuyển sang Vue, giữ DOM hierarchy, CSS tokens,
responsive breakpoint và interaction đã duyệt; chỉ thay data layer bằng Provider Hub
thật, thêm model benchmark/fallback/health và hiển thị rõ local-only hay external.

## 9. Câu hỏi còn cần xác nhận

- 9router đang expose Chat Completions hay Responses, có SSE/tool calling/cancel và
  auth token riêng cho app không?
- Máy chạy voice-server có CPU/RAM/VRAM và có GPU nào; dự kiến bao nhiêu session đồng
  thời?
- Exact repository/commit, runtime format và license của ba model local là gì?
- VieNeu-TTS v3 Turbo có streaming hay chỉ batch trên runtime bạn định dùng?
- V1 có chấp nhận Zipformer-only khi ChunkFormer benchmark chưa đạt hay không?
- Giới hạn latency/privacy nào quan trọng hơn nếu phải đánh đổi?
