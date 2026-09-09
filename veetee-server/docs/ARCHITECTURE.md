# VeeTee Server Architecture

Cập nhật tài liệu: **2026-09-09**
Source snapshot đối chiếu: **HEAD `51ec30b`**, working tree sạch trước migration docs.

Tài liệu này là nơi mô tả **behavior hiện hành**. Backlog/runtime changes nằm trong [plan AI/persona/tools/memory/latency](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md); không coi nội dung `PLANNED` trong plan là behavior đã có.

## Pipeline

```text
ESP32/Xiaozhi stock
  -> WebSocket/audio protocol
  -> VAD + ASR
  -> context + persona + history/memory + tool schemas
  -> unified AI turn
  -> validate/execute tool, memory, confirmation nếu AI chọn
  -> AI speech / receipt synthesis
  -> TTS stream + pacing
  -> ESP32 playback
```

## Ranh giới AI và server

### IMPLEMENTED

AI sở hữu các quyết định ngữ nghĩa: intent, ngôn ngữ, cách trả lời, chọn tool/function, confirmation, memory mutation và kết thúc hội thoại. Server không dùng keyword list, exact phrase matcher, regex classifier hoặc whitelist câu người dùng để tự route các quyết định này.

Server chịu trách nhiệm deterministic cho:

- protocol/lifecycle và generation ownership;
- cancellation/deadline;
- schema/argument validation;
- permission/ownership/write barriers;
- execution safety;
- receipt/state invariants.

`listen:detect` có text, `chat`, `text` và ASR final đều có thể đi vào unified AI turn. `abort`, `listen:start`, disconnect và binary framing vẫn là protocol/lifecycle events, không phải semantic intent do server suy từ câu chữ.

## Runtime constants hiện hành

Các giá trị dưới đây được đối chiếu từ `config/settings.py` và `config.example.yaml`, không lấy từ local `config.yaml`:

| Hạng mục | Giá trị hiện hành |
| --- | ---: |
| Silero end silence default | `450 ms` |
| Session event queue | `8` |
| TTS sample rate | `24000 Hz` |
| LLM first event timeout default | `6000 ms` |
| Tool calls tối đa/turn | `3` |
| Tool schemas expose tối đa | `16` |
| LLM rounds hợp lệ | `1` hoặc `2` |

Local config có thể override default. Một override local quan sát trong audit không trở thành default tài liệu.

## Persona và context

### IMPLEMENTED

Provider load saved persona runtime trước; nếu không có saved persona thì dùng `llm.base_prompt` từ config. Prompt template vẫn nằm ở `agent-base-prompt.txt` và chèn `{{base_prompt}}` vào system prompt.

Persona cập nhật qua management API được persist cho các lượt sau. API/UI hiện cùng giới hạn `4000` ký tự.

Context builder có budget cấu hình và reserve output tokens trước khi gọi model.

### KNOWN_GAP

- **A04:** bước budget hiện chưa tính đầy đủ semantic system prompt cuối cùng; estimate `4 chars/token` không phải tokenizer chính xác cho tiếng Việt/JSON.
- **A05:** trần persona `4000` ký tự ở API/UI là giới hạn tĩnh, chưa được thống nhất với model context capacity và request byte/token budget.
- Persona/history lớn vẫn cần nghiệm thu theo route/model thật trước khi khẳng định không truncate sai phần bắt buộc.

## Tools, confirmation và receipts

### IMPLEMENTED

AI nhận tool schema và tự quyết định khi nào gọi tool. Server validate request trước execution, áp giới hạn số call/round và tạo receipt từ kết quả thật. Side effect cần confirmation/ownership phù hợp trước khi commit.

Built-in hiện có gồm `calculate` và `get_current_time`; MCP device chỉ được expose khi board quảng bá capability và discovery trả tool tương ứng.

### KNOWN_GAP

- **A01:** `get_current_time` còn nhánh direct render trong `core/session.py`, nên một số receipt clock chưa đi qua persona/ngôn ngữ/synthesis AI đầy đủ.
- **A02:** `render_action_receipt_fallback(...)` vẫn còn literal fallback và chưa diễn đạt đúng mọi nested outcome như confirmation execution failure hoặc revision conflict.
- **A03:** provider có thể phát content trước terminal tool validation; mixed content + read-only tool vẫn có thể tới TTS trước khi toàn round được chốt.
- **A06:** orchestrator hiện chỉ cho 1 hoặc 2 LLM rounds; vòng synthesis thứ hai không mở agent loop phụ thuộc nhiều bước.
- **A09:** argument validation hiện còn nông, chủ yếu top-level/primitive properties; recursive JSON Schema coverage chưa hoàn chỉnh.
- **A10:** tool catalog bị giới hạn ở 16 schema đầu; chưa có discovery flow cho catalog lớn.
- **A11:** resource/session execution còn nhiều đoạn serialize theo tool/TTS/ASR shared resource.

Các gap này thuộc [runtime plan](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md), không được “sửa” bằng cách viết tài liệu như thể đã hoàn tất.

## Memory và structured history

### IMPLEMENTED

Session memory và durable memory có ownership/revision semantics. Durable personal memory chỉ nên bật khi operator có `memory.trusted_owner_id`; `Device-Id`/`Client-Id` tự khai báo không đủ làm owner tin cậy.

AI quyết định mutation từ context/schema; server không parse các từ kiểu “nhớ/quên” để tự ghi dữ liệu.

### KNOWN_GAP

- **A07:** retrieval hiện chủ yếu lexical/FTS/BM25/LIKE + recent top-k, chưa có hybrid semantic retrieval/RAG production.
- **A08:** dialogue history chủ yếu giữ role/content; structured receipts của lượt trước chưa được giữ đầy đủ trong context lượt sau.

## Latency và audio ownership

Server dùng generation/capture ownership để loại stale transcript/audio sau cancel. `client_only` là policy barge-in mặc định: stock `abort` hoặc `listen:start` có thể cancel turn đang nói; speech-start tự động không được bật chỉ vì hello có `aec=true`.

TTS được pace và bounded để giảm lượng audio gửi trước. Stock protocol không có playback queue depth/flush ACK chung, nên:

- `tts:stop` hoặc last binary ở server không phải physical speaker stop;
- first binary sent không phải first useful/voiced audio trên loa;
- AEC hiệu quả và tail thực tế phải đo trên board.

Metric benchmark hiện hành là `speech_end_to_first_voiced_pcm_received_ms`; xem [TESTING.md](TESTING.md) để biết gate và giới hạn của metric.

## Trạng thái và nguồn evidence

Ba nhãn được dùng nhất quán:

- **IMPLEMENTED:** source hiện có behavior tương ứng; nếu claim về chất lượng/runtime cần thêm evidence riêng.
- **KNOWN_GAP:** source hiện vẫn có giới hạn/nhánh cần sửa.
- **PLANNED:** chỉ có trong task plan, chưa được triển khai/kiểm chứng.

Snapshot evidence và các mục runtime/hardware còn thiếu nằm trong [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md). Cách nghiệm thu nằm trong [TESTING.md](TESTING.md).
