# Provider va cau hinh

## Provider model

Python server tham khao dung factory/plugin de chon implementation theo config.

| Loai | Vai tro | Vi du implementation |
| --- | --- | --- |
| VAD | Xac dinh co giong noi/end-of-speech | Silero |
| ASR | PCM/stream -> text | FunASR, Sherpa-ONNX, Vosk, cloud streaming |
| LLM | Dialogue/tool calling -> token stream | OpenAI-compatible, Gemini, Ollama, Dify |
| VLLM | Vision-language | OpenAI-compatible vision |
| TTS | Text -> audio/Opus | Edge, OpenAI, Aliyun, FishSpeech, streaming SDK |
| Intent | Route text/tool | none, intent LLM, function calling |
| Memory | Luu/lay ngu canh dai han | mem0/PowerMem-style provider |
| Tools | Hanh dong noi bo/ngoai/device | Plugin, MCP, IoT, Home Assistant |

`core/utils/modules_initialize.py` lay ten provider tu `selected_module`, tim config
trong section tuong ung va import implementation. Adapter can co base contract nhat
quan cho initialize, call/stream, cancellation va cleanup.

## Shared va per-connection

| Nen shared | Nen rieng theo connection |
| --- | --- |
| Model local read-only, nang RAM | Stream socket/client co state |
| VAD/ASR engine thread-safe | ASR utterance buffer |
| LLM client stateless | TTS stream/session |
| Immutable tokenizer/config | Dialogue, memory context, tool state |

Khong quyet dinh shared chi dua tren chi phi khoi tao. Can kiem tra thread safety, kha
nang reset, rate limit va credential/tenant isolation.

## Thu tu load config

### Local mode

```text
config.yaml (default)
  + data/.config.yaml (override de quy)
  -> config runtime
```

`merge_configs` merge mapping de quy, custom value thang default. `data/.config.yaml`
duoc doc truc tiep va phai ton tai trong implementation tham khao.

### Manager API mode

Neu `data/.config.yaml` co `manager-api.url`:

1. Khoi tao API client bang URL/secret local.
2. Lay server config tu Java API.
3. Giu mot so `server` field local nhu IP/port/http port/vision/auth key.
4. Danh dau `read_config_from_api=true`.
5. Theo connection, lay agent model va correction words bang device/client ID.

Config chung co cache. Provider co the duoc hot reinitialize khi config thay doi; can
dam bao request dang chay khong bi mat dependency hoac dung nham config nua cu nua moi.

## Nhom config quan trong

- `server`: bind address, WebSocket/HTTP port, auth, external URL.
- `manager-api`: URL va service secret.
- `selected_module`: provider duoc chon cho VAD/ASR/LLM/TTS/intent/memory.
- Provider sections: endpoint, model, API key, voice, output dir va tuning.
- Conversation: prompt, welcome message, wake words, exit command, timeout.
- Logging/reporting/chat history.

## Quy tac provider de xuat

- Mot interface nho, typed va co test contract dung chung.
- Khong de provider doc global config tuy y; inject config da validate.
- Secret den tu secret manager/environment, khong dua vao log hay API response.
- Timeout va retry phan biet connect, first-byte va total deadline.
- Retry chi cho thao tac idempotent; co jitter/backoff va circuit breaker.
- Normalize output, error va usage/cost metric giua provider.
- Cancellation phai dung duoc ASR/LLM/TTS stream va giai phong connection.
- Health check khong phat sinh chi phi lon hoac noi dung nguoi dung.

## Them provider moi

1. Xac dinh base contract va lifecycle shared/per-session.
2. Them implementation trong dung nhom provider.
3. Khai bao schema config va validation, khong chi them key mau.
4. Dang ky vao factory/selection.
5. Them unit contract test voi fake transport/provider.
6. Test timeout, cancellation, malformed response va rate limit.
7. Ghi sample config khong chua secret that.

## Source doi chieu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/config/config_loader.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/config.yaml`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/utils/modules_initialize.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
