# Semantic corpus (seed)

Seed này chứng minh cấu trúc corpus theo plan M0.3/M7. Review queue 200-case
đã được sinh deterministic, nhưng **chưa phải gold corpus** cho tới khi có
human review độc lập.

- Mỗi case có `expected_semantic_outcome` và `evidence_needed`, không
  exact-match câu trả lời AI.
- `split`: `train` dùng tune prompt, `dev` dùng kiểm hồi quy,
  `held-out` không dùng tune. Critical negative phải 0 unauthorized
  mutation/action và 0 false success.
- Mock chỉ kiểm transport/execution, không chứng minh model hiểu intent.
  Corpus model thật + hardware vẫn PENDING.

Mở rộng lên đủ gate:

- Semantics tổng: 200 case riêng, ≥80 critical negative, ≥50 held-out.
- Persona/ngôn ngữ: 4 persona tổng hợp × 3 mức dài × ≥10 case.
- Memory/Tool-confirmation/Retrieval-RAG: mỗi nhóm ≥30 dialogue/query.

File `semantic_corpus_seed.json` hiện có **77 case**. Chạy
`scripts/build_semantic_review_queue.py` tạo
`semantic_corpus_review_queue.json` với **200 case / 143 critical /
52 held-out** bằng cách giữ seed và thêm generated candidates deterministic.
Mọi case trong review queue đều là `needs_human_review`.

`scripts/semantic_review_probe.py` dùng WS/auth thật + management turn-detail
để thu evidence, nhưng không auto-grade semantic. Held-out không chạy mặc định.
Reviewer vẫn phải xác nhận nhãn và verdict trước khi corpus được coi là gold.

## Audio fixtures (`audio/`)

Tạo bằng Vieneu local (giọng `Xuân Vĩnh`, 48 kHz; xem script đã dùng trong log
thực thi 2026-09-10). Dùng cho test acoustic qua loa và benchmark:

- `wake_hi_esp_v1.wav` (`"Hi, ESP!"`), `wake_hi_esp_v2.wav` (`"Hi Esp!"`):
  thử đánh thức wakenet tiếng Anh bằng Vieneu (TTS tiếng Việt, phát âm lệch).
- `wake_hi_esp_en_us.wav`: `"Hi ESP"` giọng `en-US` thật — file đã chứng minh
  đánh thức được board (2026-09-10). `scripts/hw_acoustic_test.py` dùng mặc định.
- `vn_may_gio_roi.wav`, `vn_hom_nay_thu_may.wav`, `vn_ban_ten_la_gi.wav`:
  câu hỏi tiếng Việt để phát kiểm tra vòng mic → ASR → LLM → TTS → loa.
- `bench_question_16k.wav`: mono PCM16 16 kHz cho
  `scripts/benchmark_pipeline.py` (`--speech-end-sample 16933`, trailing
  silence 0.5 s hai đầu, ngưỡng RMS 0.02/10 ms).
