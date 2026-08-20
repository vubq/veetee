# Âm thanh và trạng thái thiết bị

## Hai hướng audio

```text
MIC
  -> Audio Engine (AFE/VAD/Wake/AEC)
  -> Encode Queue
  -> Opus Encoder
  -> Send Queue
  -> Protocol
  -> Server

Server
  -> Protocol
  -> Decode Queue
  -> Opus Decoder
  -> Playback Queue
  -> Speaker
```

Upstream dùng task riêng cho input, output và Opus codec. Queue PCM ngắn, queue Opus có
thể dài hơn vì packet nhỏ hơn. Giới hạn quan sát:

| Queue/công việc | Giới hạn upstream |
| --- | --- |
| Encode task đang chờ | 2 |
| Playback task đang chờ | 2 |
| Decode Opus | 2400 ms / 60 ms = 40 packet |
| Send Opus | 2400 ms / 60 ms = 40 packet |
| Timestamp cho server AEC | 3 |

Đây là tham số cần benchmark lại theo RAM, jitter và latency của phần cứng Veetee.

## Định dạng quan sát

- Microphone uplink: PCM mono 16-bit, encode Opus thường ở 16 kHz.
- Server downlink: Opus, hello upstream thường công bố 24 kHz.
- Frame duration: thường 60 ms.
- Opus: VBR và DTX bật, FEC tắt trong cấu hình encoder tham khảo.
- Decoder có thể đổi sample rate theo tham số server và resample về codec.

## Engine theo khả năng chip

Source tham khảo có AFE engine cho nhóm chip mạnh hơn và lite engine cho nhóm giới hạn.
AFE có thể kết hợp wake word, VAD và device AEC; lite engine giảm phụ thuộc và RAM/CPU.
Veetee cần chốt target chip và bài toán acoustics trước khi kế thừa phân chia này.

## AEC và listening mode

`AecMode` có ba lựa chọn: tắt, trên thiết bị, trên server.

| Listening mode | Semantic |
| --- | --- |
| `auto` | Server/VAD tự kết thúc utterance |
| `manual` | Người dùng hoặc thiết bị gửi stop |
| `realtime` | Full-duplex, yêu cầu AEC phù hợp |

Server-side AEC cần timestamp trong binary protocol để căn chỉnh uplink và downlink.
Device-side AEC tăng tải xử lý. Nếu không có AEC, cần tránh thu microphone trong khi
speaker đang phát để hạn chế loopback.

## State machine

```text
unknown -> starting
starting -> wifi_configuring | activating
wifi_configuring -> activating | audio_testing
audio_testing -> wifi_configuring
activating -> upgrading | idle | wifi_configuring
upgrading -> idle | activating
idle -> connecting | listening | speaking | activating | upgrading | wifi_configuring
connecting -> listening | idle
listening -> speaking | idle
speaking -> listening | idle
fatal_error -> (không thoát)
```

Transition cùng state là no-op hợp lệ. Transition sai bị từ chối và ghi warning. Tất cả
code cấp ứng dụng nên đi qua state machine thay vì gán state trực tiếp.

## Luồng hội thoại điển hình

```text
wake word / button
  -> connecting
  -> OpenAudioChannel + hello
  -> listening
  -> Opus uplink
  -> STT event
  -> TTS start + Opus downlink
  -> speaking
  -> TTS stop/playback drained
  -> listening hoặc idle
```

Khi wake word xuất hiện trong lúc speaking, thiết bị có thể gửi abort với reason
`wake_word_detected`, dừng playback và mở lượt nói mới.

## Rủi ro cần test trên phần cứng

- Queue overflow và mất packet khi Wi-Fi jitter.
- Click/pop khi chuyển sample rate hoặc reset decoder.
- Wake word false-positive do âm thanh loa.
- VAD cắt đầu/cuối câu và timeout trong môi trường ồn.
- Reconnect trong lúc encode/decode đang chạy.
- Playback drain, interruption và race khi state thay đổi.
- AEC delay calibration theo codec, DMA và frame size.

## Source đối chiếu

- `../references/xiaozhi-esp32/main/audio/README.md`
- `../references/xiaozhi-esp32/main/audio/audio_service.h`
- `../references/xiaozhi-esp32/main/device_state.h`
- `../references/xiaozhi-esp32/main/device_state_machine.cc`
- `../references/xiaozhi-esp32/main/application.cc`
