# Provider và cấu hình

Tài liệu này mô tả provider/config quan sát từ upstream và các nguyên tắc adapter chung.
Provider mục tiêu, trạng thái benchmark và quyết định chính thức của Veetee nằm trong
[kế hoạch triển khai server](server-implementation-plan.md); không suy ra lựa chọn Veetee
từ danh mục implementation upstream bên dưới.

## Provider model

Python server tham khảo dùng factory/plugin để chọn implementation theo config.

| Loại | Vai trò | Ví dụ implementation |
| --- | --- | --- |
| VAD | Xác định có giọng nói/end-of-speech | Silero |
| ASR | PCM/stream -> text | FunASR, Sherpa-ONNX, Vosk, cloud streaming |
| LLM | Dialogue/tool calling -> token stream | OpenAI-compatible, Gemini, Ollama, Dify |
| VLLM | Vision-language | OpenAI-compatible vision |
| TTS | Text -> audio/Opus | Edge, OpenAI, Aliyun, FishSpeech, streaming SDK |
| Intent | Route text/tool | none, intent LLM, function calling |
| Memory | Lưu/lấy ngữ cảnh dài hạn | mem0/PowerMem-style provider |
| Tools | Hành động nội bộ/ngoài/device | Plugin, MCP, IoT, Home Assistant |

`core/utils/modules_initialize.py` lấy tên provider từ `selected_module`, tìm config
trong section tương ứng và import implementation. Adapter cần có base contract nhất
quán cho initialize, call/stream, cancellation và cleanup.

## Shared và per-connection

| Nên shared | Nên riêng theo connection |
| --- | --- |
| Model local read-only, nặng RAM | Stream socket/client có state |
| VAD/ASR engine thread-safe | ASR utterance buffer |
| LLM client stateless | TTS stream/session |
| Immutable tokenizer/config | Dialogue, memory context, tool state |

Không quyết định shared chỉ dựa trên chi phí khởi tạo. Cần kiểm tra thread safety, khả
năng reset, rate limit và credential/tenant isolation.

## Thứ tự load config

### Local mode

```text
config.yaml (default)
  + data/.config.yaml (override đệ quy)
  -> config runtime
```

`merge_configs` merge mapping đệ quy, custom value thắng default. `data/.config.yaml`
được đọc trực tiếp và phải tồn tại trong implementation tham khảo.

### Manager API mode

Nếu `data/.config.yaml` có `manager-api.url`:

1. Khởi tạo API client bằng URL/secret local.
2. Lấy server config từ Java API.
3. Giữ một số `server` field local như IP/port/http port/vision/auth key.
4. Đánh dấu `read_config_from_api=true`.
5. Theo connection, lấy agent model và correction words bằng device/client ID.

Config chung có cache. Provider có thể được hot reinitialize khi config thay đổi; cần
đảm bảo request đang chạy không bị mất dependency hoặc dùng nhầm config nửa cũ nửa mới.

## Nhóm config quan trọng

- `server`: bind address, WebSocket/HTTP port, auth, external URL.
- `manager-api`: URL và service secret.
- `selected_module`: provider được chọn cho VAD/ASR/LLM/TTS/intent/memory.
- Provider sections: endpoint, model, API key, voice, output dir và tuning.
- Conversation: prompt, welcome message, wake words, exit command, timeout.
- Logging/reporting/chat history.

## Quy tắc provider đề xuất

- Một interface nhỏ, typed và có test contract dùng chung.
- Không để provider đọc global config tùy ý; inject config đã validate.
- Secret đến từ secret manager/environment, không đưa vào log hay API response.
- Timeout và retry phân biệt connect, first-byte và total deadline.
- Retry chỉ cho thao tác idempotent; có jitter/backoff và circuit breaker.
- Normalize output, error và usage/cost metric giữa provider.
- Cancellation phải dừng được ASR/LLM/TTS stream và giải phóng connection.
- Health check không phát sinh chi phí lớn hoặc nội dung người dùng.

## Thêm provider mới

1. Xác định base contract và lifecycle shared/per-session.
2. Thêm implementation trong đúng nhóm provider.
3. Khai báo schema config và validation, không chỉ thêm key mẫu.
4. Đăng ký vào factory/selection.
5. Thêm unit contract test với fake transport/provider.
6. Test timeout, cancellation, malformed response và rate limit.
7. Ghi sample config không chứa secret thật.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/config/config_loader.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/config.yaml`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/utils/modules_initialize.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
