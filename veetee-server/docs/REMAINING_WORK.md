# Việc còn lại và hướng dẫn chi tiết

Cập nhật: **2026-09-25**. Chỉ giữ các việc vẫn cần evidence runtime/hardware hoặc quyết định vận hành; production-hardening code đã tách khỏi backlog này.
Mỗi mục ghi: là gì, vì sao, làm từng bước, tiêu chí đạt, ai làm.

## 1. Corpus human-review 200 (M0.3)

200 tình huống hội thoại có nhãn đúng/sai do người chấm: ≥80 critical
negative (trích dẫn/phủ định/nhầm lẫn cấm mutation-close nhầm), ≥50 held-out
(giấu, không tune prompt).

**Tooling/coverage đã hoàn tất:** seed hiện có 77 case; `scripts/build_semantic_review_queue.py` đã tạo `eval/semantic_corpus_review_queue.json` gồm **200 case, 143 critical, 52 held-out**. Tất cả giữ `review_status=needs_human_review`; generated labels không được coi là gold. `scripts/semantic_review_probe.py` chạy case qua WS thật, thu protocol + management turn trace, không auto-grade và mặc định không chạy held-out. Smoke 2 case đã chạy sạch.

Phần còn lại là **human gate**, không phải thiếu code:

1. Người thứ hai review từng nhãn/expected outcome; sửa label sai nếu có.
2. Chạy review probe theo nhóm `dev/train`, reviewer ghi `human_verdict` + notes.
3. Chỉ mở held-out sau khi prompt/model đã khóa.
4. Đạt: 0 sai trên critical; ≥95% đúng trên case rõ ràng.

## 2. Endpoint/latency corpus mở rộng

Hạ tầng A/B đã hoàn tất và profile vận hành hiện chọn **192ms + speculative ASR/LLM confidence 0.95**. Evidence nhỏ hiện có:

- 4 fixture ×2 lượt ở 192ms: 8/8 success, 0 split/missing-final;
- 160ms làm mean strict WER tăng từ 0.0625 → 0.125, max WER 0.25 → 0.50 nên không promote;
- 96ms từng split câu dài thành hai utterance nên bị loại;
- speculative LLM giữ exact-final commit/no-side-effect trước final và giảm first-audio ở các câu high-confidence;
- warm smoke 5 lượt trên profile hiện tại: server `last_voice_to_first_ws_binary` p50 ~556ms, p95 ~597ms, WER/CER 0 trên fixture; client voiced-PCM vẫn ~0.9s p50 nên chưa claim 600ms end-to-end;
- `vad_end_threshold=0.3/0.4/0.5` không được promote: WER tăng, 0.4/0.5 còn chỉ 75% success trên corpus nhỏ;
- speculative start 32ms@0.95 giữ quality nhưng không nhanh hơn aggregate 64ms; speculative TTS prefetch cũng không thắng median server latency, nên runtime giữ 64ms và TTS speculation OFF;
- Qwen 3.8-27B nhanh hơn GPT-OSS 20B trong model smoke.

Phần còn lại là **mở rộng đại diện**, không phải thiếu code tuning:

1. Tooling đã có: `scripts/audio_corpus_tool.py plan` tạo plan **33 câu / 26 critical / 11 nhóm coverage** tại `eval/natural_audio_plan.json`; `record` có thể thu mono PCM qua `arecord`; `validate` kiểm WAV/sidecar/coverage. Phần còn lại là **thu âm người thật + review `speech_end_sample`**, không dùng TTS synthetic để thay gate tự nhiên.
2. `benchmark_corpus.py --require-representative-corpus` giờ chặn corpus <30 fixture hoặc thiếu các nhóm very_short/internal_pause/hesitation/proper_name/long_number/long_sentence/background_noise/negation. Sau khi corpus thật validate PASS, chạy benchmark cùng source/model/network và lưu raw artifact.
3. Gate production: 0 split critical; false-endpoint tăng ≤1pp và CER/WER tăng ≤1pp tuyệt đối so với profile baseline đã chấp nhận.
4. Chỉ sau corpus đại diện mới tuyên bố p95/SLA; không dùng 1 fixture đẹp để claim 600ms tổng quát.

## 3. Interrupt vật lý 20×20

20 lượt thường + 20 lượt ngắt giữa chừng trên board thật (nút BOOT/wake),
kiểm loa dừng + lượt sau không stale.

1. Thường (20): wake → hỏi → nghe hết. Ghi pass/fail.
2. Ngắt (20): hỏi câu dài → giữa chừng bấm BOOT/nói "Hi ESP" → loa phải
   dừng ~1s → hỏi tiếp câu ngắn, đáp phải đúng.
3. Ghi bảng: thời gian, cách ngắt, loa dừng, lượt sau sạch. Báo tôi
   timestamp để đối chiếu log server.
4. Đạt: 20/20 thường sạch; ngắt đạt loa-dừng + lượt-sau-sạch.

## 4. AEC (đã biết thiếu trên board hiện tại)

Board mic đơn INMP441, không đường reference → không khử được tiếng loa
chính nó (đã quan sát echo loop: board tự nghe loa mình rồi tự mở turns).
Hậu quả: không ngắt bằng giọng nói khi robot đang nói.

1. Robot đang nói, bạn nói chen → xem có dừng không (dự đoán: không).
2. Idle sau 1 lượt đáp, quan sát 2 phút có tự sinh turns không.
3. Kết luận: giữ `barge_in_policy: client_only` + ghi giới hạn; muốn
   barge-in giọng nói thật thì đổi board có AEC reference. Không có đường
   code nào fix được thiếu hụt phần cứng này.

Durable-owner deployment acceptance đã hoàn tất 2026-09-25 bằng
`scripts/durable_owner_acceptance.py` theo flow prepare → service restart thật →
resume. Evidence `docs/benchmarks/durable-owner-acceptance.json` xác nhận:
PID service đổi, fact sống qua restart, stale revision bị reject, paired device A/B
bind đúng owner, runtime trace chỉ inject durable ID thuộc owner tương ứng, forget
barrier loại fact ở session mới; namespace memory synthetic được hard-clean và
device test bị revoke/offline sau chạy.

## 5. MCP hardware (tool của board)

Board có thể công bố tools (`device_get_status`, `device_set_volume` —
volume cần xác nhận). Server chỉ expose khi board quảng bá + bật
`tools.mcp_device_enabled`.

1. Tôi check hello board có `features.mcp` không.
2. Nếu có: bật flag → restart → ra lệnh "cho nhỏ volume" → xác nhận →
   **nghe tai** loa nhỏ thật (MCP success không chứng minh được).
3. Không quảng bá: mục N/A, ghi rõ.
## 6. Capacity nhiều live session

Single-session fast path đã được tối ưu; background/dashboard TTS có cooperative preemption nên không còn chặn live nhiều giây. Tuy nhiên VieNeu local giữ internal engine lock suốt một stream. Host hiện tại GTX 1650 Ti 4GB dùng khoảng 2.9GB VRAM khi server chạy, chỉ còn ~815MB nên không đủ an toàn để load replica TTS thứ hai.

Load probe authoritative (server `turn_finish.outcome`, recovery audio không tính success) cho thấy:

- 1 session: 2/2 completed, first-WS p50 ~537ms, TTS queue ~0.15ms;
- 2 sessions: 4/4 completed nhưng TTS queue p50 ~961ms, p95 ~1.76s;
- 4 sessions ×2 turns: 6/8 completed; 2 turn fail do Groq capacity, TTS queue p50 ~2.52s ở các turn hoàn tất.

Source đã giới hạn live `first_chunk_timeout_ms` theo **enqueue → first PCM** để overload fail bounded thay vì treo tới total-turn timeout. `tts.stream_queue_max_chunks` đã được đưa thành runtime/UI knob và A/B 4/16/64 bằng production streaming path với prompt dài, 2 live sessions: cả ba đều 100% success ở mẫu nhỏ nhưng 16/64 **không giảm** queue/lease; 64 còn xấu hơn nhẹ. Runtime vì vậy giữ **4 chunks**; tăng RAM buffer không giải quyết single-engine serialization.

Scheduler hiện ưu tiên `live_first` (turn chưa có audio) trước continuation `live`, không đọc prompt/intent. Boost và aging đều là hot-runtime/UI knob, profile hiện tại **5.0 / 2.0**. A/B 2 sessions ×2 turns trên cùng đường production giảm first-WS p95 khoảng **3.14s → 1.51s** và queue-to-lock p95 **2.62s → 1.00s**, 4/4 completed. Mẫu 2×4 có 6/8 completed; 2 failure là `LLM capacity exhausted` trước TTS do quota Groq, không phải starvation scheduler.

Phần scale còn lại cần tài nguyên thật: GPU/TTS replica khác hoặc provider TTS thứ hai; Groq cần quota/route bổ sung nếu muốn burst 4+ session. Không giảm token reservation giả hoặc retry nối tiếp để che capacity.
