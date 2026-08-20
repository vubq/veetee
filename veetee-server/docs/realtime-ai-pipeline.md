# Realtime AI pipeline

## Ket noi va routing

Khi co WebSocket connection, `ConnectionHandler.handle_connection()`:

1. Lay event loop va request headers.
2. Xac dinh client IP va `device-id`.
3. Phat hien ket noi tu MQTT gateway qua query string.
4. Khoi dong task timeout va AEC cache cleanup.
5. Khoi tao config/provider rieng o background.
6. Lap `async for` nhan text hoac bytes.
7. Save memory/title va cleanup khi dong.

Message text duoc dispatch theo `type`. Registry hien co:

| Type | Handler semantic |
| --- | --- |
| `hello` | Audio params, feature MCP/AEC, hello response |
| `listen` | Start/stop/detect va listen mode |
| `abort` | Dung generation/TTS hien tai |
| `iot` | Descriptor/state IoT legacy |
| `mcp` | Response/tool data tu MCP device |
| `server` | Message noi bo/gateway |
| `ping` | Heartbeat |

Binary message duoc decode Opus mot lan thanh PCM de VAD va ASR dung chung. Ket noi
qua MQTT gateway co binary envelope rieng va duoc tach truoc khi decode.

## Hello va feature negotiation

Client hello co `audio_params` va `features`. Server cap nhat audio format/session params,
khoi tao `MCPClient` neu `mcp=true`, bat server-side AEC neu `aec=true`, sau do tra
`welcome_msg` co `session_id` va audio params.

Viec server ghi de `welcome_msg.audio_params` bang tham so client trong source tham
khao can duoc danh gia ky: production nen validate format, sample rate, channels va
frame duration theo danh sach server ho tro, khong tin truc tiep input.

## Audio den text

```text
Opus bytes
  -> decode PCM
  -> VAD window
  -> gom utterance
  -> ASR
  -> correction/normalization
  -> voiceprint (tuy chon)
  -> dialogue user message
```

VAD theo doi activity time, last voice time va voice-stop. ASR audio/session variable
nam trong `ConnectionHandler` de provider dung chung khong lam tron state giua device.
Streaming ASR va batch ASR co lifecycle khac nhau; adapter can khai bao ro reset/close.

## Text den hanh dong/phan hoi

```text
recognized text
  -> wake-word shortcut (tuy chon)
  -> exit command
  -> intent strategy
       -> no-intent: vao LLM
       -> intent LLM: phan loai truoc
       -> function calling: tool schema trong LLM
  -> local plugin / MCP device tool / external MCP / IoT
  -> LLM response stream
  -> tach cau
  -> TTS
```

Tool co the la plugin Python (`plugins_func`), device MCP, Home Assistant, search,
weather, music hoac service ngoai. Moi tool can timeout, cancellation, authorization va
output size limit; khong dua output tool chua sanitize vao prompt/system command.

## Text den audio

TTS co provider batch va streaming. Server gui control message song song audio:

| Su kien | Tac dung thiet bi |
| --- | --- |
| `tts/start` | Chuyen sang speaking |
| `tts/sentence_start` | Hien subtitle hien tai |
| Opus binary packets | Decode va playback |
| `tts/stop` | Ket thuc response |

`sentence_id` phan biet luot TTS va reset flow controller. Khi client abort, worker can
dung sinh cau/audio cu va khong gui packet tre vao luot moi.

## Backpressure va latency budget

Nhung diem can do rieng:

- Device frame -> server decode.
- VAD end-of-speech delay.
- ASR first/final token.
- LLM time-to-first-token.
- TTS time-to-first-audio.
- Queue/network jitter va device playback buffer.

Can gioi han queue theo byte/thoi gian, drop theo policy ro rang va cancellation xuyen
suot pipeline. Queue khong gioi han se bien ket noi cham thanh memory leak.

## Cleanup va failure mode

- Device chua bind: drop message va phat bind prompt theo interval.
- Provider chua initialize: binary audio bi bo qua.
- Socket close: cancel task, dong TTS/ASR va giai phong executor/queue.
- Memory/reporting loi khong duoc ngan viec dong socket.
- Provider timeout phai chuyen thanh loi co the recover, khong treo event loop.
- Reconnect tao session moi; packet/result session cu phai bi loai.

## Source doi chieu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
