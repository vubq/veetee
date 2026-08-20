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
