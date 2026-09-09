# Kết nối ESP32 / Xiaozhi firmware nguyên bản

Cập nhật: **2026-09-09**

VeeTee hỗ trợ đường server mặc định với firmware Xiaozhi đang có trên thiết bị. Không cần sửa source FW, thêm capability VeeTee, áp patch, chạy `idf.py` hay flash firmware tùy biến.

## 1. Endpoint

OTA:

```text
http://<server>:8003/ota/
```

OTA trả WebSocket endpoint, ví dụ:

```json
{
  "websocket": {
    "url": "ws://<server>:8000/",
    "version": 1
  }
}
```

Có thể cấu hình WebSocket trực tiếp nếu firmware/build đang dùng hỗ trợ tùy chọn đó:

```text
ws://<server>:8000/
```

Baseline audio stock: mic Opus 16 kHz mono, speaker Opus 24 kHz mono, baseline frame 60 ms. Binary details nằm ở [API_PROTOCOL.md](API_PROTOCOL.md).

## 2. Hello, MCP và AEC

Server chấp nhận hello stock không có `features`, hoặc có capability firmware hỗ trợ như `mcp`/`aec`.

Không thêm `device_aec` chỉ để dùng VeeTee. `features.aec=true` liên quan profile AEC của firmware nhưng không chứng minh echo đã được loại đủ tốt cho automatic speech barge-in; VeeTee hiện chưa có server-side AEC hoàn chỉnh.

Policy mặc định:

```text
server.barge_in_policy = client_only
```

## 3. Wake/listen và AI turn

Nếu firmware gửi stock event:

```json
{"type":"listen","state":"detect","text":"VeeTee ơi"}
```

server giữ text thật và có thể đưa vào AI. Nếu build hiện tại không gửi `listen:detect`, mic/ASR vẫn hoạt động; VeeTee không yêu cầu patch firmware để tạo event mới.

Kết thúc hội thoại, memory, confirmation và tool selection do AI xử lý phía server. Các config legacy như `wake_words`/`exit_commands` không phải matcher semantic bắt buộc cho đường hiện hành.

## 4. Interrupt stock

Khi firmware chủ động gửi:

```json
{"type":"abort","reason":"wake_word_detected"}
```

hoặc:

```json
{"type":"listen","state":"start","mode":"realtime"}
```

server cancel turn hiện tại, invalidate capture cũ, ngừng gửi audio mới và gửi `tts:stop` chuẩn. Không cần firmware hiểu `interrupt=true`.

Do stock protocol không có playback flush ACK chung, loa có thể còn một ít audio đã nằm trong decoder/playback queue. Thời điểm loa thật dừng phải đo trên board.

## 5. Smoke test nhanh

1. Mở OTA/WS và xác nhận server nhận hello.
2. Nói một câu và xác nhận mic -> ASR -> AI -> TTS -> loa.
3. Nếu có `listen:detect`, ghi lại event/text thực tế.
4. Nếu board có nút/wake word tạo `abort`/`listen:start`, thử ngắt khi robot đang nói.
5. Xác nhận lượt tiếp theo không nhận stale transcript/audio.

Checklist hardware đầy đủ, số lần lặp và metric p50/p95 nằm trong [TESTING.md](TESTING.md). Trạng thái evidence nằm trong [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md).

## 6. Patch lịch sử

`patches/xiaozhi-esp32-barge-in.patch` là artifact thử nghiệm cũ. Không áp patch này cho đường hỗ trợ mặc định; giữ file chỉ để truy vết lịch sử.
