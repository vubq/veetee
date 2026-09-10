# Hoàn thiện AI semantics, persona lớn, tool/memory và pipeline dưới 1 giây

Status: `PARTIAL`

Created: `2026-09-09`

## Goal

Người dùng hội thoại tự nhiên với nhân vật AI, kể cả khi persona dài, cần gọi tool, nhớ thông tin hoặc tra cứu thêm. AI quyết định intent, ngôn ngữ, cách diễn đạt, chọn capability, tham số, xác nhận, memory mutation và kết thúc từ context. Server thực thi có kiểm soát và cung cấp dữ liệu thật; không dùng matcher câu nói hoặc template nghiệp vụ để thay AI.

Mục tiêu trải nghiệm là audio trả lời hữu ích đầu tiên dưới 1 giây, lý tưởng p50 ≤600 ms, càng thấp càng tốt nhưng phải giữ chất lượng, persona và receipt đúng. Không đổi định nghĩa thành thời gian tới câu đệm, token hoặc binary có silence để báo đạt.

Đây là kế hoạch thực thi sau audit, không phải tuyên bố implementation đã hoàn tất. Trong lượt lập plan chỉ tạo/cập nhật tài liệu trong `task-plans`; không sửa runtime, đổi model/config, restart, bật durable/MCP, chạy tải hay xóa tài liệu.

## Current state

### Baseline và độ tin cậy của evidence

- Audit trên working tree ngày 2026-09-09, HEAD `986674c`, có nhiều thay đổi tracked/untracked từ các lượt trước. HEAD một mình không đại diện source đã audit.
- Đã đọc ASR/VAD, session, provider/runner/parser, TTS/scheduler, tools/executor, memory/context, management persona API/UI, config, tests, benchmark và docs. Không coi số test trong tài liệu cũ là kết quả test của lần audit này.
- Kiểm tra cô lập trong bộ nhớ đã tái hiện fallback mất receipt thứ hai, che lỗi execution/revision và validator bỏ qua schema lồng nhau. Đây là kiểm tra hàm, không phải model/runtime/ESP32.
- Artifact gần nhất `../veetee-server/benchmark-artifacts/pipeline-auto-20260909-013401.jsonl` chỉ có 1 lượt completed: STT final 350.008 ms, first clause 1026.926 ms, first binary 1340.281 ms, first voiced PCM received 1536.762 ms kể từ speech-end. Sample gate chưa đạt, không suy p95 quần thể từ 1 mẫu.
- Config local đang có VAD silence 320 ms, correction tắt, context budget 8192 token ước lượng, durable tắt, device MCP tắt, tối đa 3 business calls/2 LLM rounds. `settings.py` và example vẫn dùng VAD 450 ms. Đây là file config đã đọc, chưa xác minh process hiện chạy đúng snapshot đó.
- Prompt persona lưu ở `veetee-server/data/base-prompt.txt` có thể ghi đè persona trong config. Không in/commit nội dung persona riêng hoặc credential để lập benchmark.

### Findings cần xử lý

Đường dẫn trong bảng tính từ `veetee-server/`; số dòng là mốc lúc audit, executor tìm lại theo symbol.

| ID | Ưu tiên | Hiện trạng và tác động | Điểm vào source |
| --- | --- | --- | --- |
| A01 | P1 | `get_current_time` đi nhánh direct render: câu tiếng Việt, ngày/giờ và emotion cố định; nhiều receipt chỉ trả kết quả đầu; mất persona/ngôn ngữ/ý nghĩa câu hỏi. | `core/session.py:1518,1985` |
| A02 | P1 | Fallback literal không hiểu nested confirmation execution/memory statuses; `approve + execution.failed` và `revision_conflict` thành “Mình đã xử lý yêu cầu”. | `core/session.py:1518,1796,2101` |
| A03 | P1 | Speech được phát trước terminal validation; mixed read-only được cho phép, mixed action bị reject sau khi speech có thể đã tới TTS. | `core/providers/llm/omniroute_groq.py:746,799,813`; `core/session.py:1737` |
| A04 | P1 | Budget bỏ sót semantic system prompt; estimate 4 chars/token không đảm bảo tiếng Việt/JSON; persona lớn có thể làm request lỗi hoặc mất context. | `core/session.py:_fit_llm_context`; `core/context_builder.py:fit_to_budget` |
| A05 | P1 | API và textarea giới hạn persona 4000 ký tự; setter/state path cần validation budget thống nhất, không chỉ tăng maxlength. | `http_server.py:handle_set_prompt`; `static/index.html:inputBasePrompt`; provider setter |
| A06 | P2 | Round 2 ép no-tools/no-end; config chỉ chấp nhận 1/2 rounds và ≤3 calls; chưa chạy được tool phụ thuộc kết quả lượt trước. | `core/session.py:2048`; `config/settings.py:_validate_app_config` |
| A07 | P2 | Memory FTS/BM25/LIKE và recent top-k; lookup timeout im lặng; chưa semantic retrieval/tóm tắt history. | `core/memory/retrieval.py`; `store.py:search_active`; `context_builder.py:build` |
| A08 | P2 | Dialogue chỉ giữ role/content; receipt có cấu trúc của lượt xong không tồn tại trong context lượt tiếp theo. | `core/dialogue.py`; `core/session.py:2140` |
| A09 | P1 trước mở rộng tools | Schema validation nông; semantic memory/confirmation events có nhánh riêng cần kiểm tra cùng chuẩn trước mutation. | `core/tools/registry.py:validate_arguments`; provider semantic event adapter |
| A10 | P2 | Catalog cắt 16 tool đầu theo thứ tự đăng ký; MCP chỉ status/volume; timezone mặc định nằm trong handler/schema description. | `registry.py:openai_tools`; `mcp_device.py:SAFE_DEVICE_TOOLS`; `builtin/time_tool.py` |
| A11 | P2 | Session await từng tool; TTS lease kéo dài qua consumption/pacing/backpressure; ASR inference dùng lock chung. | `core/session.py:1948,1664`; `providers/tts/vieneu_local.py`; ASR runtime |
| A12 | P1 nghiệm thu | Test đang bắt clock skip AI synthesis; docs vừa cấm hardcode vừa cho phép direct clock; sample/corpus chưa đủ. | `tests/test_turn_lifecycle.py:531`; docs/status; benchmark artifacts |

### Quan hệ với các plan trước

- Tiếp nối [plan AI không hardcode](2026-09-08-hoi-thoai-ai-khong-hardcode.md), [pipeline 600 ms](2026-09-08-pipeline-600ms-intent-memory-tools.md) và [ổn định pipeline](2026-09-08-on-dinh-va-toi-uu-pipeline.md).
- Không xóa, đổi tên hoặc viết lại lịch sử các plan. Checkbox cũ là evidence của thời điểm đó, không chứng nhận các nhánh mới/thay đổi sau đó.
- Trong phạm vi A01–A12, plan này thay hướng thực thi cũ cho direct clock/template fallback, explicit-only matcher, trần đúng 2 rounds và assertions cho các hành vi đó. Không phục hồi chúng khi tối ưu tốc độ.
- Corpus/hardware/ownership gates còn thiếu ở plan trước tiếp tục được giữ, không bị bỏ vì chuyển sang plan mới.
- Việc sắp xếp docs theo [plan tài liệu đi kèm](2026-09-09-hop-nhat-tai-lieu-du-an.md). Mỗi quyết định kỹ thuật chỉ có một nơi mô tả hiện hành; `task-plans` giữ handoff và evidence lịch sử.

## Scope

### In scope

- A01–A12: runtime semantics/receipts, schema, persona/context, bounded multi-round tool loop, structured history, memory retrieval và điểm mở rộng RAG.
- Đo và tối ưu ASR/LLM/TTS/network/queue theo từng đường chat/tool/memory; thử nghiệm model/route trong profile tách biệt khi cần so sánh, không tự thay deployment mặc định.
- Regression có ý nghĩa hành vi; corpus model thật, latency theo persona/tải, test stock ESP32; cập nhật evidence và docs theo implementation thực tế.
- Nền tảng RAG: interface, provenance, ingestion/index ngoài live path, fixture test và một thử nghiệm retrieval giới hạn để chứng minh mở rộng được.

### Out of scope

- Sửa/build/flash firmware, yêu cầu capability hoặc playback ACK mới; tự áp patch trong repo/reference.
- Xây RAG production với nguồn dữ liệu thật chưa được chỉ định; tự crawl/index dữ liệu riêng, mua dịch vụ/GPU, đổi model mặc định hoặc bật side effects/durable trên môi trường đang dùng.
- Giao protocol, SQL, phép tính, đồng hồ, permissions và cancellation cho LLM; “AI quyết định semantics” không có nghĩa model được bịa dữ liệu/execution receipt.
- Cam kết mọi tác vụ mạng/RAG nhiều bước hoàn tất dưới 1 giây không phụ thuộc backend. Các đường chưa đạt vẫn phải báo `NOT_MET`, không được bỏ khỏi báo cáo.

## Implementation plan

### Thứ tự và dependency

```text
M0 baseline + corpus + thống nhất phép đo
  -> M1 contract/receipt/speech ordering + M2 schema/execution guards
  -> M3 persona/context + M4 bounded tool loop/history
  -> M5 memory/retrieval/RAG seam
  -> M6 latency A/B và scheduling
  -> M7 model corpus + tải + hardware + docs acceptance
```

M3 có thể chuẩn bị song song M1/M2, nhưng tích hợp phải dùng cùng request/contract. Chỉnh docs theo plan đi kèm có thể làm sau M0; không mô tả behavior tương lai như đã triển khai. M6 profiling bắt đầu từ M0, thay đổi tối ưu chỉ merge khi correctness gates liên quan đã đạt.

### M0 — Snapshot, phép đo và ca tái hiện

Files: `core/turn_metrics.py`, `scripts/benchmark_pipeline.py`, `tests/`, `benchmark-artifacts/` khi thực thi.

- [x] M0.1 Ghi HEAD + dirty source fingerprint, config fingerprint đã lọc secret, persona hash/token count, schema hash, route/model thực nếu quan sát được, hardware/driver, phiên bản deps và process start. Không gán artifact cũ cho source mới.
- [x] M0.2 Reproduce A01/A02/A03/A04/A05/A09 thành regression: 2 múi giờ; hỏi ngày nhưng không hỏi giờ; persona khác xưng hô; ngôn ngữ khác; mixed receipts; nested failure; schema lồng; prompt gần/vượt budget; persona qua API/UI.
- [ ] M0.3 Tạo corpus có expected semantic outcome và evidence cần dùng, không exact-match câu trả lời AI. Tách train/dev dùng tune prompt và held-out không dùng tune.
- [x] M0.4 Tách client speech-end, endpoint, ASR queue/lock/infer/final, context lookup, LLM request/header/content/control/tool-ready, receipt-ready, TTS admission/first PCM/Opus, first binary/voiced PCM và playback physical.
- [x] M0.5 Phân biệt VeeTee request, LLM round và gateway attempt/fallback. Không quan sát được route/attempt/cache thì ghi `unknown`, không suy từ HTTP headers nhanh hoặc GET `/models`.
- [x] M0.6 Thống nhất sample gate và success gate giữa script, diagnostics và docs: benchmark quick smoke không được dùng chứng nhận SLA. Nâng gate nghiệm thu 100 attempts/nhóm và success ≥99%, giữ thống kê lỗi đầy đủ.

Gate: có danh sách lỗi fail trước fix, định nghĩa metric và snapshot dùng lặp A/B. Chưa tuyên bố runtime baseline mới cho tới khi thực sự chạy.

### M1 — Contract AI thống nhất, receipt và thứ tự speech

Files: `core/ai_contract.py`, `core/turn_events.py`, `core/providers/llm/omniroute_groq.py`, `core/providers/llm/stream_parser.py`, `core/turn_runner.py`, `core/session.py`, `core/response_audio_cache.py`.

- [x] M1.1 Chốt semantic contract version mới nếu thay wire format nội bộ. AI chọn speech/tool/memory/confirmation/end từ context/schema; metadata nội bộ không xuất ra ESP32/TTS. Server validate enum/schema/ownership, không phân tích keywords câu nói.
- [x] M1.2 Bỏ `direct_receipt_ready` riêng clock và `render_action_receipt_fallback`. Mọi kết quả nghiệp vụ cần diễn đạt đều đi AI với persona/ngôn ngữ/context hiện tại và toàn bộ receipt liên quan.
- [x] M1.3 Chuẩn hóa envelope receipt cho business tool, memory và confirmation: ID, origin turn, name/args có giới hạn, outcome, execution outcome, changed, data/error, observed_at, provenance. Phân biệt awaiting confirmation với approved và với execution succeeded; `unknown` không thành failed/succeeded tự đoán.
- [ ] M1.4 Spike ordering của model/gateway thật: tool-only, speech-only, content trước tool, tool trước content, nhiều tool, EOF/length/errors, late tool sau speech. Ghi thứ tự byte/event và trạng thái phát audio, không chỉ kiểm tra handler không chạy.
- [x] M1.5 Chốt cơ chế speech commit từ contract có cấu trúc do AI sinh. Một nhãn prefix đơn lẻ không chứng minh phần còn lại sẽ không phát tool; không dùng keyword trên lời nói để lọc claim. Nếu route không cung cấp ranh giới đủ kiểm chứng, buffer speech của round có thể gọi tool tới terminal hợp lệ là phương án correctness mặc định; đo rõ chi phí và giữ SLA `PARTIAL` nếu chậm.
- [x] M1.6 Với round cuối sau receipt có thể stream speech theo mode không dispatch action mới; việc cần tool tiếp theo do AI quyết định ở vòng reasoning/tool được phép trước đó. Không giải quyết mixed stream bằng cho phát claim rồi reject tool quá muộn.
- [x] M1.7 Lỗi trước speech: recovery AI-authored đã prewarm đúng persona/language profile, không assertion về nghiệp vụ. Thiếu asset tương thích báo degraded theo protocol/status; không chọn ngôn ngữ bằng ký tự/regex và không thêm inference nối tiếp vô hạn khi upstream lỗi.
- [x] M1.8 Lỗi sau partial speech: giữ evidence generated/sent/interrupted, không phát lại toàn bộ, không retry side effect đã dispatch. Phát recovery chỉ theo policy lỗi hữu hạn, không diễn dịch lỗi thành ý định mới.
- [x] M1.9 Ngày/giờ default là cấu hình server cung cấp trong context/schema; AI chọn timezone khi user/context yêu cầu. Clock thực thi thời gian thật bằng thư viện; không cache đáp án “hiện tại” qua lượt khác và không để persona example làm dữ liệu giờ.

Gate: đổi persona/ngôn ngữ không làm tool reply rơi về câu literal; nhiều receipt được AI đọc đủ; không lời thành công từ renderer, không speech từ mixed invalid round; side effect chỉ sau terminal/schema/permission hợp lệ. Cold/failure asset và latency của buffering được báo riêng.

### M2 — Validation và execution invariants

Files: `core/tools/registry.py`, `core/tools/base.py`, `core/tools/executor.py`, `core/tools/results.py`, `core/intent.py`, `core/memory/models.py`, session semantic event handlers, `tests/test_tool_execution.py`, `tests/test_intent_policy.py`, `tests/test_memory.py`.

- [x] M2.1 Dùng validator JSON Schema được xác minh phù hợp hoặc công bố subset hỗ trợ và reject schema ngoài subset; kiểm tra recursive object/array/items, required, enum, bounds, additionalProperties, nullable/combinators nếu hỗ trợ. Không mặc định coi type chưa biết là hợp lệ.
- [x] M2.2 Validate cả `veetee_memory`/confirmation args trước coercion và trước mutation; boolean không thành revision integer, chuỗi số không tự nhận nếu schema yêu cầu integer; giới hạn độ dài/depth/bytes.
- [x] M2.3 Schema phải được kiểm tra/compile khi đăng ký, cache theo version/hash; validation mỗi call không kéo dependency/model initialization vào hot path.
- [x] M2.4 Ownership/cancel/deadline kiểm lại ngay trước dispatch/write commit. Dedupe theo origin turn/call + args; confirmation gắn đúng ID/args/revision/TTL; không suy approve từ lời nói ngoài AI decision.
- [x] M2.5 Cancel queued tool không dispatch; dispatched tool có completion/unknown receipt giữ được sau caller cancel. Với DB thread, cancel coroutine không chứng minh SQL rollback: kiểm tra write barrier và owner/revision tại commit, có test race.
- [x] M2.6 Policy MCP thuộc quyền cho phép capability, không semantic routing. Không tự tin read-only marker trong description không tin cậy; metadata execution safety do server quản lý. Chưa mở tool mới trước schema/permission gate.

Gate: các nested-invalid cases audit bị reject; không mutation từ semantic args sai; cancel/revision/confirmation races có receipt thật và không execute trùng. Không bỏ validator để giảm latency.

### M3 — Persona lớn và request context có ngân sách chính xác

Files: `core/context_builder.py`, provider request assembly/setter, `core/ai_contract.py`, `http_server.py`, `static/index.html`, `config/settings.py`, `config.example.yaml`, `server.py`, `tests/test_context_budget.py` và management tests.

- [x] M3.1 Một request assembly dùng chung cho budget và provider: persona, semantic/control prompt, history, memory/RAG, pending actions, schemas, receipt và output reserve đều được tính. Không append system prompt sau bước fit mà không tính lại.
- [x] M3.2 Đếm token theo tokenizer/model đã xác minh; cache token counts persona/schema bất biến và history incrementally. Nếu route không cung cấp tokenizer phù hợp, dùng ước lượng đã hiệu chuẩn bằng usage thật, safety margin và nhãn estimated; không gọi API đếm token nối tiếp mỗi turn.
- [x] M3.3 Thay trần API/UI 4000 ký tự bằng giới hạn cấu hình hợp lý gồm request bytes và token budget theo model. API, UI, config startup, saved persona và runtime setter dùng chung validation; giữ hạn mức chống input quá lớn.
- [x] M3.4 Persona vượt budget phải bị reject trước persist/apply với thông báo rõ phần vượt; persona cũ vẫn dùng được. Không truncate âm thầm instruction danh tính. Test persona trên 4000 ký tự nhưng trong budget, 2k/8k/16k token và sát giới hạn model; các profile không đủ context phải được báo unsupported rõ.
- [x] M3.5 Snapshot persona version cho mỗi turn để các round trong cùng lượt nhất quán; đổi persona áp dụng cho lượt sau. Invalidate recovery/prefix/count caches đúng version và kiểm tra cách clear history khi update, không xóa memory ngoài ý định user.
- [x] M3.6 Tách mandatory persona/contract/current user/pending action khỏi optional evidence. Bảo toàn tool-call/result groups; nếu mandatory không vừa thì trả lỗi cấu hình/input có giới hạn, không bỏ permission/receipt để nhét prompt.
- [ ] M3.7 Tóm tắt history bằng AI nền khi gần high-water mark; snapshot generation/version, bounded concurrency, chỉ commit summary nếu snapshot còn hợp lệ. Không trì hoãn mọi turn để chờ summary, không biến summary thành chứng cứ user đã nghe audio.
- [ ] M3.8 Giữ prefix ổn định khi đúng nghĩa: persona/contract/schema ổn định trước, dữ liệu phiên thay đổi sau. Đo cached input tokens và warm/cold TTFT; GET `/models` chỉ warm connection, không chứng minh model/prefix cache đã nóng.
- [ ] M3.9 Kiểm tra route alias/model thật và support caching ở thời điểm thực thi. Tài liệu Groq được đọc lúc audit giới hạn model có prompt caching; không mặc định alias Qwen qua OmniRoute đã hỗ trợ. Không đổi model chỉ để có cache mà bỏ gate persona/tool/tiếng Việt.

Gate: budget khớp request thật; persona dài dùng được qua UI/API/config trong profile hỗ trợ; no silent truncation; round-to-round persona nhất quán; cold miss vẫn đúng và có số đo riêng.

### M4 — Bounded agent loop, catalog và structured history

Files: `core/session.py`, `core/turn_runner.py`, `core/dialogue.py`, `core/turn_events.py`, `core/tools/registry.py`, executor/MCP adapter, config/settings/example.

- [x] M4.1 Tách orchestration theo round khỏi audio delivery nếu cần, giữ single owner/cancel token. AI có thể tiếp tục tool sau receipt; server giữ trần configurable và overall generation deadline, không mặc định chạy hết số round.
- [x] M4.2 Thay validation trần 1/2 rounds bằng giới hạn hữu hạn đã test; tính cả semantic calls vào budget để không có đường vòng. Chat thường vẫn một inference logic, không thêm intent classifier/mandatory planner request.
- [x] M4.3 Chạy đồng thời các tool đọc độc lập đã có args hoàn chỉnh, bị chặn bởi max concurrency/resource groups. Không song song tool B phụ thuộc data A hoặc write cùng resource; dependency do structured AI calls/context, không parse lời user.
- [x] M4.4 AI có thể clarify hoặc end sau receipt; xác nhận pending action khác hoặc đổi args tạo decision/action mới đúng ownership. Không tự auto-approve để giữ target latency.
- [x] M4.5 Lưu transcript có cấu trúc cho tool call/result/control và reply; bound bytes/tokens; phân biệt created/generated/sent/interrupted/unknown playback. Receipt sau dispatch-cancel vẫn reconcile được vào context lượt sau với nguồn gốc rõ.
- [x] M4.6 Catalog lớn không cắt im lặng 16 tool đầu: profile nhỏ expose toàn bộ tool được phép trong budget; profile lớn có discovery/search capability do AI chọn, trả schema bounded để AI gọi tiếp. Không keyword-route user text sang tool.
- [x] M4.7 Repeated call/loop detection chỉ dựa call IDs/args/receipt/deadline, không mẫu câu. Không tái dispatch side effect khi synthesis hoặc network retry; rõ số round/attempt tại diagnostics.

Gate: A->receipt->B->AI speech chạy được, chat không tool vẫn một round; follow-up hiểu receipt trước; independent reads có bằng chứng overlap và dependent/write cases giữ thứ tự; max budget/cancel không làm vòng lặp vô hạn.

### M5 — Memory retrieval và nền tảng RAG

Files: `core/memory/`, `core/context_builder.py`, structured dialogue, config, module retrieval interface mới chỉ khi cần; `tests/test_memory.py` và retrieval fixture tests.

- [x] M5.1 Tách mutation và retrieval. Mutation luôn do AI đề xuất; lưu/quên/sửa theo IDs/revisions/owner. Retrieval trả candidate data, không tự tạo intent hoặc memory write từ keyword trùng.
- [x] M5.2 Định nghĩa retriever contract dùng chung cho memory và RAG: query, owner/scope/permissions, max results, cancellation/deadline; output có ID/version/source/score/observed_at. Server chỉ dùng owner đã xác thực, AI không tự chọn namespace người khác.
- [x] M5.3 Thử hybrid lexical + embeddings với facts diễn đạt lại, phủ định, tên riêng/số và nhiều ngôn ngữ. FTS hiện có là baseline kỹ thuật hợp lệ; embedding/reranker chỉ giữ khi cải thiện quality theo corpus và còn budget.
- [x] M5.4 Index/embedding facts khi write hoặc job nền; cache theo fact version, invalidate khi sửa/quên. Read authoritative DB state trước đưa kết quả cache vào prompt, không hồi sinh tombstone từ index chậm cập nhật.
- [x] M5.5 Query embedding/retrieval có latency budget riêng; không thêm LLM classifier nối tiếp mọi turn. Best-effort enrichment có thể timeout và báo missing context; nếu AI cần dữ liệu để trả lời, cho gọi retrieval tool hoặc hỏi lại, không tự bịa câu trả lời khi timeout.
- [ ] M5.6 Background retrieval trên partial input chỉ là speculative read, có snapshot/generation và ownership; không commit memory, gọi side effect hoặc phát câu trả lời trước final intent. Hủy/bỏ kết quả nếu transcript đổi.
- [x] M5.7 RAG fixture gồm tài liệu có source/version và nội dung instruction-like không được làm system instructions. Ingestion/chunk/index nằm ngoài critical path; chỉ đưa bounded relevant chunks, giữ provenance để AI dẫn nguồn khi cần.
- [x] M5.8 Memory budget chia session/durable theo relevance, không để recent session facts chiếm toàn bộ top-k khiến durable không xuất hiện. Có metric hit/miss/timeout/truncated, không nuốt lỗi lookup hoàn toàn im lặng.
- [x] M5.9 Durable vẫn off cho tới owner binding và write-barrier gates; profile production RAG/dataset mới là công việc riêng. Fixture retrieval và interface phải hoàn tất trong plan này, không đánh dấu đã có RAG production.

Gate: paraphrase tìm lại được fact liên quan; no wrong-owner/deleted/stale facts; retrieval timeout không tạo mutation hoặc fabrication; source provenance đi xuyên suốt tới AI và history.

### M6 — Tối ưu latency có A/B, không đổi semantics lấy số đẹp

Files: ASR provider, LLM provider/splitter, TTS scheduler/bridge, `core/session.py`, `core/audio_pacing.py`, metrics/benchmark/config.

- [ ] M6.1 Chạy baseline cùng source/persona/corpus/network trước mỗi biến thể. Ghi timing distribution từng stage và critical path; không cộng p95 các stage rồi gọi đó là p95 end-to-end.
- [ ] M6.2 Endpoint A/B 450/320/256/192 ms trên tiếng Việt có ngập ngừng/tên/số/câu dài. Kiểm tra frame quantization; false endpoint và WER/CER quyết định cấu hình, không lấy trường hợp nói rất ngắn để chọn chung.
- [ ] M6.3 Đánh giá ASR incremental/streaming hoặc speculative preprocessing phía server khi runtime hỗ trợ; không tự sửa firmware hoặc dùng transcript chưa ổn định để dispatch. Correction hiện bị config chặn trong unified profile; không vô tình thêm lượt LLM correction trước chat.
- [ ] M6.4 A/B đoạn đầu TTS theo dấu câu/metadata AI, độ dài, prosody và leading silence. Không bỏ mandatory persona hoặc thay câu đáp án bằng preamble cache để giảm first-audio.
- [ ] M6.5 Đo TTS lease inference time so với thời gian giữ qua queue/pacing. Nếu tách synthesis khỏi network delivery, queue PCM/Opus vẫn bounded theo bytes/duration, worker ownership/cancel rõ, lease chỉ release khi engine thật sự rảnh; không release sớm giả để chạy song song engine không an toàn.
- [ ] M6.6 Kiểm tra live/dashboard/prewarm contention; priority chỉ có tác dụng admission, không coi prewarm đang inference được preempt. Có quota/skip prewarm khi tải, aging chống starvation, pending worker không giữ lease vô hạn.
- [ ] M6.7 Tách first-useful-audio deadline, generation/tool deadline, TTS first-chunk/stall deadline và delivery/playback budget. Không dùng timeout generation để cắt câu dài đang phát bình thường; inference native không trả quyền phải báo degraded/quarantine phù hợp, không hứa cancel thread tức thời.
- [ ] M6.8 Connection reuse, schema serialization/count cache, independent read overlap, local index và prompt prefix cache được đo riêng. Tool dữ liệu “hiện tại” cần freshness rõ; không tái dùng stale receipt cho lượt mới.
- [ ] M6.9 Chạy profile 1/2/4 session + dashboard/prewarm, warm/cold persona. Nếu model/route hiện tại không đủ, lập bảng tradeoff từ thử nghiệm profile riêng; ghi `NOT_MET` thay vì âm thầm đổi deployment hoặc tăng timeout vô hạn.

Gate: phương án chọn có giảm critical path đã đo, chất lượng không vượt ngưỡng suy giảm, không regress lifecycle/receipt; rollback từng biến thể về baseline **đã sửa correctness**, không rollback về literal/matcher.

### M7 — Nghiệm thu và handoff

- [ ] M7.1 Full regression sau milestone tích hợp, runtime semantic corpus, benchmark và hardware theo mục Validation; lưu evidence/path/version và limitations.
- [ ] M7.2 Đồng bộ tài liệu hiện hành theo plan docs; ghi các finding cũ resolved/partial/open với evidence mới. Không sửa số test lịch sử thành số mới như thể cùng một lần chạy.
- [ ] M7.3 Cập nhật checklist/Execution status từng milestone, chỉ tick behavior đã kiểm chứng. Hardware/SLA/corpus thiếu thì `PARTIAL`, có phần remaining cụ thể.
- [ ] M7.4 Handoff gồm file diff, config migration, chosen route/profile, retry/cancel behavior, metrics, test commands, rollback và những profile chưa đạt. Không commit/push/restart/deploy chỉ vì có bước trong plan.

## Compatibility constraints

- Tuân thủ [AGENTS.md](../AGENTS.md) và [REFERENCE_BASELINES.md](../REFERENCE_BASELINES.md).
- FW baseline: `c7241272f2d5fd140c77542f3cf12d09e717fc2f`; reference server: `c478257517b892047db3afaaeeb25e2b1e115931`. Đọc bằng `git show <commit>:<path>` nếu cần; kiểm tra local changes, không reset/sửa reference.
- ESP32 vẫn hello/listen/abort/TTS/MCP stock; format nội bộ AI không yêu cầu client biết. Không ép listening mode/AEC từ server khi protocol không hỗ trợ.
- Giữ V1/V2/V3, baseline Opus input 16 kHz/output 24 kHz và 60 ms; codec/pacing thay đổi chỉ sau kiểm chứng tương thích.
- Không suy first binary/sent stop thành ACK speaker/flush. Device thiếu MCP/detect vẫn có chat/ASR bình thường.
- Config legacy/inert vẫn load được hoặc có migration/deprecation rõ. Không khôi phục matcher qua flag legacy.

## Validation

### Regression và kiểm tra tĩnh

Các lệnh sau dành cho executor, chưa chạy trong lượt lập plan. Working directory là `veetee-server`; xác minh venv tồn tại trước khi chạy.

```bash
/home/quangvu/Project/venv/bin/python -m unittest tests.test_llm_stream_events tests.test_turn_lifecycle tests.test_intent_policy tests.test_tool_execution tests.test_context_budget tests.test_memory
/home/quangvu/Project/venv/bin/python -m unittest tests.test_tts_scheduling tests.test_tts_backpressure tests.test_audio_pacing tests.test_mcp_device tests.test_stock_fw_protocol tests.test_benchmark_summary
/home/quangvu/Project/venv/bin/python -m unittest discover -s tests -v
/home/quangvu/Project/venv/bin/python -m compileall -q core config server.py http_server.py scripts tests
git diff --check
```

- [ ] Focused tests cho module vừa đổi; thêm test cho lỗi audit và race, không chỉ assert chuỗi prompt hoặc hằng số.
- [ ] Config example/minimal/local redacted load; unknown schema, missing tools, invalid JSON/EOF/length, HTTP error và nonnative adapter không bypass contract.
- [ ] Persona đổi giữa round, context overflow, tool receipt dài, cancelled summary/index/write; không lẫn owner/persona/generation.
- [ ] Các nhánh recovery và optional legacy adapter đều được kiểm tra không literal nghiệp vụ hoặc keyword routing.

### Corpus model thật

| Nhóm | Tối thiểu | Kiểm tra |
| --- | --- | --- |
| Semantics tổng | 200 case riêng; ≥80 critical negative; ≥50 held-out trong 200 | Negation/quote/giả định, đổi chủ đề, multi-intent, end/continue, tool cần/không cần |
| Persona/ngôn ngữ | 4 persona tổng hợp × 3 mức độ dài được hỗ trợ × ≥10 case | Xưng hô, tone, ngôn ngữ, identity giữ qua chat/tool/error; không cần ngôn từ giống hệt |
| Memory | ≥30 dialogue nhiều lượt | Save/update/forget/recall, paraphrase, ambiguous target, revision/owner/deleted facts |
| Tool/confirmation | ≥30 dialogue nhiều lượt | 2 múi giờ, A->B, independent reads, approve/reject/đổi args/TTL, nested failed/unknown, retry |
| Retrieval/RAG fixture | ≥30 query | Relevant evidence/provenance, paraphrase, no-hit, timeout, conflicting/expired docs, instruction-like text |

- [ ] Expected labels/outcomes do người đánh giá kiểm tra; AI judge có thể hỗ trợ nhưng không tự chứng nhận critical safety. Mock chỉ kiểm transport/execution, không chứng minh model hiểu intent.
- [ ] Critical negative: 0 unauthorized mutation/action, 0 false success theo receipt trong tập kiểm; semantic outcome đúng ≥95% trên held-out. Báo toàn bộ failures và bất định, không nói bảo đảm mọi câu ngoài corpus.
- [ ] Persona adherence ≥95% trên rubric đã chốt; bắt buộc không đổi identity/language do direct renderer. Grounded answer chỉ nêu dữ liệu receipt/evidence hiện hành cần thiết.

### Latency, chất lượng âm thanh và tải

Định nghĩa chính: `speech_end_to_first_useful_voiced_audio_received_ms` là client nhận audio có tiếng đầu tiên thuộc đáp án hữu ích, tính từ speech-end fixture đã gắn nhãn. Metric existing `speech_end_to_first_voiced_pcm_received_ms` là acoustic proxy: chỉ được dùng chứng nhận sau khi case pass quality/grounding; acknowledgement/recovery không đủ điều kiện. Giữ tên cũ và version metric khi migration, không đổi âm thầm ý nghĩa artifact lịch sử.

| Profile | Sample/gate | Mục tiêu |
| --- | --- | --- |
| Chat warm LAN, 1 session, persona chuẩn và lớn trong capacity hỗ trợ | ≥100 attempts mỗi profile chứng nhận; success ≥99% | p95 <1000 ms; p50 ≤600 ms báo ACHIEVED/NOT_MET |
| Local clock/calculator và memory read/write có receipt/AI diễn đạt | ≥100 attempts mỗi nhóm chứng nhận; tách khỏi chat | Cùng target dưới 1s/600ms; chưa đạt giữ NOT_MET, không dùng direct render |
| Cold persona/model/cache miss | ≥30 attempts/profile khảo sát | Công bố riêng cold distributions; không dùng kết quả warm thay cold |
| Network tools, RAG và dependent multi-step | ≥30 attempts/nhóm khảo sát; ≥100 nếu chứng nhận | Báo first useful audio và grounded result latency; target giữ nguyên, external wait tách stage |
| 2/4 session + dashboard/prewarm | ≥100 attempts mỗi profile tải chứng nhận | Công bố p50/p95/errors/queue; không áp SLO single-session khi chưa đo |

Không bắt buộc chạy full Cartesian product ngay: chọn 2k/8k/16k persona token khi model context đủ và output/history/tools còn chỗ; mỗi profile được công bố PASS phải có mẫu riêng. Unsupported size phải báo rõ trước runtime; không loại lỗi khỏi denominator để biến profile thành supported.

Ngân sách thiết kế ban đầu cho warm chat 600 ms: endpoint ~200 + ASR/final ~80 + context ~20 + LLM tới đoạn hữu ích ~170 + TTS tới voiced ~100 + transport/decode ~30 ms. Đây là **giả thuyết để đo**, không benchmark, không cam kết GPU hiện có và không yêu cầu hạ endpoint xuống 200 bất chấp chất lượng. Với silence 320 ms hiện tại, phần còn lại chỉ còn 280 ms; tool round bổ sung phải có breakdown riêng.

- [ ] Dùng fixture tiếng Việt gắn speech_end_sample theo đúng source sample rate; warmup tách measured runs; lưu manifest đầy đủ attempt, source/config/persona/schema hashes, raw timings và rubric outcomes.
- [ ] Success gate tính trên toàn bộ attempts yêu cầu, gồm timeout/failure; cancel chủ động dùng corpus riêng. 1 success/99 timeout và all-timeout phải FAIL; thiếu sample/missing marks không PASS.
- [ ] Endpoint A/B: false-end tăng không quá 1 điểm phần trăm và WER/CER xấu đi không quá 1 điểm phần trăm tuyệt đối so với baseline cùng corpus; giữ tên/số quan trọng đúng. Đây là gate đề xuất phải ghi trước thí nghiệm, không chỉnh sau để hợp kết quả.
- [ ] Listening test so sánh đoạn đầu/tail/underrun, không chỉ first packet. Không chấp nhận cắt câu hoặc silence kéo dài để giữ scheduling đẹp.
- [ ] Rerun full suite/benchmark khi thay đổi mới tác động hoặc phát hiện lỗi, không lặp chạy rộng vô ích sau kết quả đã đủ.

### Hardware/device

- [ ] ESP32 stock hiện có: OTA/hello/audio, wake detect nếu có, nhiều lượt, persona/tool/memory, idle/end, recovery rồi hỏi tiếp.
- [ ] Ít nhất 20 normal +20 interrupt, ghi board/version/mode/network; capture server traces và acoustic recording nếu kết luận audible latency/stop/tail.
- [ ] Abort/listen-start -> last binary server đo riêng với thao tác -> loa dừng. Không yêu cầu firmware gửi telemetry mới để đạt test.
- [ ] Hardware chưa sẵn thì phần server vẫn tiếp tục; kết quả cuối `PARTIAL`, hardware `PENDING` và mô tả evidence thiếu.

## Acceptance criteria

- [ ] A01–A03: không direct/template business reply, đầy đủ receipts, ordering speech/action được kiểm chứng trên route thật; không false success/late mixed speech.
- [ ] A04–A05: request budget đầy đủ, persona >4000 ký tự trong capacity chạy qua UI/API; mandatory identity không bị truncate, cache/version nhất quán.
- [ ] A06/A08/A10: bounded multi-round + structured receipt history + catalog discovery; chat thông thường không thêm classifier; pending/end vẫn AI quyết định.
- [ ] A07: hybrid retrieval có evidence chất lượng/latency; mutation không keyword; RAG seam/fixture xong, production RAG ghi out of scope.
- [ ] A09: recursive schema/semantic guards và ownership/write/cancel barriers có test; không bật durable/device side effects khi gate chưa đạt.
- [ ] A11/A12: A/B có breakdown/tải/quality; test không bắt hành vi hardcode cũ; docs dẫn đúng evidence mới.
- [ ] Warm chat persona chuẩn/lớn thuộc supported profile đạt ≥100 attempts, success ≥99%, p95 <1000 ms; p50 ≤600 ms báo riêng. Tool/memory/RAG profiles giữ bảng mục tiêu và trạng thái thực, không ẩn profile chưa đạt.
- [ ] Hardware và critical semantic corpus có evidence. Thiếu gate bắt buộc hoặc SLO chưa đạt thì tổng `PARTIAL`; chỉ `COMPLETED` khi đã đạt tiêu chí bắt buộc và không còn việc trong scope chưa làm. p50 600 ms là stretch goal phải công bố riêng nếu chưa đạt.
- [ ] Không sửa FW/reference, không mất task-plans, không commit dữ liệu riêng/secret; mọi delta với kế hoạch được ghi lý do và ảnh hưởng chất lượng/latency.

## Risks / open questions

- Streaming speech trước terminal và native tool calling có tradeoff correctness/latency. M1 spike là gate kiến trúc bắt buộc; không tuyên bố prefix metadata đã giải quyết mọi late mixed output khi chưa có evidence.
- Chưa biết model thực sau alias OmniRoute, context capacity/tokenizer/cache support/cached tokens và gateway attempts; M0/M3 phải kiểm chứng, không dựa tên model trong config.
- Người dùng chưa chốt kích thước persona tối đa hoặc corpus RAG production. Dùng persona tổng hợp 2k/8k/16k token để khảo sát, giữ nguyên persona riêng; báo capacity hỗ trợ thay vì mặc định chỉ hỗ trợ persona ngắn.
- Single shared ASR/TTS engine và GPU contention có thể không đạt 600 ms/4 phiên; tối ưu bounded scheduling trước, không hứa đạt bằng cấu hình timeout.
- Hybrid retrieval/query embedding/reranker có thể tăng TTFT; chỉ giữ cấu hình giúp chất lượng trong latency budget, có lexical fallback kèm missing-evidence status thay vì hallucination.
- Tool execution phụ thuộc dịch vụ ngoài không có trần thực tế 1s; không giảm semantics/confirmation để đạt số. Ghi stage và gap, đề xuất bước tiếp theo nếu profile chưa đạt.
- Long-prompt cache miss, summary race, persona change giữa lượt và stale index/tombstone là ca bắt buộc, không để ngoài acceptance chỉ vì khó đo.
- Tài liệu cũ còn explicit-only/template assertions và trạng thái đã tick quá rộng. Chỉ dẫn user mới và plan này có ưu tiên trong scope; không tự xóa evidence lịch sử để hết mâu thuẫn.

## Tài liệu tham chiếu

- [Quy tắc dự án](../AGENTS.md), [baseline reference](../REFERENCE_BASELINES.md), [quy tắc task-plans](AGENTS.md).
- [Plan hợp nhất tài liệu](2026-09-09-hop-nhat-tai-lieu-du-an.md).
- [Groq Prompt Caching](https://console.groq.com/docs/prompt-caching): đã đọc trong audit 2026-09-09; executor kiểm tra lại supported models/cache behavior của route thực trước áp dụng.
- [Groq Local Tool Calling](https://console.groq.com/docs/tool-use/local-tool-calling): tham khảo khi spike round loop; không coi tài liệu provider là evidence gateway đã hỗ trợ tương đương.

## Execution status

- Status: `PARTIAL`
- Executed: `2026-09-09` trên HEAD `469f941` + working tree (không commit/push/restart/deploy).
- Completed (code + unit evidence):
  - M0: snapshot `veetee-server/benchmark-artifacts/m0-snapshot-20260909-201955.json`; regression `tests/test_ai_semantics_regression.py` (7); corpus seed `veetee-server/eval/semantic_corpus_seed.json` (42 case) + `eval/README.md`; metric version `v2` + smoke/cert gates trong `scripts/benchmark_pipeline.py`.
  - M1: bỏ direct clock + literal fallback; receipt envelope `core/receipts.py`; buffer speech tới terminal, discard khi có action; synthesis bounded cho mọi receipt; end intent ở vòng synthesis; recovery prewarm giữ nguyên; clock freshness via receipt-only.
  - M2: recursive JSON Schema validator + schema compile/cache (`core/tools/registry.py`); validate memory/confirmation trước mutation; ownership/cancel/deadline recheck; executor dedupe/receipt TTL; MCP capability gate.
  - M3: unified request assembly tính semantic prompt + `persona_version`/`catalog_hash`; shared persona byte/token budget (`config/settings.py`, `http_server.py`, `static/index.html`, `config.example.yaml`); reject over-budget; snapshot persona/turn; mandatory vs optional via fit error (không bỏ permission/receipt).
  - M4: bounded loop `1..4` rounds (default 2), chat 1 inference; A→B chain khi `max>=3`; parallel independent reads (executor semaphore + gather follow-up); clarify/end sau receipt; structured transcript + receipt history + playback states; explicit catalog notice + `registry.search()`; loop detection theo IDs/args/receipt/deadline.
  - M5: retriever contract (`RetrievalQuery`/`RetrievalCandidate`/`BaseRetriever`), lexical baseline + embedding seam, session/durable budget split, lookup metrics, tombstone guard, RAG fixture `FixtureRAGRetriever` + `tests/test_retrieval_rag.py` (5); durable vẫn off.
  - M6 (code): benchmark cert gates `100/99%` + `sla_status`, TTS lease hold metrics + scheduler snapshot, split deadlines `tts.first_chunk/stall`, `tests/test_bounded_loop.py` (5, gồm overlap read).
  - M7 (partial): unit `148/148 PASS` (venv `/home/quangvu/Project/venv/bin/python`, 2026-09-09; gồm 4 test mới `test_ai_semantics_regression/test_bounded_loop/test_clock_context/test_retrieval_rag`), `compileall` PASS, `git diff --check` PASS; docs `ARCHITECTURE/TESTING/STATUS/SETUP` đồng bộ theo implementation thực tế.
  - Files: `core/session.py`, `core/tools/registry.py`, `core/receipts.py` (mới), `core/dialogue.py`, `core/context_builder.py`, `core/memory/retrieval.py`, `core/providers/llm/omniroute_groq.py`, `core/providers/tts/scheduler.py`, `core/providers/tts/vieneu_local.py`, `config/settings.py`, `config.example.yaml`, `http_server.py`, `static/index.html`, `scripts/benchmark_pipeline.py`, `scripts/snapshot_m0.py` (mới), `tests/test_turn_lifecycle.py`, `tests/test_ai_semantics_regression.py` (mới), `tests/test_bounded_loop.py` (mới), `tests/test_retrieval_rag.py` (mới), `veetee-server/eval/*`, docs `ARCHITECTURE/TESTING/STATUS/SETUP`.
  - Commands (từ `veetee-server/`, venv `/home/quangvu/Project/venv/bin/python`): `unittest discover -s tests` → `148 OK`; focused suites `test_llm_stream_events/test_turn_lifecycle/test_intent_policy/test_tool_execution/test_context_budget/test_memory` OK; `test_tts_scheduling/test_tts_backpressure/test_audio_pacing/test_mcp_device/test_stock_fw_protocol/test_benchmark_summary` OK; `compileall -q core config server.py http_server.py scripts tests` OK; `git diff --check` OK; `scripts/snapshot_m0.py` OK.
- Remaining (giữ `NOT_MET`/`PENDING`, không ẩn):
  - M0.3 full corpus 200 case/≥80 critical negative/≥50 held-out + persona 4×3×10 + memory/tool/retrieval ≥30 mỗi nhóm; M1.4 spike ordering route/gateway thật; M3.7 summary nền; M3.8/M3.9 tokenizer/prefix-cache/route-alias trên model thật; M5.6 speculative read guard live; M6 A/B endpoint 450/320/256/192ms + WER/CER, ASR streaming, TTS segmentation/prosody, contention 1/2/4 sessions + dashboard/prewarm, warm/cold persona, 100-attempt warm chat/tool/memory/RAG chứng nhận p95<1000ms + p50≤600ms báo riêng; M7 hardware ESP32 stock 20 normal + 20 interrupt + acoustic recording.
  - Tổng acceptance/SLA/hardware: `PARTIAL`; chỉ `COMPLETED` khi gates bắt buộc trên có evidence.
- Deviations from plan:
  - Không rollback về literal/matcher khi tối ưu latency; test cũ bắt direct-clock/literal đã được thay bằng assertions synthesis/receipt mới.
  - First-round speech buffering làm first-audio trễ tới sau LLM round completion (correctness mặc định M1.5); SLA giữ `PARTIAL` nếu chậm, không đổi semantics lấy số đẹp.
  - Persona default vẫn `2` rounds và `max_calls=3`; chain A→B cần operator cấu hình `max_llm_rounds_per_turn>=3`.
  - Không sửa FW/reference; không commit/push/restart/deploy; không in persona riêng/secret vào artifact.
  - Hotfix 2026-09-09 (ngoài checklist gốc): model phát `[end]` nhầm cho câu hỏi ngày (`'Ngày mấy.'`, `'Hôm nay là thứ mấy.'`) khiến server đóng transport sau vài lượt (log `conversation_close_requested reason=ai_end_intent`). Đã siết `INLINE_CONVERSATION_CONTROL_PROMPT` (câu hỏi xin thông tin luôn `[continue]`), giữ AI là bên quyết định end intent; restart server PID `1873444`, health `healthy`/readiness `ready`. Nếu tái diễn, nghi route/gateway không ổn định (2 lượt chậm 5s gợi ý fallback model khác) → A/B route riêng.
- Handoff rule: executor đã đọc lại source/working tree trước sửa; mỗi milestone có ngày/files/commands/outcomes/artifacts/limitations/remaining ở trên. Không kế thừa PASS từ lượt lập kế hoạch; số `148` là lần chạy 2026-09-09 (144 cũ + 4 test mới), không gộp với `127/84/50/112` lịch sử.
- Follow-up 2026-09-09 (audit hardening): xóa secret Deepgram khỏi `config.yaml` local (chuyển sang `DEEPGRAM_API_KEY` env + fallback khi YAML trống trong `config/settings.py`); đồng bộ `config.yaml` với `config.example.yaml` (timezone/persona budget/TTS deadlines/`max_parallel_read_only`, comment LEGACY/INERT, giữ local override có ghi chú); thêm `requirements.txt` pin + `start.sh` cài từ file; `SETUP/TESTING.md` ghi rõ dùng venv + baseline 148; `eval/` seed 42 cases được track như corpus khởi điểm (chưa phải gate 200-case M0.3/M7).
- Follow-up 2026-09-10 (deterministic idle end, theo yêu cầu user): hết `idle_timeout_seconds` không tương tác hội thoại (câu hỏi user / câu trả lời AI) thì phiên luôn kết thúc — LLM chỉ sinh 1 câu chào, phát xong đóng WebSocket code 1000. Bỏ vòng AI `[continue]` vô hạn và bỏ special-case `idle_timeout` giữ transport mở trong `_close_transport_and_session`. Lượt user chen vào giữa chừng vẫn hủy flow qua activity revision. Tests idle viết lại theo behavior mới (149 tests).
- Follow-up 2026-09-10 (farewell validation + multilingual note): spot-check model thật lòi 3 lỗi (HTTP 400 khi phiên chưa có user turn; câu hỏi ngược; câu mời nói tiếp) — đã fix (placeholder user, retry 1 lần, fallback an toàn, validate cả waiting/invite thiếu closing signal). Markers hiện vi-only + fallback tiếng Việt: đúng vì product vi-only; khi đa ngôn ngữ thì dùng union markers đa ngữ + dòng prompt "cùng ngôn ngữ hội thoại", không classifier. Tests 154. Docs ARCHITECTURE/STATUS/TESTING/SETUP/README đồng bộ HEAD `c92192a`.
