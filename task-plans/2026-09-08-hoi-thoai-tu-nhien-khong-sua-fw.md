# Phản hồi thức dậy và kết thúc hội thoại tự nhiên, không sửa FW

Status: `PARTIAL`

Created: `2026-09-08`

## Goal

ESP32 dùng FW Xiaozhi nguyên bản nhận lời đáp ngắn khi thức dậy mà không chờ LLM; có thể tắt lời chào, tự kết thúc sau thời gian không có hội thoại và chủ động thoát bằng câu lệnh rõ ràng. Câu hỏi thông thường vẫn đi qua pipeline ASR → LLM → TTS hiện tại.

Đây là kế hoạch handoff theo yêu cầu user, **chưa được yêu cầu thực thi**. Khi được yêu cầu thực hiện, cập nhật checklist và `Execution status` ngay sau mỗi phần hoàn thành. Chỉ commit/push khi user yêu cầu mới; quyền push của task UI trước không áp dụng cho task này.

## Current state

### Baseline đã kiểm tra

- VeeTee HEAD: `68b5c9a` (`feat: redesign dashboard for responsive mobile UX`); working tree sạch trước khi lập plan.
- FW: `references/xiaozhi-esp32`, commit `c7241272f2d5fd140c77542f3cf12d09e717fc2f`.
- Server tham khảo: `references/xiaozhi-esp32-server`, commit `c478257517b892047db3afaaeeb25e2b1e115931`.
- Cả hai reference worktree sạch, HEAD trùng [REFERENCE_BASELINES.md](../REFERENCE_BASELINES.md) lúc rà. Khi thực thi phải kiểm tra lại, không sửa/reset reference.
- Plan [server không sửa FW](2026-09-08-server-khong-sua-fw.md) còn `PARTIAL` vì thiếu bằng chứng trên thiết bị thật; task này không mặc nhiên hoàn tất phần đó.

### VeeTee

- `veetee-server/core/session.py`, `_handle_text_json`: `listen:detect` có text luôn gọi `_trigger_ai_turn`; `text`/`chat` cũng đi thẳng vào đó. Không có phân loại wake/exit hay tùy chọn greeting.
- `_on_asr_transcript`: ghép transcript rồi có thể gọi LLM hiệu chỉnh trước khi mở lượt. ASR có capture generation và guard chống echo cần giữ nguyên.
- `listen:start` luôn hủy turn, invalidates capture và reset dữ liệu nhận dạng. Có thể hủy nhầm lời chào nếu triển khai cache trực tiếp tại `detect`.
- `_process_ai_response`: queue clause có giới hạn; AudioPacer; turn generation; cancellation; guard cho đuôi playback. Các hành vi này phải áp dụng cả cho audio cache/lời tạm biệt.
- `ClientSession.close()` hiện chỉ dọn session/ASR, **chưa đóng WebSocket**. `server.py::handle_ws_connection` gọi cleanup trong `finally`. Chưa có timer kết thúc hội thoại hoặc cờ đóng sau lời tạm biệt.
- `config/settings.py` dùng dataclass và loader whitelist từng nhóm; thêm nhóm config cần sửa cả loader, không chỉ YAML. `config.yaml` là file local được ignore.
- TTS dùng chung tại `server.py`; `core/providers/tts/vieneu_local.py` đã có engine lock và backpressure. `BaseTTS.stream_sentence_to_opus` trả raw Opus, phù hợp làm cache trước khi đóng gói protocol.

### Đối chiếu tham khảo (đường dẫn tương đối trong `references/`)

| Source | Hành vi dùng để thiết kế |
| --- | --- |
| `xiaozhi-esp32-server/main/xiaozhi-server/core/handle/textHandler/listenMessageHandler.py` | Phân biệt wake word; `enable_greeting`; câu khác vẫn chat. |
| `xiaozhi-esp32-server/main/xiaozhi-server/core/handle/helloHandle.py`, `core/utils/wakeup_word.py` | `checkWakeupWords`: audio theo voice, fallback và refresh cache; khác với handshake `hello`. |
| `xiaozhi-esp32-server/main/xiaozhi-server/core/handle/receiveAudioHandle.py` | Theo dõi không có tiếng nói; `close_connection_no_voice_time`; tùy chọn `end_prompt`. |
| `xiaozhi-esp32-server/main/xiaozhi-server/core/handle/intentHandler.py` | Lệnh thoát khớp trực tiếp được xử lý trước chat. |
| `xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/functions/handle_exit_intent.py` | Ý định thoát qua tool có lời tạm biệt và `close_after_chat`. VeeTee không cần sao chép framework tool trong task này. |
| `xiaozhi-esp32/main/application.cc` | `ContinueWakeWordInvoke`: có thể gửi wake audio → `listen:detect` → vào listening → `StartListeningAudio` gửi `listen:start`. Khi `CONFIG_SEND_WAKE_WORD_DATA` tắt, không có `detect`. Wake khi đang speaking/listening dùng abort/listen, không luôn gửi detect. |
| `xiaozhi-esp32/main/application.cc`, `main/protocols/websocket_protocol.cc` | FW nhận `tts:start/sentence_start/stop`; stop chuyển trạng thái khi đang speaking. Đóng WebSocket gọi `OnAudioChannelClosed` → idle. Không có ACK xác nhận loa phát xong. |

Không sao chép khoảng bỏ VAD cố định 2 giây của reference; dễ mất câu người dùng nói ngay sau wake. Không lấy logic AEC của reference để bật automatic barge-in.

## Scope

- In scope: router sự kiện/text phía server; lời chào tùy chọn; cache audio câu cố định; timeout hội thoại; lệnh thoát rõ ràng; đóng kết nối có cleanup; config, log, test và tài liệu.
- Out of scope: sửa/build/flash FW, đổi model, server AEC, automatic voice barge-in, framework semantic intent/tool calling, persistent memory, đổi giao diện dashboard, cache mọi câu LLM hoặc thêm database.
- Intent thoát ở phiên bản này là tập câu/alias xác định. Các cách nói mơ hồ đi qua chat bình thường, không tuyên bố hiểu mọi cách diễn đạt ý định thoát.

## Implementation plan

### 1. P1 — Chốt cấu hình và contract nhận diện

- [x] Thêm `ConversationConfig` vào `config/settings.py`, wire vào `AppConfig`/`load_settings`; đồng bộ `config.example.yaml`. Dùng nhóm phẳng `conversation` để phù hợp loader hiện tại.
- [x] Cấu hình đề xuất: `enabled: false` (opt-in khi triển khai); `wake_words: ["你好小智", "小爱同学", "小美同学", "VeeTee ơi"]`; `greeting_enabled: true`; `greeting_text: "Mình đây, bạn cần gì nào?"`; `audio_cache_enabled: true`; `idle_timeout_seconds: 120` (`0` tắt timeout); `exit_commands: ["tạm biệt", "kết thúc trò chuyện", "thoát trò chuyện"]`; `goodbye_enabled: true`; `goodbye_text: "Mình nghỉ nhé. Cần gì cứ gọi mình."`.
- [x] Thêm giới hạn có validation: `wake_start_wait_ms: 150`, `fixed_response_timeout_seconds: 5`, `close_grace_ms: 250`. Đây là giá trị khởi điểm cần đo; không phải số đã kiểm chứng trên ESP32. Cache giai đoạn đầu chỉ RAM, có trần 16 entries/4 MiB tổng, giới hạn một clip 5 giây; có thể để hằng số được tài liệu hóa.
- [x] Validate kiểu bool/list/string, khoảng thời gian không âm, timeout tổng hợp dương; alias không rỗng sau normalize; wake và exit không trùng nhau. Cấu hình thiếu nhóm giữ hành vi cũ; sai kiểu/bounds báo lỗi dễ hiểu, không âm thầm bật tính năng.
- [x] Viết `core/conversation.py` để normalize Unicode NFC, casefold, khoảng trắng, dấu câu ở biên. Giữ dấu tiếng Việt, không fuzzy/substr match. Trả kết quả phân loại rõ `wake`, `exit`, `chat`, `empty`, kèm nguồn input.
- [x] Wake chỉ nhận **toàn bộ text** của `listen:detect` khớp allowlist. Không coi mọi detect là wake; "VeeTee ơi, thời tiết thế nào?" vẫn là câu hỏi. Mở rộng wake qua ASR ngoài phạm vi ban đầu để tránh double greeting và câu hỏi bị nuốt.
- [x] Exit nhận toàn câu qua detect, text/chat hoặc ASR cuối lượt. "Đừng kết thúc trò chuyện", "Giải thích từ tạm biệt" không được đóng phiên. Không chạy router trên ASR partial/stale/echo.

Phụ thuộc: bước 1 là contract cho tất cả bước sau. Khi bật thử dùng config local; không tự thay API key/model/voice hay commit file local.

### 2. P1 — Tách đường phản hồi cố định và cache audio

- [x] Trong `core/session.py`, thêm đường phát câu cố định không gọi `stream_chat`, dùng cùng turn ownership/cancel event, `AudioPacer`, playback guard và lifecycle stop của turn hiện tại. Chỉ tách helper chung khi cần; giữ regression queue LLM/TTS.
- [x] Thêm `core/response_audio_cache.py`, một instance dùng chung được tạo/truyền từ `server.py`. Lưu tuple raw Opus bất biến, không lưu packet có session/version/timestamp; đóng gói riêng bằng `pack_audio_payload` khi phát cho mỗi client.
- [x] Cache key có text, provider/model identity thực tế nếu có, voice/source_voice, sample rate, frame duration, denoise/temperature và encoding/cache schema version. Đổi voice/format không được phát clip cũ.
- [x] Chỉ cache greeting/goodbye cấu hình sẵn; LRU và trần bytes/frame/duration; chỉ publish clip sau tổng hợp hoàn chỉnh, không cache lỗi/empty/cancelled/partial. Không tạo vô hạn entry từ text của người dùng.
- [x] Một job tổng hợp cho mỗi key, waiter có thể hủy độc lập mà không hủy job còn phục vụ session khác; cleanup job khi shutdown. Không dùng cancel event của một session làm cancel event chung của cache fill. Dùng TTS engine lock sẵn có, không inference song song thêm.
- [x] Prewarm tối đa hai câu khi tính năng/cache bật, có timeout, log và đường lỗi; không trì hoãn startup vô hạn. Cache hit không gọi LLM/TTS. Cache miss dùng câu cố định qua TTS với deadline; thất bại thì quay về nghe (wake) hoặc đóng có cleanup (exit/idle), không đẩy wake word sang LLM làm fallback.
- [x] Khi tắt cache nhưng greeting bật, tổng hợp câu cố định mỗi lượt, vẫn bỏ LLM. Khi tắt greeting, không tổng hợp/phát audio, không gửi chuỗi start/stop giả để reset decoder.
- [x] Gửi thứ tự chuẩn `tts:start` → `sentence_start` → binary → `tts:stop`. Không phát binary trước start/sau stop; không gửi stop của turn cũ sau khi turn mới đã sở hữu session. Lỗi send phải được phản ánh vào lifecycle, không ghi log hoàn thành giả vì `send_text/send_binary` hiện nuốt exception.

### 3. P1 — Wake routing và phối hợp `listen:start`

- [x] Route trước `_trigger_ai_turn`; giữ câu detect không khớp, text/chat và ASR thường hoạt động như trước. Không chào tại handshake `hello`, reconnect hay mỗi `listen:start`.
- [x] Khi nhận wake detect hợp lệ: hủy turn cũ đúng cơ chế, invalidate capture để loại wake audio/transcript đang trễ; tạo **pending wake** có generation/deadline, chưa phát ngay trong handler. Handler trả nhanh để nhận được `listen:start`/abort tiếp theo.
- [x] `listen:start` đầu tiên trong cửa sổ pending wake, trước khi audio greeting bắt đầu và chưa có input người dùng mới, hoàn tất bước vào nghe rồi khởi chạy greeting. Chỉ nhánh pending này có ngoại lệ; `listen:start` sau đó tiếp tục là tín hiệu ngắt rõ ràng theo contract cũ.
- [x] Hết cửa sổ không có start: nếu vẫn sở hữu pending wake và không có user turn mới thì phát greeting; nếu greeting tắt thì chỉ trở lại trạng thái nghe thích hợp. Start đến muộn vẫn được quyền ngắt. Không dùng time window để bỏ qua mọi abort/start.
- [x] Detect trùng cùng wake trong một pending/greeting đang hoạt động được gộp; sau khi chu kỳ đã kết thúc, wake mới hợp lệ được phản hồi lại. Abort, detect câu hỏi, text/chat hoặc speech-start hợp lệ khi đang pending phải hủy pending, ưu tiên lượt người dùng.
- [x] ASR trước/đang greeting tuân thủ capture generation và guard hiện có; không bật tự ngắt bằng giọng nói khi SPEAKING. Không chặn VAD 2 giây cố định.
- [x] Không thêm wake word giả làm user message trong lịch sử. Chỉ ghi assistant greeting một lần khi đường phát hoàn thành, không ghi toàn bộ câu như đã hoàn tất nếu bị ngắt. Không reset lịch sử vì wake trùng.

Giới hạn: FW không gửi detect sẽ không có greeting server; vẫn dùng âm báo sẵn có và nghe như trước. Ghi rõ phải cấu hình allowlist theo text thực tế mà FW đã gửi, không bắt buộc sửa/build FW để tạo event còn thiếu.

### 4. P1 — Kết thúc phiên và nhận diện câu lệnh thoát

- [x] Tạo lifecycle kết thúc dùng chung cho exit/idle, có `closing_reason`, generation và task được quản lý; tách **yêu cầu kết thúc** khỏi **cleanup**. `close()` phải idempotent; tránh task tự cancel/await chính nó và tránh cleanup hai lần từ `server.py::finally`.
- [x] Exit rõ ràng đi thẳng vào phản hồi cố định/tạm biệt và đóng socket chuẩn (`1000`), không gọi chat LLM. Trong ASR, kiểm tra bản cuối gốc sau ghép và guard **trước** `_maybe_correct_asr_transcript`; không để LLM hiệu chỉnh biến câu thường thành lệnh đóng. Giữ STT final/VAD end đúng một lần cho câu ASR đã xử lý.
- [x] Goodbye bật: phát theo bước 2; chờ ước tính playback tail của pacer + `close_grace_ms` theo deadline có thể hủy **trước khi phát stop kết thúc rồi đóng socket**. Không chờ ACK không tồn tại. Trong thời gian chờ, kiểm tra turn ownership và lỗi kết nối.
- [x] Giữ state/echo guard trong khi gửi và chờ tail; ASR echo không hủy đóng. Abort, explicit listen:start, wake mới/câu hỏi mới hợp lệ trước khi commit đóng phải hủy goodbye và pending close, reset capture và trở về nghe theo mode. `listen:start` có thể do FW tự gửi sau stop, nên đoạn stop → đóng cuối cùng không thêm một khoảng sleep để bị nhận nhầm thành lượt mới.
- [x] Goodbye tắt hoặc TTS lỗi/timeout: kết thúc có cleanup, không treo THINKING/SPEAKING. Đóng WebSocket chỉ để kết thúc phiên, không dùng thay cơ chế barge-in/ép reset audio.
- [x] `server.py` thu hồi `active_sessions`; hủy/await turn, timer, pending wake/close và các tác vụ riêng của session, dừng ASR. Không đóng cache/engine chung khi một client rời đi.

### 5. P2 — Timeout khi im lặng lâu

- [x] Một watchdog bất đồng bộ/session dùng `time.monotonic`, chỉ arm sau tương tác hội thoại đầu tiên (wake, listen:start, text/chat hoặc speech-start được chấp nhận). Handshake/heartbeat đơn thuần không mở hội thoại.
- [x] Timeout không phụ thuộc có binary frame mới; hoạt động cả khi client ngừng gửi mic. Ping, silence PCM, ASR stale và echo không reset đồng hồ. Speech-start hợp lệ/ASR hữu ích, text và thao tác nghe mới có thể reset theo lifecycle.
- [x] Không timeout khi pending wake, đang nói, đang xử lý câu/ASR correction, đang có speech được chấp nhận hoặc đang kết thúc. Bảo vệ khoảng `speech_final` đang await hiệu chỉnh; không chỉ nhìn SessionState hiện tại.
- [x] Bắt đầu lại khoảng chờ sau khi phản hồi bình thường/greeting đã gửi và hết đuôi playback ước tính; manual mode chờ trong IDLE sau trả lời vẫn có thể hết hạn. Không đóng giữa câu TTS dài hoặc do callback ASR speech-final cũ đến trễ.
- [x] Khi đủ timeout, gọi lifecycle kết thúc bước 4 đúng một lần. Recheck activity revision/generation sau mọi await để câu mới sát deadline thắng timer cũ. `idle_timeout_seconds=0` tắt watchdog; cleanup không rò task.

### 6. P2 — Đo lường, tài liệu và handoff

- [x] Log event/reason, session/turn generation, cache hit/miss/fill/error, thời gian detect → first binary gửi, thời gian tổng hợp cold, lý do đóng và thời gian drain ước tính. Không gọi số đo server là thời gian đến loa.
- [x] Cập nhật `veetee-server/docs/API_PROTOCOL.md` (bảng route, sequence wake/start/abort/close), `docs/ESP32_CONFIG.md` (FW giữ nguyên, event có/không có), `docs/SETUP.md`, `README.md` (config và bật/tắt), `docs/VOICE_PIPELINE_STATUS.md` (bằng chứng/giới hạn). Cập nhật `docs/PLAN.md` nếu mục tiến độ tương ứng còn thiếu.
- [ ] Ghi kết quả test, số đo cold/hot và task đã xong vào plan này khi thực thi; cập nhật status trong `task-plans/README.md`. Test và trạng thái đã được cập nhật; profile cold/hot thực tế vẫn `PENDING`, nên mục này chưa hoàn tất toàn bộ.

## Compatibility constraints

- Chỉ server; giữ baseline nguyên bản nêu trên. Không thêm capability, command, patch hay ACK mà FW chưa hỗ trợ; không ép đổi listening mode/AEC.
- Giữ `barge_in_policy=client_only`, VAD 450 ms, queue bounded, TTS serialization, packet V1/V2/V3 và PCM16 browser input.
- Auto/manual/realtime đều phải kiểm tra. `abort.reason=wake_word_detected` khi đang nói là ngắt theo FW, không tự suy thành yêu cầu phát greeting mới.
- Không dùng `tts:stop` đơn lẻ như bằng chứng thiết bị vào listening hoặc đã phát hết audio; không dùng start/stop giả để xóa buffer. Nhánh greeting tắt dựa vào flow listening vốn có của FW.
- Grace sau audio chỉ là ước tính mạng/playback. Tương thích protocol khác với kiểm chứng nghe được toàn bộ lời chào trên board.

## Validation

- [x] Syntax/static: chạy compile cho các Python module thay đổi; `git diff --check`; kiểm tra config mẫu nạp được, không chứa secret và defaults đồng nhất.
- [x] Unit router/config (`tests/test_conversation_policy.py`): Unicode tiếng Việt/Trung, dấu câu, alias trùng, invalid config, feature off, exit nguyên câu và câu phủ định/trích dẫn nằm trong câu hỏi; wake+question giữ nguyên.
- [x] Cache (`tests/test_response_audio_cache.py` + integration): cache hit không gọi TTS lại; lỗi không poison key; cùng key chỉ fill một lần; hủy một waiter không hại waiter khác; đổi voice tạo miss; LRU bounded; shutdown hủy fill; V1/V2/V3 được đóng gói độc lập từ raw Opus cache.
- [x] Lifecycle (`tests/test_conversation_lifecycle.py`): fake WebSocket/ASR/LLM/TTS kiểm tra wake→listen:start, fallback không start + dedupe, greeting off, detect thường, cache hit và cancellation của pending close khi input mới tới.
- [x] Exit/idle: raw ASR exit được route trước correction; correction không tạo false exit; explicit exit bỏ LLM, goodbye + close `1000`; listen:start hủy close; idle không cần mic frame, ping không reset và không đóng giữa SPEAKING.
- [x] Regression từ cwd `veetee-server`: `/home/quangvu/Project/venv/bin/python -m unittest discover -s tests -v` → `51/51 PASS`, gồm `test_turn_lifecycle`, `test_tts_backpressure`, `test_audio_pacing`, `test_stock_fw_protocol`, `test_e2e_contract` và HTTP diagnostics/health/OTA integration.
- [x] Integration WebSocket thật với provider giả: handshake và trình tự TTS, shared cache qua reconnect, binary V1/V2/V3, close code `1000`, `active_sessions` được thu hồi và reconnect chat được; không phụ thuộc provider ngoài.
- [ ] Runtime profile bật tính năng: đo ít nhất 20 lượt wake cache nóng và mẫu cold riêng, ghi p50/p95 detect→first binary gửi, cache-hit ratio và cấu hình máy. Đối chiếu baseline detect→LLM trên cùng môi trường; báo hiệu quả thực đo, không đặt SLA latency tùy ý.
- [ ] ESP32 nguyên bản: wake→greeting→câu hỏi, tắt greeting nói ngay, wake/ngắt khi speaking, im lặng đủ hạn, nói sát deadline, thoát có/không goodbye, nghe đủ đuôi lời tạm biệt, wake lại sau socket close. Ghi board/FW commit/mode và actual wake text; test mode/biến thể không có thiết bị thì ghi PENDING.
- [x] Browser/dashboard smoke: dashboard có protocol console cho V1/V2/V3, listen auto/manual/realtime, wake detect/wake→start, listen start/stop, abort, exit, reconnect, ping, health, OTA, raw JSON và TTS endpoint. Runtime thật đã kiểm tra direct WS V1/V2/V3 greeting, V3 goodbye + close `1000`, `/ws` HTTP transport và synthetic TTS→PCM16→VAD/ASR→LLM→TTS. Mic vật lý vẫn thuộc checklist ESP32/browser thủ công.

## Acceptance criteria

- [x] Bật cấu hình: wake allowlist dùng đường phản hồi riêng; cache nóng bỏ chat LLM và bỏ TTS inference mới; greeting tắt không phát câu chào và không thêm chặn VAD cố định.
- [x] Câu hỏi detect không phải wake và chat thông thường tiếp tục hoạt động; không lặp greeting ở listen:start/reconnect; input mới hủy pending/closing turn cũ theo lifecycle.
- [x] Exit toàn câu đóng WebSocket thật với tùy chọn goodbye; câu không khớp không đóng. Idle hoạt động khi không còn audio frame và không đóng khi đang SPEAKING.
- [x] Task/queue/cache bounded; cancellation và cleanup không treo trong regression; lỗi TTS/cache/socket có đường kết thúc rõ.
- [x] Tắt `conversation.enabled` giữ luồng trước nâng cấp; config ví dụ, code và docs đồng bộ; không thay model/FW.
- [ ] Regression/integration PASS, số đo server được ghi rõ giới hạn. Hardware checklist có kết quả thực hoặc ghi PENDING, không suy diễn đã sẵn sàng trên ESP32.

## Risks / open questions

- Allowlist phải khớp wake text từ thiết bị đang dùng; ví dụ config không bảo đảm bao phủ mọi board. Thiếu detect không thể suy ra wake đáng tin cậy từ hello/listen:start.
- Cửa sổ chờ start 150 ms cân bằng latency và race; phải đo trên FW thật. Nếu start đến sau khi greeting phát, ưu tiên ngắt chuẩn dù câu chào bị cắt; không nới thành bỏ qua thao tác ngắt của user.
- Không có audio playback ACK: grace không bảo đảm hết đuôi trên mọi mạng. Chỉ điều chỉnh ước tính/tùy chọn goodbye phía server, không chuyển sang sửa FW.
- Cache fill dùng chung engine có thể tranh tài nguyên với hội thoại; bounded prewarm/deadline, giữ engine lock và đo. RAM cache mất khi restart là chủ ý của phiên bản đầu.
- Intent định nghĩa bằng alias có thể bỏ sót lời thoát tự nhiên; ưu tiên giảm đóng nhầm. Semantic intent bằng LLM/tool là task sau nếu user yêu cầu.
- Thời gian im lặng 120 giây và câu chào/tạm biệt là default đề xuất, có thể chỉnh config; không tự áp dụng lên server đang chạy trong lượt chỉ lập plan.

## Execution status

- Status: `PARTIAL`
- Completed:
  - Đã rà lại hai baseline reference; FW và server tham khảo vẫn sạch, đúng commit đã ghi trong plan.
  - Đã hoàn tất cấu hình/router exact-match Unicode, fixed greeting/goodbye, shared bounded Opus cache, pending wake coordination, exit routing, idle watchdog, WebSocket close/cleanup và giữ `barge_in_policy=client_only`.
  - Đã giữ hướng server-only: không sửa `references/xiaozhi-esp32`, không thêm protocol/capability/ACK riêng và không yêu cầu build/flash FW tùy biến.
  - Đã cập nhật tài liệu protocol, cấu hình ESP32, setup, README, plan/status voice pipeline với giới hạn rõ về `listen:detect`, AEC và playback ACK.
  - Unit/regression + WebSocket integration bằng provider giả đã PASS; integration bao phủ handshake, cache dùng chung qua reconnect, binary V1/V2/V3, close code `1000`, HTTP diagnostics/health/OTA và thu hồi `active_sessions`.
  - Đã bổ sung dashboard protocol/lifecycle test console; JavaScript `node --check` PASS.
  - Đã bật `conversation.enabled=true` trong `veetee-server/config.yaml` local/ignored để runtime test, giữ nguyên model/API/voice và `barge_in_policy=client_only`.
  - Runtime smoke PASS trên server thật: `/health`, `/ota/`, `/api/diagnostics`, prompt GET/POST cùng giá trị, TTS WAV endpoint, direct WS V1/V2/V3 greeting, V3 exit/goodbye/close và HTTP `/ws` synthetic audio end-to-end qua ASR→LLM→TTS.
- Remaining:
  - Runtime profile bật feature trên môi trường chạy thật: tối thiểu 20 lượt cache nóng + mẫu cold, p50/p95 detect→first binary và cache-hit ratio.
  - Checklist ESP32 nguyên bản trên thiết bị thật, gồm actual wake text, mode, wake/greeting/interrupt/idle/exit và kiểm tra nghe đủ đuôi goodbye.
- Deviations from plan:
  - Không sửa FW; mọi thay đổi giữ đúng baseline stock FW. Playback drain chỉ là ước tính theo pacer vì protocol không có speaker playback ACK.
- Evidence:
  - Verify cuối sau cập nhật web diagnostics: `51/51 PASS` bằng `/home/quangvu/Project/venv/bin/python -m unittest discover -s tests -v`; `compileall`, `node --check` và `git diff --check` PASS.
  - Runtime HTTP: `/health=healthy`, OTA trả WS endpoint, diagnostics báo `conversation=true`, `barge_in_policy=client_only`, protocol `[1,2,3]`; prompt round-trip cùng giá trị và TTS endpoint trả WAV hợp lệ.
  - Runtime WS: V1/V2/V3 đều nhận greeting đủ `start → sentence_start → binary → stop`; V3 goodbye phát binary và đóng code `1000`. HTTP `/ws` synthetic TTS→PCM16 có STT final, LLM event và TTS binary response.
  - `config.example.yaml` vẫn mặc định `conversation.enabled=false`; chỉ `config.yaml` local/ignored được bật để test runtime.
  - Không có bằng chứng hardware ESP32; không ghi hardware PASS và chưa kết luận barge-in/AEC/loa vật lý đã ổn định.
 - Completion rule:
   - Server/test xong nhưng runtime/hardware còn thiếu: `PARTIAL`. Chỉ `COMPLETED` khi các tiêu chí bắt buộc còn lại có bằng chứng thực tế.
 - Note 2026-09-10 (không đổi checkbox kiến trúc cũ): hướng exact-match/allowlist/fixed-greeting của plan này đã bị thay bằng AI-first + deterministic idle end theo yêu cầu user (xem plan AI-không-hardcode và follow-up deterministic idle). Behavior mới đã kiểm: unit idle, live WS, hardware wake/QA/idle-close/re-wake. Runtime profile 20 lượt + full ESP32 checklist vẫn `PENDING`; giữ `PARTIAL`.
