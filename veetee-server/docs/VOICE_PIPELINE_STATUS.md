# Voice Pipeline Status

Snapshot tài liệu: **2026-09-10**
Source đối chiếu: **HEAD `c92192a`**, working tree sạch.

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
| Prompt management | IMPLEMENTED | Saved persona precedence + shared byte/token budget (`32 KiB`/est. `8000`); reject over-budget, version snapshot/turn |
| Tool limits | IMPLEMENTED | `max_calls 1..8` (default 3), `schema_limit` max 64, rounds `1..4` (default 2), catalog notice explicit |
| Benchmark client | IMPLEMENTED | Metric `v2` voiced proxy + useful certification metric; smoke 20/95% vs cert 100/99% (`--certification`) |
| Runtime latency SLA | NOT_MET | Smoke 2026-09-10: success 100% nhưng p50 ~6.0s / p95 ~6.6s (target p50 ≤0.6s, p95 <1.0s). Bimodal: lượt nhanh ~1.5s, lượt chậm ~6s, phần chậm nằm ở chân LLM gateway (STT chỉ ~0.5-0.7s). Cert 100-attempt chưa chạy |
| ESP32 physical playback/AEC | PENDING | Chưa có hardware/acoustic evidence mới cho snapshot này |

## A01–A12 sau runtime M1–M6 (code)

| ID | Trạng thái source hiện tại |
| --- | --- |
| A01 | RESOLVED (code): clock qua AI synthesis, bỏ direct render; cần corpus model thật |
| A02 | RESOLVED (code): bỏ literal fallback; nested failure không false success; cần corpus |
| A03 | RESOLVED (code): buffer speech tới terminal, discard khi có action; cần spike route thật |
| A04 | RESOLVED (code): unified assembly tính semantic prompt + budget/persona_version/catalog_hash; cần tokenizer/route check |
| A05 | RESOLVED (code): shared byte/token budget thay cap 4000; UI maxlength 32768 + server validate; cần persona lớn thật |
| A06 | RESOLVED (code): rounds 1..4 + bounded chain A→B; chat 1 round; cần corpus dependent |
| A07 | PARTIAL: lexical baseline + retriever contract/RAG fixture/metrics xong; production embedding/RAG chưa |
| A08 | RESOLVED (code): structured transcript + receipt history + playback states; cần follow-up corpus |
| A09 | RESOLVED (code): recursive validator + semantic guards + ownership/cancel barriers; cần race/hardware |
| A10 | RESOLVED (code): explicit catalog notice + search; MCP capability gate; chưa mở tool mới |
| A11 | PARTIAL: parallel independent reads + lease hold metrics + split deadlines xong; cần A/B tải thật |
| A12 | PARTIAL: unit 154 PASS (2026-09-10, HEAD `c92192a`); corpus/SLA/hardware còn thiếu |
| A13 | IMPLEMENTED (code) + spot-check tay: deterministic idle end — chào theo persona rồi đóng phiên code 1000; farewell retry/fallback có regression; đã kiểm 3 lượt model thật qua WebSocket |

## Test evidence

```text
154/154 tests PASS (2026-09-10, HEAD c92192a)
compileall PASS
git diff --check PASS (cần rerun trước commit)
```

Gồm regression mới `test_ai_semantics_regression`, `test_bounded_loop`, `test_retrieval_rag`, `test_clock_context` và idle farewell (retry/fallback/waiting/invite, đóng transport sau chào). Số `127/144/148` trước đây là snapshot lịch sử, không dùng thay cho lần chạy này. Corpus model thật, latency 100-attempt và hardware vẫn PENDING nên tổng là PARTIAL.

## Runtime/hardware còn PENDING

- semantic/tool corpus đủ lớn trên route/model thật;
- persona lớn và context budget theo tokenizer/model thật;
- multi-round dependent tools, large catalog và recursive schema validation;
- warm/cold latency corpus với sample/success gate production;
- load 1/2/4 sessions + dashboard/prewarm contention;
- ESP32 stock: `listen:detect` thực tế, normal tail, interrupt nếu FW hỗ trợ;
- thao tác ngắt -> physical speaker stop và AEC/acoustic behavior.

Không có build/flash patched firmware trong baseline acceptance.
