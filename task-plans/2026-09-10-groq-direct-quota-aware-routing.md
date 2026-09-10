# Groq API trực tiếp: multi-key quota-aware routing cho voice latency thấp

Status: `PLANNED`

Created: `2026-09-10`

## Goal

Thay OmniRoute bằng Groq API trực tiếp. User xác nhận 5–6 key thuộc các nguồn quota độc lập. Trước mỗi inference, server dự toán toàn bộ request, giữ chỗ quota và chọn nguồn đủ dung lượng có latency dự kiến thấp; không gửi thử vào key đã biết hết quota rồi chờ fallback. Giữ persona dài, memory, intent, native tools, receipt synthesis và firmware stock hoạt động đúng.

Đây là plan, chưa phải yêu cầu implement trong lượt lập tài liệu. Không sửa config đang chạy, không gọi key thật, không restart/flash/commit/push chỉ vì plan có các bước đó.

## Current state

- Snapshot đọc trực tiếp: HEAD `6aefa4f`, working tree sạch trước khi tạo plan. Executor phải kiểm tra lại; không kế thừa HEAD/PID/test count từ hội thoại.
- `server.py:29–53` log và khởi tạo trực tiếp `OmnirouteGroqLLM`; chỉ đổi `llm.provider` trong YAML chưa thay provider thực tế.
- `core/providers/llm/omniroute_groq.py` chứa cả persona persistence/template, connection pool, recovery generation, correction, legacy streaming, typed stream, speech splitter và parsing. Các đường `/models`, `/chat/completions` đang dùng một base URL/key; payload có `reasoning_format` và `reasoning_effort=none` cần kiểm tra theo model Groq thật.
- Route cũ `groq/qwen/qwen3.6-27b` là alias gateway, KHÔNG phải model ID Groq đã xác minh. Không tự bỏ prefix rồi coi là model hợp lệ.
- `TurnRunner` quản lý typed events và deadlines; session giữ ownership/cancel, bounded rounds, tool receipts, TTS scheduling/pacing. Không phá contract này khi thay transport.
- Recovery prewarm/repair ở `server.py`, idle farewell, correction và legacy adapter cũng gọi LLM: tất cả phải đi qua quota ledger chung, không chỉ `stream_turn`.
- `config/settings.py`/example hiện có LLM một key; chưa có pool, reservation, shared quota identity hay quota telemetry.
- Các số benchmark trước là bằng chứng lịch sử qua OmniRoute. Chưa đủ log upstream để quy mọi lượt chậm cho 429/fallback; baseline mới phải có attempts/headers/usage đo được.
- Harness hardware hiện dùng timestamp journal tạo từ UTC nhưng chuỗi thiếu timezone; có nguy cơ đọc log cũ. Load probe đếm `tts:stop` như completed, có thể tính error fallback là answer. Phải sửa chất lượng phép đo trước khi dùng làm nghiệm thu.

## Scope

- In scope: provider Groq direct, migration provider factory/persona, quota ledger multi-key, atomic reservation, token estimation, admission/deadline, bounded failover, headers reconciliation, metrics, deterministic simulations, runtime Groq và ESP32 regression, runbook.
- Không còn OmniRoute trong đường runtime mới; không âm thầm quay về localhost gateway khi direct lỗi.
- Không sửa firmware/reference, không đổi ASR/TTS/model chất lượng một cách ngầm định, không classifier LLM chọn key, không thêm keyword semantic router, không hedging song song mặc định.
- Một server process là deployment đầu tiên: shared in-process router dùng chung cho mọi session/job. Redis chỉ cần khi nhiều process thực sự cùng tiêu thụ pool; chưa thêm service chỉ để triển khai v1.
- Plan này thay hướng “giữ OmniRoute” trước đây theo yêu cầu mới nhất. Giữ nguyên các gate correctness/SLA còn thiếu của [plan runtime](2026-09-09-ai-persona-tools-memory-latency.md); không tự close plan cũ.

## Thiết kế đích

```text
ASR final / text / lifecycle job
  -> request assembly (persona version + context + schemas + receipts)
  -> token estimate + remaining deadline + purpose
  -> quota eligibility filter (key -> quota group)
  -> predicted usable-event latency + capacity selection
  -> atomic reservation
  -> Groq HTTPS streaming
  -> headers/usage reconciliation + typed events
  -> terminal validation -> execute tool/memory -> receipt synthesis nếu cần
  -> TTS/pacing -> stock client
```

### Quota identity và ledger

- Key là credential; `quota_group` là định danh nguồn quota. User xác nhận độc lập: khai báo các group riêng theo account/organization đã có, không suy bằng hash API key. Nếu sau này nhiều key cùng group phải chia chung budget.
- Quota bucket theo scope nhà cung cấp thực sự công bố (organization/model hoặc shared pool). Một key có thể tham gia nhiều bucket; admission phải vượt qua TẤT CẢ bucket liên quan.
- RPM/RPD/TPM/TPD; ITPM/OTPM nếu tài khoản áp dụng. Chỉ enforce hạn mức biết thật; thiếu quota không coi là vô hạn. Require quota inventory hoặc chế độ discovery bảo thủ có giới hạn concurrency rõ.
- Reservation chứa ID, group/model, purpose, estimated input/output, dispatch state, actual usage nếu có, monotonic times, expiry/deadline. Không chứa key/prompt/raw memory.
- Ước lượng input trên request thực tế đầy đủ; reserve output theo cap request + margin cấu hình. Theo dõi sai số bằng actual usage; tokenizer mismatch không được che bằng việc cắt persona/permission/receipt.
- Một lock ngắn cho check+reserve nhiều bucket. Không giữ lock khi HTTP/tokenize chậm. N requests đồng thời không cùng tiêu số dư cuối.
- Sliding-window/token-bucket là mô hình local bảo thủ, không tuyên bố giống thuật toán Groq. Không reset tất cả ở phút tròn; hỗ trợ burst guard local và mốc reset provider.
- Trước dispatch mà cancel: release reservation idempotent. Sau dispatch mà mất usage/cancel/timeout: giữ charge bảo thủ đến window expiry; HTTP cancel không chứng minh upstream không tính token. Usage/headers reconcile đúng một lần.
- In-flight response có thể về ngược thứ tự: không để remaining header cũ ghi đè số dư mới hoặc hoàn cả reservation request khác. Header là observation có scope/time, không phép `remaining = header` mù quáng.
- Restart mất ledger RAM: lưu snapshot aggregate không secret ngoài critical path hoặc khởi động với capacity bảo thủ/cooldown. Không reset daily usage về 0 để phát burst sau restart. Test restart trong cùng cửa sổ quota.

### Selection và deadline

- Lọc quota/context/capability/cooldown trước; chọn expected wait + usable-event latency (EWMA theo group/model/purpose), có in-flight penalty và fairness để không dồn hết tải vào một key nhanh nhất.
- Soft affinity với persona/prefix chỉ khi có bằng chứng cache benefit; không pin session vào group cạn quota. Không giả định cache dùng chung giữa quota group độc lập.
- Chat/end do user hỏi vẫn AI quyết định; router chỉ dựa token/resource/purpose/capability, không hiểu intent bằng keywords.
- Default đề xuất để benchmark: headroom 10%, tối đa 2 attempts/request, local voice admission wait 50ms, router CPU p95 <5ms (không gồm tokenize/network/queue). Đây là tham số khởi điểm, không quota/SLA đã đo đạt.
- Nếu group khác đủ quota: chọn ngay, không sleep cooldown group cũ. Nếu tất cả hết: chỉ đợi trong min(admission cap, remaining deadline); sau đó báo typed `capacity_exhausted`/degraded. Không báo answer success bằng recovery clip.
- Purpose priority: live voice/receipt continuation > management tương tác > recovery repair/summary/prewarm > benchmark. Bounded queues và chống starvation. Readiness không gọi inference mỗi GET health.
- Vòng synthesis dùng request/token estimate mới và deadline còn lại; ưu tiên continuation/capacity reserve hữu hạn. Không reserve hai full rounds cho mọi chat. Persona/catalog snapshot nhất quán qua key switch; tất cả request stateless gửi đủ history cần thiết.

### HTTP, failover và bảo toàn action

- Dùng `aiohttp` pool hiện có để tránh thêm SDK retry ẩn. HTTPS endpoint `https://api.groq.com/openai/v1`; giới hạn connect/read/total theo deadline còn lại. Tối đa một routing layer sở hữu attempts.
- 429: đọc body xác định loại limit, `retry-after`/reset headers để cooldown group tương ứng. Key khác cùng exhausted group không phải fallback hợp lệ. 401 credential disable; 403/capability và 400/model/payload lỗi không retry vòng tất cả keys.
- 5xx/network failures chỉ failover bounded khi chưa publish semantic events/action và còn budget. Timeout sau dispatch tính uncertain usage; không gọi nó là request miễn phí.
- Khi đã publish speech/control/tool proposal hoặc có side effect/receipt: không replay cùng inference tự động; trả failure/partial theo contract hiện hành. Vòng synthesis mới với receipt là round hợp lệ, khác replay thao tác đã dispatch.
- Stream parse tới terminal đúng; content trước tool vẫn buffer theo contract hiện tại. Không lấy “first token nhanh” bằng cách phát lời claim trước receipt.
- Nếu dùng SDK thay aiohttp theo evidence, phải đặt SDK retries=0 và test tổng attempt đúng trần.

### Prompt dài và caching

- Static persona/technical instructions/tool schemas có thứ tự ổn định; context động, thời gian, ID đặt sau phần prefix ổn định nếu contract cho phép.
- Cache token estimate theo nội dung/version/model; không cache raw private prompt vào telemetry. Cache hit estimate không được xem là actual cached tokens.
- Groq docs tại lúc lập plan chỉ liệt kê prompt caching cho một số GPT-OSS models; model chọn thực tế phải kiểm tra lại. Không hứa cache cho Qwen alias cũ.
- Cached tokens có thể được khấu trừ rate limit sau processing: reserve bảo thủ trước request; reconcile theo usage, không cấp oversubscription vì dự đoán prefix hit.
- Không semantic response-cache giờ hiện tại, tool side effect hay memory mutation. Background summary nếu cần vẫn AI, bounded, không chặn chat và không thêm trước mỗi turn.

## Implementation plan

### G0 — Baseline, model và quota inventory

- [x] G0.1 Đọc root/task AGENTS, toàn plan; ghi HEAD/dirty/config fingerprint/PID/invocation và timestamp UTC-aware. Không dump config/key/persona vào artifact.
- [x] G0.2 User cấp 5–6 key qua env/secret store, alias `groq_a..f`; xác nhận quota_group độc lập đã khai báo, quota/model permissions theo dashboard. Không request key trong nội dung commit/chat.
- [x] G0.3 List models trực tiếp, chọn một model Groq production thực sự có native tools/context phù hợp. Nếu alias cũ không có, trình user bảng model khả dụng và quality/latency spike; chưa có quyết định thì BLOCKED model migration, không tự chọn tên gần giống.
- [x] G0.4 Kiểm tra token/reasoning/stream_options/tool_choice support cho model đó bằng request tối thiểu; mapping parameters theo capability, không gửi `reasoning_effort=none` mọi model. Lưu HTTP status/usage sanitized.
- [x] G0.5 Chốt RPM/TPM/ngày + extra dimensions; ít nhất một quota group hợp lệ mới readiness. Test secret missing/duplicate alias/shared group config.

Files: `config/settings.py`, `config.example.yaml`, local `config.yaml` khi implement, script `scripts/probe_groq.py` mới, `docs/SETUP.md`. Gate: inventory đủ; không đo latency trên model alias chưa xác minh.

### G1 — Provider separation và migration toàn bộ đường gọi

- [x] G1.1 Tạo `core/providers/llm/groq_direct.py` + factory. Tách persona/template persistence và shared streaming helpers ra module chung khi cần; không copy cả provider rồi để hai parser drift.
- [x] G1.2 `server.py` khởi tạo/log provider từ config thực tế; `BaseLLM`/`TurnRunner` giữ contract có typed failure và request purpose/deadline. Không phụ thuộc provider class cụ thể trong session/HTTP.
- [x] G1.3 Migrate `stream_turn`, `stream_chat`, `stream_chat_with_control`, correction opt-in, `_control_completion`, recovery prewarm/repair và idle farewell qua một request dispatcher. Test spy đảm bảo không đường nào bypass quota.
- [x] G1.4 Giữ saved persona precedence, budget byte/token, setter/version API, cache invalidation và shutdown. Provider factory không reset persona/file state.
- [x] G1.5 Loại OmniRoute khỏi runtime default, base URL/alias/log/docs và startup dependencies. Code legacy nếu giữ để truy vết phải không reachable trong deployment mới; cấu hình cũ báo migration error rõ, không tự redirect sang gateway.

Dependency: G0. Files: provider mới/shared helpers, `base.py`, `server.py`, `http_server.py`, `core/turn_runner.py`, `core/session.py`, `core/response_audio_cache.py`, tests imports/fixtures. Gate: contract tests chạy Groq adapter, không request localhost:20128.

### G2 — Atomic quota ledger và estimator

- [x] G2.1 Tạo `quota.py`: injectable clock, typed buckets/reservations, bounded retention, invariant số dư không overspend dưới concurrent admission.
- [x] G2.2 Tạo `token_budget.py`: whole-request estimator/tokenizer phù hợp, static prefix cache, output cap/margin, actual-error metrics. Context overflow fail trước network, không silent truncate mandatory data.
- [x] G2.3 Implement predispatch release, postdispatch uncertain charge, usage settle một lần, headers thiếu/malformed/out-of-order, minute/day expiry, cooldown/backoff.
- [ ] G2.4 Persist aggregate/restart conservative strategy; key disable/re-enable hoặc quota config update không xóa usage đã tiêu. (Chưa làm: restart mất ledger RAM; discovery mode + cooldown ngắn hạn che một phần. Ghi nhận gap.)
- [ ] G2.5 Test ≥100 concurrent admission bằng fake clock/boundary, repeated cancellations, response reordering, external quota consumption và reload. RAM/ledger không tăng vô hạn.

Dependency: G0/G1. Gate: known-exhausted group không dispatch; independent group vẫn chạy; no double release/charge lost.

### G3 — Router, fairness, priority và failover

- [x] G3.1 Tạo `router.py`: eligibility + latency-aware selection + bounded fairness; single-process shared instance. Routing không network I/O, không LLM classification.
- [ ] G3.2 Queue admission bounded và deadline propagation tới HTTP/round/cleanup; all-exhausted không sleep nhiều giây trên voice path. Quota replenishment/cancel đánh thức waiter đúng.
- [x] G3.3 Purpose priority + continuation priority; background không chiếm last headroom dành live. Mỗi purpose vẫn tính vào quota đầy đủ.
- [x] G3.4 429/401/403/400/5xx/network handling theo thiết kế; attempt IDs và request IDs riêng LLM rounds. Circuit breaker lỗi transport tách cooldown quota.
- [ ] G3.5 Không retry sau observable events/action dispatch; preserve receipts khi cancel và follow-up sang key mới. Test tool-only, mixed-stream, late tool, truncated EOF, failed synthesis.

Dependency: G2. Gate: exhausted-before-call skip không HTTP; unexpected 429 chuyển group khác không ngủ retry-after nếu có capacity; max attempts/deadline enforce được.

### G4 — Config và vận hành

- [x] G4.1 Schema đề xuất: `llm.provider=groq`, `llm.model=<verified-id>`, base_url Groq, `llm.key_pool[]` gồm `id`, `api_key_env`, `quota_group`, `enabled`; `llm.quota_groups[]` chứa quota scopes/limits; `llm.routing` chứa headroom, concurrency/burst, admission wait, max attempts và priority policy. Final schema có examples/types/validation rõ.
- [x] G4.2 Dùng `GROQ_API_KEY_A..F` env references; không secret trong YAML. Cấu hình mẫu không có key thật và không giả quota tài khoản. Missing quota/model cần operator điền rõ, không lấy một RPM/TPM bịa làm default production.
- [x] G4.3 Đồng bộ config local và example theo yêu cầu user: cùng schema và giá trị operational đã chốt, secret env riêng. Nếu muốn giữ tuning local khác phải trình rõ diff và xin user quyết, không tự diễn giải “giống nhau” thành chỉ keys.
- [ ] G4.4 Readiness: active eligible groups, exhausted vs invalid credentials, recovery readiness; status quota không phải health model thời gian thực. Không probe nóng tất cả key mỗi lần restart.
- [ ] G4.5 Single-process ownership ghi rõ; nếu chia nhiều worker, bắt buộc shared ledger trước khi gọi pool. Service manager/env scope đúng; kiểm transient unit còn tồn tại trước restart, không chạy hai server/GPU owners.

### G5 — Telemetry và test harness đáng tin

- [ ] G5.1 Metrics theo run/session/turn/round/request/attempt: group/key alias, estimate/reserved/actual tokens, quota skip, reason, queue_ms, route_cpu_ms, HTTP headers wait, first content/usable event, 429/reset, retry count, cache usage, outcome. Không raw key/header Authorization/error body chưa sanitize.
- [ ] G5.2 Phân biệt request accepted, inference success, recovery sent và valid answer. `tts:stop` hoặc binary không đủ để chấm semantic success.
- [ ] G5.3 Sửa `scripts/hw_acoustic_test.py`: journal cursor thực hoặc UTC có offset, chỉ log mới đúng service invocation/device/session/turn; baseline fresh session + wake event đúng board; matching STT và TTS cùng turn. Idle phải reason `idle_timeout`, re-wake session ID mới. Không gán transcript bất kỳ là echo; output enable có thể là error alert, không phải answer proof.
- [ ] G5.4 Sửa `scripts/load_probe.py`: deadline tuyệt đối (mỗi recv dùng remaining), validate hello, stop timeout session trước lượt kế để tránh nhận stale audio, accounting mỗi requested turn đúng một lần, failure không xóa completed trước đó. Dùng terminal telemetry để không tính fallback thành answer. Lưu raw samples + failure reasons, percentile có quy ước thống nhất.
- [ ] G5.5 Test negative harness: log cũ, timezone +07, session khác, service restart, error fallback, empty TTS, missing usage, timeout rồi late stop. Tất cả phải FAIL/BLOCKED đúng thay vì PASS giả.

### G6 — Persona dài, semantics và end-to-end validation

- [x] G6.1 Unit/static full suite + typed provider/router/ledger tests. Ghi count thật, không hardcode kế thừa 163. → 207/207 PASS 2026-09-10 (quota 14, router 7, provider 12, config 7, còn lại regression cũ).
- [ ] G6.2 Deterministic upstream simulator: 6 groups quota riêng, shared-group variant, RPM/TPM/day exhausted, 429 unexpected, header reordering, cancel trước/sau dispatch, restart mid-window, external consumer. Không tiêu quota Groq thật để cố tạo daily exhaustion.
- [ ] G6.3 Real Groq canary từng group bằng synthetic data: native tools, receipt synthesis, no tool side effects, language/persona, stream terminal/usage. Không thấy backend support thì UNSUPPORTED/BLOCKED, không tick từ mock.
- [ ] G6.4 Matrix context ngắn/vừa/gần budget, session memory và giả lập RAG, 1/2/4 clients, chat/tool/confirmation; giữ cùng persona/version qua group switch. Không tự giảm prompt/max_tokens để lấy số đẹp.
- [ ] G6.5 Benchmark 100 attempts cho workload hợp lệ sau quality gate; tách warm/cold, chat/tool/memory, admission/inference/TTS. Không chạy nhiều benchmark cùng lúc làm hỏng single-session baseline. Record quota headroom và attempt count.
- [ ] G6.6 Hardware stock hiện có: loa laptop phát EN wake + VieNeu câu Việt → nhận STT/answer liên kết đúng; idle→close→re-wake, request mới sau lỗi. Speaker audio/tail/AEC chỉ PASS nếu có ghi âm vật lý/quan sát phù hợp, không suy từ board state.

### G7 — Rollout, docs và handoff

- [ ] G7.1 Deploy theo stage: provider direct canary 1 group → 2 → toàn pool; identity/quotas đúng trước tăng concurrency. Không mở hai GPU servers; runtime probe trực tiếp không cần load thêm ASR/TTS.
- [ ] G7.2 Sau implement: đồng bộ config/env/example/docs, restart service hiện có theo scope user cho phép, đợi ready, xác minh PID/source fingerprint/model endpoint. Backup config non-secret trước migration, giữ saved persona/memory.
- [ ] G7.3 Rollback về bản Groq-direct/config ổn định hoặc báo degraded nếu chưa có; không tự khôi phục OmniRoute ngoài ý user. Emergency rollback cần quyền rõ ràng, không reset DB/user changes.
- [ ] G7.4 Cập nhật README, SETUP, ARCHITECTURE, API_PROTOCOL/diagnostics, TESTING, STATUS, REMAINING_WORK và index/plans liên quan. Historical evidence giữ nhãn gateway-era, không đổi tên thành Groq-direct results.
- [ ] G7.5 Commit/push chỉ khi user yêu cầu rõ; không thực hiện chỉ vì checklist này hoặc rule docs cũ ghi commit. Tổng task PARTIAL nếu thiếu keys/model/quota/hardware hoặc gate SLA không đạt.

## Compatibility constraints

- Tuân thủ [root AGENTS](../AGENTS.md), [reference baseline](../REFERENCE_BASELINES.md): ESP32 `c7241272f2d5fd140c77542f3cf12d09e717fc2f`, server reference `c478257517b892047db3afaaeeb25e2b1e115931`. Không sửa/reset/commit references.
- Firmware giữ hello/listen/abort/tts/binary/MCP stock; router/key aliases không xuất xuống ESP32. Không yêu cầu firmware hiểu Groq, quota hoặc retry.
- AI sở hữu semantics; không mở rộng `_FAREWELL_*` keyword lists hoặc literal fallback như giải pháp routing/quota. Những matcher/fallback đang có là known gap cần rà riêng, không hợp thức hóa bằng plan này.
- Context/permission/receipt/cancel invariants ưu tiên hơn TTFT đẹp. Không strip arbitrary nội dung `[ ... ]` có nghĩa chỉ để làm sạch metadata; migration parser phải có regression bảo toàn quoted/foreign-language text.
- Không định danh durable owner từ key Groq, quota group, Device-Id hoặc Client-Id. Không bật durable/MCP side effects để test router.

## Validation

- [ ] Từ `veetee-server/`: `../../venv/bin/python -m unittest discover -s tests -v` và `../../venv/bin/python -m compileall -q core config server.py http_server.py scripts tests`; `git diff --check` từ root. Capture exit status thật, không pipeline `tail` che lỗi.
- [ ] Config parser tests: missing secrets/quota/model, invalid periods/caps, duplicate aliases, same-group sharing, example/local parity, secrets absent from logs/artifacts.
- [ ] Router property/race tests: budget invariant, ≤max attempts, deadline not reset, cancel idempotent, daily quota retained, stream failure không double execution.
- [ ] HTTP fake server integration: real streaming socket/chunk boundaries/status/headers/usage; không chỉ mock một helper.
- [ ] Runtime corpus/sample labels chốt trước; evaluator phân biệt answer/recovery. Không khẳng định 100/100 semantic success chỉ từ audio received.
- [ ] Board smoke + correlated new logs, acoustic measurements báo riêng. Không gán failure do laptop thiếu loa/PSRAM/AEC nếu chưa đủ evidence.
- [ ] Docs-only lập plan: file links/headings/index/diff; không restart hoặc chạy inference chỉ để validate Markdown.

## Acceptance criteria

- [x] Không HTTP runtime nào còn đi OmniRoute; model ID Groq xác minh, mọi LLM purpose dùng pool chung. → journal process mới chỉ có GroqDirectLLM warmup/requests; grep code không còn default localhost:20128 trong đường groq.
- [ ] 5–6 group độc lập cấu hình hợp lệ; các key cùng group nếu có không được nhân quota.
- [ ] Known-exhausted group bị loại trước network; atomic reservations không oversubscription trong simulation. Out-of-band usage vẫn có thể gây 429 và được ghi/hiệu chỉnh trung thực.
- [ ] Admission wait bounded, router CPU p95 <5ms ở tải test công bố; tokenizer cost và cold estimate đo riêng. Không fallback-sleep chain nhiều giây khi còn group đủ quota.
- [ ] Key failover không đổi persona/context/receipt và không execute action hai lần; chat 1 round, action rounds có trần hiện hành, attempts tách rounds.
- [ ] Prompt dài/context + tool/memory semantic gates không regression; cache support/actual cached usage được xác minh theo model, không giả định.
- [ ] Quota exhausted/credential error/upstream fail có degraded outcome rõ; recovery không tính valid answer. Safe shutdown/restart không rò reservation/secret.
- [ ] Quality-qualified benchmark ≥100 attempts/nhóm, success ≥99%; p95 speech-end→first useful voiced audio <1000ms, p50 ≤600ms báo riêng. Nếu router đúng nhưng end-to-end chưa đạt: routing gate PASS, tổng latency PARTIAL/NOT_MET, không hạ gate.
- [ ] Config/example/env/docs đã đồng bộ và deployed provider fingerprint được xác minh khi triển khai; hardware gates có đúng loại evidence.

## Risks / open questions

- User đã xác nhận quota riêng; executor vẫn cần actual limit values, secret env names và model được phép. Không cần hỏi lại quyền đổi khỏi OmniRoute, nhưng không được bịa quota/model.
- Groq model availability và reasoning parameters có thể đổi; Qwen alias gateway cũ không chứng minh direct equivalent.
- 429 dự đoán không thể tuyệt đối khi consumers ngoài router chia quota. Header reconciliation/cold restart có thể giảm utilization để đổi lấy an toàn; phải đo.
- HTTP /models 200 chỉ chứng minh credential/list access, không chứng minh model inference/TTFT/stream tools.
- Real cache behavior có scope riêng; phân tán quá nhiều group có thể giảm cache hit dù giảm quota wait. Chọn theo evidence latency, không round-robin mù quáng.
- Benchmark cũ có false-positive risks; phải sửa harness trước khi so baseline/after. “Không retry ở provider VeeTee” chưa đủ chứng minh mọi delay do OmniRoute.
- Không cam kết 600ms từ 6 keys: endpoint ASR, TTS GPU/shared lease và playback vẫn là giới hạn độc lập.

## Tài liệu tham khảo

- [Groq Rate Limits](https://console.groq.com/docs/rate-limits): org scope, RPM/RPD/TPM/TPD, extra dimensions; headers requests=RPD và tokens=TPM, retry-after khi 429.
- [Groq API Reference](https://console.groq.com/docs/api-reference): direct requests, model-specific supported parameters, streaming usage.
- [Groq Models](https://console.groq.com/docs/models): xác minh IDs/capability tại thời điểm triển khai.
- [Groq Prompt Caching](https://console.groq.com/docs/prompt-caching): exact prefix, supported models, actual cached tokens và rate-limit accounting.
- [LiteLLM Load Balancing](https://docs.litellm.ai/docs/proxy/load_balancing): tham khảo pre-call checks/usage/latency routing; không bắt buộc thêm gateway vào VeeTee.

## Execution status

- Status: `IN_PROGRESS` (executor bắt đầu 2026-09-10 theo yêu cầu user; G0/G1/G2(lệch G2.4 persist)/G4 xong, G3/G6.1+G6.3 một phần, G5/G7 còn lại).
- Completed G0 (2026-09-10):
  - 4 keys (`GROQ_API_KEY_A..D` trong `~/.config/veetee/server.env`, mode 600, ngoài repo) đều `200` trên `/models`; user xác nhận quota độc lập.
  - Lưu ý mạng: Cloudflare trả 1010 với fingerprint python-urllib; curl browser-UA và aiohttp (stack của server) đều `200`. Mọi probe sau dùng curl/aiohttp.
  - Model direct đã xác minh: `qwen/qwen3.6-27b` tồn tại (ctx 131072, max_out 16384). Minimal completion `200`, có `usage` + headers `x-ratelimit-*` (`limit-requests=1000`, `limit-tokens=8000` quan sát trên key A; limits thật lấy theo từng group khi chạy).
  - Qwen trả `<think>` blocks — provider mới phải giữ strip như Omniroute cũ.
  - Key linh hoạt số lượng: code quét mọi `GROQ_API_KEY_*`, alias = suffix; config map alias → quota_group (mặc định mỗi alias một group riêng).
- Completed G1+G2+G4 (code, 2026-09-10):
  - Mới: `quota.py` (atomic ledger, sliding windows, cooldown, discovery, settle ok/rejected/uncertain), `token_budget.py` (whole-request estimate), `router.py` (eligibility + latency selection + bounded admission + failover exclude), `groq_direct.py` (provider riêng, không import omniroute; cùng wire contract).
  - Config: `provider/key_pool/quota_groups/routing` + validation; example + local đồng bộ (groq, 4 keys, discovery mode).
  - `server.py` dựng Groq path theo provider; omniroute chỉ còn khi cấu hình legacy.
  - Phát hiện khi implement: Qwen direct nuốt `max_tokens` nhỏ bằng `<think>` làm recovery/correction rỗng → gửi `reasoning_format=hidden` + `reasoning_effort=none` (Groq direct chấp nhận, đã verify 200) trên mọi payload.
- Completed G3 logic + G6 unit (2026-09-10): 34 tests mới (ledger 12, router 7, provider 8, config 7); full suite `197/197 PASS`; `compileall` + `diff --check` PASS.
- Completed runtime canary (2026-09-10, process Groq-direct, health ready): chat + tool clock đúng giờ + recall lịch sử đúng; log không còn traffic OmniRoute/20128; prewarm recovery đã về ready sau fix reasoning params.
- Completed A/B qwen vs gpt-oss-20b (2026-09-10, `scripts/ab_model.py`, cùng router/ledger/persona): transport gpt-oss OK sau 2 fix — (1) `reasoning_effort=none` bị Groq 400 nên per-model effort (`extra_models` + `model_reasoning_effort`, gpt-oss dùng `low`); (2) gpt-oss đôi khi đóng stream ngay sau `finish_reason` không gửi `[DONE]` nên provider chấp nhận terminal finish (có regression test). TTFT tương đương (~0.4–0.7s), native tool call gpt-oss chạy live. Chất lượng 1 mẫu: qwen đọc snapshot giờ đúng, gpt-oss bịa "+1h" ("15:30" lúc 14:27) — cần eval rộng trước khi đổi default; default giữ qwen.
- Remaining: G3 live failover/cooldown quan sát thật; G5 (quota metrics endpoint/diagnostics, sửa `tts:stop`-as-success trong harness, deadline propagation vào router); G6 benchmark 100/hardware trên đường Groq mới; G7 rollout docs + commit/push (chờ user cho phép).
- Deviations from plan: deadline chưa truyền từ TurnRunner vào router (TurnRunner không đưa deadline cho provider); admission bounded + HTTP/turn timeouts ngoài vẫn giữ. TurnRunner/shared omniroute file không sửa để tránh regression đường legacy/tests.
