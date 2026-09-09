# VeeTee Testing

Cập nhật: **2026-09-09**

Tài liệu này sở hữu cách kiểm thử và cách diễn giải evidence. Unit test, runtime/model test và ESP32 thật là các loại bằng chứng khác nhau; không dùng một loại để suy kết luận của loại khác.

## 1. Unit và integration

Chạy từ `veetee-server/`:

```bash
python -m unittest discover -s tests -v
```

Suite này dùng để kiểm protocol/lifecycle, cancellation, validation, tool/memory contract, semantic event plumbing và các invariant server-side. Suite xanh không tự chứng minh route/model production, latency SLA, AEC hoặc playback vật lý.

## 2. Runtime E2E

```bash
PYTHONPATH=. python test_e2e.py
```

`test_e2e.py` synthesize input audio rồi đi qua ASR pipeline của server; không nên mô tả test này như một test riêng cho Deepgram. Kết quả runtime phải ghi model/route/config/source snapshot và phân biệt `PASS`, `BLOCKED`, `FAILED`.

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

Script hiện dùng gate kỹ thuật:

- tối thiểu `20` measured samples;
- success rate tối thiểu `95%`;
- report p50/p90/p95/max;
- cờ SLA `p95 < 1000 ms` chỉ true khi sample gate và success gate cùng đạt.

Đây là gate **hiện có trong code**, phù hợp quick/runtime benchmark. [Runtime plan M0/M7](../../task-plans/2026-09-09-ai-persona-tools-memory-latency.md) yêu cầu acceptance mạnh hơn (`100` attempts/nhóm, success `>=99%`) trước khi dùng làm chứng nhận production; mức đó đang `PLANNED`.

Không đổi tên metric thành “first binary” hoặc “first audio sent”. First binary server-side là diagnostic khác và không chứng minh loa đã phát âm hữu ích.

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

## 5. Hardware ESP32/Xiaozhi stock

Hardware test phải dùng firmware nguyên bản đang có trên board. Không cần patch/build/flash firmware để hoàn tất baseline server.

Checklist acceptance:

1. OTA/WS hello kết nối ổn định và audio hai chiều hoạt động.
2. Mic -> ASR -> AI -> TTS -> loa chạy nhiều lượt liên tiếp.
3. Nếu board có `listen:detect`, ghi text thực tế và kiểm detect -> AI response -> lượt tiếp theo.
4. Test contextual end, câu trích dẫn từ kết thúc và câu có chứa từ tương tự nhưng không mang intent kết thúc.
5. Test idle timeout với cả AI continue và AI end.
6. Nghe câu dài để xác nhận không mất tail.
7. Khi board/FW có cơ chế stock tạo `abort` hoặc `listen:start`, ngắt lúc robot đang nói và xác nhận turn sau không nhận stale transcript/audio.
8. Đo request/gesture -> last binary server riêng với thao tác -> physical speaker stop; không thay thế hai metric cho nhau.
9. Ghi board/model/FW/config, network condition, số lần lặp, failure rate, p50/p95.

Nếu board không có cơ chế stock để tạo interrupt, baseline chat vẫn có thể đánh giá; automatic speech barge-in vẫn `PENDING` cho tới khi AEC/device behavior được đo độc lập.

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
