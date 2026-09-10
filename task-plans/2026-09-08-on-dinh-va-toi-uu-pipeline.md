# Ổn định và tối ưu pipeline sau review server

Status: `PARTIAL`

Created: `2026-09-08`

## Goal

Người dùng nhận được phản hồi có nghĩa hoặc thông báo lỗi rõ ràng sau khi nói; có thể ngắt AI, hỏi tiếp theo nội dung vừa trao đổi và tiếp tục sau timeout/idle mà không phải reset thiết bị. Memory không ghi/xóa trái ý; tool bị hủy trước dispatch không được chạy; server không báo thành công khi không sinh được audio hoặc chưa biết kết quả thao tác.

Sau khi sửa độ ổn định, đo lại pipeline bằng corpus và trace đầy đủ, rồi tối ưu endpoint/context/TTS theo bằng chứng. Mục tiêu kế thừa: warm chat single-session trên LAN có p95 speech-end → first audio received < 1.000 ms; p50 ≤ 600 ms là mục tiêu nâng cao. Đo âm có nội dung và loa vật lý riêng, không dùng packet im lặng hoặc câu báo lỗi làm bằng chứng đạt tốc độ trả lời.

Plan đã được triển khai phần server chính trong ngày 2026-09-08. Các gate cần corpus runtime đủ lớn, A/B endpoint, semantic/durable memory và nghiệm thu ESP32 thật vẫn để `PARTIAL` cho tới khi có evidence tương ứng.

## Current state

### Baseline và quan hệ với task trước

- HEAD khi lập plan: `4819c6ae1a077857adffeee59b369487028dceb8` — `feat: optimize realtime voice pipeline`.
- Working tree trước plan chỉ có báo cáo chưa track: [PIPELINE_REVIEW_2026-09-08.md](../veetee-server/docs/PIPELINE_REVIEW_2026-09-08.md). Giữ báo cáo như evidence, không xóa hoặc coi là source thay đổi ngoài ý muốn.
- Review cùng phiên đã đọc implementation, cấu hình local theo whitelist, diagnostics và journal; chạy **84/84 unittest PASS**. Các ca bổ sung tái hiện bên dưới dùng dependency giả, chưa có regression test lưu trong repo.
- Tiếp nối [plan pipeline 600 ms](2026-09-08-pipeline-600ms-intent-memory-tools.md), hiện `PARTIAL`. Plan này chốt thứ tự sửa F1–F7 và các khoảng trống phát hiện sau implementation; không làm lại các module đã có và không tự đánh dấu hoàn thành các gate còn thiếu của plan cũ.
- Giữ regression của [plan hội thoại](2026-09-08-hoi-thoai-tu-nhien-khong-sua-fw.md) và [plan server stock](2026-09-08-server-khong-sua-fw.md). Nếu sửa cùng một hạng mục, dẫn chung evidence và đồng bộ checklist theo thực tế.

### Luồng và cấu hình đã quan sát

`Opus/PCM → decode + Silero → endpoint → bounded ASR queue → Parakeet → generation guard → explicit memory/context → OmniRoute stream → event queue → speech/tool renderer → VieNeu → Opus/pacing → WebSocket`.

- Endpoint cấu hình 450 ms, theo frame thực tế 480 ms; queue ASR tối đa 2; utterance tối đa 30 giây; correction tắt.
- Chat mặc định một vòng LLM; first-token config 4.000 ms đang thực thi như timeout event đầu; total-turn config 15.000 ms chưa bao phủ toàn bộ lifecycle.
- Event queue 8; bridge TTS có giới hạn; Opus 60 ms, send-ahead 120 ms. TTS engine dùng chung, admission ưu tiên live/dashboard/prewarm.
- Memory bật, durable tắt; tools native bật calculator/time, MCP device tắt. Greeting pool 0/chưa ready, fallback text rỗng ở snapshot review.
- `/health=healthy` chỉ chứng minh HTTP sống; không chứng minh inference/loa thành công. Diagnostics hiện chỉ giữ lịch sử của session đang tồn tại.
- ESP32 người dùng: ESP32-S3 N16R8, ST7789 240×280, firmware tiếng Việt, OTA server của người dùng. Xác minh lại version/board thực tế lúc test; baseline tham khảo không chứng minh binary đang cài trùng baseline.

### Lỗi đã tái hiện, cần chuyển thành regression

| ID | Trigger / bằng chứng | Hiện trạng sai | Điểm bắt đầu đọc |
| --- | --- | --- | --- |
| F1 | LLM giả chậm hơn first timeout | `failed`, IDLE, không gửi phản hồi nào | `core/session.py:_process_ai_response`, `core/turn_runner.py:_with_deadlines` |
| F2 | “Đừng quên mọi thứ tôi đã nói”; “Tôi không nhớ tên bạn”; câu trích dẫn “nhớ…” | Lần lượt `forget_all` hoặc upsert sai, áp dụng trước LLM | `core/memory/policy.py`, `core/session.py:_apply_memory_proposal` |
| F3 | VieNeu worker ném lỗi / TTS iterator rỗng | Exception bị nuốt; session không binary vẫn `completed` | `core/providers/tts/vieneu_local.py`, `core/session.py:speak_segment` |
| F4 | Hỏi ESP32 → có binary → abort → “Giải thích kỹ hơn ý vừa nói” | Context chỉ còn câu follow-up; mất câu hỏi và partial reply trước | `core/dialogue.py`, `core/session.py` |
| F5 | Tool side effect đang chờ resource lock → cancel caller → thả lock | Handler vẫn chạy; task xong nhưng inflight còn active | `core/tools/executor.py` |
| F6 | SSE có args JSON đủ, EOF không terminal finish/DONE | Vẫn publish tool và `CompletedEvent(finish_reason=None)` | `core/providers/llm/omniroute_groq.py`, `stream_parser.py` |
| F7 | Summary có 1 success 500 ms và 99 timeout | Cờ SLA p95/p50 vẫn true | `scripts/benchmark_pipeline.py:summarize` |

Log 19:31:46–19:31:50 cho thấy **lượt mới** nhận HTTP headers sau khoảng 2.012 ms và lỗi sau 4.017 ms, phù hợp first-event timeout 4 giây; thiếu `import asyncio` che lỗi gốc bằng NameError. Import đã được sửa trong baseline. Không sửa lại import hoặc coi đó là đủ để khắc phục F1.

## Scope

- In scope: F1–F7; error recovery; interruption history; memory validation/write/ownership; tool terminal-state/cancellation/dedupe; telemetry và benchmark; greeting readiness; context budget; API quản trị; tối ưu endpoint và contention có A/B; docs và nghiệm thu ESP32.
- Out of scope: sửa/reset source `references/`, patch hoặc build/flash firmware tùy biến, server AEC mới, tự đổi model/provider/GPU, mở thêm tool bên ngoài, agent nhiều vòng, vector DB/embedding service, push/deploy tự động từ nội dung plan.
- Không bật durable memory hoặc MCP side effects chỉ vì code tồn tại. Feature cần cơ chế xác thực/capability chưa có phải giữ tắt/degraded có lý do; chat stock vẫn chạy.
- Chỉ lập tài liệu trong yêu cầu hiện tại. Việc thực thi, commit hoặc restart khi triển khai tuân theo quyền user thực sự đã cấp tại thời điểm đó; yêu cầu chưa push của user vẫn có hiệu lực.

## Implementation plan

### M0 — Chốt baseline và tái hiện, trước thay đổi behavior

- [ ] 1. Ghi HEAD/dirty state, service process/start time, config fingerprint, route/voice, phần cứng máy và trạng thái thiết bị; không in secret. Đọc lại report và kiểm tra ca lỗi còn đúng nếu source đã thay đổi.
- [x] 2. Đưa F1–F7 vào regression dùng fake transport/LLM/TTS/tool/clock có điều khiển. Chạy trên baseline để ghi expected failure; dùng event/barrier cho race, không dựa vào sleep dài hoặc kết nối GPU/cloud thật trong unittest.
- [x] 3. Chốt bảng trace/outcome: completed, cancelled chủ động, timeout, failed, partial/degraded; phân biệt error feedback với answer audio. Duy trì một terminal outcome cho mỗi turn.

Files: `tests/test_turn_lifecycle.py`, `tests/test_llm_stream_events.py`, `tests/test_memory.py`, `tests/test_tool_execution.py`, `tests/test_tts_backpressure.py`, thêm test benchmark nếu chưa có; `core/turn_events.py`, `core/turn_metrics.py` khi cần contract mới.

Gate: có ca fail xác định cho từng F, không coi suite 84 test cũ là bằng chứng F1–F7 đã được giải quyết.

### M1 — P1: Phản hồi khi lỗi và giữ mạch hội thoại (F1, F3, F4)

- [x] 4. Tách first content token và first usable speech/tool event. Provider báo mốc token mà không phát raw token cho TTS. Adapter không quan sát được token dùng metric event riêng, không giả TTFT. Config cũ được migrate/alias rõ và có validation quan hệ deadline.
- [x] 5. Dùng deadline monotonic chung cho context/LLM/tool và phần sinh nội dung, truyền remaining budget xuống các tầng và vòng synthesis; không reset ngân sách mỗi vòng/segment. Tách ngân sách playback/send khỏi ngân sách sinh nội dung để không cắt câu hợp lệ chỉ vì audio dài hơn 15 giây. Bounded send stall và bounded cleanup; giữ ownership khi timeout/cancel.
- [x] 6. Trước audio mà LLM/DB/TTS lỗi: phản hồi ngắn tiếng Việt phù hợp lỗi qua audio fallback có sẵn, không gọi lại model/TTS đang lỗi theo vòng lặp. Fallback phải dùng protocol TTS chuẩn, có cùng cancellation/generation guard và timeout riêng có giới hạn. Nếu transport hỏng thì ghi failure thật, không cam kết có thể phát thông báo. Sau partial audio không phát lại toàn bộ câu trả lời hoặc tool.
- [x] 7. Truyền lỗi VieNeu worker qua bridge; empty stream và lỗi giữa stream không được biến thành EOF thành công. Dọn queue waiters/thread future/admission lease đúng lifecycle kể cả repeated cancellation. Không thả quyền engine khi worker cũ còn dùng engine; nếu worker không dừng trong hạn thì báo engine unavailable/degraded và không cho worker mới chạy chồng.
- [x] 8. Hoàn tất/lỗi/abort đều trả state đúng auto/manual/realtime và giải phóng capture/playback guard thích hợp. Câu hỏi sau timeout/lỗi trên cùng kết nối phải hoạt động; không bị dedupe theo nội dung từ lượt cũ.
- [x] 9. Giữ user turn trước và partial assistant khi bị ngắt. Theo dõi generated/sent/interrupted riêng; phần chưa gửi không được ghi như đã trả lời. Với segment mới gửi một phần audio, ghi rõ là interrupted/không biết đã nghe bao nhiêu; không lấy binary đầu làm bằng chứng nghe trọn segment. Preserve tool receipts trong ngữ cảnh nếu action đã dispatch.

Files: `core/session.py`, `core/dialogue.py`, `core/turn_runner.py`, `core/turn_events.py`, `core/providers/llm/{base,omniroute_groq}.py`, `core/providers/tts/{base,vieneu_local,scheduler}.py`, `core/response_audio_cache.py`, `config/settings.py`, `config.example.yaml` và regression liên quan.

Gate: F1/F3/F4 pass; timeout trước token, token về nhưng chưa có câu, TTS lỗi trước/giữa audio, socket send stall, hỏi cùng câu ở capture mới và abort 3 lần liên tiếp đều kết thúc đúng. Không có task cũ phát audio/history vào lượt mới; không success giả; giữ một LLM call cho chat thông thường.

### M2 — P1: Memory đúng ý định và giới hạn phạm vi (F2)

- [x] 10. Chặn mutation từ regex tìm từ “nhớ/quên” ở bất kỳ vị trí. Đường deterministic chỉ nhận lệnh explicit không mơ hồ; negation/quote/roleplay/giả định không được ghi/xóa tự động. Có test cả có/không dấu, phủ định kép và câu có nhiều ý định; không dùng một denylist nhỏ làm bằng chứng hiểu ngữ nghĩa đầy đủ.
- [ ] 11. Chốt contract proposal trong chính LLM stream nếu hỗ trợ memory semantic; schema/action/evidence bounded, không cho model chọn owner/scope. Validator đối chiếu evidence từ lời user, kiểm tra secret và turn ownership. Giữ speech streaming, không thêm classifier/extractor LLM thứ hai. Nếu chưa có grammar/runtime evidence ổn định, công bố mode explicit-only và để phần semantic `PARTIAL`.
- [ ] 12. Với lệnh lưu/xóa, server chỉ phát receipt “đã lưu/đã xóa” sau commit. Không chuyển nguyên lời khẳng định thành công của model ra loa trước write barrier. Forget không tìm thấy trả trạng thái không tìm thấy; không lấy bool đã chạy hàm làm bằng chứng đã xóa fact. Xác định rõ quên fact cụ thể/quên mọi thứ trong phạm vi nào.
- [ ] 13. Durable: thêm optimistic concurrency/expected revision hoặc mutation epoch, tombstone barrier và bounded serialized write queue ngoài event loop. Cancel await không có nghĩa SQLite thread đã rollback. Write cũ đến sau forget không làm sống lại dữ liệu; lệnh nhớ mới rõ ràng sau forget vẫn được phép với revision mới.
- [ ] 14. Đồng bộ session projection/retrieval/cache/history và pending proposal sau forget; không đưa dữ liệu vừa quên trở lại context lượt sau. Test 2 session cùng owner, DB locked/unavailable, cancel giữa commit, reconnect và restart bằng DB tạm.
- [ ] 15. Resolve owner bằng binding đã được server xác thực qua cơ chế kết nối sẵn có. Global `trusted_owner_id` không được ngầm gán cho mọi client không xác thực. Anonymous/unknown chỉ session memory; shared device phải có scope rõ. Không có binding đáng tin thì giữ durable tắt, ghi limitation và phần nghiệm thu còn thiếu.

Files: `core/memory/{models,policy,store,retrieval}.py`, `core/context_builder.py`, `core/session.py`, provider/parser/events, `config/settings.py`, `tests/test_memory.py`, `tests/test_intent_policy.py` và integration bổ sung.

Dependency: M0; phần receipt/context lifecycle dựa M1. Gate: F2 pass; tối thiểu 100 intent cases gồm tập phủ định/trích dẫn trọng yếu **0 mutation sai**; ≥30 hội thoại memory nhiều lượt. Durable chỉ nghiệm thu khi owner isolation, forget barrier và reconnect/restart đã có evidence. Mode explicit-only không được đánh dấu semantic memory hoàn tất.

### M3 — P1 trước side effects: Terminal stream và tool lifecycle (F5, F6)

- [x] 16. Chốt terminal-state contract bằng spike route OmniRoute hiện dùng: tool-only, content+tool, ID/args chia delta, DONE/finish reason/usage cuối. Không suy khả năng từ tên model. Ghi unsupported nếu gateway không đủ bằng chứng; khi đó không dispatch side effects từ stream thiếu terminal validation.
- [x] 17. Parse JSON đầy đủ là điều kiện cần; publish action chỉ khi terminal finish hợp lệ theo adapter đã chọn. EOF thiếu terminal, finish `length`, stream error, call thiếu/sai ID hoặc dữ liệu vượt giới hạn không được execute. Giữ lời đã gửi nhưng outcome phản ánh lỗi. Không tự gọi lại model sửa JSON.
- [x] 18. Executor nhận server-owned turn/session identity, cancel token và deadline. Lúc chờ semaphore/resource lock phải cancel được; kiểm tra lại ownership ngay trước dispatch. Sau dispatch không coi abort là rollback; giữ receipt/readback phù hợp backend. Side effect timeout chưa biết kết quả là unknown, kể cả operation idempotent nếu chưa có bằng chứng thành bại.
- [x] 19. Dọn inflight khi task tự kết thúc, độc lập caller còn await hay đã cancel; bound receipts/fingerprints bằng TTL/cap có policy cho action chưa kết thúc. Dedupe cùng call trong cùng turn, ID model tái dùng ở turn khác không nhận receipt cũ. Confirmation tham chiếu đúng action gốc, không sinh execution thứ hai.
- [x] 20. Không phát lời model khẳng định action thành công trước tool result. Dùng preamble trung tính có kiểm soát/renderer từ kết quả thật; nếu content/native-call đan xen không thể phân loại đáng tin thì trì hoãn speech của tool turn theo contract, không hy sinh tính đúng để lấy first audio sớm.
- [x] 21. Giữ receive loop nhận MCP replies độc lập executor; confirmation/abort/disconnect không làm deadlock waiters. Two-round synthesis nếu bật dùng remaining budget chung, không tool vòng ba hoặc retry side effect sau partial failure.

Files: `core/tools/{executor,results,registry,mcp_device}.py`, `core/intent.py`, `core/providers/llm/{omniroute_groq,stream_parser}.py`, `core/turn_runner.py`, `core/session.py`, tests tool/parser/MCP/lifecycle.

Dependency: M0, deadline/ownership M1. Gate: F5/F6 pass; queued-cancel không handler call; dispatched-cancel có receipt truthful; inflight về 0; duplicate cùng turn execute một lần; turn mới ID lặp được xử lý riêng; malformed/truncated stream không action. ≥30 tool turns runtime; MCP phần cứng chỉ test tool board thật có công bố.

### M4 — P2: Đo đúng và giữ evidence qua disconnect (F7)

- [x] 22. Tách percentile trên lượt thành công khỏi gate SLA: số lượt yêu cầu/đã thực hiện/hợp lệ/lỗi/timeout/cancel, sample size tối thiểu và success-rate threshold phải công khai. Corpus còn thiếu lượt không PASS; error feedback không phải câu trả lời hợp lệ. Fixture 1 success/99 timeout và all-timeout bắt buộc FAIL.
- [x] 23. Xuất capture events endpoint/ASR queue/lock/infer/final vào trace artifact cùng turn, không chỉ giữ RAM. Đo `infer_ms` sau lock; token/câu đầu/first PCM/first Opus/send riêng; dùng monotonic clock và ghi run ID, HEAD + dirty fingerprint/process version, config fingerprint không secret.
- [x] 24. Lưu bounded recent traces cấp server sau session close; diagnostics thể hiện đúng active task/outcome/degraded reason. Không xuất transcript/memory/credential không cần thiết để đo latency.
- [x] 25. Benchmark decode đầu ra xác định leading silence và first voiced PCM; giữ metric packet riêng. Fixture có speech-end label trong cùng clock client; ghi jitter/pacing sai lệch. Câu báo lỗi, lời đệm và silence không tính là đáp án có nội dung. Âm đầu trên ESP32 phải đo bằng bản ghi vật lý riêng.

Files: `scripts/benchmark_pipeline.py`, `core/turn_metrics.py`, `core/providers/asr/parakeet_silero.py`, provider/TTS/session, `server.py`, `http_server.py`, tests metrics/benchmark/diagnostics.

Dependency: contract M0; có thể làm song song phần đầu M1–M3, nhưng benchmark nghiệm thu chạy sau fixes. Gate: F7 pass; trace còn truy được sau disconnect; một turn truy vết được tất cả mốc hoặc ghi unavailable; không suy TTFT/loa từ metric khác.

### M5 — P2: Readiness, context và API vận hành

- [ ] 26. Greeting pool lỗi được retry nền có backoff/singleflight; có fallback tiếng Việt đã sẵn audio khi cần. Wake không chờ sinh cả pool; prewarm không chiếm vô hạn admission của live. Tách liveness khỏi readiness, công bố degraded thay vì luôn ngụ ý model đang hoạt động. Probe định kỳ có giới hạn, không gọi model ở mỗi health request.
- [x] 27. Context budget bao gồm system, history, memory, tools và reserve output; giữ current user/pending action, trim theo turn/tool-call-result group. Dùng tokenizer sẵn có nếu phù hợp, nếu ước lượng thì ghi rõ. Prompt/current input quá lớn có xử lý hữu hạn; không tăng context bằng cách bỏ mọi giới hạn.
- [x] 28. Bảo vệ API quản trị prompt/test-voice bằng auth/access boundary phía server; rate/concurrency limit GPU test và kiểm tra request size. Không áp cơ chế mới làm stock OTA/WS bắt buộc sửa firmware. Test unauthenticated management request bị từ chối, UI quản trị hợp lệ dùng được, chat/OTA stock không regression. Chọn credential deployment khi có thông tin thực tế, không ghi secret vào example/log/artifact.

Files: `server.py`, `http_server.py`, `core/context_builder.py`, `core/dialogue.py`, `core/response_audio_cache.py`, config/docs và UI quản trị nếu cần.

Dependency: fallback/engine lifecycle M1, memory/context M2, trace M4. Gate: wake degraded có phản hồi bounded, context không vượt budget đã công bố, API quản trị kiểm soát được mà stock protocol vẫn hoạt động.

### M6 — P2: Tối ưu có A/B và nghiệm thu trên thiết bị

- [ ] 29. Đo baseline sau fixes trước tuning: ≥100 warm auto chat attempts tiếng Việt có speech-end label; tách greeting/tool/cold/error khỏi tập answer latency. Giữ raw JSONL và summary, công bố mẫu hợp lệ và toàn bộ failure. Ghi route, prompt, voice, config, máy, điều kiện LAN và workload.
- [ ] 30. A/B endpoint 450/320/256/192 ms với frame rounding thực tế; corpus có ngập ngừng, tên, số và câu dài. Gate chất lượng: false endpoint tăng không quá 1 điểm phần trăm và CER không tăng quá 1 điểm phần trăm so với cùng corpus baseline. Không chọn threshold chỉ vì median đẹp; giữ 450 ms làm rollback nếu chưa đủ evidence.
- [ ] 31. Đo tải 1/2/4 session, thêm dashboard/prewarm: queue wait, time-to-first-audio, event-loop lag, lỗi/timeout, GPU memory. Nếu playback/backpressure giữ TTS lease chiếm đa số, thử scheduling/buffering hữu hạn hoặc giải phóng admission sau khi inference thật sự kết thúc; không queue audio vô hạn hoặc preempt engine không hỗ trợ.
- [ ] 32. Đánh giá event queue chung làm tool/memory chờ sau speech. Chỉ tách control/action processing khỏi playback khi đo được benefit và giữ ordering, ownership, write barrier, backpressure. Không khởi động LLM/tool từ speculative ASR.
- [ ] 33. Runtime service khi triển khai dùng supervisor hiện có `veetee-server-bg.service`, không chạy thêm foreground chiếm port/GPU. Nếu restart nằm trong scope đã được user cấp, chờ preload/READY và kiểm tra health/readiness trước test. Rollback từng feature/config khi regression, không reset changes khác của user.
- [ ] 34. ESP32 thật: 20 lượt thường + 20 lượt interrupt, thêm idle hết 120 giây → wake lại, lỗi/timeout → hỏi lại; hỏi follow-up phụ thuộc phần trước. Test mode stock đang có, tiếng Việt và OTA server của user. Ghi version/board/hello và tình huống đã test; nghe normal tail, đo speech-end → first audible và thao tác ngắt → loa dừng. MCP status/volume chỉ nghiệm thu nếu được board quảng bá và policy M3 pass.
- [ ] 35. Đồng bộ `docs/VOICE_PIPELINE_STATUS.md`, setup/protocol/config docs, report và checklist plan cũ bằng evidence mới. Không đổi `PARTIAL` thành `COMPLETED` chỉ vì suite pass hoặc service healthy; không giữ nguyên kết luận cũ “import fix đã giải quyết hoàn toàn barge-in”.

Gate: lựa chọn tối ưu có A/B và rollback; báo năng lực đa session riêng, không áp SLO single-session cho 4 client khi chưa đo. Phần hardware chưa test hoặc SLA chưa đạt giữ `PARTIAL`, ghi kết quả cụ thể.

## Compatibility constraints

- Tuân thủ [AGENTS.md](../AGENTS.md): server tương thích firmware Xiaozhi nguyên bản, không thêm capability/JSON command bắt buộc hoặc đổi listening mode/AEC bằng suy đoán phía server.
- Firmware tham khảo: `xiaozhi-esp32@c7241272f2d5fd140c77542f3cf12d09e717fc2f`; server tham khảo: `xiaozhi-esp32-server@c478257517b892047db3afaaeeb25e2b1e115931`, theo [REFERENCE_BASELINES.md](../REFERENCE_BASELINES.md). Đọc bằng `git show` tại pin nếu cần đối chiếu, phân biệt reference working tree local với baseline; không sửa/reset/commit references.
- Giữ hello/listen/abort/tts/STT/binary V1/V2/V3 và numeric JSON-RPC IDs MCP. Fallback dùng wire contract sẵn có. Chat không phụ thuộc MCP discovery thành công.
- Stock protocol không có ACK loa phát hết/flush; history interrupted, pacing và thời điểm stop chỉ là evidence server/estimate, không là xác nhận AEC hoặc playback thật.
- Cấu hình firmware tiếng Việt và OTA qua tùy chọn stock được phép; task không yêu cầu build/flash lại.

## Validation

- [x] Static/syntax: `git diff --check`; `/home/quangvu/Project/venv/bin/python -m compileall -q core config server.py http_server.py scripts tests` trong `veetee-server`; load example/minimal config và validation deadline/budget/owner/auth không in credential.
- [x] Unit/integration: chạy focused tests cho milestone trước, sau đó `/home/quangvu/Project/venv/bin/python -m unittest discover -s tests`; bổ sung test có ý nghĩa cho F1–F7, không chỉ assert hằng số/import. Ghi số test và outcome thật, không hardcode tiếp tục là 84.
- [ ] Cancellation/resource: barrier tests cho producer queue đầy, TTS thread chậm/lỗi, socket stall, queued/dispatched tools, DB writer đang chạy; không task/lease/waiter stale, outcome chỉ một lần.
- [ ] Runtime: capability spike + corpus chat/intent/memory/tools + cold/warm greeting; counter LLM round và gateway attempts nếu quan sát được, nếu không thì unknown. Giữ backend mock metrics tách runtime model thật.
- [ ] Hardware/device: 20 normal/20 interrupt và idle/wake/recovery trên ESP32 được user test; audio physical nếu kết luận audible latency/stop. Raw traces/báo cáo ẩn danh theo nhu cầu, không commit raw audio/DB/secrets.
- [ ] Sau mỗi milestone: cập nhật execution status, files đổi, commands, số đo, remaining và deviations; tránh chạy lại full suite/runtime khi chưa có thay đổi hoặc nghi vấn mới.

## Acceptance criteria

- [ ] F1–F7 có regression fail trước/pass sau; chat sau timeout/TTS lỗi/abort hoạt động trên cùng transport, không cần reset ESP32 và không success giả.
- [ ] Câu trước và partial assistant được giữ đúng mức evidence cho follow-up; không lịch sử generated-only giả làm user đã nghe.
- [ ] Negation/quote không mutation; save/forget receipt sau commit, owner isolation và stale-write barrier đã kiểm chứng trước bật durable.
- [ ] Queued-cancel không dispatch; dispatched-cancel có receipt thật; dedupe đúng turn; terminal-invalid stream không action; default chat một LLM round, tool synthesis tối đa hai vòng dùng chung ngân sách.
- [ ] Greeting/health thể hiện readiness thật và lỗi không làm treo; context budget và API quản trị có kiểm soát, stock OTA/WS tương thích.
- [ ] Corpus ít nhất 100 warm auto attempts đã chạy đủ và được phân loại hết; success rate ≥99%, không silent failure không được ghi nhận. Cờ SLA chỉ PASS khi đạt sample gate, success gate và p95 answer audio received <1.000 ms; công bố riêng p50 ≤600 ms `ACHIEVED`/`NOT_MET`. Cancel chủ động thuộc corpus interrupt riêng, không loại timeout khỏi denominator để làm đẹp số liệu.
- [ ] Tối ưu endpoint vượt gate chất lượng đã đặt; có baseline/A-B/raw traces và rollback. Binary/voiced PCM/acoustic không bị trộn thành một metric.
- [ ] ESP32 normal/interrupt/idle/wake/recovery có evidence; chỉ tuyên bố loa đạt dưới 1 giây khi acoustic metric đo đạt. Hardware thiếu, durable/semantic chưa thực hiện đủ, hoặc p95 chưa đạt: tổng task `PARTIAL`.
- [ ] Docs/status/checklists khớp implementation và giới hạn thật; không firmware patch, không thay model/GPU ngoài scope đã thống nhất, không tự push.

## Risks / open questions

- Gateway có thể chậm trước token hoặc không forward terminal metadata như kỳ vọng; spike quyết định adapter, không tăng timeout vô hạn hoặc tự thêm retry vào strict one-call.
- Deadline sinh nội dung và playback phải tách để không cắt câu dài bình thường. Native inference/thread không cancellable ngay; ưu tiên lifecycle/engine ownership đúng hơn việc báo cleanup đã xong giả.
- Text/âm thanh gửi một phần không xác định chính xác user đã nghe tới đâu; cần interrupted annotation trong context, không tính cả segment đã nghe từ first binary.
- Memory grammar semantic và trusted binding chưa hoàn chỉnh; chọn explicit-only/session-only làm fallback, không bật durable bằng header Device-Id tự khai báo. Executor ghi rõ phần còn thiếu thay vì lặng lẽ thu hẹp nghiệm thu.
- API auth cần cơ chế operator dùng được trên dashboard hiện tại; giữ thay đổi ở management boundary và cấu hình server, không áp capability mới lên ESP32.
- 600 ms có thể chưa đạt với route/GPU hiện tại; chỉ đề xuất đổi model/hardware sau breakdown và A/B cho thấy nút thắt, ngoài scope tự quyết định của plan này.
- Endpoint giảm có thể làm tăng ASR sai hoặc cắt tên/số; 100 mẫu là minimum thử nghiệm, không chứng nhận mọi giọng nói/môi trường.
- Nếu không có thiết bị hoặc người test, hoàn thành phần server độc lập và ghi hardware `PENDING`; không suy test mock thành hardware PASS.

## Execution status

- Status: `PARTIAL`
- Completed: F1/F3/F4 recovery và interrupted history; F2 deterministic explicit-only; F5/F6 tool cancellation + terminal stream validation; F7 SLA sample/success gate; context budget; server-retained turn traces + ASR capture events; first-voiced-PCM benchmark; cached Vietnamese error fallback; management auth/rate/concurrency boundary. Chat thường giữ một LLM round; tool synthesis tối đa round 2 và dùng remaining generation budget.
- Validation 2026-09-08: `git diff --check` PASS; `compileall` PASS; example + minimal config load/validation PASS; focused benchmark/metrics/integration/cache **21/21 PASS**; full `/home/quangvu/Project/venv/bin/python -m unittest discover -s tests` **112/112 PASS**.
- Remaining: semantic memory proposal vẫn `PARTIAL`; durable memory vẫn tắt nếu không có trusted owner và chưa có stale-write/tombstone/reconnect evidence; M5 greeting retry có background backoff cấp server nhưng chưa chứng minh singleflight giữa background repair và session-local generation khi startup prewarm lỗi; chưa chạy ≥100 warm Vietnamese runtime corpus, endpoint A/B 450/320/256/192 với CER/false-end gate, tải 1/2/4 session, ≥30 runtime tool turns, hoặc ESP32 thật 20 normal + 20 interrupt + idle/wake/recovery/acoustic.
- Runtime/hardware evidence: service `veetee-server-bg.service` đã được quan sát active trong phiên; Parakeet đã load CUDA và stock OTA/WS routes hoạt động trong integration. Đây không phải bằng chứng loa vật lý/AEC/first-audible trên ESP32.
- Deviations from plan: Không thay firmware/model/GPU, không bật durable memory/MCP side effects, không sửa `references/`. 600 ms/p95 SLA chưa được tuyên bố đạt vì chưa có corpus/acoustic gate.
- Verification 2026-09-10: F-gates giữ PASS trên unit 154; live WS abort-giữa-TTS + follow-up sạch (không stale audio/history); management API 401-unauth/400-overbudget đúng, persona không đổi sau reject; references sạch đúng baseline, không secret trong HEAD. Smoke 20 mẫu success 100% nhưng p50 ~6.0s/p95 ~6.6s (chân chậm ở LLM gateway) → SLA `NOT_MET`; corpus ≥100/cert/load/endpoint-A/B/20+20 hardware vẫn PENDING. Giữ `PARTIAL`.
- Completion rule: chỉ đặt `COMPLETED` khi mọi acceptance bắt buộc có evidence; p50 600 ms báo riêng nếu chưa đạt. Runtime/hardware/semantic/durable còn thiếu hoặc p95 chưa đạt thì giữ `PARTIAL`.
