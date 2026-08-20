# Benchmark ASR/VAD M0.5

## Phạm vi và giới hạn

Benchmark chạy ngày 20/08/2026 trên máy local, GPU GTX 1650 Ti 4 GB, dùng runtime trong
`/home/quangvu/vieneu-venv`:

- `faster-whisper 1.2.1` + `ctranslate2 4.8.1`.
- `silero-vad 6.2.1` + ONNX Runtime 1.28.0.
- CUDA float16, audio resample mono 16 kHz.

Audio là `/home/quangvu/test-vne.wav`, tiếng Việt tổng hợp, dài 5,84 giây. Đây là fixture
local đã có, không chứa dữ liệu người dùng; không commit audio/transcript vào repository.
Fixture không có ground truth nên **không đo hoặc kết luận WER/CER**. Kết quả chỉ dùng cho
latency, RTF, load và lựa chọn runtime ban đầu.

## PhoWhisper

Nguồn model Hugging Face:

- `mad1999/pho-whisper-small-ct2`, model size khoảng 484 MB.
- `mad1999/pho-whisper-medium-ct2`, model size khoảng 1,53 GB.

| Model | Load | Inference | Audio | RTF | Language |
| --- | ---: | ---: | ---: | ---: | --- |
| small | 0,912 s | 1,637 s | 5,84 s | 0,280 | `vi` |
| medium | 42,800 s | 4,015 s | 5,84 s | 0,687 | `vi` |

Concurrency probe dùng hai worker nhưng serialize GPU decode trên cùng model để tránh
tranh chấp runtime:

| Model | Worker 1 | Worker 2 | Wall time |
| --- | ---: | ---: | ---: |
| small | 1,366 s | 2,748 s | 2,749 s |
| medium | 4,022 s | 8,073 s | 8,073 s |

### Decision record tạm thời

- Runtime ASR ban đầu: faster-whisper/CTranslate2.
- Variant mặc định M2 đề xuất: PhoWhisper small, vì RTF `0,280`, cold load ngắn và phù hợp
  GPU 4 GB hơn.
- PhoWhisper medium giữ làm quality candidate; không bật mặc định khi chưa có WER/CER có
  ground truth và capacity test thực tế.
- ASR adapter phải cho phép đổi `small`/`medium` bằng config typed, không đổi code pipeline.
- Cold model load không nằm trong turn deadline; model phải warm trước khi nhận session.

## Silero VAD ONNX

Probe dùng frame sample rate 16 kHz, `speech_pad_ms=80`,
`min_silence_duration_ms=150`, `min_speech_duration_ms=250`:

| Threshold | Segments | Speech detected | Inference |
| ---: | ---: | ---: | ---: |
| 0,35 | 4 | 5.552 ms | 62,8 ms |
| 0,50 | 4 | 5.552 ms | 36,1 ms |
| 0,65 | 4 | 5.488 ms | 37,3 ms |

Baseline đề xuất là threshold `0,50`, pre-roll `80 ms`, silence `150 ms`. Đây là điểm
khởi đầu trên audio tổng hợp, chưa khóa cho môi trường ồn; M2 phải đo false start/false
stop trên tiếng quạt, tiếng người xa mic và barge-in thực tế.

## Artifact và lệnh chạy

Tool benchmark nằm tại `veetee-server/tools/benchmark_asr_vad.py`. Report raw nằm ngoài
repo tại `/tmp/opencode/veetee-m05-asr-vad.json`; chỉ chứa metric và không chứa transcript.

```bash
/home/quangvu/vieneu-venv/bin/python \
  veetee-server/tools/benchmark_asr_vad.py \
  --output /tmp/opencode/veetee-m05-asr-vad.json
```

## Khoảng trống cần xử lý ở M2

- Tập đánh giá có quyền sử dụng, giọng Bắc/Trung/Nam, câu ngắn/dài, nhiễu và im lặng.
- Ground-truth transcript để tính WER/CER.
- VRAM/RAM theo concurrency thật, không chỉ serialized probe.
- Audio 16 kHz từ Opus device và resample quality.
- VAD calibration trên thiết bị/microphone mục tiêu.
