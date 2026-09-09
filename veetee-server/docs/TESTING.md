# VeeTee Testing

Cập nhật: 2026-09-09

## Loại kiểm thử

### Unit/integration

Kiểm protocol, lifecycle, validation, cancellation, tools, memory contract và AI semantic events.

### Runtime

Runtime test phải ghi model/route/config/source snapshot. Không suy SLA từ một mẫu hoặc từ test unit.

### Hardware ESP32

Kiểm thử firmware nguyên bản tách biệt với server simulation. Server gửi audio không phải bằng chứng loa đã phát hoặc AEC hoạt động.

## Latency metrics

Cần phân biệt:

- speech end -> ASR final
- context build
- LLM first token/decision
- tool receipt
- TTS first audio
- first binary sent
- first voiced PCM/playback nếu đo được

## Quality gates

Corpus semantic dùng expected outcome, không exact match câu trả lời AI. Các báo cáo phải ghi sample size, snapshot, limitation và trạng thái PASS/PARTIAL/PENDING.

## Lệnh cơ bản

```bash
python -m unittest discover -s tests -v
PYTHONPATH=. python test_e2e.py
```
