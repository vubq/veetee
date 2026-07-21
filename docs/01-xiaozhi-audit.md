# Audit source Xiaozhi

Tài liệu này ghi lại những capability `veetee` cần giữ lại từ hai source tham chiếu. Các kết luận được đối chiếu với code, không chỉ với README.

## 1. Firmware

### Runtime và state

`main/application.cc` + `main/device_state_machine.*` tổ chức một main task nhận event từ FreeRTOS. Các state quan trọng là:

- `starting`: khởi tạo board và network.
- `wifi_configuring`: đang phát AP/BluFi/acoustic provisioning.
- `activating`: gọi OTA/bootstrap, nhận server time, firmware config và activation.
- `idle`: sẵn sàng chờ PTT hoặc wake word.
- `connecting`: mở audio channel.
- `listening`: capture, VAD/encode và upload Opus.
- `speaking`: nhận Opus/TTS, decode và phát loa.
- `upgrading`: ghi OTA partition.
- `audio_testing`: test mic/loa khi cấu hình Wi-Fi.

Runtime tham chiếu đã xử lý các tình huống cần giữ: server ngắt socket, Wi-Fi mất, TTS bị abort, wake detector lifecycle khi đang nói, playback drain trước khi bật voice processing và không gọi mutation từ callback ngoài main task. Veetee chỉ quảng bá voice interrupt khi board/AEC benchmark pass; button interrupt luôn là recovery guarantee.

### Audio

`main/audio/audio_service.*` tách codec I/O, Opus encode/decode, queue và audio task. Profile simplex trong `main/audio/codecs/no_audio_codec.*` dùng hai I2S controller: TX cho MAX98357A và RX cho INMP441. Input mặc định 16 kHz mono, output 24 kHz mono, Opus frame thường 60 ms.

Các capability nên giữ trong `veetee`:

- capture không block main loop;
- bounded queue, không allocation lớn lặp lại trong audio path;
- Opus binary frame hai chiều;
- resample nếu server sample rate khác output codec;
- VAD, wake-word và decoder reset độc lập;
- abort làm rỗng send/decode queue để không phát âm thanh cũ;
- tùy chọn server AEC bằng timestamp protocol v2.

### Network provisioning

`main/boards/common/wifi_board.cc` có behavior đúng với yêu cầu:

1. Nếu đã có SSID trong NVS, thử station connection.
2. Timeout 60 giây thì dừng station và vào config mode.
3. Nếu chưa có SSID, vào config mode sau delay ngắn.
4. Hotspot provisioning phát AP, hiển thị SSID/URL captive portal.
5. Có nhánh BluFi và acoustic provisioning qua Kconfig.
6. Khi nhận credentials mới, thoát config mode và thử connect lại.

`veetee` sẽ giữ AP fallback là đường mặc định; BluFi/acoustic để feature flag sau khi profile phần cứng ổn định.

### OTA và activation

`main/ota.cc` gửi bootstrap HTTP với các header `Activation-Version`, `Device-Id`, `Client-Id`, `Serial-Number` (nếu có), `User-Agent`, `Accept-Language` và body system info.

Response có thể gồm:

- `server_time`: timestamp + timezone offset;
- `websocket`: URL/token;
- `mqtt`: broker credentials/topic;
- `firmware`: version/url/force;
- `activation`: `code`, `message`, `challenge`.

Firmware hiển thị mã 6 số bằng text + âm thanh từng chữ số. `OTAController` của server tạo one-time code + random challenge/cache cho device chưa bind; web manager dùng mã đó để bind vào agent. Sau bind, firmware gọi `/ota/activate`; khi thành công mới chuyển `activating -> idle`. MAC chỉ là device identifier, không phải challenge secret.

Điểm cần cải thiện khi viết mới:

- mã phải dùng CSPRNG, TTL ngắn, one-time use và rate limit;
- challenge không được coi là secret nếu không có proof-of-possession; cần HTTPS và token scope;
- không log API key, activation payload hoặc audio transcript đầy đủ ở production;
- OTA phải kiểm tra model, version, SHA-256/signature và rollback.

### Board/display/MCP

Firmware sử dụng `Board` interface và đúng một `DECLARE_BOARD(...)`. Display ST7789 đi qua `esp_lcd_panel_io_spi`; LVGL là optional. `McpServer` có tool thường và user-only tool, chạy callback trên main task qua `Application::Schedule()`.

## 2. Wire protocol

### WebSocket

- Handshake headers: `Authorization`, `Protocol-Version`, `Device-Id`, `Client-Id`.
- Device gửi `hello` với `version`, `features`, `transport`, `audio_params`.
- Server trả `hello` có `session_id`, `transport` và audio params canonical.
- Text frame là JSON; binary frame là Opus.
- Device messages: `listen(start|stop|detect)`, `abort`, `mcp`.
- Server messages: `stt`, `tts(start|sentence_start|stop)`, `llm`, `mcp`, `system`, `alert`, optional `custom`.
- Binary protocol v1 là raw Opus; v2 có timestamp/payload length; v3 có header nhỏ hơn.
- Hello timeout mặc định 10 giây; idle channel timeout khoảng 120 giây.

### MQTT + UDP

- MQTT giữ control JSON và hello.
- UDP giữ encrypted Opus với header 16 byte: type, flags, payload length, SSRC, timestamp, sequence.
- AES-CTR key/nonce được phân phối qua MQTT hello.
- Sequence chống replay/reorder; gap nhỏ log warning.

`veetee` giữ WebSocket làm transport mặc định của native MVP. MQTT+UDP chỉ là compatibility/production profile bật explicit khi có nhu cầu scale gateway/latency; không để bootstrap tự làm native firmware chuyển transport ngoài ý muốn.

## 3. MCP

MCP là JSON-RPC 2.0 bọc trong `{session_id,type:"mcp",payload}`.

- `initialize` với capability optional (vision URL/token).
- `tools/list` có pagination cursor và `withUserTools`.
- `tools/call` với `name` + `arguments`.
- notification không có `id`.
- regular tools có thể được AI gọi; user-only tools chỉ companion/manager được phép list.
- payload size firmware bị giới hạn khoảng 8 KB, nên phải pagination.

## 4. Backend Xiaozhi

`xiaozhi-server` hiện có:

- asyncio WebSocket server, one `ConnectionHandler` per device/session;
- VAD -> ASR -> intent/tool -> LLM -> sentence chunk -> TTS streaming;
- abort/cancel, no-voice timeout, memory save và chat title generation;
- provider registry trong `core/providers/`;
- device MCP, server MCP endpoint, server plugin và IoT descriptor;
- config merge từ YAML và `manager-api`;
- OTA/vision HTTP endpoints.

`manager-api` hiện quản lý user/RBAC, agent/template/snapshot, device/bind, model provider/config, timbre, OTA, knowledge base, voiceprint/clone, context provider, correction word và chat history. `manager-web` cung cấp SPA cho các module này.

## 5. Provider inventory

Source tham chiếu đã có các port/adapter sau; `veetee` chỉ nên mang port vào core và thêm adapter theo nhu cầu:

- VAD: Silero.
- ASR: FunASR local/server, Sherpa-ONNX, OpenAI, Aliyun, Baidu, Tencent, Doubao, Xunfei, Vosk, Qwen ASR.
- LLM: OpenAI-compatible, Gemini, Ollama, Dify, FastGPT, Coze, Xinference, Home Assistant.
- TTS: Edge, OpenAI, Aliyun, Tencent, Doubao, Xunfei, FishSpeech, GPT-SoVITS, Index, Paddle, SiliconFlow, Minimax và các stream adapter khác.
- Memory: no-memory, local short, mem0ai, PowerMem, report-only.
- Tools: server plugins, device MCP, remote MCP endpoint, device IoT.

## 6. Kết luận audit

Không nên copy toàn bộ repository. Nên copy có chọn lọc các compatibility facts và viết lại các boundary:

1. Firmware core chỉ support một board profile đầu tiên.
2. Voice server hot path không phụ thuộc manager API ở mỗi frame.
3. Provider interface là contract độc lập với vendor SDK.
4. Manager API là control plane, không phải audio relay.
5. MCP và protocol schemas phải nằm trong package contract có test fixture.
6. Mọi behavior realtime phải có cancellation token và deadline.

Xiaozhi đã có VAD, intent/tool routing và no-voice timeout. Veetee giữ các capability đó nhưng làm rõ thêm `InputAdmissionGate` tổng quát trước LLM/MCP, hai nguồn wake (button/wake word), interrupt profile và closing grace; các nguồn âm thanh như quạt/TV chỉ là test data, không phải rule riêng trong domain logic.
