# Semantic corpus (seed)

Seed này chứng minh cấu trúc corpus theo plan M0.3/M7, chưa phải full
200-case + held-out để nghiệm thu production.

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

File `semantic_corpus_seed.json` chứa 72 case: 42 seed gốc + 30 case
mẫu `origin=template-2026-09-10` (end-negatives, confirmation, memory
negatives, clock/tool, smalltalk, language, intent edge — toàn `split=dev`,
`held-out` giữ nguyên 10 case review tay). Case mẫu là điểm khởi đầu rõ
nghĩa, chưa phải nhãn human-reviewed; vẫn cần review + mở rộng lên gate
200/80/50 của M0.3.

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
