# Hướng Dẫn Kết Nối ESP32 / Xiaozhi

VeeTee Server hiện được thiết kế để dùng với **firmware Xiaozhi nguyên bản đang có trên thiết bị**. Không cần sửa source FW, thêm field hello, áp patch, chạy `idf.py` hay flash firmware tùy biến để dùng đường hỗ trợ mặc định.

## 1. Kết nối server

Qua OTA URL:

```text
http://<IP_MAY_TINH>:8003/ota/
```

Server trả WebSocket endpoint, ví dụ:

```json
{
  "websocket": {
    "url": "ws://192.168.1.100:8000/",
    "version": 1
  }
}
```

Hoặc cấu hình WebSocket trực tiếp:

```text
ws://<IP_MAY_TINH>:8000/
```

Baseline audio stock:

- Mic -> server: Opus 16 kHz mono.
- Server -> speaker: Opus 24 kHz mono.
- Frame duration: 60 ms.

## 2. Hello và AEC

Không cần thêm `device_aec` vào firmware. Server chấp nhận hello stock khi:

- không có `features`;
- có `features.mcp=true`;
- có `features.aec=true` theo cấu hình firmware.

`features.aec=true` không chứng minh device-side AEC đang loại echo đủ tốt cho barge-in. Trong source stock, cờ này liên quan hướng server-side AEC; VeeTee hiện chưa triển khai server-side AEC hoàn chỉnh.

Vì vậy policy mặc định là:

```text
server.barge_in_policy = client_only
```

Robot chỉ bị server ngắt turn khi client chủ động gửi message chuẩn như `abort` hoặc `listen:start`. Automatic speech-start barge-in trong lúc loa đang phát được giữ tắt mặc định.

## 3. Wake, greeting và kết thúc do AI xử lý, không cần sửa FW

Tính năng hội thoại tự nhiên chạy hoàn toàn phía server khi bật `conversation.enabled=true`:

- `listen:detect` có text -> giữ nguyên text và đưa vào AI sau cửa sổ phối hợp `listen:start`;
- `text`/`chat`/ASR final -> cùng đi qua AI theo history/context, không match exit phrase local;
- idle timeout -> tạo tối đa một AI semantic evaluation mỗi inactivity epoch; AI quyết định tiếp tục chờ hay kết thúc logical conversation.

Điều kiện duy nhất cho wake greeting là firmware **đang dùng vốn đã gửi** event stock dạng:

```json
{"type":"listen","state":"detect","text":"VeeTee ơi"}
```

Một số build/config stock có thể chỉ phát âm báo wake và vào listening mà không gửi `listen:detect`. Trong trường hợp đó, VeeTee vẫn hội thoại bình thường qua mic/ASR nhưng không có text wake event để AI phản hồi riêng. Không cần patch/build FW để dùng server.

Không khai báo server-side AEC chỉ để bật greeting. Semantic routing độc lập với AEC/barge-in. Các field `wake_words` và `exit_commands` trong config chỉ còn là legacy/inert compatibility data, không quyết định intent.

## 4. Ngắt robot bằng firmware stock

Khi board/wake word/nút của firmware stock gửi:

```json
{"type":"abort","reason":"wake_word_detected"}
```

hoặc explicit:

```json
{"type":"listen","state":"start","mode":"realtime"}
```

server sẽ:

1. hủy turn LLM/TTS hiện tại;
2. invalidate ASR/capture cũ để transcript đến trễ không tạo turn mới;
3. ngừng gửi binary audio cũ;
4. gửi `{"type":"tts","state":"stop"}` chuẩn;
5. cập nhật state theo listening mode client đang dùng.

Không cần firmware hiểu `interrupt=true`.

## 5. Vì sao vẫn có thể còn một ít audio sau khi ngắt

Firmware stock có decoder/playback queue riêng và protocol không cung cấp flush ACK chung cho server. Server không biết chính xác còn bao nhiêu audio đã nằm phía thiết bị.

VeeTee giảm phần này bằng `AudioPacer`:

```text
tts.send_ahead_ms = 120
```

Với frame 60 ms, server mặc định chỉ gửi trước khoảng 2 frame theo playback estimate. Khi có abort, server ngừng gửi mới ngay; lượng audio đã nằm trong mạng/decoder/playback queue vẫn phải đo trên thiết bị thật.

Không nên giảm send-ahead quá thấp trước khi test vì có thể gây underrun/ngắt tiếng trên Wi-Fi kém ổn định.

## 6. Checklist test board stock FW

Không build/flash FW mới. Dùng đúng firmware đang có trên board và ghi lại board/model, version/config nếu xem được.

1. Kết nối OTA/WS, xác nhận server nhận hello.
2. Test mic -> ASR -> LLM -> TTS -> loa ít nhất vài lượt liên tiếp.
3. Nếu firmware có `listen:detect`, ghi lại **text thực tế**; test detect -> AI greeting/response -> câu hỏi tiếp theo. Không cần thêm text vào matcher server.
4. Tắt greeting rồi nói ngay sau wake để xác nhận không có VAD block cố định làm mất câu.
5. Test nhiều cách nói kết thúc, câu trích dẫn “tạm biệt” và câu hỏi chứa từ đó để xác nhận AI quyết định theo ngữ cảnh.
6. Test idle timeout, gồm trường hợp AI chọn continue, AI chọn end và user nói sát deadline.
7. Test câu dài và nghe hết bình thường để xác nhận đuôi câu không bị cắt.
8. Trong lúc robot nói, dùng nút hoặc wake word mà firmware hiện tại hỗ trợ để tạo `abort`/`listen:start`.
9. Xác nhận server ngừng gửi binary cũ và gửi stop chuẩn.
10. Quan sát/ghi âm để đo thời gian từ thao tác ngắt/close đến loa thật sự dừng hoặc phát hết goodbye.
11. Xác nhận lượt nói tiếp theo không bị transcript cũ/echo tạo turn giả.
12. Lặp tối thiểu 20 lượt normal và 20 lượt interrupt; ghi số lỗi, p50 và p95.

Nếu board không có nút/wake word tạo abort trong firmware hiện tại, baseline vẫn có thể nghiệm thu hội thoại stock FW; automatic voice barge-in là hạng mục tùy chọn sau này và cần AEC được xác minh độc lập.

## 7. Patch lịch sử

`veetee-server/patches/xiaozhi-esp32-barge-in.patch` là artifact từ thử nghiệm trước đây. **Không áp patch này cho đường hỗ trợ hiện tại.** Nó được giữ để truy vết lịch sử thiết kế.

Theo dõi trạng thái tại [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md).
