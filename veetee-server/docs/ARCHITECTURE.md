# VeeTee Server Architecture

Cập nhật tài liệu: **2026-09-10**
Source snapshot đối chiếu: **HEAD `c92192a`**, working tree sạch.

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
| Tool calls tối đa/turn | `1..8` (default `3`) |
| Tool schemas expose tối đa | `16` (default, max `64`; vượt giới hạn expose explicit catalog notice) |
| LLM rounds hợp lệ | `1`, `2`, `3` hoặc `4` (default `2`) |
| Persona budget | `32 KiB` bytes + est. `8000` tokens (API/UI/config/runtime chung) |
| TTS deadlines | `first_chunk 4000 ms`, `stall 2500 ms` (tách khỏi generation timeout) |

Local config có thể override default. Một override local quan sát trong audit không trở thành default tài liệu.

## Persona và context

### IMPLEMENTED

Provider load saved persona runtime trước; nếu không có saved persona thì dùng `llm.base_prompt` từ config. Prompt template vẫn nằm ở `agent-base-prompt.txt` và chèn `{{base_prompt}}` vào system prompt.

Persona cập nhật qua management API được persist cho các lượt sau. API/UI/config/runtime dùng chung budget `base_prompt_max_bytes` + est. tokens (mặc định `32 KiB`/`8000`), reject over-budget thay vì truncate âm thầm. Persona version được snapshot mỗi turn để các round nhất quán.

Request assembly duy nhất tính persona, semantic/control prompt, history, memory/RAG, pending actions, schemas, receipts và output reserve. Estimate chars/token giữ safety margin tiếng Việt/JSON và nhãn `estimated`; không gọi API đếm token nối tiếp mỗi turn.

### REMAINING

- Persona/history lớn vẫn cần nghiệm thu theo route/model thật (tokenizer/cache support) trước khi khẳng định capacity; cold miss có số đo riêng.
- Tóm tắt history AI nền khi gần high-water mark là seam có界限, chưa production workload.

## Tools, confirmation và receipts

### IMPLEMENTED (2026-09-09, HEAD `469f941` + working tree M1/M2/M4)

- Mọi receipt nghiệp vụ (kể cả clock) đều qua AI synthesis với persona/ngôn ngữ/context hiện tại; nhánh direct clock và literal `render_action_receipt_fallback` đã bỏ. Synthesis thất bại không phát claim thành công, chỉ giữ receipt có cấu trúc.
- Speech của round có thể gọi tool được buffer tới terminal validation; round có action thì discard speech vòng đó, round chat thuần mới flush. Không còn mixed invalid speech tới TTS.
- Receipt envelope chuẩn: id, origin turn, name/args bounded, status, execution outcome, changed, data/error, observed_at, provenance. `unknown` giữ unknown; awaiting confirmation khác approved khác execution succeeded.
- Bounded agent loop `1..4` rounds (default `2`): chat thường 1 inference, turn có action được synthesis + chain A→B khi cấu hình cho phép. Semantic calls tính vào budget; loop detection theo call IDs/args/receipt/deadline.
- Validation JSON Schema recursive (object/array/items, required, enum, bounds, additionalProperties, nullable/combinators, pattern, length/depth/bytes), compile/cache theo version/hash. Memory/confirmation args validate cùng chuẩn trước mutation.
- Ownership/cancel/deadline kiểm lại ngay trước dispatch/commit; cancel queued không dispatch; dispatched giữ receipt sau caller cancel. MCP là capability gate, không tin read-only marker trong description.
- Catalog vượt `schema_limit` expose explicit `veetee_tool_catalog` notice thay vì cắt im lặng; có `registry.search()` cho discovery. Independent read-only overlap qua executor semaphore + gather ở follow-up rounds.

Built-in hiện có gồm `calculate` và `get_current_time`; MCP device chỉ được expose khi board quảng bá capability và discovery trả tool tương ứng.

Chi tiết thuộc [runtime plan](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md); trạng thái trên có regression `tests/test_ai_semantics_regression.py`, `test_bounded_loop.py`.

## Memory và structured history

### IMPLEMENTED

Session memory và durable memory có ownership/revision semantics. Durable personal memory chỉ nên bật khi operator có `memory.trusted_owner_id`; `Device-Id`/`Client-Id` tự khai báo không đủ làm owner tin cậy.

AI quyết định mutation từ context/schema; server không parse các từ kiểu “nhớ/quên” để tự ghi dữ liệu.

### IMPLEMENTED (M5 seam, 2026-09-09)

- Mutation và retrieval tách bạch; retrieval trả candidate data có ID/version/source/score/observed_at/provenance, không tự tạo intent/write từ keyword.
- Retriever contract chung cho memory và RAG (`RetrievalQuery`/`RetrievalCandidate`/`BaseRetriever`). Lexical FTS/BM25/LIKE là baseline; embedding/reranker là seam optional chỉ giữ khi cải thiện quality trong budget.
- Session/durable budget split để recent session không starve durable; lookup timeout/miss có metric显式, không nuốt im lặng. Authoritative DB state đọc trước khi đưa cache vào prompt; tombstone không hồi sinh.
- RAG fixture (`FixtureRAGRetriever`) với source/version/provenance; instruction-like text là data, ingestion ngoài critical path. Production RAG vẫn out of scope.
- Dialogue giữ transcript có cấu trúc (tool call/result/control + reply), bound bytes/tokens, phân biệt created/generated/sent/interrupted/unknown; receipt sau dispatch-cancel reconcile vào lượt sau.

## Latency và audio ownership

Server dùng generation/capture ownership để loại stale transcript/audio sau cancel. `client_only` là policy barge-in mặc định: stock `abort` hoặc `listen:start` có thể cancel turn đang nói; speech-start tự động không được bật chỉ vì hello có `aec=true`.

TTS được pace và bounded để giảm lượng audio gửi trước. Stock protocol không có playback queue depth/flush ACK chung, nên:

- `tts:stop` hoặc last binary ở server không phải physical speaker stop;
- first binary sent không phải first useful/voiced audio trên loa;
- AEC hiệu quả và tail thực tế phải đo trên board.

Metric benchmark hiện hành là `speech_end_to_first_voiced_pcm_received_ms` (`v2`); certification metric là `speech_end_to_first_useful_voiced_audio_received_ms` (voiced hiện chỉ là acoustic proxy cho tới khi case pass quality/grounding). TTS lease đo riêng inference vs hold (`tts_lease_held`, scheduler snapshot holds/avg/max); generation/tool/TTS first-chunk/stall/delivery budgets tách riêng. Xem [TESTING.md](TESTING.md).

## Vòng đời hội thoại và idle

### IMPLEMENTED (2026-09-10)

- Hết `conversation.idle_timeout_seconds` không có tương tác hội thoại (câu hỏi user / câu trả lời AI — không phải silence âm thanh thô) thì phiên **luôn kết thúc** (deterministic deadline, không vòng AI `[continue]`).
- Một bounded LLM inference sinh câu chào tạm biệt theo persona; lượt user chen vào giữa chừng hủy flow qua activity revision.
- Câu chào phải là câu kết thúc, không phải câu hỏi/lời mời nói tiếp: output-shape validation → retry 1 lần → fallback an toàn (`IDLE_FAREWELL_FALLBACK`). Đây là validate output do AI sinh trong path lifecycle, không phân loại lời user.
- Phát hết câu chào (drain đuôi + `close_grace_ms`, gửi `tts:stop`) rồi đóng WebSocket code 1000 reason `idle_timeout` và dọn session. Muốn nói tiếp phải hello phiên mới.

## Trạng thái và nguồn evidence

Ba nhãn được dùng nhất quán:

- **IMPLEMENTED:** source hiện có behavior tương ứng; nếu claim về chất lượng/runtime cần thêm evidence riêng.
- **KNOWN_GAP:** source hiện vẫn có giới hạn/nhánh cần sửa.
- **PLANNED:** chỉ có trong task plan, chưa được triển khai/kiểm chứng.

Snapshot evidence và các mục runtime/hardware còn thiếu nằm trong [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md). Cách nghiệm thu nằm trong [TESTING.md](TESTING.md).
