# Rà soát, hợp nhất tài liệu và bảo toàn task-plans

Status: `PLANNED`

Created: `2026-09-09`

## Goal

Người mới và model thực thi tìm được hướng dẫn hiện hành, hiểu đúng AI semantics/server-only và biết bằng chứng nào đã có/chưa có. Giảm các bản sao kiến trúc/config/checklist làm tài liệu lệch source; giữ toàn bộ task-plans và bằng chứng review lịch sử.

Yêu cầu hiện tại là lên plan và xem tài liệu nào có thể xóa/hợp nhất. Lượt này chỉ tạo hai plan mới và cập nhật index `task-plans/README.md`; các thao tác sửa/hợp nhất/xóa docs phía dưới là công việc cho executor khi user yêu cầu thực hiện, không phải đã làm.

## Current state

### Phạm vi đã đọc

- Root: [AGENTS.md](../AGENTS.md), [REFERENCE_BASELINES.md](../REFERENCE_BASELINES.md).
- Server: [README](../veetee-server/README.md), tất cả 6 file Markdown hiện có trong `veetee-server/docs/`.
- `task-plans/AGENTS.md`, README, template, các plan trước và note rules; đối chiếu sections/status/dependencies với audit runtime. Plan này không tái nghiệm thu các checkbox cũ.
- Prompt template và saved persona được phân loại là input runtime, không phải tài liệu tùy ý dọn. Không đọc/xuất persona riêng chỉ để làm inventory.
- Đối chiếu source: session event queue/direct clock/fallback/context; config validator/example/local; management persona API/UI; start script; benchmark CLI và sample summary.

### Mâu thuẫn và trùng lặp đã xác định

| ID | Nơi hiện có | Vấn đề | Hướng xử lý |
| --- | --- | --- | --- |
| D01 | `docs/PLAN.md`, phần wake/greeting/exit | Mô tả allowlist exact wake, fixed greeting không LLM, exit exact-match; sai với hướng AI và source hiện tại. | Loại khỏi mô tả hiện hành; không chuyển nguyên văn sang architecture mới. |
| D02 | `docs/PLAN.md`, `VOICE_PIPELINE_STATUS.md` | Queue 3/50 tests so với event queue 8 và số tests lịch sử khác; status bị dùng như kiến trúc. | Runtime constants dẫn source; test count gắn snapshot/evidence, không khẳng định “hiện tại” vô thời hạn. |
| D03 | README, SETUP, PLAN, STATUS | Lặp VAD 450 ms; local config đã có 320 ms. | Ghi default vs local observed config vs process-verified measurement rõ ràng; không tự đổi default theo local config. |
| D04 | STATUS, README, SETUP, API_PROTOCOL | Rule AI/receipt synthesis nhưng vẫn chấp nhận direct clock hoặc phủ nhận toàn bộ literal fallback. | Architecture phân biệt implemented/known gap/planned, link A01–A03 ở plan runtime; chỉ nói fixed khi có evidence. |
| D05 | README, API_PROTOCOL, SETUP, STATUS | Lặp kiến trúc intent/memory/tool rounds/deadlines và nhiều bảng config. | Gom architecture vào một nơi; setup chỉ cấu hình/vận hành, protocol chỉ wire contract, status chỉ acceptance/evidence. |
| D06 | `SETUP.md` | “100% Local” nhưng LLM qua OmniRoute có thể ra provider ngoài; Python mở đầu 3.12+ nhưng prerequisites 3.10–3.12. | Nêu rõ process local và inference provider; phiên bản support phải có môi trường/test evidence. |
| D07 | `SETUP.md` | Danh sách pip thiếu ONNX Runtime/NeMo so với start.sh; E2E mô tả Deepgram dù ASR mặc định Parakeet; YAML sao chép thiếu/bị cũ. | Xác minh dependency và CLI từ source; config example là nguồn chính, hướng dẫn không chép cả file. |
| D08 | `SETUP.md` | Vừa user unit `veetee-server-bg.service` vừa hướng dẫn tạo system unit, dùng đường dẫn/user cá nhân. | Chia local environment note và portable setup; chọn một cách chạy trên mỗi môi trường, kiểm tra xung đột port/GPU. |
| D09 | `API_PROTOCOL.md` | Section 5.1 trước 5; trộn nội bộ AI với protocol stock; authorization handshake cần đối chiếu enforcement thật. | Sửa cấu trúc, tách stock/browser/internal/management; không mô tả header được nhận thành auth đã enforce. |
| D10 | `ESP32_CONFIG.md`, API, README, STATUS | Lặp giải thích AEC/barge-in/tail và checklist hardware. | Protocol sở hữu wire/limits; ESP32 sở hữu thao tác kết nối; TESTING sở hữu nghiệm thu; status dẫn evidence. |
| D11 | `PIPELINE_REVIEW_2026-09-08.md` | Báo cáo commit cũ có đề xuất explicit-only; hữu ích lịch sử nhưng không còn chỉ dẫn thực thi. | Giữ nguyên evidence, thêm nhãn snapshot lịch sử và link plan hiện hành khi thực thi docs. |
| D12 | Các task plan trước | Có statuses/test counts/explicit-only/2-round constraints đã bị hướng mới thay thế; note rules 2026-09-09 không theo template plan. | Giữ nguyên files/lịch sử; README chỉ rõ supersession theo phạm vi và phân loại note, không biến note thành plan COMPLETED. |
| D13 | README benchmark vs script | README dùng first audio/binary tên cũ; benchmark hiện đo first voiced PCM; cả hai chưa bảo đảm useful/grounded answer. | TESTING định nghĩa metric/version/quality gate; status ghi artifact theo đúng metric gốc. |
| D14 | Docs persona | Chưa nói rõ saved persona ghi đè config, API/UI cap 4000, prefix cache không đồng nghĩa cached audio. | SETUP mô tả vận hành/cap hiện hành; ARCHITECTURE mô tả context/version/caches; cập nhật cùng M3 runtime. |

### Nguyên tắc về quyền ưu tiên

Yêu cầu mới nhất của user và root AGENTS có ưu tiên; implementation/test/evidence xác định trạng thái thật. Tài liệu architecture mô tả behavior hiện hành, task plan mô tả công việc tương lai và review mô tả snapshot lịch sử. Không dùng câu trong review cũ để khôi phục matcher ngữ nghĩa hoặc build firmware.

## Scope

- In scope: kiểm kê file, chuyển nội dung có mapping, loại đoạn trùng/lỗi thời, điều hướng, chuẩn hóa trạng thái/evidence, kiểm link và ví dụ config/CLI.
- In scope: tạo `docs/ARCHITECTURE.md` và `docs/TESTING.md` khi thực thi để có nơi sở hữu nội dung hiện rải rác. Chỉ tạo thêm docs khi có nội dung riêng đủ cần thiết.
- Out of scope: sửa runtime/config/service để làm source khớp tài liệu; nội dung kỹ thuật chưa implemented thuộc [plan runtime](2026-09-09-ai-persona-tools-memory-latency.md).
- Out of scope: xóa/đổi tên/gộp mất file bất kỳ trong `task-plans/`; xóa artifact/raw evidence/reference/model/data/secret/config riêng hoặc prompt runtime.
- Không tự commit/push/deploy/restart vì đang chỉnh docs.

## Implementation plan

### D0 — Inventory, ownership và bảo toàn lịch sử

- [ ] D0.1 Chụp danh sách/hash docs/task-plans trước sửa và dirty diff hiện có; không ghi đè thay đổi ngoài scope. Phân loại tracked/untracked/ignored bằng metadata, không xem untracked là rác.
- [ ] D0.2 Rà inbound links và plain-text references bằng `rg`; gồm README, docs và tất cả task-plans. Khi ref từ plan cũ còn tồn tại, giữ đường dẫn đích hoặc trang redirect Markdown, không làm hỏng handoff cũ.
- [ ] D0.3 Xác minh source/symbol cho D01–D14, ghi date/HEAD/dirty fingerprint khi dùng số liệu. Không sao chép local hostname/user/credential/fixture riêng vào hướng dẫn portable.
- [ ] D0.4 Giữ task-plans metadata/checklist của lần thực thi cũ; nếu cần addendum sau này chỉ append rõ ngày/evidence/superseded scope. Không xóa hoặc reset checkbox hàng loạt.

Gate: inventory đủ, mỗi file có vai trò và phương án dưới đây, biết nơi nhận mọi nội dung độc nhất trước khi bỏ đoạn.

### D1 — Cấu trúc tài liệu đích và mapping từng file

| File hiện có | Quyết định đề xuất | Nội dung giữ/chuyển | Điều kiện bỏ nội dung |
| --- | --- | --- | --- |
| `/AGENTS.md` | **Giữ** | Rule phát triển bắt buộc AI/server-only/reference/evidence. Docs khác dẫn link, không tự tạo rule cạnh tranh. | Không xóa. Chỉ bổ sung rule khi requirement đã rõ và tránh copy toàn bộ architecture. |
| `/REFERENCE_BASELINES.md` | **Giữ** | Commit upstream, cách phân biệt baseline/local changes. | Không xóa/di chuyển. |
| `veetee-server/README.md` | **Giữ, rút gọn** | Giới thiệu, quickstart ngắn, compatibility summary, links architecture/setup/protocol/testing/status/task plans. | Chuyển YAML dài, architecture chi tiết, số test tạm thời và checklist sang owner docs. |
| `docs/PLAN.md` | **Hợp nhất, retire vai trò plan hiện hành** | Chuyển kiến trúc đã đối chiếu sang ARCHITECTURE; cách test sang TESTING; evidence hợp lệ có snapshot sang STATUS. | Mặc định giữ file làm trang dẫn ngắn tới architecture/task plans vì plan cũ tham chiếu đường dẫn này; không xóa file ngay. |
| `docs/SETUP.md` | **Giữ, chỉnh thành runbook** | Install deps đã xác minh, config source/overrides, start/service modes, endpoints/readiness, persona management, troubleshoot. | Bỏ full YAML copy, legacy semantic tutorial, hardware/architecture/test matrix lặp sau khi link đích tồn tại. |
| `docs/API_PROTOCOL.md` | **Giữ** | Stock wire messages/binary/handshake/MCP, extension browser được phân biệt, permission boundary đã xác minh, ordering/cancel/limits. | Chuyển tool reasoning/memory internals sang ARCHITECTURE; giới thiệu ngắn + link thay cho duplicate prose. |
| `docs/ESP32_CONFIG.md` | **Giữ, rút gọn** | Kết nối qua tùy chọn stock, OTA/WS, modes/capabilities thực, troubleshooting và quick smoke board. | Full JSON schema sang API; full hardware acceptance sang TESTING; không bỏ giới hạn không ép AEC/mode hoặc không có playback ACK. |
| `docs/VOICE_PIPELINE_STATUS.md` | **Giữ làm bảng trạng thái duy nhất** | Feature, source snapshot, evidence type/path/date, outcome, missing gate, link active plan. | Bỏ architecture/how-to/checklist dài; không coi test count/healthy từ phiên cũ là runtime mới. |
| `docs/PIPELINE_REVIEW_2026-09-08.md` | **Giữ tại đường dẫn cũ như evidence lịch sử** | Nội dung review/reproduction/commit/test count nguyên gốc; thêm banner đầu tài liệu phân biệt historical/non-normative và link tiếp nối. | Không xóa/gộp mất raw findings; không cần tạo archive copy trùng hoặc di chuyển để “gọn”. |
| `task-plans/*.md` (tất cả) | **Giữ toàn bộ** | Handoff, template/rules, kết quả từng lần, note legacy. | Không delete/rename/merge files. Index được cập nhật; checklist chỉ đổi khi có execution evidence. |
| `task-plans/2026-09-09-ai-first-semantics-rules.md` | **Giữ, phân loại note lịch sử** | Rule note đã có; dẫn root AGENTS và plan mới để áp dụng. | Không tự xóa vì không theo template; không tự gán execution status/test pass. |
| `agent-base-prompt.txt` | **Giữ, không thuộc dọn docs** | Template đọc bởi provider runtime; ràng buộc persona/TTS. | Chỉ sửa theo plan runtime và có test, không xóa/hợp nhất vào Markdown. |
| `data/base-prompt.txt` | **Giữ, không thuộc dọn docs** | Saved persona của user/operator, ưu tiên hơn config khi load. | Không copy vào docs/commit/raw benchmark. |
| `benchmark-artifacts/*` | **Giữ làm evidence** | Source metric/samples/status/hash liên kết từ STATUS/TESTING. | Dọn retention là task riêng; không xóa để che thất bại SLA. |
| `patches/xiaozhi-esp32-barge-in.patch` | **Giữ artifact lịch sử ngoài scope dọn docs** | Hướng dẫn kết nối stock nêu không áp; không coi đây là requirement. | Không tự xóa patch hoặc sửa reference; cân nhắc riêng nếu user yêu cầu. |
| `config.example.yaml` / `config/settings.py` | **Giữ nguồn cấu hình thực thi** | Docs dẫn defaults/validation từ đây. | Dọn docs không sửa default/validation. |

**Kết luận về xóa:** chưa có tài liệu hoàn chỉnh nào nên xóa ngay mà không mất vai trò hoặc làm hỏng references. Ứng viên retire rõ nhất là nội dung cũ của `docs/PLAN.md`, chuyển xong giữ link stub; phần có thể bỏ trực tiếp sau migration là duplicate YAML/architecture/checklists và các mô tả hiện hành sai. Giữ nguyên tất cả task-plans theo yêu cầu user.

- [ ] D1.1 Tạo `docs/ARCHITECTURE.md`: pipeline/current behavior; AI/server boundary; persona/context budget; tool/confirmation/receipt/history; memory/RAG seam; latency critical path; cancellation/ownership/audio; known gaps và links plan. Không chép toàn bộ backlog/checklist vào đây.
- [ ] D1.2 Tạo `docs/TESTING.md`: unit/integration/runtime/hardware khác nhau; lệnh chạy thực; fixtures/labels; metrics/quality/sample gates; persona/semantic/load matrix; cách đọc artifact và khi nào PARTIAL. Chi tiết kế hoạch tương lai vẫn link task plan.
- [ ] D1.3 Rút README thành entrypoint và quickstart; không tạo thêm docs/README hoặc AI_RULES riêng nếu không cần navigation mới. Rule ở root AGENTS, giải thích kỹ thuật ở ARCHITECTURE.

### D2 — Hợp nhất nội dung theo nguồn sự thật

Dependency: D0/D1. Có thể làm trước runtime fixes nếu ghi đúng current gaps, không viết như A01–A12 đã fixed.

- [ ] D2.1 Architecture mới phân biệt `IMPLEMENTED`, `KNOWN_GAP`, `PLANNED`. Ghi rõ clock direct/literal fallback, speech-before-terminal và 2-round limit hiện còn; đổi mô tả khi milestone runtime có evidence.
- [ ] D2.2 Bỏ exact wake/exit/greeting template khỏi current architecture; bảo toàn lịch sử ở review/plans. Chọn thông số queue/default từ source và link, local observed VAD 320 chỉ là snapshot ngày audit.
- [ ] D2.3 SETUP bỏ lời hứa “100% local/offline”: ASR/TTS process local, LLM qua gateway và route có thể remote. Dependencies/Python/driver ghi tested version hoặc chưa xác minh, không tự dự đoán compatibility.
- [ ] D2.4 So lệnh cài với imports/start.sh và environment phục vụ test. Nếu thiếu manifest/lock tái lập, ghi limitation và issue handoff runtime, không tự cài/upgrade môi trường đang dùng để làm docs pass.
- [ ] D2.5 Config example là full reference; SETUP chỉ ví dụ delta tối thiểu có giá trị. Phân biệt config defaults, local overrides, saved persona và env; thay đổi persona theo behavior thật API/UI (hiện cap 4000, tương lai configurable theo M3).
- [ ] D2.6 Service instructions dùng placeholder/path portable; user unit và system unit là lựa chọn rõ, không hướng dẫn bật cả hai. Existing dev unit là snapshot, không yêu cầu tạo system unit trùng. README không mặc định start thêm khi service đang chạy.
- [ ] D2.7 API_PROTOCOL xác minh WS auth/OTA token/management bearer riêng từ code; mô tả chính xác header nào parsed/enforced. Internal control metadata không được viết như JSON command ESP32 phải hiểu; browser-only events có nhãn rõ.
- [ ] D2.8 ESP32_CONFIG giữ thao tác stock và giới hạn AEC/listen modes/wake/MCP; wire schemas dẫn API; hardware acceptance dẫn TESTING. Không yêu cầu patch/build/flash để hoàn tất tài liệu.
- [ ] D2.9 STATUS chỉ giữ bằng chứng có ngày/commit+dirty context và phân loại mock/model/hardware. Thay “regression hiện tại xanh 127” bằng “lần chạy đã ghi nhận …; chưa rerun ở snapshot này” nếu không có lần chạy mới.
- [ ] D2.10 Di chuyển nội dung độc nhất từ docs/PLAN trước, kiểm tra link đích, rồi thay bằng stub. Giữ `PIPELINE_REVIEW_2026-09-08.md` nguyên nội dung sau banner; đề xuất explicit-only cũ không là ngoại lệ deterministic được phép hiện nay.

### D3 — Index và quan hệ với task-plans

- [ ] D3.1 `task-plans/README.md` liệt kê cả hai plan 2026-09-09 và note rules hiện thiếu index; tag PLANNED chỉ trạng thái thực thi, không phải đã triển khai.
- [ ] D3.2 Nêu runtime plan mới tiếp nối các plan PARTIAL theo scope A01–A12; docs plan là migration riêng. Giữ danh sách và mô tả evidence lịch sử cũ, thêm caveat để không suy “đã hết hardcode”.
- [ ] D3.3 Links từ README/server STATUS/architecture dẫn plan mới; links trong plan cũ giữ được bằng file/stub. Không làm “consolidation” bằng xóa các file 2026-09-08 hoặc note 2026-09-09.
- [ ] D3.4 Khi executors hoàn tất milestone, cập nhật Execution status trong đúng plan và status index. Không đánh dấu plan cũ/new COMPLETED chỉ vì docs đã hợp nhất.

Trong lượt lập plan, index đã được bổ sung để người dùng tìm hai plan mới. D3 vẫn cần kiểm tra navigation toàn bộ sau migration docs; không vì index đã có mà tick các mục thực thi khác.

### D4 — Kiểm chứng và handoff

- [ ] D4.1 Kiểm local Markdown links, relative paths, anchors sau merge; loại code-fence/example URLs khỏi checker để không false positive. Plain-text paths của code mới dự kiến phải có nhãn planned.
- [ ] D4.2 Rà lại D01–D14: câu sai trong historical review/plan được phép giữ khi có nhãn snapshot; câu sai trong docs hiện hành phải hết. Không dùng grep cấm từ “keyword/regex” vì đó có thể là rule cấm matcher hoặc protocol parser hợp lệ.
- [ ] D4.3 Kiểm snippets với CLI `--help`/config schema thật, tránh khởi động engine hoặc gọi external API chỉ để check Markdown. Các lệnh runtime/hardware chưa chạy ghi NOT_RUN/PENDING, không tick theo suy luận.
- [ ] D4.4 Diff chỉ chứa docs/index được phép; mọi task-plan cũ giữ đường dẫn và content lịch sử. Không có `.py`, `.yaml`, prompt runtime, service, reference hoặc artifacts bị sửa ngoài scope.
- [ ] D4.5 Báo file giữ/hợp nhất/stub, nội dung chuyển đích, links checked, technical claims còn chờ evidence và các decision thay đổi so với plan.

## Compatibility constraints

- Documentation-only; giữ stock ESP32/Xiaozhi theo [baseline](../REFERENCE_BASELINES.md), không yêu cầu sửa FW hoặc extension nội bộ VeeTee.
- `references/` dùng để đối chiếu theo commit, không commit/sửa/reset working tree reference. Nếu không cần xác minh claim stock mới thì không phải đọc reference lần nữa.
- Preserved paths: tất cả task-plans và review lịch sử; docs/PLAN stub duy trì handoff cũ.
- Thứ tự nguồn: user/AGENTS → code/test/evidence hiện tại cho trạng thái → architecture/runbook/protocol → plan/review lịch sử có date. Không để một file docs cũ trở thành nguồn cho hành vi bị user cấm.
- Không tạo tài liệu trộn model-specific hidden reasoning với nội dung user-facing; docs chỉ giải thích contract/receipt có thể kiểm chứng.

## Validation

- [ ] `git diff --check` trên docs mới/sửa; Markdown headings/tables/code fences đúng, không thiếu relative-link targets hiện hành.
- [ ] Inventory before/after: không mất task-plans, review evidence, prompt/config/data/artifacts. Nếu file đích mới chưa triển khai, references chỉ dùng trong ngữ cảnh planned.
- [ ] Đối chiếu các thông số có nguy cơ lệch: queue 8, default/local VAD, correction validator, persona API/UI 4000 hiện tại, max rounds/calls, schema coverage, service modes, benchmark metric và sample/success gates.
- [ ] Mỗi PASS/COMPLETED/test count/số latency có evidence loại gì, ngày/source snapshot và limitations. Không nối số 50/84/112/127 từ các lần chạy khác nhau thành trạng thái mới.
- [ ] Không có yêu cầu default patch FW, exact phrase routing, literal tool result hoặc first binary = speaker ACK trong docs hiện hành. Review lịch sử giữ nguyên nhưng có banner rõ.
- [ ] Không chạy full server/unit suite chỉ vì sửa văn bản; nếu validate config/code example bắt buộc side effect thì chuyển sang runtime milestone có environment phù hợp và ghi chưa chạy ở docs pass.
- [ ] Smoke đọc theo 3 hành trình: người mới setup server; người có ESP32 kết nối stock; executor đọc kiến trúc → active plan → testing → evidence. Không phải tìm một quyết định qua nhiều file mâu thuẫn.

## Acceptance criteria

- [ ] Hai nguồn mới ARCHITECTURE/TESTING chứa đủ nội dung độc nhất chuyển từ docs cũ; README ngắn hơn và điều hướng đầy đủ.
- [ ] docs/PLAN không còn chỉ dẫn thực thi lỗi thời; stub dẫn đúng docs/task-plans, không broken historical path.
- [ ] SETUP tái sử dụng config reference, phân biệt local/remote và service modes, API/ESP32 không bị lẫn internal semantics.
- [ ] STATUS mô tả đúng known gaps và evidence, không nói SLA/hardware/full AI semantics đã đạt khi runtime plan chưa có bằng chứng.
- [ ] Review lịch sử được giữ tại đường dẫn cũ, note rules và toàn bộ task-plans được giữ và tìm được qua index. Không có yêu cầu xóa task-plans như điều kiện dọn docs.
- [ ] D01–D14 đã xử lý hoặc ghi PARTIAL kèm dependency runtime cụ thể; link/diff/inventory checks pass.
- [ ] Không đổi runtime/config/prompt/reference/deployment vì dọn docs; không sao chép credential/saved persona vào tài liệu.

## Risks / open questions

- `docs/PLAN.md` có inbound references từ task-plan cũ, nên chọn giữ stub thay vì xóa hẳn. Chỉ cân nhắc delete file về sau khi không còn người dùng/links phụ thuộc và không vi phạm yêu cầu giữ handoff.
- Một số khẳng định stock auth/AEC cần đối chiếu code/reference baseline kỹ hơn trước viết lại; đánh dấu chưa xác minh thay vì thêm lời hứa.
- Mốc source mới có thể khác audit lúc executor bắt đầu. Recheck symbols/defaults trước move; không copy finding đã được sửa như lỗi còn hiện hành.
- Việc thêm ARCHITECTURE/TESTING chỉ có ích nếu xóa đoạn trùng từ owner cũ; không được giữ nguyên tất cả rồi thêm hai bản sao mới.
- Sửa docs không chứng minh lỗi runtime đã hết. Có thể hoàn tất docs migration với known gaps ghi đúng trong khi runtime plan vẫn PARTIAL/PLANNED.
- Các dependency/CLI install có thể thiếu môi trường sạch để kiểm chứng; ghi tested/not verified chính xác và handoff riêng, không chạy pip upgrade hoặc tạo service trong task tài liệu.

## Execution status

- Status: `NOT_STARTED`
- Completed: Chưa thực thi migration docs. Lượt lập kế hoạch đã đọc/đối chiếu docs, lập inventory D01–D14, tạo hai plan và bổ sung index handoff.
- Remaining: D0–D4, tạo owner docs, hợp nhất sections, stub docs/PLAN, historical banner, validation/navigation và evidence status.
- Deviations from plan: Không có.
- Executor notes: ghi path trước/sau, nội dung chuyển đi, validation, unresolved runtime dependencies; không gọi migration complete chỉ vì có danh sách đề xuất.
