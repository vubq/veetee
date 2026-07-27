# voice-server

Hot path WebSocket/Opus và conversation engines. App này không phụ thuộc manager API cho mỗi audio frame; config được tải theo immutable snapshot/version.

Vertical slice hiện chạy thật tại `/veetee/v1/`:

```text
Opus -> Silero VAD -> Zipformer Vietnamese INT8 -> local admission
     -> structured planner/9Router -> streaming LLM -> VieNeu native/ONNX
     -> Opus
```

VieNeu đọc toàn bộ graph/codec từ `models/` và khởi động được với
`HF_HUB_OFFLINE=1`. Model được prewarm trước khi `/health/ready` trả `200`.
Button/wake word mở cùng assistant gate; `abort` tăng generation, hủy provider
scope và loại output cũ. Inactivity timeout synthesize goodbye từ config rồi sleep.
WebSocket handshake xác thực `Device-Id` + device token qua Manager một lần khi mở
session; sau đó audio hot path dùng immutable config snapshot đã cache, không gọi
Manager theo từng frame.

Chạy local:

```bash
cp .env.example .env
npm run models:prepare
npm run dev:voice
```

Clone mới dùng `VEETEE_TTS_BACKEND=onnx`. Host đã benchmark có thể chạy
`npm run models:prepare-native`, build thư viện VieNeu-TTS.cpp đã pin và đặt
`VEETEE_TTS_BACKEND=native`; voice-server sẽ fail readiness nếu model hoặc shared
library không đủ thay vì âm thầm đổi backend.

VieNeu gom các câu ngắn hoàn chỉnh thành natural batch để không tách cụm từ hoặc
restart TTS quá thường xuyên. Baseline ONNX giữ natural cap 160 ký tự và chỉ dùng
emergency bound 256 cho output bất thường không có dấu câu; native giữ 72/72. A/B
first-160/steady-256 giảm inference starts nhưng làm aggregate RTF xấu hơn nên không
được rollout. Câu cuối không dấu vẫn được đọc khi LLM hoàn tất.
`VEETEE_TTS_STYLE=tu_nhien` là style hội thoại mặc
định; agent có thể chọn riêng `doc_truyen` hoặc `tin_tuc`. Tempo từ agent được áp
dụng đúng, còn runtime log headroom và cảnh báo quality khi profile quá nhanh.

Nếu 9Router bật `Require API key`, đặt key riêng của app trong
`VEETEE_9ROUTER_API_KEY`; không commit key và không đưa key xuống firmware.

Conversation mặc định là `mode=auto`: button/wake word chỉ mở assistant gate; VAD tự finalize, admission gate quyết định có gọi LLM/MCP, inactivity timeout phát goodbye rồi sleep.

## Web Device Simulator

Manager Web mở pipeline thật qua `ws://<voice-host>:8000/veetee/lab/v1/`. Manager
API cấp token JWT dùng một lần và voice-server consume token trước khi tạo session.
Các biến cấu hình chính:

```dotenv
VEETEE_LAB_WEBSOCKET_PATH=/veetee/lab/v1/
VEETEE_LAB_ALLOWED_ORIGINS=http://127.0.0.1:8081,https://veetee-dev.example.ts.net
VEETEE_LAB_MAX_SESSIONS=4
```

- Text bypass VAD/ASR có event công khai; admission/LLM/MCP/VieNeu vẫn là thật.
- Audio Replay/Live Mic gửi PCM16 mono 16 kHz qua Silero và Zipformer thật.
- PCM downlink chỉ phục vụ browser playback; không đo Opus, AEC hay speaker ESP32.
- Mobile browser phải unlock/resume `AudioContext` bằng user gesture trước PCM đầu;
  banner/nút `Bật âm thanh` là recovery path, không phải trạng thái thành công.
- Live Mic trên LAN HTTP thường không có `getUserMedia`; dùng HTTPS/localhost hoặc
  Audio Replay.
- `lab_playback_schedule_summary` là timeline estimate. Device
  `tts.paced_sender_summary` tách queue starvation và scheduler lateness; cả hai đều
  không phải measured speaker underrun.
