# Việc còn lại và hướng dẫn chi tiết

Cập nhật: **2026-09-10**. Tổng hợp từ các task-plans còn `PARTIAL`.
Mỗi mục ghi: là gì, vì sao, làm từng bước, tiêu chí đạt, ai làm.

## 1. Corpus human-review 200 (M0.3)

200 tình huống hội thoại có nhãn đúng/sai do người chấm: ≥80 critical
negative (trích dẫn/phủ định/nhầm lẫn cấm mutation-close nhầm), ≥50 held-out
(giấu, không tune prompt). Hiện `eval/semantic_corpus_seed.json` có 72
(30 template `dev` chưa review tay, held-out giữ 10).

1. Xem mẫu (`end-002`, `mem-002`), viết thêm ~130 case các nhóm end,
   memory, confirmation, tool, ngôn ngữ, intent edge. Mỗi case: `user`,
   `expected_semantic_outcome`, `evidence_needed`, `critical`.
2. Người thứ hai review từng nhãn.
3. Chấm bằng hỏi thật qua board/WS từng case (hoặc viết runner sau).
4. Đạt: 0 sai trên critical; ≥95% đúng trên case rõ ràng.

## 2. Endpoint A/B (VAD silence 450/320/256/192ms)

`asr.min_silence_duration_ms` (default 450) thấp thì đáp nhanh nhưng dễ cắt
ngang người ngập ngừng.

1. Chuẩn bị ~30 câu audio có nhãn điểm kết thúc đúng (ngập ngừng, tên
   riêng, số dài, câu ngắn).
2. Mỗi mức: sửa `config.yaml` local → restart → chạy `test_e2e.py`/
   benchmark → ghi false-endpoint + CER/WER.
3. Gate: false-endpoint tăng ≤1pp và CER tăng ≤1pp so với 450. Đạt mới đổi
   default, không thì rollback 450 (config local 320 hiện tại là tune tay).

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

## 5. Durable owner (memory bền + riêng tư)

Durable hiện tắt. Bật = nhớ qua restart, nhưng loa dùng chung nên phải bind
`trusted_owner_id`, nếu không rò dữ liệu giữa người dùng.

1. Bạn quyết owner (tôi không tự đặt).
2. Set `memory.trusted_owner_id` + `durable_enabled: true` → restart.
3. Test: phiên 1 nhớ fact → restart → phiên 2 cùng owner hỏi lại phải nhớ;
   khác/không owner không được đọc; forget → kiểm tra DB hết thật.
4. Đạt: isolation đúng, forget barrier đúng, restart không mất/không rò.

## 6. MCP hardware (tool của board)

Board có thể công bố tools (`device_get_status`, `device_set_volume` —
volume cần xác nhận). Server chỉ expose khi board quảng bá + bật
`tools.mcp_device_enabled`.

1. Tôi check hello board có `features.mcp` không.
2. Nếu có: bật flag → restart → ra lệnh "cho nhỏ volume" → xác nhận →
   **nghe tai** loa nhỏ thật (MCP success không chứng minh được).
3. Không quảng bá: mục N/A, ghi rõ.
