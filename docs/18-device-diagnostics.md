# Device diagnostics V1

Tài liệu này là contract cho ba capability P0: audio debugger có kiểm soát,
self-test từ Manager và device health/system information. Đây là control-plane
diagnostics; `manager-api` không tham gia đường frame-by-frame audio.

## 1. Nguyên tắc an toàn

- Diagnostic audio V1 chỉ tính metrics trên PCM mà capture task đã đọc; không gửi,
  ghi flash, ghi NVS hoặc lưu raw audio.
- Mỗi phiên audio kéo dài từ 1 đến 30 giây, chỉ có một phiên chạy tại một thời
  điểm và tự hoàn tất theo monotonic clock.
- Firmware chỉ giữ current/latest result trong RAM. Reboot xóa kết quả; Manager
  không persist audio metrics trong V1.
- Self-test không đổi Wi-Fi, không reconnect, không scan, không sửa profile, không
  xóa NVS và không đổi assistant state.
- Không phát speaker tone từ xa trong V1. Software chỉ có thể kiểm tra task/codec
  path; xác nhận loa phát thật được trả là `not_run` với `requires_listener=true`.
- User-only MCP tools luôn cần xác nhận rõ từ Manager và bị ẩn khỏi catalog tool
  dành cho AI.
- Payload không chứa SSID password, token, activation secret, transcript hoặc
  audio sample.

## 2. MCP tools

MCP tiếp tục dùng JSON-RPC 2.0 trong envelope hiện có, giữ pagination và giới hạn
payload. Ba tool sau có `audience=user` và `requiresConfirmation=true`:

| Tool | Safety | Arguments | Kết quả |
|---|---|---|---|
| `self.diagnostics.get_health` | `read_only` | object rỗng | health snapshot |
| `self.diagnostics.audio.start` | `disruptive` | `duration_seconds`, integer 1–30 | session vừa bắt đầu |
| `self.diagnostics.run_self_test` | `disruptive` | object rỗng | self-test snapshot |

Schema của audio start:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["duration_seconds"],
  "properties": {
    "duration_seconds": {
      "type": "integer",
      "minimum": 1,
      "maximum": 30
    }
  }
}
```

Tool result vẫn dùng `result.content[0]` kiểu `text`. `text` chứa một JSON object
có `schema_version=1`; Manager phải parse và validate object này thay vì tin string
từ device.

## 3. Health snapshot

Health response có các nhóm bounded:

- `device`: board, firmware, state, assistant gate, uptime và reset reason;
- `memory`: free/min internal heap và free/min PSRAM;
- `network`: connected, RSSI, IPv4, disconnect count, reconnect-attempt count và
  last disconnect reason;
- `audio`: trạng thái capture/playback task, lifetime error/drop counters,
  playback queue high-water và diagnostic session current/latest;
- `resources`: wake detector/UI health và wake detector dropped frames.
- `tasks`: trạng thái và minimum free stack từng quan sát của capture, playback,
  wake detector và task điều phối WebSocket của Veetee.

`tasks.minimum_stack_free_bytes` là ngưỡng cảnh báo chung, hiện là 2 KiB. Mỗi task
trả `expected`, `running` và `stack_free_bytes`; task không được profile hiện tại
yêu cầu có `expected=false`, `running=false`, `stack_free_bytes=0` và không làm
health suy giảm. ESP-IDF trả high-water mark theo byte, vì vậy firmware không nhân
thêm `sizeof(StackType_t)`. `websocket_control` chỉ tên task điều phối do Veetee sở
hữu; không giả là task nội bộ riêng của component WebSocket. Trường `tasks` là bổ
sung tương thích trong schema V1: Manager mới vẫn chấp nhận firmware V1 cũ chưa
gửi trường này và hiển thị trạng thái chưa có telemetry.

`audio.diagnostic.state` là `not_run`, `running` hoặc `completed`. Metrics phiên:

- PCM frame/sample count;
- RMS, peak absolute, DC offset;
- clipped sample count và clipping percent;
- delta của mic timeout/read error, detector drop, Opus encode/decode error,
  uplink/playback queue drop và speaker write error;
- `raw_audio_stored=false`.

## 4. Self-test snapshot

Self-test trả `overall=pass|fail` và danh sách tối đa 16 check. Check V1:

- application/state provider;
- Wi-Fi hiện đang connected;
- capture task;
- playback task;
- headroom của mọi realtime task đang được yêu cầu, dùng cùng ngưỡng 2 KiB;
- mic frame đã được quan sát;
- internal heap;
- PSRAM;
- wake detector/resource;
- UI Pack/display;
- physical speaker output (`not_run`, cần người nghe xác nhận).

Self-test không chờ vài giây trên application task. Nếu cần đánh giá chất lượng mic,
Manager chạy audio diagnostic riêng rồi đọc RMS/DC/clipping. Hardware acceptance
cho mic, loa, button và LCD vẫn được báo riêng, không suy ra từ host test.

## 5. Manager API

Manager expose các route tenant-scoped:

```text
GET  /api/v1/devices/:id/diagnostics/health
POST /api/v1/devices/:id/diagnostics/audio-sessions
POST /api/v1/devices/:id/diagnostics/self-test
```

Audio body là `{"durationSeconds": 1..30}`. API kiểm tra ownership trước khi proxy,
gọi đúng canonical MCP tool với confirmation, parse JSON text và validate toàn bộ
range/size. Audio start và self-test ghi audit requested/succeeded/failed; audit
chỉ lưu hash/metadata bounded, không lưu raw MCP payload hoặc audio.

## 6. Manager Web

Tab `Chẩn đoán` của device workspace phải có:

- loading, disconnected/error và retry state;
- summary của device, memory, network, audio và resources;
- nút chạy self-test và kết quả từng check;
- lựa chọn 3/5/10 giây cho audio metrics;
- notice rõ không lưu raw audio;
- polling/countdown khi session đang chạy và kết quả RMS/peak/DC/clipping.

UI không mô tả một check software-only là xác nhận phần cứng. Các bước còn cần
người dùng/board thật phải hiện riêng.

## 7. Validation

- Host unit test accumulator audio, timeout, bounds, counter delta và session busy.
- Firmware MCP test catalog regular/user-only, input bounds và structured result.
- Manager API test tenant guard, parsing, malformed/unbounded result và audit.
- Manager Web schema/component test loading, error, running và completed state.
- Manager Web hiển thị từng task, headroom theo KiB và cảnh báo khi task dừng hoặc
  thấp hơn ngưỡng firmware công bố.
- ESP-IDF build trước flash. Flash/monitor không được erase NVS hoặc đổi Wi-Fi.
- Hardware test riêng: thu giọng/im lặng/clipping, nghe loa, nhìn LCD, mất/kết nối
  lại voice session và xác nhận Manager polling trên LAN/Tailscale.
