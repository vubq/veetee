# Voice Pipeline Status

Cập nhật: **2026-09-09**

## Trạng thái tổng thể

Hướng triển khai hiện tại là **server-only, firmware nguyên bản**. Semantic routing end/memory/confirmation/wake-text/idle đã chuyển sang AI contract `veetee.semantic.v1`; server giữ schema/permission/ID/revision/cancel/deadline/receipt làm rào cản thực thi. Runtime corpus model thật, latency SLA và hardware ESP32/acoustic chưa được chứng nhận nên trạng thái tổng vẫn là **PARTIAL / hardware PENDING**.

## Đã hoàn thành

| Mức | Hạng mục | Trạng thái | Ghi chú |
| :--- | :--- | :---: | :--- |
| P1 | VAD end silence 650 -> 450 ms | ✅ | Threshold frame thực tế khoảng 480 ms |
| P1 | Stock FW protocol contract | ✅ | Hello thiếu features / `mcp` / `aec`, V1/V2/V3 |
| P1 | Barge-in policy mặc định | ✅ | `client_only`, không yêu cầu `device_aec` |
| P1 | Client abort/listen-start cancellation | ✅ | Dùng `tts:stop` chuẩn |
| P1 | Queue cancellation deadlock | ✅ | Không block producer khi consumer bị hủy |
| P1 | Turn/capture generation | ✅ | Chặn turn/audio/transcript cũ |
| P1 | Echo guard qua playback tail estimate | ✅ | Giảm self-turn khi chưa có AEC xác minh |
| P1 | Audio pacing | ✅ | Monotonic pacer, send-ahead mặc định 120 ms |
| P1 | TTS bounded backpressure | ✅ | VieNeu queue mặc định 4 chunk |
| P2 | LLM clause pipelining | ✅ | Queue `maxsize=3`, TTS vẫn tuần tự |
| P2 | E2E contract fail đúng | ✅ | Missing marker/order sai fail; runtime thiếu -> BLOCKED |
| P1 | Wake text routing | ✅ | `listen:detect` text đi qua AI; không exact wake matcher |
| P1 | AI-authored recovery cache | ✅ | Startup/persona refresh sinh recovery bằng AI, cache audio + provenance |
| P1 | Exit + goodbye lifecycle | ✅ | Semantic end từ AI; không whole-command exit matcher |
| P2 | Idle semantic timeout | ✅ | Một AI evaluation/inactivity epoch, continue re-arm |
| P2 | WebSocket integration | ✅ | V1/V2/V3, shared cache qua reconnect, close cleanup |
| P1 | Unified AI contract | ✅ | Chat 1 call; typed semantic events + action receipts; action tối đa 2 rounds |
| P1 | AI-controlled session memory | ✅ | AI chọn mutation/fact ID; server kiểm revision/receipt; durable vẫn gated |
| P1 | Tool/confirmation executor | ✅ | AI quyết định; server schema/ID/permission/locks/receipts; synthesis sau kết quả thật |
| P2 | Stock MCP adapter | ✅ mock | Numeric IDs, init/list pagination/call; hardware tool vẫn PENDING |
| P1 | TTS priority scheduler | ✅ | live > dashboard > prewarm, shared engine vẫn serialized |
| P1 | Turn telemetry/benchmark client | ✅ | Speech-end fixture contract + raw JSONL/percentiles; SLA corpus lớn chưa chạy |
| P2 | Docs server-only | ✅ | Không yêu cầu patch/build FW tùy biến |

## Regression hiện tại

Suite hiện bao phủ protocol stock, audio pacing, lifecycle/cancel, stale ASR, TTS backpressure, AI semantic routing, idle, memory, confirmation, action synthesis và E2E contract.

Kết quả regression gần nhất:

```text
127/127 tests PASS
compileall PASS
git diff --check PASS
```

Regression/unit hiện xanh trên source working tree. Đây chưa phải bằng chứng route/model thật, latency SLA hay audio vật lý trên ESP32. Durable memory vẫn không được coi là hoàn tất khi thiếu trusted owner binding/write barriers được nghiệm thu.

Runtime hiện dùng `veetee-server-bg.service`; health trả `healthy/ready`, cùng process phục vụ HTTP `:8003` và WebSocket `:8000`. Với model thật `groq/qwen/qwen3.6-27b`, các lượt “Mấy giờ rồi?”, “Bây giờ là mấy giờ?” và “Hôm nay ngày mấy?” đều do model tự phát `get_current_time` trong round 1, không có server-side phrase matcher hay bước xác nhận. Ca negative “Mấy giờ nên đi ngủ để mai dậy sớm?” không gọi clock tool và không nêu giờ hiện tại giả.

## Quyết định kỹ thuật

### ASR/VAD

- Primary: Parakeet CTC 0.6B Vietnamese + Silero VAD local.
- End silence: 450 ms.
- Deepgram là provider dự phòng.
- Capture generation loại callback transcript cũ sau abort/listen-start/close.

### LLM/TTS

- Unified turn là đường mặc định: intent/control/speech và semantic/business tool request đi cùng stream; không có classifier LLM riêng trước chat.
- LLM sở hữu semantic routing và tool selection qua context + tool schema (`tool_choice=auto`). Không có keyword/exact-phrase/regex/whitelist classifier để server tự ép intent hoặc function từ user text; tối ưu semantic phải đi qua prompt/schema/context/model. Deterministic server logic chỉ áp dụng cho protocol/lifecycle, validation, permission/ownership, deadline/cancel và receipt/state invariants.
- Chat thường đúng 1 LLM call. Turn có action/receipt được phép thêm đúng 1 vòng synthesis; vòng 2 dùng remaining deadline và `tool_choice=none`.
- Mixed speech + action vi phạm contract bị reject trước side effect; receipt synthesis timeout/empty/action mới làm turn fail thay vì phát success giả.
- LLM producer -> bounded event/speech queue; TTS consumer tổng hợp từng segment tuần tự.
- VieNeu thread -> async bridge có bounded queue và cancel-aware shutdown.
- Scheduler ưu tiên live > dashboard > prewarm và vẫn chỉ cho một inference trên engine TTS dùng chung.

### Conversation lifecycle

- `conversation.enabled` mặc định `false`; bật/tắt không yêu cầu sửa FW.
- `listen:detect` text, `chat`, `text` và ASR final đều đi qua AI; `wake_words`/`exit_commands` không còn matcher runtime.
- Greeting/goodbye bình thường do AI diễn đạt; legacy `greeting_text`/`goodbye_text`/`greeting_pool_size` không tạo semantic fallback.
- Idle watchdog dùng monotonic time và gọi AI đúng một lần mỗi inactivity epoch; `continue` re-arm, `end` logical-idle.
- Recovery khi live turn lỗi chỉ dùng startup AI-authored cached asset; cache chưa ready thì degraded, không literal fallback.
- Playback close grace vẫn chỉ là estimate, không phải ACK từ loa.

### Intent / Memory / Tools

- `intent.semantic_end_enabled` mở semantic goodbye trong main stream; không có exact wake/exit alias bypass AI.
- Memory context đưa bounded facts dạng structured JSON với opaque fact ID/revision cho AI chọn; server không parse “nhớ/quên” từ câu user. Durable personal memory không tin `Device-Id`/`Client-Id`; thiếu `memory.trusted_owner_id` thì chỉ giữ session memory.
- Confirmation dùng typed AI decision với đúng pending `action_id`; server không có yes/no keyword parser.
- Native streamed tool/semantic calls chỉ publish khi terminal/JSON/schema hợp lệ. Runtime quality trên route/model thật vẫn cần corpus riêng.
- Built-in `calculate` và `get_current_time` chạy qua registry/executor. MCP device chỉ expose status/volume nếu board quảng bá đúng schema; mock stock protocol đã pass, board thật vẫn PENDING.
- `get_current_time` không có route riêng theo câu chữ. Model tự quyết định có gọi clock hay không trong unified turn; receipt clock có thể được server render trực tiếp để tránh thêm một LLM round sau khi AI đã chọn tool.

### Barge-in stock FW

- Mặc định `server.barge_in_policy=client_only`.
- Explicit stock `abort` và `listen:start` có thể cancel turn đang nói.
- Server gửi stop chuẩn, không phụ thuộc `interrupt=true`.
- Không dựa vào `device_aec`; stock hello không cần field này.
- `features.aec=true` hoặc `mode=realtime` không tự bật speech-start barge-in.
- Server dùng send-ahead pacing để giảm audio đã gửi trước; không cam kết loa dừng tức thì vì stock protocol không có playback flush ACK.

## Còn phải hoàn thành

| Ưu tiên | Task | Trạng thái |
| :--- | :--- | :---: |
| P1 | ≥100 warm auto Vietnamese latency corpus + p50/p95 | PENDING |
| P2 | Native semantic/tool quality trên route/model runtime | PENDING |
| P2 | Runtime profile detect/chat -> first binary | PENDING |
| P2 | Load 1/2/4 session + dashboard/prewarm contention | PENDING |
| P1 | ≥200 semantic corpus, ≥80 critical negative, ≥50 held-out | PENDING |
| P2 | ≥30 memory dialogues / ≥30 tool-confirmation turns runtime corpus | PENDING |
| P1 | Test ESP32 bằng firmware nguyên bản đang có | PENDING |
| P1 | Xác nhận actual `listen:detect` behavior trên board | PENDING |
| P1 | Test detect -> AI response -> câu hỏi / nói ngay sau wake | PENDING |
| P1 | Test contextual end + quoted exit + idle continue/end trên board | PENDING |
| P1 | Test nhiều lượt + normal tail | PENDING |
| P1 | Test nút/wake-word abort nếu FW hỗ trợ | PENDING |
| P1 | Đo request-abort -> last binary server | PENDING |
| P1 | Đo thao tác ngắt -> loa vật lý dừng | PENDING |
| P2 | 20 normal + 20 interrupt, báo lỗi/p50/p95 | PENDING |
| P3 | Optional speech barge-in cho device profile có AEC xác minh độc lập | backlog |

Một log runtime trước đây từng có `Post-ASR first TTS binary sent in 10.220s`; vì vậy chưa có cơ sở tuyên bố p95 < 1 giây hoặc p50 ≤ 600 ms. `speech_end_to_first_audio_received_ms` phải được đo từ fixture/acoustic speech-end thật; first binary server-side không thay thế metric loa vật lý.

Không có task build/flash patched FW trong đường nghiệm thu hiện tại.

## Tiêu chí hardware baseline

Chỉ đánh hardware stock FW hoàn thành khi có bằng chứng từ board thật:

1. OTA/WS hello và audio hai chiều ổn định.
2. Hội thoại nhiều lượt không sai state.
3. Normal TTS không mất đuôi câu.
4. Ngắt bằng cơ chế stock của board tạo turn mới ổn định nếu board/FW hỗ trợ.
5. Có số đo physical speaker stop, không dùng thời điểm JSON stop làm thay thế.
6. Có kết quả lặp và p50/p95 trong điều kiện test được ghi lại.

## File liên quan

- `core/session.py`: state machine, generation ownership, cancel, echo guard, pacing integration.
- `core/audio_pacing.py`: giới hạn send-ahead theo monotonic clock.
- `core/protocol.py`: stock protocol + binary V1/V2/V3 validation.
- `core/providers/asr/`: capture generation/stale callback filtering.
- `core/providers/tts/vieneu_local.py`: bounded backpressure.
- `tests/`: regression server-only/stock-FW.
- `test_e2e.py`: runtime/contract E2E với exit status rõ.
- `patches/xiaozhi-esp32-barge-in.patch`: artifact thử nghiệm lịch sử, không cần áp.
