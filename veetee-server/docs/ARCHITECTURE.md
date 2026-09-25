# VeeTee Server Architecture

Cập nhật tài liệu: **2026-09-25**
Source snapshot đối chiếu: **working tree production-hardening hiện tại** (baseline trước hardening: HEAD `3767532`). Core security/runtime changes được kiểm bằng full regression suite; xem [TESTING.md](TESTING.md).

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
| TTS deadlines | `first_chunk 1500 ms`, `stall 1500 ms`; live first-chunk budget tính từ lúc enqueue, gồm cả scheduler wait |

Local config có thể override default. Một override local quan sát trong audit không trở thành default tài liệu.

## Persona và context

### IMPLEMENTED

Provider load saved persona runtime trước; nếu không có saved persona thì dùng `llm.base_prompt` từ config. Prompt template vẫn nằm ở `agent-base-prompt.txt` và chèn `{{base_prompt}}` vào system prompt.

Persona cập nhật qua management API được persist cho các lượt sau. API/UI/config/runtime dùng chung budget `base_prompt_max_bytes` + est. tokens (mặc định `32 KiB`/`8000`), reject over-budget thay vì truncate âm thầm. Persona version được snapshot mỗi turn để các round nhất quán.

Request assembly duy nhất tính persona, semantic/control prompt, history, memory/RAG, pending actions, schemas, receipts và output reserve. Estimate chars/token giữ safety margin tiếng Việt/JSON và nhãn `estimated`; không gọi API đếm token nối tiếp mỗi turn.

### REMAINING

- Persona/history lớn vẫn cần nghiệm thu theo route/model thật (tokenizer/cache support) trước khi khẳng định capacity; cold miss có số đo riêng.
- Tóm tắt history AI nền đã có defer + snapshot/revision guard và không chặn critical path; phần còn lại là workload/correctness acceptance trên hội thoại dài thật.

## Tools, confirmation và receipts

### IMPLEMENTED (2026-09-09, HEAD `469f941` + working tree M1/M2/M4)

- Mọi receipt nghiệp vụ (kể cả clock) đều qua AI synthesis với persona/ngôn ngữ/context hiện tại; nhánh direct clock và literal `render_action_receipt_fallback` đã bỏ. Synthesis thất bại không phát claim thành công, chỉ giữ receipt có cấu trúc.
- Speech của round có thể gọi tool được buffer tới terminal validation; round có action thì discard speech vòng đó, round chat thuần mới flush. Không còn mixed invalid speech tới TTS.
- Receipt envelope chuẩn: id, origin turn, name/args bounded, status, execution outcome, changed, data/error, observed_at, provenance. `unknown` giữ unknown; awaiting confirmation khác approved khác execution succeeded.
- Bounded agent loop `1..4` rounds (default `2`): chat thường 1 inference, turn có action được synthesis + chain A→B khi cấu hình cho phép. Semantic calls tính vào budget; loop detection theo call IDs/args/receipt/deadline.
- Validation JSON Schema recursive (object/array/items, required, enum, bounds, additionalProperties, nullable/combinators, pattern, length/depth/bytes), compile/cache theo version/hash. Memory/confirmation args validate cùng chuẩn trước mutation.
- Ownership/cancel/deadline kiểm lại ngay trước dispatch/commit; cancel queued không dispatch; dispatched giữ receipt sau caller cancel. MCP là capability gate, không tin read-only marker trong description.
- Catalog vượt `schema_limit` expose explicit `veetee_tool_catalog` notice thay vì cắt im lặng; có `registry.search()` cho discovery. Independent read-only overlap qua executor semaphore + gather ở follow-up rounds.

Built-in hiện có gồm `calculate` và `get_time_in_timezone`; MCP device chỉ được expose khi board quảng bá capability và discovery trả tool tương ứng.

Chi tiết thuộc [runtime plan](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md); trạng thái trên có regression `tests/test_ai_semantics_regression.py`, `test_bounded_loop.py`.

## Memory và structured history

### IMPLEMENTED

Session memory và durable memory có ownership/revision semantics. Durable personal memory chỉ dùng authenticated owner từ paired-device mapping; `memory.trusted_owner_id` là fallback legacy, còn `Device-Id`/`Client-Id` tự khai báo không đủ làm owner tin cậy. Deployment acceptance 2026-09-25 đã PASS qua restart thật: persistence, stale-revision reject, paired owner A/B isolation trên runtime context, forget barrier và cleanup namespace synthetic đều đúng. Event `context_memory` chỉ ghi durable fact IDs (không value) để management trace có thể chứng minh isolation mà không log nội dung memory.

AI quyết định mutation từ context/schema; server không parse các từ kiểu “nhớ/quên” để tự ghi dữ liệu.

### IMPLEMENTED (M5 seam, 2026-09-09)

- Mutation và retrieval tách bạch; retrieval trả candidate data có ID/version/source/score/observed_at/provenance, không tự tạo intent/write từ keyword.
- Retriever contract chung cho memory và RAG (`RetrievalQuery`/`RetrievalCandidate`/`BaseRetriever`). Lexical FTS/BM25/LIKE là baseline; embedding/reranker là seam optional chỉ giữ khi cải thiện quality trong budget.
- Session/durable budget split để recent session không starve durable; lookup timeout/miss có metric显式, không nuốt im lặng. Authoritative DB state đọc trước khi đưa cache vào prompt; tombstone không hồi sinh.
- RAG fixture (`FixtureRAGRetriever`) với source/version/provenance; instruction-like text là data, ingestion ngoài critical path. Production RAG vẫn out of scope.
- Dialogue giữ transcript có cấu trúc (tool call/result/control + reply), bound bytes/tokens, phân biệt created/generated/sent/interrupted/unknown; receipt sau dispatch-cancel reconcile vào lượt sau.

## Latency và audio ownership

Server dùng generation/capture ownership để loại stale transcript/audio sau cancel. Phần state thuần lifecycle đã được tách vào `SessionLifecycleState` (active/closed, turn/capture/wake generations, current turn task/cancel event); `ClientSession` giữ compatibility properties để refactor từng bước thay vì big-bang. Semantic/tool/audio vẫn nằm ngoài lifecycle controller. `client_only` là policy barge-in mặc định: stock `abort` hoặc `listen:start` có thể cancel turn đang nói; speech-start tự động không được bật chỉ vì hello có `aec=true`.

Playback stock chỉ có một kênh TTS/audio nên source hiện có `PlaybackCoordinator` làm **single-writer lease** theo `owner + generation + revision`. AI speech/fixed response/error recovery dùng owner `assistant`; music dùng owner `music`. Một producer mất lease không được gửi thêm binary hoặc `tts:stop` stale. Music pause/reuse/resume lấy lease mới; semantic intent vẫn do AI/tool contract quyết định, coordinator không đọc hay match câu người dùng.

TTS được pace và bounded để giảm lượng audio gửi trước. Scheduler ưu tiên `live > dashboard > prewarm`; khi live voice đến trong lúc dashboard/prewarm đang giữ VieNeu, scheduler phát cooperative-preempt signal và background job nhường engine ở native chunk boundary. Partial background WAV không được publish/cache; dashboard nhận `409 TTS_PREEMPTED` thay vì lỗi 500 chung. Test contention thật giảm live queue wait từ khoảng **1935ms xuống 31.7ms**. Live-vs-live không preempt nhau vì VieNeu giữ internal engine lock suốt một stream; trên host GTX 1650 Ti 4GB hiện chỉ còn khoảng 815MB VRAM khi server chạy, không đủ an toàn cho replica local thứ hai.

Với live TTS, `first_chunk_timeout_ms` là budget **enqueue → first PCM**, bao gồm scheduler wait; không còn trường hợp xếp hàng nhiều giây rồi mới bắt đầu đếm first-chunk timeout. Các knob endpoint/VAD/speculation và `tts.send_ahead_ms` có thể hot-apply qua Runtime UI/API. Local Parakeet có speculative ASR: inference có thể bắt đầu trong trailing silence, bị invalidate nếu người dùng nói tiếp và chỉ reuse snapshot đủ confidence. Speculative LLM là lớp tiếp theo: chỉ bắt đầu từ transcript speculative đủ confidence, buffer toàn bộ event, **không commit speech/tool/memory trước ASR final**, cancel khi speech resume, và chỉ reuse khi final transcript + capture/dialogue/persona/catalog/pending-state khớp; lệch thì discard và chạy live turn bình thường. Đây là overlap compute theo lifecycle/acoustic confidence, không phải keyword routing.

`scripts/benchmark_endpoint_matrix.py` có thể A/B endpoint, VAD end threshold và speculative ASR/LLM trên cùng fixture, giữ OTA/auth chuẩn, khóa process-wide để không có hai matrix restore đè nhau và restore config trong `finally`. Với fixture có expected transcript, report thêm split/missing-final + WER/CER. Management-only `GET /api/turns/{turn_id}` trả retained event-level trace có secret redaction cho review/debug; stock client không dùng endpoint này.

Stock protocol không có playback queue depth/flush ACK chung, nên:

- `tts:stop` hoặc last binary ở server không phải physical speaker stop;
- first binary sent không phải first useful/voiced audio trên loa;
- AEC hiệu quả và tail thực tế phải đo trên board.

Metric benchmark hiện hành là `speech_end_to_first_voiced_pcm_received_ms` (`v2`); certification metric là `speech_end_to_first_useful_voiced_audio_received_ms` (voiced hiện chỉ là acoustic proxy cho tới khi case pass quality/grounding). TTS lease đo riêng inference vs hold (`tts_lease_held`, scheduler snapshot holds/avg/max); generation/tool/TTS first-chunk/stall/delivery budgets tách riêng. Tool schema payload và catalog fingerprint được cache theo registry revision để giảm request-assembly overhead mà không đổi semantic.

LLM providers có contract `health()/capabilities()`; diagnostics công bố provider/model/capability hiện hành thay vì suy từ config. Groq direct vẫn ưu tiên multi-key quota-aware routing trong cùng provider để giữ fast path; source không tự nối thêm một provider mạng khác sau timeout vì fallback nối tiếp có thể làm TTFA xấu đi và che lỗi cấu hình.

Runtime fast-balanced hiện hành được chọn bằng A/B ngày 2026-09-24/25: `min_silence=192ms`, `vad_end_threshold=0.2`, speculative ASR start `64ms` với confidence `0.95`, speculative LLM confidence `0.95`, model `qwen/qwen3.8-27b`. Mức 160ms nhanh hơn nhưng corpus 4 fixture ×2 lượt làm mean WER tăng từ **0.0625 → 0.125** và max WER từ **0.25 → 0.50**, nên không promote; 96ms còn split một utterance thành hai final. 192ms giữ 8/8 success, 0 split/missing-final trên corpus nhỏ hiện có.

Speculative LLM không commit side effect trước ASR final và chỉ reuse khi transcript/state snapshot khớp. Trên cùng corpus 192ms, bật speculative LLM giữ nguyên WER/CER/split và giảm latency; transport/prosody `first_soft_cut_chars=8`, tối thiểu 3 từ và chỉ cắt ở whitespace, tiếp tục đưa speech sang TTS sớm mà không đổi ASR quality. Warm smoke 5 lượt trên profile vận hành hiện tại đạt server `last_voice_to_first_ws_binary` **p50 556ms, p95 597ms**, WER/CER 0 trên fixture; TTS first PCM warm khoảng **107ms** nên bottleneck còn lại chủ yếu ở endpoint/ASR + upstream LLM. Client benchmark `speech_end_to_first_voiced_pcm_received_ms` vẫn khoảng **0.9s p50** trên fixture hiện tại, vì vậy **không tuyên bố SLA 600ms end-to-end đã đạt tổng quát**.

Các đối chứng không được promote: `min_silence=160ms` làm strict WER xấu hơn; `96ms` split utterance; `vad_end_threshold=0.3/0.4/0.5` tăng WER và 0.4/0.5 còn hạ success xuống 75%; speculative ASR start 32ms với confidence 0.95 giữ quality nhưng không cải thiện aggregate latency so với 64ms; speculative TTS prefetch giữ quality nhưng median server latency xấu hơn baseline nên vẫn OFF. Runtime vì thế giữ **192ms / VAD end 0.2 / speculative ASR 64ms@0.95 / speculative LLM@0.95 / speculative TTS OFF / soft-cut 8 chars, 3 words**.

Load probe dùng server `turn_finish.outcome` làm authoritative: recovery audio/`tts:stop` không được tính là AI success. `tts.stream_queue_max_chunks` là hot-runtime/UI knob cho bounded producer queue của VieNeu, nhưng A/B production streaming path 4/16/64 chunks với prompt dài và 2 live sessions không cho thấy 16/64 giảm queue wait hay lease hold; 64 còn xấu hơn nhẹ. Runtime giữ **4 chunks**.

Để giảm head-of-line blocking mà không thêm matcher semantic, scheduler phân biệt **first audio của turn** (`live_first`) với continuation segment (`live`). Turn chưa phát binary nào được ưu tiên trước continuation đã có audio; aging tăng ưu tiên theo thời gian chờ để continuation không starvation. Hai hệ số là runtime/UI knobs `tts.first_audio_priority_boost` và `tts.scheduler_aging_per_second` (profile hiện tại **5.0 / 2.0**), hot-apply không restart. A/B nhỏ cùng 2 sessions ×2 turns: so với FIFO trước đó, first-WS p95 giảm khoảng **3137 → 1514 ms**, TTS queue-to-lock p95 **2616 → 1002 ms**, full-turn p50 **4.84 → 4.44 s**, vẫn 4/4 completed. Mẫu 2×4 giữ queue/first-audio tốt hơn ở các turn hoàn tất nhưng 2/8 fail do **Groq quota capacity trước TTS**, nên không quy lỗi đó cho scheduler và không che bằng retry nối tiếp.

Trên host hiện tại, 1 session có thể đạt first-WS khoảng vùng 0.5–0.6s; 2 live sessions vẫn bị single-engine TTS serialization dù fairness đã tốt hơn; 4 sessions burst có thể chạm cả TTS queue và Groq capacity. Đây là capacity limit cần scale resource/provider, không được che bằng retry/fallback nối tiếp. A/B model vẫn giữ Qwen 3.8 27B; `gpt-oss-20b` chậm hơn trong smoke.

Management plane có `GET /api/turns/{turn_id}` để trả retained event-level trace cho debug/human review; endpoint yêu cầu management token và scrub credential-shaped fields. `build_semantic_review_queue.py` tạo review queue 200/143-critical/52-held-out, còn `semantic_review_probe.py` thu protocol + turn trace thật nhưng **không tự chấm semantic** và không chạy held-out mặc định. Đây là evidence tooling, không phải runtime router. Xem [TESTING.md](TESTING.md).

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
