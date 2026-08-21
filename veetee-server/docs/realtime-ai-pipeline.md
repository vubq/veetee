# Realtime AI pipeline

## Kết nối và routing

Khi có WebSocket connection, `ConnectionHandler.handle_connection()`:

1. Lấy event loop và request headers.
2. Xác định client IP và `device-id`.
3. Phát hiện kết nối từ MQTT gateway qua query string.
4. Khởi động task timeout và AEC cache cleanup.
5. Khởi tạo config/provider riêng ở background.
6. Lặp `async for` nhận text hoặc bytes.
7. Save memory/title và cleanup khi đóng.

Message text được dispatch theo `type`. Registry hiện có:

| Type | Handler semantic |
| --- | --- |
| `hello` | Audio params, feature MCP/AEC, hello response |
| `listen` | Start/stop/detect và listen mode |
| `abort` | Dừng generation/TTS hiện tại |
| `iot` | Descriptor/state IoT legacy |
| `mcp` | Response/tool data từ MCP device |
| `server` | Message nội bộ/gateway |
| `ping` | Heartbeat |

Binary message được decode Opus một lần thành PCM để VAD và ASR dùng chung. Kết nối
qua MQTT gateway có binary envelope riêng và được tách trước khi decode.

## Hello và feature negotiation

Client hello có `audio_params` và `features`. Server cập nhật audio format/session params,
khởi tạo `MCPClient` nếu `mcp=true`, bật server-side AEC nếu `aec=true`, sau đó trả
`welcome_msg` có `session_id` và audio params.

Việc server ghi đè `welcome_msg.audio_params` bằng tham số client trong source tham
khảo cần được đánh giá kỹ: production nên validate format, sample rate, channels và
frame duration theo danh sách server hỗ trợ, không tin trực tiếp input.

## Audio đến text

```text
Opus bytes
  -> decode PCM
  -> VAD window
  -> gom utterance
  -> ASR
  -> correction/normalization
  -> voiceprint (tùy chọn)
  -> dialogue user message
```

VAD theo dõi activity time, last voice time và voice-stop. ASR audio/session variable
nằm trong `ConnectionHandler` để provider dùng chung không làm trộn state giữa device.
Streaming ASR và batch ASR có lifecycle khác nhau; adapter cần khai báo rõ reset/close.

## Text đến hành động/phản hồi

```text
recognized text
  -> wake-word shortcut (tùy chọn)
  -> exit command
  -> intent strategy
        -> no-intent: vào LLM
        -> intent LLM: phân loại trước
       -> function calling: tool schema trong LLM
  -> local plugin / MCP device tool / external MCP / IoT
  -> LLM response stream
  -> tách câu
  -> TTS
```

Tool có thể là plugin Python (`plugins_func`), device MCP, Home Assistant, search,
weather, music hoặc service ngoài. Mỗi tool cần timeout, cancellation, authorization và
output size limit; không đưa output tool chưa sanitize vào prompt/system command.

## Text đến audio

TTS có provider batch và streaming. Server gửi control message song song audio:

| Sự kiện | Tác dụng thiết bị |
| --- | --- |
| `tts/start` | Chuyển sang speaking |
| `tts/sentence_start` | Hiện subtitle hiện tại |
| Opus binary packets | Decode và playback |
| `tts/stop` | Kết thúc response |

`sentence_id` phân biệt lượt TTS và reset flow controller. Khi client abort, worker cần
dừng sinh câu/audio cũ và không gửi packet trễ vào lượt mới.

## Backpressure và latency budget

Những điểm cần đo riêng:

- Device frame -> server decode.
- VAD end-of-speech delay.
- ASR first/final token.
- LLM time-to-first-token.
- TTS time-to-first-audio.
- Queue/network jitter và device playback buffer.

Cần giới hạn queue theo byte/thời gian, drop theo policy rõ ràng và cancellation xuyên
suốt pipeline. Queue không giới hạn sẽ biến kết nối chậm thành memory leak.

## Audio primitives (M1.5 - Quyết định Veetee)

`veetee_server.audio` cung cấp các primitives được dùng bởi device gateway và sẽ được
dùng bởi pipeline M1.6 trở đi:

- **Bounded queue** (`BoundedAudioQueue`): giới hạn đồng thời theo item count, tổng bytes
  và tổng thời lượng (ms). Overflow policy:
  - `DROP_OLDEST`: ingress — đánh rơi item cũ nhất khi đầy.
  - `REJECT_NEW`: từ chối item mới (trả `False`).
  - `FAIL_SESSION`: egress — client chậm khiến queue đầy sẽ ném
    `SlowClientQueueOverflowError` để server đóng session.
  - Item quá lớn hơn capacity tổng bị drop/reject/raise tùy policy.
- **Generation filtering**: mỗi item mang generation của queue; `set_generation` purge
  ngay frame cũ (abort/barge-in), `get`/`drain` tự bỏ item stale.
- **Cancellation awareness**: `get` chờ trên `asyncio.Condition` và bị `CancelledError`
  sạch khi task bị hủy; `close()` đánh thức mọi waiter.
- **Packet pacer** (`PacketPacer`): paced downlink theo monotonic clock, không sleep âm,
  drift vượt `VEETEE_AUDIO_PACING_MAX_DRIFT_MS` thì reset anchor thay vì tích lũy;
  `reset()` được gọi khi abort để stream TTS kế tiếp không kế thừa drift. Sleep dưới
  1 microsecond được clamp về 0 tránh float no-op.
- **Codec boundaries**: fake encoder/decoder deterministic cho test; deferred native
  boundary raise khi chưa có libopus; resampler passthrough khi cùng format, cấm
  interpolation giả khi khác format.

Các settings áp dụng: `VEETEE_AUDIO_MAX_QUEUE_ITEMS`, `VEETEE_AUDIO_MAX_QUEUE_BYTES`,
`VEETEE_AUDIO_MAX_QUEUE_DURATION_MS`, `VEETEE_AUDIO_PACING_MAX_DRIFT_MS` (validator bảo
đảm duration >= 60ms và drift < duration).

## Fake AI pipeline (M1.6 - Quyết định Veetee)

`veetee_server.pipeline` là test harness deterministic chạy hoàn toàn in-process, không
phải adapter AI production. Gateway chỉ enqueue binary audio hợp lệ khi session ở
`LISTENING`; frame vẫn luôn được kiểm tra size và wire structure trước state gate để
malformed/oversized giữ nguyên close policy `1002`/`1009`.

Khi nhận `listen/stop`, pipeline drain các frame của turn hiện tại và chạy:

```text
FakeOpusDecoder -> FakeVAD -> FakeASR -> FakeLLM -> FakeTTS
  -> FakeOpusEncoder -> negotiated v1/v2/v3 framing -> bounded downlink
```

Luồng thành công phát đúng thứ tự `stt`, `tts/start`, `tts/sentence_start`, một hoặc
nhiều binary audio frame, rồi `tts/stop`. Không có speech hợp lệ thì turn được abort và
không phát output. Fake VAD dùng RMS và frame count; Fake ASR dùng fingerprint/default
text; Fake LLM chỉ tách câu; Fake TTS sinh PCM deterministic. Các thành phần này không
được dùng làm bằng chứng về chất lượng nhận dạng, giọng nói hoặc native Opus.

Mỗi turn giữ một queue generation cố định. `abort`, barge-in hoặc `listen/start` của turn
mới tăng generation đồng bộ cho ingress/downlink, purge item cũ và reset pacer. Pipeline
kiểm tra turn/generation trước từng event; sink không được gắn lại event cũ bằng generation
mới; sender kiểm tra lại ngay trước control/audio write. Downlink queue giới hạn theo item,
byte và duration, dùng `FAIL_SESSION` cho slow client.

Config fake pipeline: `VEETEE_PIPELINE_VAD_SPEECH_THRESHOLD`,
`VEETEE_PIPELINE_VAD_START_FRAMES`, `VEETEE_PIPELINE_VAD_END_SILENCE_FRAMES`,
`VEETEE_PIPELINE_MAX_UTTERANCE_FRAMES` và `VEETEE_PIPELINE_TTS_CHUNKS_PER_SENTENCE`.
`pipeline_max_utterance_frames` phải không nhỏ hơn `pipeline_vad_start_frames`.

## Silero VAD adapter (M2.1 - Quyết định Veetee)

`veetee_server.pipeline.vad` cung cấp VAD contract typed độc lập với transport và engine:

- **Contract typed**: `VADUtterance`, `VADSampleOffset`, `VADEndReason`, `SileroVADConfig` sử dụng sample offset và ms timestamp chuẩn hóa (16 kHz s16le mono).
- **Runtime application-scoped** (`SileroVADRuntime`): load model ONNX, warmup 1 lần tại startup lifespan khi `VEETEE_VAD_PROVIDER=silero_onnx`, quản lý readiness (`/readyz`) và shutdown giải phóng tài nguyên.
- **Per-stream isolation** (`SileroVADStream`): mỗi session/turn nhận 1 stream instance riêng chứa `_recurrent_state`, `_pcm_buffer`, `_pre_roll_buffer`, không chia sẻ mutable state giữa các session.
- **Rechunking & Remainder**: tự động gom/tách frame PCM có kích thước bất kỳ (như 960 samples / 60 ms Opus) sang window 512 samples (32 ms) để nạp vào Silero; remainder < 512 samples được giữ lại cho frame kế tiếp không gây đứt/trùng sample.
- **Boundary Detector**:
  - Hysteresis: `threshold` (0.5) và `neg_threshold` (0.35).
  - Pre-roll 80 ms (`pre_roll_ms=80`): tự động prepend audio buffer trước thời điểm bắt đầu nói vào `VADUtterance.pcm_data`.
  - Noise / Short burst rejection: đoạn nói ngắn dưới 250 ms (`min_speech_ms=250`) bị hủy và stream chuyển về trạng thái idle.
  - End silence 150 ms (`end_silence_ms=150`): xác định kết thúc nói khi im lặng đủ 150 ms.
  - Max utterance 12.000 ms (`max_utterance_ms=12000`): ngắt và chốt utterance khi nói liên tục vượt quá thời lượng tối đa.
- **Concurrency & Cancellation safety**:
  - Inference chạy bất đồng bộ qua thread pool executor để không block event loop.
  - Quản lý giới hạn đồng thời bằng `asyncio.Semaphore` (`VEETEE_VAD_MAX_CONCURRENCY=4`) và admission timeout (`VEETEE_VAD_ADMISSION_TIMEOUT_SECONDS=2.0`), ném `VADAdmissionTimeoutError` khi quá tải.
  - Khi coroutine bị hủy, permit chỉ được giải phóng sau khi native worker thực sự kết thúc; cancellation không dừng cưỡng bức ONNX thread. Kết quả suy luận chậm từ stream đã bị hủy/reset không được phát thành event.
- **Config typed**: `VEETEE_VAD_PROVIDER` (`fake` | `silero_onnx`), `VEETEE_VAD_MODEL_PATH`, `VEETEE_VAD_THRESHOLD`, `VEETEE_VAD_NEG_THRESHOLD`, `VEETEE_VAD_PRE_ROLL_MS`, `VEETEE_VAD_MIN_SPEECH_MS`, `VEETEE_VAD_END_SILENCE_MS`, `VEETEE_VAD_MAX_UTTERANCE_MS`, `VEETEE_VAD_MAX_CONCURRENCY`, `VEETEE_VAD_ADMISSION_TIMEOUT_SECONDS`.

Runtime dùng `numpy` và `onnxruntime` trực tiếp, không kéo Torch/Torchaudio vào server.
Model đã smoke-test là `silero_vad.onnx` từ package PyPI `silero-vad 6.2.1`, giấy phép
MIT, SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
Binary model không nằm trong repository; operator phải cung cấp artifact đã kiểm checksum
qua `VEETEE_VAD_MODEL_PATH`. Wrapper chỉ nhận contract ONNX v5 `input/state/sr` và giữ
64 context samples cùng recurrent state riêng cho từng stream.

Giới hạn M2.1: VAD thật hiện chạy trên batch đã thu sau `listen/stop`; inference trên
audio uplink liên tục, tự kết thúc lượt và barge-in realtime thuộc M2.6.

## PhoWhisper ASR adapter (M2.2 - Quyết định Veetee)

`veetee_server.pipeline.asr` cung cấp ASR contract typed và PhoWhisper runtime application-scoped:

- **Contract typed**: `ASRTranscribeRequest`, `ASRResult`, `ASRSegment`, `PhoWhisperConfig` với request nhận PCM 16 kHz s16le mono (tương thích `VADUtterance`), trả kết quả có `raw_text`, `normalized_text`, `language`, `duration_seconds`, `segments` và `provider_metadata`.
- **Privacy Policy**: Không log PCM bytes hay transcript text trong log mặc định để bảo vệ dữ liệu người dùng.
- **Normalization có kiểm soát**: `normalize_transcript` loại bỏ khoảng trắng thừa, chuẩn hóa khoảng trắng giữa các từ mà không tự động đổi từ hay sửa dấu câu sai lệch.
- **Runtime application-scoped** (`PhoWhisperRuntime`): Quản lý CTranslate2 engine (`faster-whisper`), thực hiện model load và warmup 0,5s audio im lặng một lần khi khởi động lifespan (`startup()`) nếu `VEETEE_ASR_PROVIDER=pho_whisper`, quản lý readiness (`/readyz`) và shutdown giải phóng tài nguyên.
- **Concurrency & Cancellation safety**:
  - Inference sync chạy trong thread pool executor (`max_workers=max_concurrency`), không làm block asyncio event loop.
  - Admission control qua `asyncio.Semaphore` (`VEETEE_ASR_MAX_CONCURRENCY=1`, đề xuất theo benchmark GPU serialized) và admission timeout (`VEETEE_ASR_ADMISSION_TIMEOUT_SECONDS=2.0`), ném `ASRAdmissionTimeoutError` khi hàng chờ quá hạn.
  - Total timeout (`VEETEE_ASR_TOTAL_TIMEOUT_SECONDS=10.0`), ném `ASRTimeoutError` khi thời gian xử lý tổng vượt quá ngưỡng.
  - Khi caller cancel coroutine, permit semaphore được giữ cho đến khi native compute thread thực sự kết thúc trong `finally` của background worker task, tránh rò rỉ permit/VRAM hay deadlock.
  - Lifecycle lock & state check ngăn ngừa race condition giữa `shutdown()` và request mới (ném `ASRNotReadyError`).
- **PCM Validation & Oversized Audio Rejection**:
  - Bắt buộc audio 16k s16le mono, độ dài byte chẵn.
  - Từ chối audio vượt thời lượng tối đa (`VEETEE_ASR_MAX_AUDIO_SECONDS=30.0`), ném `ASROversizedAudioError`.
  - Audio im lặng / biên độ 0 trả về kết quả rỗng typed `ASRResult(raw_text="", normalized_text="", provider_metadata={"silence": True})` ngay lập tức mà không chạy inference.
- **Config typed**: `VEETEE_ASR_PROVIDER` (`fake` | `pho_whisper`), `VEETEE_ASR_MODEL_ID` (mặc định `mad1999/pho-whisper-small-ct2`), `VEETEE_ASR_DEVICE` (`cuda`), `VEETEE_ASR_COMPUTE_TYPE` (`float16`), `VEETEE_ASR_MAX_CONCURRENCY`, `VEETEE_ASR_ADMISSION_TIMEOUT_SECONDS`, `VEETEE_ASR_TOTAL_TIMEOUT_SECONDS`, `VEETEE_ASR_MAX_AUDIO_SECONDS`, `VEETEE_ASR_LANGUAGE` (`vi`) và `VEETEE_ASR_LOCAL_FILES_ONLY` (mặc định `true`). Total timeout bao gồm cả thời gian chờ admission.

Giới hạn M2.2:
- Model thật (`mad1999/pho-whisper-small-ct2` / `medium`) cần artifact/cache local; mặc định server không tải model từ Hugging Face khi startup.
- Không kết luận WER/CER nếu không có bộ test audio có ground truth.

## OmniRoute Groq LLM adapter (M2.3 - Quyết định Veetee)

`veetee_server.pipeline.llm` cung cấp typed streaming contract và runtime HTTP
application-scoped. Model mặc định là `groq/openai/gpt-oss-120b` với
`reasoning_effort=low`; `groq/qwen/qwen3.6-27b` chỉ là candidate cấu hình được, không tự
fallback khi chưa có policy.

- Gửi OpenAI-compatible `POST /chat/completions` với `stream=true`; key chỉ đến từ
  `VEETEE_LLM_API_KEY` hoặc injected client trong test.
- Decoder SSE incremental hỗ trợ UTF-8/HTTP chunk bị cắt, comment, multi-line `data`,
  blank-line dispatch và `[DONE]`; giới hạn tổng byte response.
- Text delta và tool-call delta được typed; tool fragment merge theo `index`, usage và
  finish reason được giữ trong completion event. Reasoning content bị bỏ, không gửi TTS,
  history hoặc log.
- Admission, connect, first-output và total timeout là lỗi typed riêng. Cancellation đóng
  HTTP response trong `finally` và trả semaphore permit.
- HTTP `401/403`, `429` (`Retry-After` capped), `5xx`, malformed/oversized/empty response
  được normalize mà không log response body, prompt, tool arguments hoặc key.
- Circuit breaker chỉ tính lỗi transient, dùng monotonic cooldown và chỉ cho một probe
  half-open. Readiness fail-closed khi OmniRoute được bật nhưng runtime/key chưa sẵn sàng.

Tool call được parse nhưng chưa execute vì prompt/tool registry thuộc M3.

## Bộ ghép token và tách đoạn TTS (M2.4 - Quyết định Veetee)

`TTSTokenSegmenter` nhận text delta trực tiếp từ LLM và phát đoạn nói trước khi generation
kết thúc. Pipeline giữ tối đa hai đoạn chờ TTS; khi TTS chậm, backpressure dừng việc kéo
delta mới từ HTTP stream thay vì tăng bộ nhớ không giới hạn.

- Đoạn đầu dùng ngưỡng ngắn hơn để giảm first-audio latency; đoạn sau ưu tiên dấu câu và
  độ dài tự nhiên. `max_chars` là hard bound và `max_wait_seconds` flush câu không có dấu
  chấm ngay cả khi provider tạm ngừng gửi delta.
- Dấu câu trong số thập phân, ngày, viết tắt Việt/Anh, URL, quote và bracket không tạo
  điểm cắt sớm. Fragment giữa các delta vẫn dùng cùng state.
- Normalization chỉ áp dụng tại ranh giới TTS: bỏ Markdown/ký hiệu trực quan và emoji,
  giữ label của Markdown link, thay URL thô bằng cụm `liên kết`. Transcript và LLM event
  gốc không bị sửa.
- Các ngưỡng được cấu hình bằng `VEETEE_TTS_SEGMENT_FIRST_MIN_CHARS`,
  `VEETEE_TTS_SEGMENT_MIN_CHARS`, `VEETEE_TTS_SEGMENT_MAX_CHARS` và
  `VEETEE_TTS_SEGMENT_MAX_WAIT_SECONDS`; cấu hình không hợp lệ fail startup.
- Cancellation hủy đúng một pending read, đóng LLM stream và ngăn segment/chunk thuộc turn
  cũ. Lỗi provider được truyền lên supervisor, không bị đổi thành completion rỗng.

Giới hạn M2.4: TTS vẫn là deterministic fake provider. Native Gemini streaming, key pool,
resample và Opus production thuộc M2.5.

## Device simulator (M1.7 - Quyết định Veetee)

`veetee_server.simulator` là client contract độc lập ngoài `references/`. Simulator đọc
OTA/audio golden vectors trong `contracts/device`, có transport WebSocket local thật và
adapter TestClient cho integration test deterministic. Luồng demo xác minh hello, turn
fake-AI đầy đủ, binary downlink v1/v2/v3 và goodbye; OTA token luôn được redact khỏi log.

Contract suite bao phủ malformed vectors, nhiều session mở đồng thời và isolation,
reconnect với session ID mới, hello/idle timeout, slow-client overflow `1009`, active
session shutdown `1012` và namespace của OpenAPI. Simulator là bằng chứng protocol M1,
không thay thế `digital-human`, native Opus hoặc hardware E2E của M2.

## Cleanup và failure mode

- Device chưa bind: drop message và phát bind prompt theo interval.
- Provider chưa initialize: binary audio bị bỏ qua.
- Socket close: cancel task, đóng TTS/ASR và giải phóng executor/queue.
- Memory/reporting lỗi không được ngăn việc đóng socket.
- Provider timeout phải fail turn hiện tại và không treo event loop. M2.2 đánh dấu ASR
  runtime không ready sau total timeout vì native CTranslate2 worker không thể bị kill an
  toàn; process cần restart để nhận lượt mới. Error envelope chuyên biệt cho device chưa
  thuộc contract hiện tại.
- Reconnect tạo session mới; packet/result session cũ phải bị loại.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
