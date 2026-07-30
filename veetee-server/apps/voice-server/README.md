# voice-server

Hot path WebSocket/Opus và conversation engines. App này không phụ thuộc manager API cho mỗi audio frame; config được tải theo immutable snapshot/version.

Vertical slice hiện chạy thật tại `/veetee/v1/`:

```text
Opus -> Silero VAD -> Zipformer Vietnamese INT8 -> local admission
     -> structured planner/CLIProxyAPI -> streaming LLM -> VieNeu native/ONNX
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

Chạy `npm run env:voice:sync` để lấy CLIProxyAPI client key từ trusted local config
vào ignored Voice `.env` mode `0600`. Không đặt key trên command line, không commit key
và không đưa key xuống firmware. Lệnh sync giữ `OPENBLAS_NUM_THREADS=1`; bare
`dev:voice`, local E2E và benchmark cũng pin giá trị đó trước khi Python khởi động. Đây
là cap process-wide cho NumPy/OpenBLAS; `VEETEE_TTS_THREADS=2` chỉ giới hạn ONNX Runtime
nên không thay thế được cap này. 9Router
đang tạm dừng. Quy trình restart, kiểm tra effective env và soak 5--10 phút nằm ở
`../../docs/21-local-development-runbook.md`.

Conversation mặc định là `mode=auto`: button/wake word chỉ mở assistant gate; VAD tự finalize, admission gate quyết định có gọi LLM/MCP, inactivity timeout phát goodbye rồi sleep.

## YouTube Music

`VEETEE_MEDIA_PROVIDER=youtube_music` publish server tool `media.play` cho cả ESP32 và
Realtime Lab. Planner AI chọn structured `specific_track` hoặc `any_track`; code không
dò keyword/câu nói. `specific_track` cần đủ title + artist, còn `any_track` cần query do
AI tạo từ context. Tool không nhận URL. Adapter dùng `yt-dlp` đã pin để resolve một
provider item ID rồi stream qua FFmpeg thành PCM 24 kHz mono trong cùng turn/generation;
button/abort đóng downloader và decoder.

FFmpeg là host dependency. Kiểm tra read-only trước khi start hoặc sau khi nâng `yt-dlp`:

```bash
command -v ffmpeg
npm run media:probe:youtube -- --title "<tên bài>" --artist "<nghệ sĩ>" --decode-seconds 5
```

Probe không ghi audio ra file. Adapter không dùng cookie hoặc bypass DRM/private/
age-restricted content mặc định. Anonymous YouTube có thể trả `429`/bot challenge sau
nhiều request. Production có thể đặt `VEETEE_MEDIA_YOUTUBE_COOKIE_FILE` tới Netscape
cookie export mode `0600` của một tài khoản riêng cho thiết bị; không trỏ vào browser
profile cá nhân, không commit/sync cookie và không log path/content. Cookie không được
dùng để bypass DRM/private/age restriction. Availability vẫn phụ thuộc YouTube và có
thể cần cập nhật extractor sau thay đổi upstream. Đặt
`VEETEE_MEDIA_PROVIDER=disabled` để bỏ tool khỏi catalog.

Lần sync đầu có thể truyền `VEETEE_MEDIA_YOUTUBE_COOKIE_FILE` cho
`npm run env:voice:sync`; lần sau script giữ giá trị không rỗng trong Voice `.env`
ignored, còn process environment có quyền override. Adapter ưu tiên HLS có audio trước
audio-only fallback và dùng FFmpeg downloader qua stdout để không giữ fragment tạm;
selector này không nằm trong schema AI. Abort phải reap cả yt-dlp và FFmpeg, không còn
PCM sau `abort.complete`.

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
