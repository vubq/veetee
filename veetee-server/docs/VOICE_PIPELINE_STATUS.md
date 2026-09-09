# Voice Pipeline Status

Snapshot tài liệu: **2026-09-09**
Source đối chiếu trước migration docs: **HEAD `51ec30b`**, working tree sạch.

Tài liệu này chỉ giữ trạng thái/evidence. Kiến trúc hiện hành nằm ở [ARCHITECTURE.md](ARCHITECTURE.md), test/acceptance ở [TESTING.md](TESTING.md), công việc runtime còn mở ở [plan AI/persona/tools/memory/latency](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md).

## Evidence snapshot

| Hạng mục | Trạng thái | Evidence/giới hạn |
| --- | --- | --- |
| Stock WebSocket/audio V1/V2/V3 | IMPLEMENTED | Source protocol/session hiện có; hardware board vẫn cần test riêng |
| AI semantic routing | IMPLEMENTED | Intent/tool/memory/confirmation/end đi qua AI contract; không dùng phrase matcher để semantic route |
| Client `abort` / `listen:start` cancellation | IMPLEMENTED | Server cancel generation/capture và dùng `tts:stop` chuẩn |
| Event queue | IMPLEMENTED | `core/session.py` dùng `asyncio.Queue(maxsize=8)` |
| VAD default | IMPLEMENTED | Source/example default `450 ms`; local config có thể override |
| Local audit override | SNAPSHOT | Audit ngày 2026-09-09 quan sát ignored `config.yaml` dùng `320 ms`; không phải project default |
| TTS sample rate | IMPLEMENTED | `24000 Hz` source/example |
| Prompt management | IMPLEMENTED + GAP | Saved persona precedence hoạt động; API/UI hiện cap `4000` ký tự (A05) |
| Tool limits | IMPLEMENTED + GAP | `max_calls=3`, `schema_limit=16`, rounds chỉ `1/2`; catalog/loop còn A06/A10 |
| Benchmark client | IMPLEMENTED | Metric `speech_end_to_first_voiced_pcm_received_ms`, current gate 20 samples/95% |
| Runtime latency SLA | PENDING | Chưa có corpus đủ để chứng nhận production p50/p95 |
| ESP32 physical playback/AEC | PENDING | Chưa có hardware/acoustic evidence mới cho snapshot này |

## KNOWN_GAP A01–A12

| ID | Trạng thái source hiện tại |
| --- | --- |
| A01 | Clock receipt còn direct render trong `core/session.py` |
| A02 | Literal `render_action_receipt_fallback(...)` vẫn tồn tại |
| A03 | Content có thể được phát trước terminal tool validation cho một số read-only mixed stream |
| A04 | Context budget chưa tính đầy đủ semantic system prompt; estimate chars/token |
| A05 | Persona API/UI cap cứng `4000` ký tự |
| A06 | LLM round validation chỉ cho `1` hoặc `2` |
| A07 | Memory retrieval còn lexical/recent, chưa hybrid semantic production |
| A08 | Dialogue chưa giữ đầy đủ structured receipts qua lượt |
| A09 | Tool schema validation còn nông, chưa recursive JSON Schema đầy đủ |
| A10 | Tool catalog cắt ở `schema_limit=16`, chưa discovery flow lớn |
| A11 | Một số ASR/TTS/tool resources còn serialized/shared |
| A12 | Corpus/model/load/SLA/hardware evidence chưa đủ; test/docs cũ còn assertion lịch sử cần thay ở runtime plan |

Các mục trên là `KNOWN_GAP`, chưa được đánh `resolved` bởi migration tài liệu.

## Test evidence lịch sử

Tài liệu trước migration từng ghi:

```text
127/127 tests PASS
compileall PASS
git diff --check PASS
```

Đây là **kết quả đã ghi nhận của một lần chạy trước**, không được rerun trong task documentation-only này và không được gọi là “regression hiện tại” cho HEAD sau mọi thay đổi. Review 2026-09-08 còn lưu snapshot `84 tests PASS` tại commit cũ; các số test ở các plan khác cũng thuộc snapshot riêng.

Task docs hiện tại không chạy full server/unit suite vì plan yêu cầu tránh side effect/runtime initialization chỉ để kiểm Markdown. Validation của migration docs được ghi trong chính [plan tài liệu](../../task-plans/2026-09-09-hop-nhat-tai-lieu-du-an.md).

## Runtime/hardware còn PENDING

- semantic/tool corpus đủ lớn trên route/model thật;
- persona lớn và context budget theo tokenizer/model thật;
- multi-round dependent tools, large catalog và recursive schema validation;
- warm/cold latency corpus với sample/success gate production;
- load 1/2/4 sessions + dashboard/prewarm contention;
- ESP32 stock: `listen:detect` thực tế, normal tail, interrupt nếu FW hỗ trợ;
- thao tác ngắt -> physical speaker stop và AEC/acoustic behavior.

Không có build/flash patched firmware trong baseline acceptance.
