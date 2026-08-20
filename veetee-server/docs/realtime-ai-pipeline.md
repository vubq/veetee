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
