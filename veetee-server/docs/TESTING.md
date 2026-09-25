# VeeTee Testing

Cập nhật: **2026-09-25**

Tài liệu này sở hữu cách kiểm thử và cách diễn giải evidence. Unit test, runtime/model test và ESP32 thật là các loại bằng chứng khác nhau; không dùng một loại để suy kết luận của loại khác.

## 1. Unit và integration

Chạy từ `veetee-server/` bằng venv của project (không dùng `python3` hệ thống):

```bash
../../venv/bin/python -m unittest discover -s tests -v
```

Baseline production-hardening 2026-09-18 là `282/282 PASS`. Verification working tree 2026-09-25 sau playback ownership, speculative ASR/LLM, hot latency tuning, benchmark isolation, corpus/continuity metrics, lifecycle-state extraction, deterministic TTS lease teardown, semantic-review tooling và TTS-buffer matrix: **`427/427 PASS` (6.110s)**. Suite gồm auth/OTA one-time credential, pairing bounds, device-owner memory mapping, degraded boot khi thiếu LLM credential, quota/router/provider/config, playback stale-writer ownership, speculative ASR reuse/invalidate/fallback, speculative LLM no-side-effect/exact-final reuse/mismatch discard, management turn-detail auth/redaction, semantic held-out protection, lifecycle/tool/memory và benchmark helpers. `compileall`, `git diff --check` và `systemd-analyze verify deploy/veetee.service` đều PASS trong cùng verification. Focused TTS/load reconciliation tests còn kiểm cooperative preemption, deterministic scheduler release và first-chunk deadline bao gồm scheduler wait.

Suite này dùng để kiểm protocol/lifecycle, cancellation, validation, tool/memory contract, semantic event plumbing và các invariant server-side. Frontend có Vitest/Vue Test Utils (`cd web && npm test`) và production build gate (`npm run build`); verification 2026-09-25 hiện là **`26/26` tests PASS trên 9 file**, production build PASS và `npm audit --audit-level=high` báo **0 vulnerabilities**. Các suite xanh không tự chứng minh route/model production, latency SLA, AEC hoặc playback vật lý.

## 2. Runtime E2E

```bash
PYTHONPATH=. python test_e2e.py
```

test_e2e.py synthesize input audio rồi đi qua pipeline Parakeet/Silero thực của server. Kết quả runtime phải ghi model/route/config/source snapshot và phân biệt PASS, BLOCKED, FAILED.

Không coi timeout hoặc thiếu dependency/model là `PASS`.

## 3. Benchmark latency

Xem CLI hiện hành:

```bash
python scripts/benchmark_pipeline.py --help
```

Metric chính trong script hiện tại:

```text
speech_end_to_first_voiced_pcm_received_ms
```

Script hiện dùng hai chế độ gate (`--certification`):

- smoke (mặc định): tối thiểu `20` samples, success `>=95%`, trạng thái `SMOKE_ONLY`, không chứng nhận SLA;
- certification: `100` attempts/nhóm, success `>=99%` trên toàn bộ attempts (gồm timeout/failure), trạng thái `ACHIEVED`/`PARTIAL`/`NOT_MET`.

Cả hai report p50/p90/p95/max và không cộng p95 các stage thành p95 end-to-end. Metric version `v2`; certification metric là `speech_end_to_first_useful_voiced_audio_received_ms`, voiced PCM hiện chỉ là proxy cho tới khi case pass quality/grounding.

Không đổi tên metric thành “first binary” hoặc “first audio sent”. First binary server-side là diagnostic khác và không chứng minh loa đã phát âm hữu ích.

### Endpoint matrix không restart

Khi server hiện hành có management token và fixture audio gắn nhãn, có thể A/B cùng source/runtime mà không sửa YAML/restart từng mức:

```bash
VEETEE_MANAGEMENT_TOKEN='...' ../../venv/bin/python scripts/benchmark_endpoint_matrix.py \
  --wav eval/audio/<fixture>.wav \
  --values 450,320,256,192 \
  --runs 20
```

Runner hot-apply `asr.min_silence_duration_ms` và có thể A/B thêm `vad_end_threshold`, speculative ASR/LLM; chạy `benchmark_pipeline` cho từng mức, lưu raw/summary theo variant và restore toàn bộ effective value ban đầu trong `finally`. Runner tạo paired benchmark device tạm qua OTA/auth chuẩn, revoke sau chạy, chờ `active_sessions=0`, chờ HTTP ready sau cold restart và dùng process-wide lock để hai matrix không restore đè nhau. Với fixture có expected transcript, report thêm split/missing-final + WER/CER; benchmark còn ghi `audio_frame_gap_p95_ms`, max gap và số gap >2× frame để phát hiện TTS segmentation gây stall. **Latency không tự chọn winner**. Dùng `--certification --runs 100` khi đủ corpus và hardware evidence.

Evidence nhỏ hiện tại: 192ms + speculative ASR đạt 8/8 success, 0 split/missing; 160ms làm WER/CER xấu hơn nên bị loại, còn 96ms từng split câu dài thành hai utterance nên không promote. Speculative LLM 0.95 giữ nguyên quality và giảm mean fixture p50 ~37ms. Soft-cut 8/3 giữ nguyên quality, continuity smoke 4 fixture không có gap >2× frame và giảm mean p50 so với soft-cut 0. Đây vẫn là smoke/corpus nhỏ, không thay thế acoustic listening/hardware certification.

## 4. Semantic/tool quality

Corpus semantic nên đánh expected outcome thay vì exact-match câu trả lời AI. Tách tối thiểu các nhóm:

- normal chat;
- contextual end và negative/quoted end;
- clock/date cần tool và câu có nhắc giờ nhưng không cần current clock;
- memory remember/update/forget và revision conflict;
- confirmation approve/reject/expired;
- read-only tool, side-effect tool, tool failure và nhiều receipt;
- persona/ngôn ngữ khác nhau;
- prompt/history lớn;
- cancellation/late output.

Mỗi report cần ghi source HEAD/dirty state, route/model quan sát được, config fingerprint đã loại secret, corpus/sample size và failure count.

### Semantic human-review tooling

`scripts/build_semantic_review_queue.py` tạo deterministic review queue **200 case / 143 critical / 52 held-out** từ seed + generated candidates. Mọi case đều giữ `review_status=needs_human_review`; tool không tự biến generated label thành gold.

`scripts/semantic_review_probe.py` chạy case qua WS/auth thật và lấy event-level trace từ management-only `GET /api/turns/{turn_id}`. Runner **không auto-grade semantic**; output giữ `human_verdict: null` và `reviewer_notes` trống. Held-out bị loại mặc định và chỉ chạy khi reviewer truyền `--include-held-out`. Smoke `chat-001,neg-001` đã tạo evidence packet thành công, paired credential tạm được revoke sau chạy.

`scripts/durable_owner_acceptance.py` dùng flow hai pha `prepare → restart service minh bạch → resume` để chứng minh durable memory trên deployment thật mà không đụng fact user. State chỉ chứa owner/marker synthetic; resume yêu cầu MainPID service đổi rồi kiểm persistence, optimistic revision conflict, paired-device owner binding, runtime context durable-ID isolation A/B và forget barrier. Evidence hiện tại: `docs/benchmarks/durable-owner-acceptance.json` PASS; memory acceptance rows được hard-delete, state file xóa, device test revoke/offline sau chạy.

## 5. Hardware ESP32/Xiaozhi stock

Hardware test phải dùng firmware nguyên bản đang có trên board. Không cần patch/build/flash firmware để hoàn tất baseline server.

Checklist acceptance:

1. OTA/WS hello kết nối ổn định và audio hai chiều hoạt động.
2. Mic -> ASR -> AI -> TTS -> loa chạy nhiều lượt liên tiếp.
3. Nếu board có `listen:detect`, ghi text thực tế và kiểm detect -> AI response -> lượt tiếp theo.
4. Test contextual end, câu trích dẫn từ kết thúc và câu có chứa từ tương tự nhưng không mang intent kết thúc.
5. Test idle timeout: im lặng quá `conversation.idle_timeout_seconds` (local 120s) thì server deterministic kết thúc phiên — AI sinh 1 câu chào, phát xong đóng WebSocket code 1000. Kiểm có chào + socket đóng + session được dọn; lượt nói chen vào giữa chừng phải hủy flow idle.
6. Nghe câu dài để xác nhận không mất tail.
7. Khi board/FW có cơ chế stock tạo `abort` hoặc `listen:start`, ngắt lúc robot đang nói và xác nhận turn sau không nhận stale transcript/audio.
8. Đo request/gesture -> last binary server riêng với thao tác -> physical speaker stop; không thay thế hai metric cho nhau.
9. Ghi board/model/FW/config, network condition, số lần lặp, failure rate, p50/p95.

Nếu board không có cơ chế stock để tạo interrupt, baseline chat vẫn có thể đánh giá; automatic speech barge-in vẫn `PENDING` cho tới khi AEC/device behavior được đo độc lập.

### 5b. Hardware acoustic tự động (`scripts/hw_acoustic_test.py`)

Vòng loa laptop → mic board → server → loa board, verify tự động qua
`/health` + journal + serial, exit `0` PASS / `1` FAIL / `2` BLOCKED:

```bash
../../venv/bin/python scripts/hw_acoustic_test.py --expect "mấy giờ"
../../venv/bin/python scripts/hw_acoustic_test.py --expect "mấy giờ" --idle  # kèm idle-close + re-wake (chậm)
```

Yêu cầu: server ready, quyền `sudo -n` cổng serial, `pw-play`, board đã
provision Wi-Fi/OTA, loa PC đặt gần mic. Mỗi lần chỉ một tiến trình đọc
serial. Fixture trong `eval/audio/` (`wake_hi_esp_en_us.wav` đã chứng minh
đánh thức được board).

Giới hạn đã biết (board mic đơn, không AEC reference): board nghe được cả
loa của chính nó nên có echo turns xen giữa; harness chờ kênh lặng rồi hỏi
lại nhiều lần (`--qa-tries`) và chỉ PASS khi transcript khớp `--expect`.
PASS tự động không thay kiểm tra nghe tail/dừng-loa vật lý (mục 6, 8).

## 6. Khi dùng PASS / PARTIAL / PENDING

- `PASS`: gate cụ thể có đủ evidence theo đúng loại test và snapshot.
- `PARTIAL`: phần server/mock có evidence nhưng còn route/model, corpus, SLA hoặc hardware chưa chạy/không đủ sample.
- `PENDING`: chưa có evidence phù hợp.
- `BLOCKED`: test không thể chạy vì dependency/model/environment thiếu; không chuyển thành PASS.

Một số test count lịch sử trong task-plans/review chỉ là snapshot của lần chạy cũ. Không nối các số `50/84/112/127` từ các thời điểm khác nhau thành “regression hiện tại”.

## 7. Validation cho thay đổi chỉ tài liệu

Khi chỉ sửa docs, đủ dùng các kiểm tra không có side effect runtime:

```bash
git diff --check
python scripts/benchmark_pipeline.py --help
```

Ngoài ra phải kiểm relative Markdown links, code fences/headings, inventory trước/sau và đảm bảo diff không chạm `.py`, `.yaml`, prompt runtime, service, `references/` hoặc benchmark artifacts.
