# voice-server

Hot path WebSocket/Opus và conversation engines. App này không phụ thuộc manager API cho mỗi audio frame; config được tải theo immutable snapshot/version.

Vertical slice hiện chạy thật tại `/xiaozhi/v1/`:

```text
Opus -> Silero VAD -> Zipformer Vietnamese INT8 -> local admission
     -> structured planner/9Router -> streaming LLM -> VieNeu ONNX INT8
     -> Opus
```

VieNeu đọc toàn bộ graph/codec từ `models/` và khởi động được với
`HF_HUB_OFFLINE=1`. Model được prewarm trước khi `/health/ready` trả `200`.
Button/wake word mở cùng assistant gate; `abort` tăng generation, hủy provider
scope và loại output cũ. Inactivity timeout synthesize goodbye từ config rồi sleep.

Chạy local:

```bash
cp .env.example .env
npm run models:prepare
npm run dev:voice
```

Nếu 9Router bật `Require API key`, đặt key riêng của app trong
`VEETEE_9ROUTER_API_KEY`; không commit key và không đưa key xuống firmware.

Conversation mặc định là `mode=auto`: button/wake word chỉ mở assistant gate; VAD tự finalize, admission gate quyết định có gọi LLM/MCP, inactivity timeout phát goodbye rồi sleep.
