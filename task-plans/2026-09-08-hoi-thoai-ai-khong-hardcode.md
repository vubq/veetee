# Hội thoại do AI quyết định, bỏ hardcode ý định và câu trả lời

Status: `PARTIAL`

Created: `2026-09-08`

## Goal

Thực hiện yêu cầu mới nhất của user: mọi hành vi hiểu lời nói và tạo phản hồi phải theo hướng AI, dựa trên toàn bộ ngữ cảnh liên quan. Không dùng từ khóa, regex, danh sách câu hoặc mẫu trả lời cố định để thay AI quyết định intent, chọn tool/function, kết thúc phiên, lưu/quên memory, xác nhận thao tác, chọn nội dung lời chào hoặc ngôn ngữ.

Người dùng không cần học câu lệnh đặc biệt. Cùng một câu có thể mang ý định khác tùy lịch sử; AI phải phân biệt câu hỏi, trích dẫn, ví dụ, giả định, phủ định, sửa ý và yêu cầu thực hiện. AI đề xuất hành động và diễn đạt; server kiểm tra quyền, trạng thái, dữ liệu và thực thi dựa trên kết quả thật.

Implementation server-side đã được thực hiện trong working tree; plan vẫn `PARTIAL` cho tới khi corpus model thật, semantic quality gate, latency SLA và ESP32 thật đạt acceptance. Không coi unit/mock tests là bằng chứng model luôn hiểu đúng.

## Current state

### Baseline và bằng chứng

- HEAD: `986674cd655a6a1c7af9ff745e45c5d3974175a8` — `feat: stabilize and optimize voice pipeline`.
- Trước khi lập plan: `task-plans/README.md` có thay đổi local; `veetee-server/docs/PIPELINE_REVIEW_2026-09-08.md` chưa track. Giữ các nội dung có sẵn.
- Suite phiên trước **112/112 PASS**; audit sau đó vẫn tái hiện lỗi mới. Đây không phải bằng chứng đạt semantic intent/memory. Không tiếp tục kết luận F2 đã hoàn tất.
- Service user `veetee-server-bg.service` được quan sát active, khởi động 19:38:15; commit hiện tại được tạo 22:06:57. Không đồng nhất source đang đọc với toàn bộ code process đã nạp; executor phải xác minh runtime version trước test.
- Cấu hình local đã đọc theo whitelist: conversation bật; exit list gồm “tạm biệt”, “kết thúc trò chuyện”, “thoát trò chuyện”; idle 120 giây; greeting/goodbye AI bật, fallback text của chúng rỗng. Semantic end bật; memory bật/durable tắt; native tools bật, MCP tắt; tool synthesis tắt; ASR correction tắt.

### Các nhánh cần thay đổi

| ID | Hiện trạng / ca tái hiện | Nguồn chính |
| --- | --- | --- |
| H1 | Exact-match exit chạy trước LLM; `"tạm biệt"` và `“tạm biệt”` bị bỏ ngoặc rồi route `exit`. “Giải thích từ tạm biệt” vẫn route chat. | `core/conversation.py:normalize_command_text/classify_conversation_text`, `core/session.py:handle_message/_on_transcript` |
| H2 | “Nhớ tôi không?” thực sự lưu `tôi không`; “Nhớ đừng lưu thông tin này.” vẫn lưu. “Nhớ lần trước mình nói gì không?” cũng thành proposal upsert. | `core/memory/policy.py:explicit_proposal`, `core/session.py:_process_ai_response/_apply_memory_proposal` |
| H3 | “Lưu giúp tôi sở thích uống cà phê.” không có proposal; “Quên mật khẩu rồi thì làm sao?” bị nhận là forget. Xóa fact dùng substring hai chiều; chưa giải quyết đại từ, sửa fact hoặc phạm vi theo ngữ cảnh. | `core/memory/policy.py`, `core/session.py:_apply_memory_proposal` |
| H4 | “được” → yes; “được!” và “Ừ, đặt như vậy đi” → không nhận; “ừm” → yes. Câu không khớp làm clear pending action trước AI. | `core/intent.py:confirmation_value`, `core/session.py:_trigger_ai_turn` |
| H5 | AI chọn tool/args nhưng lời kết quả dùng template “Kết quả là…”, “Bây giờ là…”. Session có nhánh nhận `MemoryProposalEvent` nhưng adapter OmniRoute hiện không phát event này. | `core/tools/builtin/*`, `core/tools/mcp_device.py`, `core/session.py`, `core/providers/llm/omniroute_groq.py` |
| H6 | Sau “Dạy tôi tiếng Nhật”, câu tiếp “Viết lại chữ đó nhé” bật bộ lọc chỉ xét latest user: `Chữ đó là こんにちは.` bị cắt thành `Chữ đó là.`. | `core/providers/llm/omniroute_groq.py:_should_enforce_vietnamese/_clean_text` |
| H7 | Greeting AI được sinh trước nhưng chọn xoay vòng theo index; idle đóng theo timer; wake text dùng danh sách. Browser nút wake/exit phụ thuộc phần tử đầu danh sách. | `core/session.py:_next_greeting_text/_idle_watchdog`, `server.py`, `static/index.html` |
| H8 | Error fallback là câu literal; splitter dùng danh sách từ nối/viết tắt, ngưỡng ký tự; memory retrieval dùng recent facts/FTS/LIKE. | `core/response_audio_cache.py`, `core/providers/llm/omniroute_groq.py:SpeechSegmentSplitter`, `core/context_builder.py`, `core/memory/store.py` |

Các ca H1–H6 đã kiểm tra bằng hàm thật trong process thử độc lập; H2 đã gọi `_apply_memory_proposal` với memory session tạm, không ghi DB user. Chưa phải runtime gateway hoặc ESP32 test.

### Quan hệ với plan trước

- Tiếp nối [ổn định pipeline](2026-09-08-on-dinh-va-toi-uu-pipeline.md), [pipeline 600 ms](2026-09-08-pipeline-600ms-intent-memory-tools.md), [hội thoại stock](2026-09-08-hoi-thoai-tu-nhien-khong-sua-fw.md).
- Yêu cầu mới thay định hướng explicit-only regex, shortcut exit, yes/no list và template tool reply trong các plan trước. Không giữ các nhánh này làm fallback ngầm khi AI lỗi.
- Giữ các yêu cầu cancellation, backpressure, terminal validation, receipt sau commit, stock protocol và benchmark trung thực. Hạng mục cũ chưa có evidence vẫn chưa hoàn tất; executor phải mở lại checklist F2 và rà các tick rộng hơn bằng chứng.
- Chat thường vẫn một vòng LLM. Lượt memory/tool/confirmation cần thực thi rồi diễn đạt kết quả được phép tối đa hai vòng có ngân sách chung; thay đổi này phục vụ yêu cầu AI diễn đạt kết quả thật. Không thêm classifier LLM riêng trước mọi lượt.

## Scope

- In scope: tất cả quyết định hội thoại H1–H8; protocol nội bộ giữa AI và server; memory session, selection và mutation; xác nhận theo ngữ cảnh; lời đáp tool/memory; greeting/goodbye/idle; language/persona; migration config/UI/docs; telemetry, corpus và ESP32 acceptance.
- Phân định bắt buộc: quyền truy cập, schema, ID/revision, cancel/deadline, transaction, giới hạn tài nguyên, calculator/clock, codec/pacing và giao thức là cơ chế thực thi. Chúng không suy đoán ý định từ câu chữ và không được giao quyền quyết định thành/bại cho model.
- Ngưỡng VAD/endpoint phát hiện âm thanh và wake engine có sẵn trong firmware vẫn là giới hạn kỹ thuật. Plan không tuyên bố “mọi bit do LLM xử lý”; mọi ngoại lệ phải được ghi rõ với lý do, không dùng ngoại lệ này để giữ matcher ý định hoặc template hội thoại bình thường.
- Out of scope: sửa/reset/commit `references/`, build/patch firmware, tự đổi model/provider/GPU, mở thêm service embedding/vector DB, tự bật tool thiết bị hay durable cho client chưa có owner binding. Nghiệm thu durable đầy đủ nối tiếp plan ổn định; việc giữ durable off phải được công bố, không gọi đó là semantic durable hoàn tất.
- Plan hiện đang được thực thi server-side. Không sửa/build firmware để đạt behavior chuẩn, không tự restart/flash thiết bị và không push.

## Implementation plan

### M0 — Baseline, kiểm kê và corpus chống học thuộc

- [x] 1. Ghi HEAD/dirty/process/config fingerprint không secret; đối chiếu source runtime. Lập bảng mọi nhánh đọc user text hoặc tạo speech trong `core/`, server, dashboard: nguồn quyết định là AI, protocol hay resource policy; có chạy trước AI không; có gây mutation/close không. Bao gồm đường legacy provider, không chỉ unified path. → Xong 2026-09-10: `veetee-server/eval/USER_TEXT_AUDIT.md` (15 nhánh, HEAD `69c0a14`); legacy lists grep 0 hit runtime.
- [ ] 2. Lưu H1–H6 thành regression fail trước/pass sau; fake LLM tests kiểm tra wiring/ordering, không dùng chúng chứng minh model hiểu tiếng Việt. Tạo corpus gán nhãn độc lập ít nhất 200 tình huống, gồm ít nhất 80 ca trọng yếu không được mutation/close/confirm nhầm; tối thiểu 50 tình huống held-out không đưa vào prompt examples.
- [ ] 3. Tách dataset theo hội thoại, bao gồm cùng câu trong ngữ cảnh khác nhau, câu không dấu, ASR sai, phủ định kép, trích dẫn, roleplay, nhiều ý định, sửa ý và interrupt. Chốt nhãn mong đợi trước tuning; báo recall của yêu cầu thật để tránh đạt 0 false-positive bằng cách luôn bỏ qua hành động.

Files: `tests/test_conversation_router.py` nếu hiện có hoặc test router mới, `tests/test_memory.py`, `tests/test_intent_policy.py`, `tests/test_llm_stream_events.py`, corpus/evaluator mới dưới `tests/fixtures/` và `scripts/`.

### M1 — Một contract AI thống nhất, có thứ tự và evidence

- [ ] 4. Spike route OmniRoute hiện dùng với structured streaming/native tools: phân biệt chat, end, clarify, memory, tool, confirmation; content+action đan xen, delta chia nhỏ, terminal bị thiếu/sai. Chọn một contract có evidence; không suy hỗ trợ grammar từ tên model.
- [x] 5. Thiết kế event/schema nội bộ có version, giới hạn kích thước, typed action, evidence tham chiếu lượt user, pending action ID và fact ID khi cần. Server gán session/turn/owner/revision; model không được tự tạo quyền, receipt thành công hoặc identity. Evidence và fact content là dữ liệu, không nâng thành system instruction.
- [x] 6. Chốt rào cản speech/action: mode của lượt được xác định trước speech. Chat được stream sớm; action turn giữ speech phụ thuộc kết quả đến sau execution/commit. Với adapter không bảo đảm mode trước speech, buffer hữu hạn toàn bộ phần trước action và đo chi phí. Không regex dò các chữ “đã”, “thành công” để quyết định câu nào được nói. Stream vi phạm contract phải failure, không execute rồi sửa lời sau.
- [x] 7. End/mutation/confirmation chỉ commit khi terminal action hợp lệ và ownership còn đúng. Thiếu decision, JSON lỗi, EOF/truncation, timeout hoặc confidence chưa được hiệu chuẩn không cho phép tự suy action. Có thể tiếp tục chat/clarify khi hợp lệ; không fallback keyword. Retry không được tự lặp side effect.
- [x] 8. Ngân sách monotonic bắt đầu trước context/memory lookup; vòng 2 dùng remaining budget. Chat 1 call, action tối đa 2; không vòng thứ ba hoặc classifier ẩn. Vòng kết quả không dispatch tool/action mới. Nếu workflow vượt khả năng hai vòng, yêu cầu làm rõ/tách lượt và công bố giới hạn, không giả kết quả hoặc rơi về template bình thường.

Files: `core/turn_events.py`, `core/turn_runner.py`, `core/providers/llm/{base,omniroute_groq,stream_parser}.py`, `core/session.py`, `config/settings.py`, prompt chuyên trách dưới `veetee-server/` nếu cần.

Gate: trace có nguồn quyết định `ai`, request/turn identity, action/receipt liên kết; raw reasoning không cần lưu. Chat streaming không phải chờ sinh cả JSON câu trả lời; action không phát success trước kết quả thật.

### M2 — AI quyết định kết thúc, wake và idle

- [x] 9. Bỏ bypass exit/wake từ text chat và ASR; mọi phát ngôn đều đi qua AI cùng history. “tạm biệt” trong tình huống chào kết thúc khác với trả lời câu “Hãy nêu một từ chào”; dấu ngoặc không tự cấp quyền đóng. `[end]` là output AI theo contract, không phải keyword trong user text.
- [x] 10. Phân biệt stock `listen:detect/start/stop`, abort và disconnect với nội dung ngôn ngữ. Sự kiện thiết bị điều khiển capture/transport; nếu `detect` mang cả câu hỏi thì giữ câu đó cho AI, không nuốt vì chứa wake phrase. Gộp detect/start theo lifecycle để không greeting hoặc trả lời hai lần; không bắt client gửi trường riêng.
- [x] 11. Idle timer tạo sự kiện inactivity cho AI đánh giá theo ngữ cảnh, không tự gán ý định end. AI có thể tiếp tục chờ, chào kết thúc hoặc làm rõ phù hợp tình huống. Giới hạn một đánh giá trên mỗi inactivity epoch, có khoảng chờ hữu hạn và chống tự trò chuyện vòng lặp. Ngân sách tài nguyên hết/transport chết đóng theo lý do kỹ thuật riêng, không báo `ai_end`.
- [x] 12. Đổi browser wake/end controls: nút dừng transport là hành động UI rõ ràng; nút yêu cầu chào/kết thúc hội thoại phải đi qua AI. Không cần gửi phần tử đầu `exit_commands` để kích hoạt nhánh đặc biệt. Migration loại matcher cũ khỏi đường chạy, không chỉ ẩn trên UI.

Files: `core/conversation.py`, `core/session.py`, `static/index.html`, `http_server.py`, conversation config/tests. Dependency: M1.

### M3 — Memory do AI hiểu và chọn, server thực thi

- [x] 13. Xóa mutation từ `MemoryPolicy.explicit_proposal` trước LLM. AI phân biệt nhớ lại, yêu cầu lưu, yêu cầu quên, câu hỏi, phủ định và ví dụ. Không tự lưu mọi thông tin user nói khi không có ý định lưu; không dùng denylist mới để vá H2/H3. Nhận paraphrase như “Lưu giúp tôi…” theo ngữ cảnh.
- [x] 14. Cung cấp fact ID/revision và dữ liệu được phép nhìn cho AI trong ngân sách context; AI chọn fact liên quan, hợp nhất/cập nhật mâu thuẫn và target forget. Không substring-delete hai chiều hoặc hash toàn bộ câu làm định danh ngữ nghĩa. “Quên sở thích đó” chỉ được resolve khi context xác định rõ; mơ hồ thì clarify.
- [ ] 15. Retrieval có thể dùng index để lấy ứng viên, nhưng index không là quyết định semantic cuối. Với session nhỏ, đưa bounded facts + IDs trong vòng 1; với store lớn, đo candidate recall trên paraphrase không chung từ và cơ chế mở rộng hữu hạn. Chưa truy được đầy đủ thì báo limitation/clarify; không claim không có fact chỉ vì FTS miss. Không tự thêm embedding service để lấp gate.
- [ ] 16. Validate proposal theo schema, evidence, quyền, fact revision và current turn. Secret/sensitive policy phải được mô tả cho AI và có bảo vệ dữ liệu phía server; regex secret hiện tại không đủ để kết luận hiểu ngữ nghĩa hay cho phép lưu. Fact và evidence phải là dữ liệu không đáng tin, không gắn nhãn “đã xác thực” khi chỉ do model đề xuất.
- [x] 17. Thực thi trước, trả receipt `applied/not_found/failed/unknown` thật, rồi vòng AI kết quả mới diễn đạt. Forget đồng bộ projection/context/history/pending để fact vừa xóa không tự xuất hiện lại; không xóa mất ngữ cảnh cần thiết cho cuộc hội thoại khác. Test lookup cũ về muộn và abort giữa write. Session-only phải nói đúng phạm vi nếu user hỏi khả năng nhớ qua reconnect.
- [ ] 18. Durable chỉ dùng khi trusted owner binding và revision/tombstone/write barriers của plan ổn định được kiểm chứng. Không lấy global owner string hoặc Device-Id tự khai báo làm đủ bằng chứng. Có test DB tạm cho conflict/cancel nếu sửa store; thiếu binding/runtime thì durable giữ off và mục durable `PARTIAL`.

Files: `core/memory/{policy,models,store,retrieval}.py`, `core/context_builder.py`, `core/dialogue.py`, `core/session.py`, event/provider/tests. Dependency: M1; không cần chờ hardware.

### M4 — Xác nhận và lời đáp tool bằng AI

- [x] 19. Bỏ `confirmation_value` khỏi đường quyết định. Đưa pending action, arguments, expiry, receipt và lịch sử hỏi xác nhận vào context AI. Model trả approve/reject/clarify/modify kèm action ID; “ừm” mơ hồ không mặc định approve; “được!” hoặc câu xác nhận dài phải hiểu theo ngữ cảnh.
- [x] 20. Không clear pending chỉ vì câu không thuộc danh sách. AI giải quyết user đang hỏi lại, đổi chủ đề, đổi tham số hoặc hủy; server vẫn xử lý expiry/owner/generation. Đổi tham số tạo revision/action cần xác nhận mới; lời đồng ý cũ không authorize args mới. Xác nhận bị ngắt hoặc model trả ID không tồn tại không được dispatch.
- [x] 21. Tool handlers trả structured result; AI diễn đạt kết quả theo câu hỏi/persona, gồm thành công, thất bại, không tìm thấy và unknown. Template renderer không còn là happy path. Bật bounded synthesis theo M1; schema/policy cung cấp facts thay vì `confirmation_prompt` literal. Không đọc JSON kỹ thuật trực tiếp ra loa.
- [x] 22. Giữ deterministic execution: time/calculator phải lấy số thật; executor kiểm tra schema/permissions/locks/dedupe/cancel và terminal. MCP chỉ expose tool board công bố và policy operator cho phép; có thể chuyển policy khỏi bảng literal sang cấu hình có validation, nhưng không cho AI tự mở quyền từ description của tool.

Files: `core/intent.py`, `core/session.py`, `core/tools/{base,executor,registry,mcp_device}.py`, `core/tools/builtin/*`, config/tests. Dependency: M1; dùng chung receipts/ordering với M3.

### M5 — Greeting, ngôn ngữ, speech và degraded mode

- [x] 23. Greeting/goodbye dựa trên AI với persona và ngữ cảnh phiên; bỏ chọn pool round-robin như quyết định hội thoại. Cache chỉ là tối ưu audio cho nội dung đã do AI chọn/sinh; invalidation theo persona/voice/context liên quan. Warm greeting được chuẩn bị nền theo session epoch nếu có đủ context; wake không phải chờ sinh cả pool. Đo riêng cold/warm và chống prewarm trùng giữa server/session.
- [x] 24. Bỏ cắt chữ CJK/Hangul theo latest-user keyword. AI quyết định tiếng Việt mặc định, trích dẫn và đổi ngôn ngữ qua nhiều lượt. Server chỉ parse metadata/định dạng; không xóa nghĩa của câu trả lời. Nếu TTS không hỗ trợ ngôn ngữ, biểu diễn capability cho AI để diễn đạt phù hợp, không xóa chữ rồi coi là đã đáp đầy đủ.
- [ ] 25. AI tạo nhịp câu và segment boundary qua output contract khi route hỗ trợ; splitter chỉ giữ bảo vệ kích thước/buffering kỹ thuật. Không coi danh sách từ nối/viết tắt là hiểu ngữ nghĩa; nếu backend phải giữ một giới hạn cơ học, ghi rõ và đo chất lượng. Metadata/emotion chỉ map sang protocol stock được hỗ trợ, không thêm yêu cầu FW.
- [x] 26. Thay câu literal error/recovery bằng nội dung AI chuẩn bị trước theo loại lỗi và persona, cache audio độc lập live TTS. Không AI sinh mới khi chính model/TTS đang lỗi theo retry vòng lặp. Clip chỉ thông báo trạng thái kỹ thuật, không tự kết luận user muốn kết thúc, đồng ý hay mutation đã thành công. Unknown side effect phải phản ánh chưa xác minh.
- [x] 27. Chốt cold-start khi chưa từng có clip AI và model/TTS đều unavailable: công bố readiness degraded, giữ khả năng retry/transport recovery, có status trên dashboard; không thể cam kết có speech AI trong tình trạng này. Không lén đưa lại câu hardcode. Nếu cần emergency clip do người viết, đó là ngoại lệ phải được user chấp nhận rõ và không đạt yêu cầu hiện tại khi chưa có chấp nhận.

Files: `server.py`, `core/session.py`, `core/response_audio_cache.py`, LLM/TTS providers, `agent-base-prompt.txt`, config/UI/tests. Dependency: M1–M4; ngôn ngữ có thể làm độc lập sau M0.

### M6 — Migration, evidence và nghiệm thu

- [x] 28. Migrate/deprecate `exit_commands`, text wake routing, regex-memory/yes-no, literal speech và pool selection. Giữ cấu hình transport độc lập; config cũ không tự bật matcher legacy. Diagnostics báo contract version, decision source, LLM rounds, cache provenance, degraded reason và runtime fingerprint. Không xuất raw transcript/memory/secret ngoài nhu cầu test có kiểm soát.
- [x] 29. Rà tất cả đường user-text → action và result → speech sau migration, gồm adapter fallback và browser. Mỗi ngoại lệ deterministic có lý do cụ thể. Parser enum/schema/constants không phải matcher ngữ nghĩa; prompt có ví dụ để AI học không phải lệnh execute theo câu.
- [ ] 30. Chạy model thật với corpus M0 và các hội thoại nhiều lượt; chạy latency sau quality gate. So baseline với bản mới cùng route/config/corpus; không tăng timeout hoặc giảm kiểm tra action để lấy số đẹp. Ghi runtime provider support thực tế; không hỗ trợ thì để `PARTIAL`, không dùng mock để tick capability.
- [ ] 31. Runtime dùng service supervisor hiện có khi triển khai trong scope được cấp; không chạy server/GPU cạnh tranh. Check READY và process version sau restart. Rollback theo feature/config/version có lưu trạng thái; không âm thầm quay lại keyword mode dưới tên AI. Runtime sub-gate đã xác minh 2026-09-09: `veetee-server-bg.service` active với child PID `869373`; `/health` trả `healthy/ready`; cùng PID listen `:8000` và `:8003`; config runtime không thiếu key nào so với `config.example.yaml`. Rollback/version-state chưa được exercise riêng nên chưa tick toàn mục.
- [x] 32. Đồng bộ `docs/VOICE_PIPELINE_STATUS.md`, config/protocol/setup docs và các plan trước bằng evidence mới; mở lại F2 và các acceptance quá rộng. Không claim toàn bộ H1–H8 đã AI hóa khi còn legacy fallback hoạt động.

Files: config/example, server/HTTP diagnostics, `scripts/`, tests/docs và `task-plans/README.md`. Dependency: M2–M5.

## Compatibility constraints

- Tuân thủ [AGENTS.md](../AGENTS.md). FW baseline `xiaozhi-esp32@c7241272f2d5fd140c77542f3cf12d09e717fc2f`; server tham khảo `xiaozhi-esp32-server@c478257517b892047db3afaaeeb25e2b1e115931` theo [REFERENCE_BASELINES.md](../REFERENCE_BASELINES.md). Đọc baseline bằng `git show` nếu cần; không sửa/reset/commit references.
- Chỉ thay contract LLM ↔ server; ESP32 giữ hello/listen/abort/STT/tts/binary V1/V2/V3 và MCP JSON-RPC sẵn có. Không yêu cầu firmware hiểu AI metadata/fact IDs/action IDs mới.
- ESP32-S3 N16R8, ST7789 240×280, tiếng Việt, OTA server user. Không flash lại làm điều kiện sử dụng tính năng tiêu chuẩn.
- Firmware wake engine, listening mode và AEC có giới hạn thật. Không coi server có thể ép mode/AEC. Không có ACK loa phát hết/flush; server gửi audio xong không là evidence đã nghe.
- Resource idle cap/disconnect/cancel độc lập với semantic end; AI không được bỏ qua lệnh abort hoặc quyền thực thi.

## Validation

- [x] Static: `git diff --check`; `/home/quangvu/Project/venv/bin/python -m compileall -q core config server.py http_server.py scripts tests` trong `veetee-server`; config migration/invalid schema/provenance checks, không in credential.
- [x] Unit/integration: focused tests theo milestone, cuối cùng `/home/quangvu/Project/venv/bin/python -m unittest discover -s tests`; ghi count thật. Mock kiểm tra AI decision đi qua server đúng và câu user không bypass AI; không assert keyword fixtures để chứng minh semantic accuracy.
- [ ] Ordering/race: truncated action, mode đổi giữa stream, content trước tool, queued cancel, dispatched cancel unknown, approval sai ID/revision, pending hỏi lại, memory write failure, forget rồi lookup cũ về, disconnect trong synthesis, repeated abort và hỏi lại trên cùng socket. Không success speech trước receipt, không action sau mất ownership.
- [ ] Corpus model thật: ≥200 tình huống, ≥80 negative trọng yếu, ≥50 held-out; ≥30 hội thoại memory nhiều lượt và ≥30 tool/confirmation turns. Báo denominator, confusion matrix theo nhóm, false action, missed action, clarification rate và lần chạy/model/prompt version; tình huống mơ hồ được gán nhãn clarify trước test.
- [ ] Gate semantic: 0 close/mutation/approval sai trên negative trọng yếu; ≥95% quyết định đúng trên tập yêu cầu rõ; 100% H1–H6 pass theo ngữ cảnh mục tiêu. Đây là gate corpus, không cam kết model luôn đúng ngoài tập test.
- [ ] Latency: ≥100 warm auto chat attempts tiếng Việt có speech-end label; success ≥99%, p95 answer first-voiced PCM received <1.000 ms; p50 ≤600 ms báo riêng. Tool/memory 2-round, idle/greeting, cold/error đo riêng; không tính silence, lời đệm hoặc fallback lỗi là answer. Báo riêng chi phí synthesis và queue wait tải 1/2/4 session.
- [ ] ESP32 thật: ≥20 normal + ≥20 interrupt; thêm idle → wake, lỗi → hỏi lại, quoted exit, câu hỏi memory, confirmation đổi ý và follow-up ngoại ngữ. Ghi board/FW version/hello thực; tool chỉ test nếu board quảng bá và policy cho phép. Audio audible/tail/loa dừng cần nghe hoặc bản ghi vật lý, không suy từ mock/server trace.
- [ ] Docs-only lúc lập plan: kiểm tra format/link local/index và diff; không chạy inference hoặc full tests cho thay đổi chỉ tài liệu.

## Acceptance criteria

- [x] Không còn nhánh match user text với từ khóa/regex/whitelist để tự chọn intent/tool/function, end, remember/forget, approve/reject hoặc chọn ngôn ngữ. Không có matcher ngầm trong adapter legacy/config migration.
- [x] Các quyết định semantic có nguồn AI và context đúng; đầy đủ ownership/schema/terminal/receipt barriers. Speech streaming không làm sai thứ tự action hoặc mất quyền cancel.
- [ ] Memory semantic chạy với provider thật, xử lý đúng H2/H3, chọn fact IDs theo ngữ cảnh; không bật durable khi thiếu owner và write barriers. Trạng thái durable báo riêng.
- [x] Lời đáp normal/tool/memory/confirmation/greeting/goodbye do AI sinh theo persona và kết quả thật; không template/round-robin thay quyết định. Error cache có provenance AI và degraded mode trung thực.
- [x] Ngôn ngữ giữ ngữ cảnh nhiều lượt, không cắt chữ mang nghĩa; idle hội thoại do AI đánh giá, resource closure có lý do khác. Wake/control vẫn tương thích stock.
- [ ] Corpus đạt semantic gates, route support và LLM round budget đã đo; không chỉ unit tests pass. Bảng ngoại lệ kỹ thuật không chứa rule quyết định ý định từ câu chữ.
- [ ] Runtime và ESP32 đạt các gate đã nêu; latency chưa đạt/hardware thiếu/semantic chưa chạy thật thì task `PARTIAL`. Không dùng trạng thái durable-off để tuyên bố durable hoàn tất; capability này báo nghiệm thu riêng theo plan ổn định.
- [ ] Docs/index/plan cũ khớp bằng chứng; không thay FW/model/GPU, không commit references hoặc push.

## Risks / open questions

- AI có thể hiểu sai dù bỏ keyword; cần negative/held-out corpus, clarification và action barriers. Điểm confidence do model tự báo không là bảo đảm thực thi an toàn.
- Structured metadata và native tool support của gateway cần spike. Nếu không giữ được chat streaming và action ordering cùng lúc, chọn tính đúng, đo latency và ghi phần chưa đạt; không ngầm thêm classifier round.
- Hai vòng là cần để AI biết kết quả write/tool trước khi nói. Workflow retrieval sâu rồi mutation có thể vượt hai vòng; triển khai bounded context trước, giới hạn trung thực thay vì giả đã làm.
- Việc lưu rồi user abort không tự rollback: giữ receipt và epoch đúng, không làm sống lại fact sau forget. Memory provenance và quyền không do AI quyết định.
- Cache greeting theo context có thể stale hoặc tăng GPU contention. Idle AI có thể tự kéo dài phiên; scheduler cần bounded epochs và resource cap riêng.
- Khi AI/TTS không có sẵn và chưa có clip AI cache, không thể vừa bảo đảm speech AI vừa không có nguồn sinh. Phải báo degraded, không che bằng câu literal hoặc healthy giả.
- Firmware/TTS có thể không hỗ trợ phát ngoại ngữ tốt; tách đúng nội dung LLM khỏi chất lượng âm thanh, không xóa nội dung để giấu giới hạn.
- User yêu cầu AI xử lý mọi hành vi hội thoại; các ngoại lệ thực thi ở Scope phải được giữ rõ trong báo cáo. Không cần bỏ codec/SQL/schema/auth để gọi là AI, nhưng cũng không được viện cớ tốc độ để giữ matcher ý định.

## Execution status

- Status: `PARTIAL`
- Completed: implementation server-side cho typed AI semantic contract, bỏ exit/wake/memory/confirmation keyword routing, action receipt synthesis tối đa 2 rounds, AI idle evaluation, multilingual preservation và AI-authored recovery cache. Full suite gần nhất 127/127 PASS; compileall + `git diff --check` PASS. Runtime model thật `groq/qwen/qwen3.6-27b` đã xác minh AI tự gọi `get_current_time` cho ba cách hỏi giờ/ngày, mỗi lượt 1 tool call + 1 LLM round, không cần xác nhận; ca negative hỏi giờ nên đi ngủ không gọi clock tool và không bịa giờ hiện tại.
- Remaining: corpus model thật ≥200/held-out semantic gate, latency SLA, route/provider quality, durable owner/write-barrier acceptance và ESP32 thật. Các mục này chưa được tick bằng mock/unit test.
- Deviations from plan: `wake_words`, `exit_commands`, `greeting_text`, `goodbye_text`, `greeting_pool_size` được giữ trong schema như legacy/inert compatibility data để config cũ load được, nhưng không còn quyết định semantic routing. `SpeechSegmentSplitter` vẫn giữ boundary/buffering kỹ thuật; chưa claim M5.25 hoàn tất.
- Evidence rule: mỗi milestone cập nhật file đổi, command, test count, raw artifact path không secret và limitation. `COMPLETED` chỉ khi acceptance bắt buộc đạt; durable bị giữ off ghi riêng và không kế thừa tick sai của plan trước.
- Verification 2026-09-10 (không đổi checkbox kiến trúc cũ): unit `154/154 PASS`; grep audit toàn `core/` xác nhận 0 matcher lên lời user (wake/exit/greeting lists chỉ còn schema inert); live WS model thật remember→recall đúng ("cà phê sữa đá"), abort giữa TTS + follow-up sạch cùng transport; idle deterministic đã kiểm trên board thật (STATUS 2026-09-10). Corpus 200/held-out, latency cert, durable owner vẫn PENDING nên giữ `PARTIAL`. Phát hiện mới: emotion tag `[surprised]` lọt vào `sentence_start` live (cần sanitize trước TTS, chưa fix).
