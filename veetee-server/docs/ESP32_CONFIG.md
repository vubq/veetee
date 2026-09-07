# Hướng Dẫn Cấu Hình Thiết Bị ESP32 (ESP32 Hardware Configuration)

Tài liệu này hướng dẫn cách kết nối phần cứng ESP32 tới server cục bộ **VeeTee Server**.

---

## 1. Phương Thức 1: Cấu hình qua OTA URL (Khuyến nghị)

Firmware ESP32 hỗ trợ tự động lấy cấu hình WebSocket từ endpoint OTA khi khởi động.

1. **Địa chỉ OTA Endpoint:**
   `http://<IP_MAY_TINH>:8003/ota/`
   *(Ví dụ: `http://192.168.1.100:8003/ota/`)*

2. **Cách cấu hình trên ESP32:**
   - Khi thiết bị phát Wi-Fi AP cấu hình (hoặc qua trang Web config của ESP32):
     - Mục **OTA URL** hoặc **Server URL**: Nhập `http://<IP_MAY_TINH>:8003/ota/`.
   - Thiết bị sẽ gửi request `POST /ota/` và nhận về JSON chứa WebSocket URL:
     ```json
     {
       "websocket": {
         "url": "ws://<IP_MAY_TINH>:8000/",
         "version": 1
       },
       "server_time": {
         "timestamp": 1788759000000,
         "timezone_offset": 420
       }
     }
     ```
   - Thiết bị sẽ tự động chuyển sang kết nối WebSocket `ws://<IP_MAY_TINH>:8000/`.

---

## 2. Phương Thức 2: Cấu hình trực tiếp WebSocket URL

Nếu firmware của bạn hỗ trợ nhập trực tiếp URL WebSocket:

- **WebSocket URL:** `ws://<IP_MAY_TINH>:8000/`
- **Protocol Version:** `1` (hoặc `2`, `3`)
- **Audio Format:** Opus (Sample rate: 16000 Input, 24000 Output, Frame duration: 60ms)

---

## 3. Quy Trình Vận Hành Trên Thiết Bị

1. **Khởi động:**
   - ESP32 kết nối Wi-Fi -> Kết nối tới `ws://<IP_MAY_TINH>:8000/`.
   - Gửi tin nhắn `hello`. Server phản hồi `hello` kèm `session_id`.

2. **Trò chuyện (Voice Interaction):**
   - Người dùng gọi từ khóa đánh thức (Wake word) hoặc bấm nút.
   - Thiết bị gửi `{"type": "listen", "state": "start"}` và bắt đầu stream Opus audio.
   - Khi người dùng dừng nói, thiết bị gửi `{"type": "listen", "state": "stop"}` (hoặc server tự động ngắt bằng VAD).
   - Server gửi `stt` -> Màn hình hiển thị câu hỏi.
   - Server gửi `llm` -> Màn hình hiển thị biểu cảm (emoji).
   - Server gửi `tts` -> Loa thiết bị phát âm thanh phản hồi bằng tiếng Việt với độ trễ < 1 giây.

3. **Ngắt lời (Barge-in):**
   - Khi loa đang phát câu trả lời, nếu người dùng gọi lại Wake word hoặc bấm nút -> Thiết bị gửi `{"type": "abort"}` -> Server lập tức dừng phát âm thanh và lắng nghe câu hỏi mới.
