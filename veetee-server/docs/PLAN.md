# Kiến Trúc Hệ Thống & Kế Hoạch Thiết Kế (System Architecture & Plan)

Dự án **VeeTee Realtime Server** (Local Backend) được thiết kế chuyên biệt cho các thiết bị AI phần cứng (ESP32-S3, ESP32-C3, ESP32-P4) và Web Client sử dụng giao thức chuẩn WebSocket Realtime với tiêu chí **Realtime, Streaming, phản hồi < 1s**.

---

## 1. Mục Tiêu Kỹ Thuật (Key Objectives)

- **Giao thức chuẩn tương thích 100%:** Tương thích hoàn toàn với firmware ESP32 qua WebSocket (V1/V2/V3) và OTA HTTP.
- **ASR Realtime:** Deepgram Nova-2 streaming WebSocket (độ trễ nhận diện ~100-200ms).
- **LLM Siêu Tốc:** Qwen 3.6 27B chạy qua Omniroute / Groq API (độ trễ first token ~150-250ms).
- **TTS Cục Bộ Tiếng Việt:** Vieneu-TTS v3 Turbo chạy trực tiếp trên GPU máy tính (độ trễ first audio chunk ~350-450ms).
- **Độ trễ toàn trình (End-to-End Latency):** < 1.0 giây từ lúc người dùng dứt lời đến khi thiết bị bắt đầu phát âm thanh.
- **Barge-in / Ngắt lời tức thì:** Thiết bị hoặc server phát hiện giọng nói mới -> tự động cancel pipeline đang phát và chuyển sang lắng nghe.
- **Cài đặt Local thuần (Non-Docker):** Chạy trên Python Virtual Environment, tận dụng trực tiếp GPU NVIDIA (GTX 1650 Ti / RTX).

---

## 2. Sơ Đồ Luồng Dữ Liệu Realtime (Streaming Pipeline Dataflow)

```
+---------------------------------------------------------------------------------------------------+
|                                        THIẾT BỊ ESP32 / WEB CLIENT                                 |
+---------------------------------------------------------------------------------------------------+
               | (1) Thu âm Mic (16kHz 16-bit Mono) -> Nén Opus 60ms -> WS Binary Frame
               v
+---------------------------------------------------------------------------------------------------+
|                                       VEETEE WEBSOCKET SERVER                                     |
|                                                                                                   |
|   1. Opus Decoder 16k:                                                                            |
|      - Giải nén khung Opus -> 16kHz 16-bit PCM                                                    |
|                                                                                                   |
|   2. ASR Streamer (Deepgram Nova-2 WebSocket):                                                    |
|      - Truyền luồng PCM liên tục -> VAD & Transcribe trực tiếp                                    |
|      - Khi speech_final = True -> Nhận diện câu hoàn chỉnh                                        |
|      - Gửi STT JSON về Client để hiển thị text lên màn hình:                                      |
|        {"type": "stt", "text": "Hà Nội là thủ đô của nước nào?"}                                 |
|                                                                                                   |
|   3. Multi-turn Dialogue Manager:                                                                 |
|      - Lưu ngữ cảnh hội thoại vào lịch sử                                                         |
|                                                                                                   |
|   4. Streaming LLM Engine (Omniroute / Groq Qwen 3.6 27B):                                        |
|      - Stream token với `reasoning_format: hidden` (loại bỏ think overhead)                       |
|      - Trích xuất cảm xúc [happy]/[neutral]/... -> Gửi LLM emotion JSON                           |
|      - Speech Segment Splitter: Cắt nhỏ câu thành từng mệnh đề ngay khi gặp dấu câu               |
|                                                                                                   |
|   5. Local Neural TTS Engine (Vieneu-TTS v3 Turbo on PyTorch GPU):                                |
|      - Đưa từng mệnh đề vào `infer_stream()`                                                      |
|      - Chuyển đổi 48kHz float32 -> 24kHz 16-bit PCM siêu tốc với Soxr                             |
|      - Nén thành từng khung Opus 24kHz (60ms = 1440 samples)                                      |
|                                                                                                   |
|   6. Audio Streamer & Pacing:                                                                     |
|      - Gửi `{"type": "tts", "state": "start"}`                                                    |
|      - Gửi `{"type": "tts", "state": "sentence_start", "text": "..."}`                           |
|      - Truyền các gói Opus binary frame tới ESP32 theo thời gian thực                             |
|      - Gửi `{"type": "tts", "state": "stop"}` khi hoàn tất                                       |
+---------------------------------------------------------------------------------------------------+
               | (7) Nhận khung Opus 24kHz -> Giải mã I2S -> Phát ra Loa
               v
+---------------------------------------------------------------------------------------------------+
|                                          LOA THIẾT BỊ PHÁT TIẾNG                                  |
+---------------------------------------------------------------------------------------------------+
```

---

## 3. Phân Rã Thời Gian Độ Trễ (Latency Breakdown Budget)

| Thành phần | Công nghệ | Thời gian xử lý |
| :--- | :--- | :--- |
| **ASR Endpointing & VAD** | Deepgram Nova-2 Live | ~150 - 250 ms |
| **LLM Time-to-First-Token** | Groq Qwen 3.6 27B | ~150 - 200 ms |
| **TTS Time-to-First-Audio** | Vieneu-TTS v3 Turbo GPU | ~350 - 450 ms |
| **Network & WebSocket Overhead**| Local LAN / WebSocket | ~10 - 30 ms |
| **TỔNG ĐỘ TRỄ PHẢN HỒI (TTFA)**| **Toàn trình** | **~650 - 950 ms (< 1s)** |

---

## 4. Xử Lý Ngắt Lời (Barge-in / Interruption Strategy)

1. **Từ phía Thiết bị:**
   - Khi người dùng bấm nút nói hoặc wake word được kích hoạt trong lúc AI đang nói -> ESP32 gửi tin nhắn `{"type": "abort", "reason": "wake_word_detected"}` hoặc `{"type": "listen", "state": "start"}`.
   - Server lập tức kích hoạt `cancel_event`, hủy `asyncio.Task` đang gọi TTS/LLM, giải phóng hàng đợi âm thanh và gửi `{"type": "tts", "state": "stop"}`.
2. **Từ phía Server (Server-side VAD):**
   - Khi Deepgram phát hiện sự kiện `SpeechStarted` trong khi session đang ở trạng thái `SPEAKING`, Server tự động hủy phát âm thanh hiện tại và chuyển về trạng thái `LISTENING`.

---

## 5. Thiết Kế Module Hóa (Clean OOP Architecture)

- **`config/settings.py`**: Quản lý cấu hình kiểu Dataclass, nạp từ `config.yaml` và biến môi trường.
- **`core/protocol.py`**: Mã hóa/giải mã toàn bộ định dạng gói tin (V1 raw opus, V2/V3 packed headers).
- **`core/audio_utils.py`**: Quản lý Opuslib encoder/decoder 16k & 24k, bộ đệm khung âm thanh, và thư viện resample Soxr.
- **`core/dialogue.py`**: Quản lý bộ nhớ ngữ cảnh nhiều lượt (Multi-turn dialogue).
- **`core/providers/asr/`**: Lớp trừu tượng `BaseASR` và triển khai `DeepgramStreamASR`.
- **`core/providers/llm/`**: Lớp trừu tượng `BaseLLM` và triển khai `OmnirouteGroqLLM`.
- **`core/providers/tts/`**: Lớp trừu tượng `BaseTTS` và triển khai `VieneuLocalTTS`.
- **`core/session.py`**: State machine điều phối phiên làm việc của từng client kết nối.
- **`http_server.py`**: HTTP server phục vụ cấu hình OTA (`/ota/`), health check, và Web UI.
- **`server.py`**: Main runner điều phối toàn bộ dịch vụ.
