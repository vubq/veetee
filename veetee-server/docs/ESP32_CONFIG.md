# Hướng Dẫn Kết Nối ESP32 / Xiaozhi

Tài liệu này mô tả cấu hình ESP32 để kết nối VeeTee Server và các yêu cầu cần có để test realtime barge-in an toàn.

## 1. Kết nối server

### Qua OTA URL

Cấu hình firmware dùng:

```text
http://<IP_MAY_TINH>:8003/ota/
```

Server trả về WebSocket endpoint, ví dụ:

```json
{
  "websocket": {
    "url": "ws://192.168.1.100:8000/",
    "version": 1
  }
}
```

### Kết nối WebSocket trực tiếp

```text
ws://<IP_MAY_TINH>:8000/
```

Audio mặc định:

- Mic -> Server: Opus 16 kHz mono.
- Server -> Speaker: Opus 24 kHz mono.
- Frame duration mặc định: 60 ms.

## 2. Realtime mode và AEC

Để server tự động nhận giọng người dùng khi robot đang nói, firmware phải chạy `mode=realtime` và **device-side AEC phải thực sự hoạt động**.

Hello từ firmware cần quảng bá đúng capability:

```json
{
  "type": "hello",
  "features": {
    "device_aec": true,
    "mcp": true
  }
}
```

Quy ước:

- `device_aec=true`: AEC chạy trên ESP32/codec path của thiết bị. VeeTee cho phép automatic realtime barge-in.
- `aec=true`: firmware yêu cầu AEC phía server. VeeTee hiện chưa có server-side AEC, nên automatic realtime barge-in sẽ vẫn bị echo guard chặn.

Nếu board không hỗ trợ device-side AEC, vẫn có thể dùng `auto/manual` và ngắt robot bằng wake word/nút/`abort`; không nên bật realtime VAD barge-in tự động vì tiếng loa có thể tự kích hoạt ASR.

## 3. Semantics `tts:stop` bắt buộc cho firmware tham khảo

VeeTee phân biệt hai kiểu stop:

Kết thúc bình thường:

```json
{"type":"tts","state":"stop"}
```

Firmware để audio đã đệm phát hết nhằm giữ đủ đuôi câu.

Ngắt lời thật sự:

```json
{"type":"tts","state":"stop","interrupt":true}
```

Firmware phải bỏ audio cũ ngay. Patch Xiaozhi tham khảo xử lý `interrupt=true` bằng:

```cpp
audio_service_.ResetDecoder();
```

trước khi chuyển state.

Patch được lưu tại:

```text
veetee-server/patches/xiaozhi-esp32-barge-in.patch
```

Áp dụng từ root repo `xiaozhi-esp32`:

```bash
git apply /path/to/veetee/veetee-server/patches/xiaozhi-esp32-barge-in.patch
```

## 4. Luồng test ESP32 đề xuất

1. Build/flash firmware có patch.
2. Boot thiết bị và kiểm tra log hello có `device_aec=true` khi chọn device-side AEC.
3. Cho robot trả lời một câu dài, rồi nói chen ở giữa câu.
4. Xác nhận loa dừng gần như ngay lập tức và audio cũ không phát tiếp.
5. Xác nhận câu nói chen được ASR nhận đầy đủ và tạo turn mới.
6. Lặp lại khi robot nói lớn, nhỏ và ở các khoảng cách mic khác nhau để bắt false trigger do echo.
7. Test một lượt nói bình thường không barge-in để chắc `tts:stop` thường không cắt mất âm cuối.
8. Test `abort`/wake word riêng để xác nhận state server quay về listening đúng cách.

## 5. Tiêu chí đạt trước khi bật mặc định

- Không có self-barge-in do tiếng loa trong điều kiện sử dụng bình thường.
- `interrupt=true` xóa playback buffer ổn định.
- Normal `tts:stop` vẫn phát đủ câu.
- User speech sau barge-in không bị echo guard nuốt mất.
- Không treo session khi ngắt đúng lúc queue LLM -> TTS đang đầy.
- Có số đo latency và false-trigger trên board thật.

Theo dõi trạng thái tại [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md).
