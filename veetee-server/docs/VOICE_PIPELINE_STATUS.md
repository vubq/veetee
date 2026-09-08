# Voice Pipeline Status

Cập nhật: **2026-09-08**

## Trạng thái tổng thể

Hướng triển khai hiện tại là **server-only, firmware nguyên bản**. Unified turn, semantic intent, local memory, tool executor/native tool parsing, MCP stock adapter, TTS scheduler và telemetry đã có code + regression. Runtime service hiện healthy, nhưng latency corpus lớn và hardware ESP32/acoustic chưa được chứng nhận nên trạng thái tổng vẫn là **PARTIAL / hardware PENDING**.

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
| P1 | Wake routing + greeting tùy chọn | ✅ | Exact `listen:detect`, không greeting ở hello/reconnect |
| P1 | Fixed-response Opus cache | ✅ | Shared RAM cache, bounded, cache hit bỏ LLM/TTS inference |
| P1 | Exit + goodbye + close lifecycle | ✅ | Whole-command match, WebSocket close `1000`, cancel-aware |
| P2 | Idle conversation timeout | ✅ | Monotonic watchdog, không cần mic frame |
| P2 | WebSocket integration | ✅ | V1/V2/V3, shared cache qua reconnect, close cleanup |
| P1 | Unified turn + semantic end | ✅ | Chat thường một LLM stream; semantic goodbye dùng speech cùng lượt |
| P1 | Local memory | ✅ | SQLite WAL/FTS fallback, revision/tombstone; durable chỉ với trusted owner |
| P1 | Tool executor | ✅ | Safe calculator/time, schema validation, receipts, timeout `unknown`, side-effect serialization |
| P2 | Stock MCP adapter | ✅ mock | Numeric IDs, init/list pagination/call; hardware tool vẫn PENDING |
| P1 | TTS priority scheduler | ✅ | live > dashboard > prewarm, shared engine vẫn serialized |
| P1 | Turn telemetry/benchmark client | ✅ | Speech-end fixture contract + raw JSONL/percentiles; SLA corpus lớn chưa chạy |
| P2 | Docs server-only | ✅ | Không yêu cầu patch/build FW tùy biến |

## Regression hiện tại

Suite hiện bao phủ protocol stock, audio pacing, lifecycle/cancel, stale ASR, TTS backpressure, conversation routing/cache/idle/exit và E2E contract.

Kết quả regression gần nhất:

```text
82/82 tests PASS
compileall PASS
config.example.yaml load PASS
git diff --check PASS
```

`veetee-server-bg.service` đã được kiểm tra `active/running` và `/health` trả healthy trên code hiện tại. `/api/diagnostics` cho thấy unified turn/intent/memory/tools đã nạp; durable memory hiện không bật vì chưa có trusted owner, MCP device đang tắt và greeting pool hiện chưa ready. Đây là bằng chứng liveness/config, chưa phải benchmark SLA.

## Quyết định kỹ thuật

### ASR/VAD

- Primary: Parakeet CTC 0.6B Vietnamese + Silero VAD local.
- End silence: 450 ms.
- Deepgram là provider dự phòng.
- Capture generation loại callback transcript cũ sau abort/listen-start/close.

### LLM/TTS

- Unified turn là đường mặc định: intent/control/speech và tool request đi cùng stream; memory lookup local không tạo LLM call phụ.
- Tool mặc định một round; profile `tool_result_synthesis` mới được phép dùng round thứ hai và bị chặn ở tổng tối đa 2.
- LLM producer -> bounded event/speech queue; TTS consumer tổng hợp từng segment tuần tự.
- VieNeu thread -> async bridge có bounded queue và cancel-aware shutdown.
- Scheduler ưu tiên live > dashboard > prewarm và vẫn chỉ cho một inference trên engine TTS dùng chung.

### Conversation lifecycle

- `conversation.enabled` mặc định `false`; bật/tắt không yêu cầu sửa FW.
- Wake chỉ nhận exact `listen:detect` allowlist; detect có thêm câu hỏi vẫn là chat.
- Greeting có AI pool/prewarm và raw-Opus cache; `greeting_text` là fallback. Greeting readiness tách khỏi `/health`, cold miss không được cam kết 600 ms.
- Exit exact-match nhận từ detect/text/chat/raw ASR final; semantic end dùng unified stream. Exact/idle goodbye có thể dùng AI goodbye generator hoặc fallback cấu hình.
- Idle watchdog per-session dùng monotonic time, không reset bởi ping/silence/stale/echo.
- Conversation close dùng WebSocket code `1000`; close grace chỉ là playback estimate, không phải ACK từ loa.
- Nếu firmware stock không gửi `listen:detect`, server không tự suy wake; greeting server-side không chạy trên board đó.

### Intent / Memory / Tools

- `intent.semantic_end_enabled` mở semantic goodbye trong main stream; exact wake/exit aliases vẫn route local để giữ compatibility.
- Memory dùng SQLite local với WAL, FTS5 khi có và lexical fallback. Durable personal memory không tin `Device-Id`/`Client-Id`; thiếu `memory.trusted_owner_id` thì chỉ giữ session memory.
- Native streamed `delta.tool_calls` là đường chính ở code; arguments chỉ publish sau khi JSON hoàn chỉnh và qua schema validation. Runtime capability spike trên route/model thật hiện chưa có kết luận vì bị lớp automatic approval chặn.
- Built-in `calculate` và `get_current_time` chạy qua registry/executor. MCP device chỉ expose status/volume nếu board quảng bá đúng schema; mock stock protocol đã pass, board thật vẫn PENDING.

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
| P2 | Native tool capability spike trên route/model runtime | BLOCKED (automatic approval layer) |
| P2 | Runtime profile wake cache nóng/cold, p50/p95 detect -> first binary | PENDING |
| P2 | Load 1/2/4 session + dashboard/prewarm contention | PENDING |
| P2 | ≥100 intent / ≥30 memory dialogues / ≥30 tool turns runtime corpus | PENDING |
| P1 | Test ESP32 bằng firmware nguyên bản đang có | PENDING |
| P1 | Xác nhận actual wake `listen:detect` text trên board | PENDING |
| P1 | Test wake -> greeting -> câu hỏi / greeting off nói ngay | PENDING |
| P1 | Test exit + goodbye + idle timeout trên board | PENDING |
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
