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

File `semantic_corpus_seed.json` chứa 42 case seed bao phủ các nhóm.
