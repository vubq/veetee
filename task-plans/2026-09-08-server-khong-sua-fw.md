# Tối ưu VeeTee Server tương thích firmware ESP32 nguyên bản

Status: `PARTIAL`

Created: `2026-09-08`

## Goal

ESP32 dùng firmware Xiaozhi nguyên bản kết nối, hội thoại nhiều lượt và ngắt bằng nút/wake word theo tính năng sẵn có. Giảm độ trễ và lượng audio còn phát sau yêu cầu ngắt bằng thay đổi phía server. Không yêu cầu áp patch FW, thêm trường hello hoặc cài firmware tùy biến.

User đã yêu cầu thực thi plan. Tiến độ được cập nhật trực tiếp trong file này sau từng phần lớn; hướng phát triển bắt buộc là server-only, không sửa firmware tham khảo.

## Current state

### Baseline và cách đối chiếu

- VeeTee HEAD lúc rà: `acbc848abe3577803e3dd26a1972853e435400ee`.
- FW nguyên bản: `78/xiaozhi-esp32`, commit `c7241272f2d5fd140c77542f3cf12d09e717fc2f`, theo `REFERENCE_BASELINES.md`.
- Đã đọc FW bằng `git -C references/xiaozhi-esp32 show <commit>:<file>`. Ba file C++ trong working tree tham khảo có patch thử nghiệm; không lấy chúng làm chuẩn và không reset chúng.
- Các thay đổi local có sẵn về prompt, Deepgram, LLM và web/iOS cần được giữ nguyên, không gom vào task này. Kiểm tra lại `git status` khi bắt đầu.

### Phát hiện từ source

| Vị trí (đường dẫn từ root repo) | Hành vi đang có | Hệ quả cho plan |
| --- | --- | --- |
| `veetee-server/core/session.py`, `_on_speech_started` | Auto barge-in phụ thuộc `realtime + device_aec=true` | Đây là cờ do patch riêng thêm, FW baseline không gửi |
| `veetee-server/core/protocol.py`, `make_tts_message` | Gửi `interrupt=true` khi abort/lỗi/ngắt | FW baseline không đọc cờ này; không thể dựa vào nó để flush |
| FW `main/protocols/websocket_protocol.cc`, `GetHelloMessage` khoảng dòng 198 | `aec=true` được thêm theo `CONFIG_USE_SERVER_AEC`; không có `device_aec` | Cờ compile-time không chứng minh AEC runtime; thiếu `aec` cũng không chứng minh device AEC |
| FW `main/application.cc`, `GetDefaultListeningMode` khoảng dòng 1166 | Cả device AEC và server AEC đều chọn realtime | Không suy ra AEC hiệu quả chỉ từ `mode=realtime` |
| FW `main/application.cc`, handler `tts:stop` khoảng dòng 618 | Chuyển sang listening hoặc idle, không có lệnh flush riêng | Server chỉ có thể dừng gửi mới và giới hạn audio đã gửi trước |
| FW `main/application.cc`, `HandleStateChangedEvent` khoảng dòng 1005 | Auto chờ playback drain trước khi bật mic; realtime có thể giữ processor chạy | Không ép normal stop/interrupt stop có cùng thời gian dừng vật lý; không tự thêm delay drain vào auto một cách mù quáng |
| FW `main/application.cc`, `AbortSpeaking` khoảng dòng 1153 | Đặt `aborted_` và gửi abort; hàm này không gọi `ResetDecoder()` | Nút/wake word phải được kiểm tra theo cả chuỗi state, không khẳng định local abort luôn flush ngay |
| FW `main/application.cc`, `OnIncomingAudio` khoảng dòng 543 | Chỉ đưa audio mới vào decoder khi SPEAKING | Thứ tự `tts:start`, binary và `tts:stop` phải được giữ; không gửi audio sau stop |
| `veetee-server/core/session.py`, `_process_ai_response` | Gửi frame audio mặc định 60 ms rồi sleep 5 ms | TTS sinh theo đợt có thể gửi nhanh hơn tốc độ phát; 5 ms không phải pacing theo thời lượng audio |
| FW `main/audio/audio_service.h` | Decode queue tối đa `1200 / OPUS_FRAME_DURATION_MS`, playback queue 2 task | Có nhiều nơi đệm; không lấy một con số queue làm độ trễ loa bảo đảm |
| `veetee-server/core/providers/tts/vieneu_local.py`, `stream_sentence_to_opus` | Worker thread đẩy chunk vào queue không giới hạn, future chưa được theo dõi | Khi thêm pacing, cần backpressure và kết thúc worker đúng, tránh dồn RAM/chiếm engine lock |
| `veetee-server/core/providers/asr/parakeet_silero.py` | Utterance được đưa vào queue rồi ASR callback bất đồng bộ; `stop()` finalize và drain | Xóa chuỗi trong session chưa đủ để loại transcript cũ đến trễ sau abort/close |
| `veetee-server/test_e2e.py` | Timeout thoát vòng lặp nhưng vẫn in ALL TESTS PASSED; mốc latency sau silence/finalize | Chưa phải bằng chứng nghiệm thu đáng tin; phải có assertion và exit code lỗi |

VAD 450 ms và queue clause `maxsize=3` không cần sửa FW, giữ lại. Với frame Silero 32 ms, ngưỡng silence được chạm ở 480 ms thay vì 672 ms; đây là phép tính endpointing, không phải số đo độ trễ hội thoại trên ESP32. ASR correction có timeout cấu hình 900 ms, cần đo riêng nếu được bật.

## Scope

- In scope: WebSocket/OTA sẵn có, server protocol/state/cancellation, pacing và backpressure, chống transcript cũ/echo, cấu hình server, đo latency, kiểm thử và cập nhật tài liệu.
- Out of scope: sửa FW hoặc source tham khảo; triển khai MQTT/UDP; server-side AEC hoàn chỉnh; thay model/prompt; sửa web/iOS ngoài regression cần thiết; ép client chuyển AEC/listening mode qua lệnh mới.
- Không hứa full-duplex hoặc loa dừng ngay tuyệt đối trên mọi board. Không biến thực nghiệm barge-in giọng nói thành điều kiện bắt buộc để hoàn thành đường tương thích chuẩn.

## Compatibility constraints

1. Client baseline kết nối được khi không có `features` hoặc chỉ có các cờ sẵn có; không bắt buộc `device_aec`.
2. Đường mặc định chỉ gửi `tts:start`, `sentence_start`, `tts:stop` chuẩn; không phụ thuộc `interrupt=true`. Không dùng chuỗi start/stop giả để ép reset decoder hay ngắt WebSocket để thay lệnh stop.
3. Listening mode phản ánh mode client báo. Server có thể chọn bỏ qua ASR trong lúc phát, nhưng không được tự coi mic client đã tắt hoặc mode đã đổi.
4. `aec=true` là thông tin client gửi theo FW baseline; không có AEC server đang hoạt động thì không bật automatic barge-in theo cờ này. `realtime` đơn lẻ cũng không đủ.
5. Giữ VAD 450 ms, queue clause có giới hạn, cleanup cancellation đã sửa. TTS không được chạy nhiều inference đồng thời trên engine dùng chung chỉ để giảm khoảng nghỉ.
6. Ưu tiên Opus 16 kHz input/24 kHz output, 60 ms theo baseline. Đối chiếu hello/header và packet V1/V2/V3; browser PCM16 phải tiếp tục chạy. Không tự thay format/frame duration mà client chưa hỗ trợ.

## Implementation plan

### 1. P1 — Cố định contract và tạo kiểm thử FW nguyên bản

- [x] Đọc lại baseline, working tree và ghi thông tin phiên bản vào fixtures/test notes. Không kiểm thử trên FW đã patch rồi gọi là stock.
- [x] Thêm `veetee-server/tests/test_stock_fw_protocol.py`: fixtures hello không features, `{mcp:true}`, `{aec:true}`; listen auto/manual/realtime; abort; V1/V2/V3.
- [x] Mô phỏng client stock không hiểu extension; realtime trong lúc TTS được echo-guard dưới policy mặc định. Fixture chỉ dùng kiểm thử contract, không thay cho board thật.
- [x] Tách input frame duration khỏi TTS output trong `AudioCodec`; hello client được validate, 60 ms giữ làm baseline stock.

File dự kiến: `tests/test_stock_fw_protocol.py`, `core/protocol.py`, `core/session.py`, `core/audio_utils.py` nếu cần. Bước này tạo bằng chứng cho các bước sau.

### 2. P1 — Chuyển policy ngắt về giao thức chuẩn

- [x] Trong `core/session.py` và `core/protocol.py`, bỏ đường mặc định phụ thuộc `device_aec`/`interrupt`. Abort/listen:start/lỗi dùng `tts:stop` chuẩn.
- [x] Thêm cấu hình server `barge_in_policy` mặc định `client_only`; VAD trong lúc TTS không tự ngắt turn.
- [x] Hello thiếu capability vẫn kết nối bình thường. Log tách reported server AEC khỏi policy server, không coi đó là AEC verified.
- [x] Giữ ASR khi nghe và finalize khi client gửi listen:stop; server không ép firmware đổi mode.

File dự kiến: `config/settings.py`, `core/protocol.py`, `core/session.py`. Cấu hình mẫu không chứa bí mật; `config.yaml` local có thể không được Git theo dõi, nên có default và hướng dẫn tái lập.

### 3. P1 — Pacing theo thời lượng audio, giới hạn audio gửi trước

- [x] Thay `sleep(0.005)` bằng `AudioPacer` dùng đồng hồ monotonic và duration frame.
- [x] Cửa sổ gửi trước mặc định 120 ms (2 frame 60 ms), cấu hình bằng `tts.send_ahead_ms`.
- [x] Theo dõi `playback_end`; fake-clock test xác nhận stall không burst bù vượt budget.
- [x] Chờ pacing có thể bị cancellation cắt ngay; test xác nhận cancel không đợi deadline.
- [x] Normal stop chỉ sau binary cuối; regression test khóa thứ tự binary cuối -> `tts:stop`. Đuôi phát chỉ là estimate, không coi là playback ACK.
- [x] Log audio duration sent, estimated lead, send wait và estimated tail. Việc tuning underrun/tail còn chờ test board.

File dự kiến: `core/session.py`, helper mới `core/audio_pacing.py`, `config/settings.py`, `tests/test_audio_pacing.py`. Phụ thuộc bước 2; dùng fake clock để kiểm thử tốc độ, stall và cancel có tính quyết định.

### 4. P1 — Backpressure TTS và vòng đời turn/ASR

- [x] Queue chunk VieNeu được giới hạn (`tts.stream_queue_max_chunks`, mặc định 4) và thread->async bridge dùng blocking backpressure có cancellation, không dùng `put_nowait` làm rơi audio.
- [x] Theo dõi worker future; cancellation nhả worker đang chờ queue. GPU inference chỉ dừng tại boundary mà engine trả quyền điều khiển, không cam kết preempt tức thời.
- [x] Thêm turn generation/ownership cho send, state, stop và dialogue completion; old turn không được hoàn tất sau khi owner đổi. Empty LLM trả session về trạng thái nghe/idle.
- [x] Thêm capture generation cho session + Parakeet queue/callback; Deepgram reset/drop callback cũ đến SpeechStarted kế tiếp. Stale callback có regression test.
- [x] Echo guard dùng estimated playback tail, bao phủ speech-start xảy ra sau `tts:stop` trong realtime/client_only.
- [x] Abort/listen:start invalidates turn + capture, reset repeat de-dup và state theo mode; regression test abort không có listen:start đạt pass.

File dự kiến: `core/session.py`, `core/providers/tts/vieneu_local.py`, `core/providers/asr/base.py` và provider liên quan khi cần, `tests/test_turn_lifecycle.py`. Bảo toàn các thay đổi provider có sẵn; chỉ sửa phần lifecycle cần thiết. Triển khai cùng bước 3 trước khi bật pacing trong runtime chính.

### 5. P2 — Đo đúng latency và sửa nghiệm thu tự động

- [x] Sửa `test_e2e.py`: timeout, thiếu hello/STT/audio/stop hoặc thứ tự sai fail bằng `E2EFailure`/exit 1; thiếu runtime synthesis/server được báo `BLOCKED`/exit 2. Không còn success vô điều kiện.
- [x] Tách mốc client-observed audio speech-end gửi vào, listen-stop, ASR-final, VAD `speech_ended`, first clause, first binary received và stop. Server log riêng Silero endpoint reason, ASR final/correction stage, post-ASR first clause, first binary sent và stop; log `_process_ai_response` không còn gọi là full E2E.
- [ ] Giữ VAD 450 ms làm baseline. Đo correction bật/tắt và từng khoảng chờ trước khi giảm thêm silence/đổi ngưỡng hoặc đổi câu TTS.
- [x] Kiểm thử queue đầy khi normal finish, producer error, cancel và close; TTS nhanh/chậm, network send stall, callback ASR cũ và hai session. Regression hiện `26/26` pass.

Runtime E2E của build hiện tại chưa chạy: process server đang phục vụ được khởi động từ `2026-09-08 01:20:43`, trước các thay đổi Step 5. Không dùng process cũ làm bằng chứng cho code mới và không khởi tạo thêm VieNeu/Parakeet GPU process chỉ để ép bài test. Measurement correction bật/tắt để `PENDING` cho lần chạy runtime/board có kiểm soát.

File dự kiến: `test_e2e.py`, các tests ở trên, logging trong session/provider. Tests mock không cần GPU/API; runtime model thật là bài test riêng có điều kiện môi trường.

### 6. P2 — Đồng bộ tài liệu và nghiệm thu trên ESP32 nguyên bản

- [x] Cập nhật `README.md`, `docs/PLAN.md`, `docs/API_PROTOCOL.md`, `docs/ESP32_CONFIG.md`, `docs/VOICE_PIPELINE_STATUS.md` theo code sau implementation. Đường sử dụng chính không còn yêu cầu áp patch/build FW tùy biến.
- [x] Đánh dấu `patches/xiaozhi-esp32-barge-in.patch` là thử nghiệm lịch sử, không cần áp; giữ artifact để truy vết, không tự áp lại. Tách hạng mục đã sửa server khỏi tính năng từng yêu cầu FW patch.
- [ ] Test board với firmware nguyên bản đang có: OTA/WS hello, audio hai chiều, nhiều lượt, normal tail, nút, wake word nếu FW/board hỗ trợ. Ghi board, version/config, protocol mode và điều kiện mạng/âm lượng.
- [ ] Đo request-abort -> last binary gửi và thao tác/nói ngắt -> loa thật sự dừng bằng ghi âm/quan sát có timestamp. Không dùng thời điểm stop JSON thay cho phép đo loa.
- [ ] Lặp tối thiểu 20 lượt cho normal và ngắt trong cấu hình test; báo số lần lỗi, p50/p95 latency, điều kiện đo và dữ liệu mẫu. Không coi một bài test này chứng minh tương thích mọi board.

Không có board thì đánh dấu phần hardware `PENDING`, tiến độ tổng `PARTIAL`; không đặt yêu cầu có `idf.py` để nghiệm thu phần server. Dùng firmware nguyên bản có sẵn, không flash tùy biến để làm cho bài test pass.

### 7. P3 — Tùy chọn barge-in giọng nói trên FW nguyên bản (sau baseline)

- [ ] Sau khi client_only ổn định, đánh giá cấu hình server theo Device-Id cho thiết bị có device AEC đã được xác minh độc lập trên firmware đang dùng. Cấu hình là quyết định vận hành, không phải tự chứng nhận từ hello; mặc định vẫn tắt.
- [ ] Nếu triển khai tùy chọn, yêu cầu mode client realtime và profile đã xác minh; ghi provenance board/FW/config, vô hiệu hóa khi không còn khớp. Không suy ra device AEC từ thiếu `aec`; thiết bị báo `aec=true` không được tự bật theo đường này.
- [ ] Chỉ dùng stop chuẩn + pacing; công bố giới hạn đuôi buffer. Nghiệm thu riêng false interrupt do echo, speech-start latency và câu nói chen được giữ.
- [ ] Nếu không xác minh được AEC hoặc đuôi phát không đạt nhu cầu, giữ client_only và ghi giới hạn. Server-side AEC là backlog nghiên cứu riêng, không thêm patch FW để hoàn tất bước này.

Bước 7 là mở rộng tùy chọn, không chặn nghiệm thu đường tương thích mặc định ở bước 1–6.

## Validation

- [x] Syntax/compile các Python file thay đổi và `git diff --check`; rerun sau docs bằng `/home/quangvu/Project/venv/bin/python -m py_compile ...` và `git diff --check`, đều PASS.
- [x] Tests protocol baseline không sử dụng `device_aec`/`interrupt` để đạt pass; V1/V2/V3 có round-trip header và audio, PCM16 có regression.
- [x] Fake-clock pacing giữ ngân sách lead đã chọn trong mô hình, không burst sau stall; cancel không đợi deadline dài.
- [x] Queue/cancellation và ASR generation tests không treo, không audio/stop từ turn cũ, không mất đuôi câu normal, không false turn sau disconnect.
- [x] Runtime E2E có điều kiện model/API và assert rõ; thiếu môi trường ghi `BLOCKED` chứ không PASS. Chưa dùng runtime/model thật để đo latency build mới.
- [ ] Hardware checklist bước 6 dùng FW nguyên bản; thử nghiệm bước 7 ghi kết quả tách biệt.

## Acceptance criteria

- [x] Đường mặc định hoạt động với baseline hello/giao thức trong regression, không sửa FW hoặc cần capability mới.
- [x] VAD 450 ms và chồng lấp LLM/TTS được giữ, cancellation/backpressure có test pass.
- [x] Pacing thay thế fixed 5 ms; audio gửi trước có giới hạn cấu hình và số liệu mô phỏng, không cam kết bound trên loa từ mô phỏng.
- [x] Abort/listen/start/stop/close không làm sai state hoặc phát turn cũ; echo không gây turn mới trong bài test đã định nghĩa.
- [x] Tài liệu sử dụng chính không yêu cầu patch; test tự động fail đúng khi lỗi.
- [ ] Hardware đánh dấu theo bằng chứng thực tế. Chỉ đánh `COMPLETED` toàn bộ baseline sau khi bước 1–6 đạt; nếu chưa test board dùng `PARTIAL`. Bước 7 có thể còn backlog, ghi rõ khi kết thúc.

## Risks / open questions

- Firmware thực tế của user chưa xác định board/version/AEC; chỉ có baseline source. Thu thập khi đến bài test phần cứng.
- Giao thức baseline không có flush command/playback ACK chung cho đường đang dùng. Pacing giảm lượng audio gửi trước nhưng không bảo đảm dừng tức thì hay đo chính xác độ sâu buffer mạng/codec.
- Pacing quá chặt có thể gây ngắt quãng; quá rộng kéo dài đuôi âm. 120 ms chỉ là giá trị khởi điểm để đo và chỉnh.
- Bỏ echo trong realtime chưa có AEC có thể bỏ cả giọng người dùng nói chen. Đây là giới hạn client_only; không giải quyết bằng bật barge-in bừa hoặc thêm silence cố định quá dài.
- Worker TTS dùng engine lock, inference đang chạy không bị cancel tức thời. Chậm nhả lock có thể trì hoãn câu trả lời lượt mới dù server đã ngừng gửi audio cũ.
- Capture generation phải gắn ở thời điểm audio/utterance được thu, không lấy generation hiện tại khi callback ASR trả về, nếu không transcript cũ vẫn lọt qua.

## Execution status

- Status: `PARTIAL`
- Completed: bước 1 contract stock; bước 2 policy `client_only`; bước 3 pacing 120 ms; bước 4 turn/capture ownership + bounded TTS backpressure; phần code/assert/logging + regression của bước 5; phần tài liệu server-only và đánh dấu patch lịch sử của bước 6. Sau cập nhật docs đã rerun **26/26 tests PASS**, `py_compile` PASS và `git diff --check` PASS.
- Active: chờ runtime measurement và hardware stock-FW test; phần code/docs/regression server đã sẵn sàng cho Git handoff.
- Remaining: runtime measurement correction/E2E của bước 5; hardware checklist/measurement bước 6. Bước 7 là tùy chọn sau baseline.
- Deviations from plan: test runner dùng `unittest` có sẵn trong Python thay vì thêm dependency mới; semantics acceptance giữ nguyên.
- Không sửa source FW tham khảo. Kiểm tra môi trường hiện không thấy `/dev/ttyUSB*`, `/dev/ttyACM*` hoặc USB bridge ESP32 qua `lsusb`; hardware chưa được chứng nhận và giữ `PENDING`. Kết quả hiện tại là server/unit simulation.
- Git handoff được dựng lại trên worktree sạch từ `master`, chỉ mang các thay đổi thuộc task; chạy lại **26/26 tests PASS**, `py_compile` PASS và `git diff --check` PASS trước commit.
- Implementation commit `4105d44` (`feat: support stock xiaozhi firmware voice flow`) đã push lên `origin/master`; các chỉnh sửa local không thuộc task không được đưa vào commit.
