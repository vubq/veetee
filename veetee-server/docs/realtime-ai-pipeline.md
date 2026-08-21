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
- Provider timeout phải chuyển thành lỗi có thể recover, không treo event loop.
- Reconnect tạo session mới; packet/result session cũ phải bị loại.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
