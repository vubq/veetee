# Am thanh va trang thai thiet bi

## Hai huong audio

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

Upstream dung task rieng cho input, output va Opus codec. Queue PCM ngan, queue Opus co
the dai hon vi packet nho hon. Gioi han quan sat:

| Queue/cong viec | Gioi han upstream |
| --- | --- |
| Encode task dang cho | 2 |
| Playback task dang cho | 2 |
| Decode Opus | 2400 ms / 60 ms = 40 packet |
| Send Opus | 2400 ms / 60 ms = 40 packet |
| Timestamp cho server AEC | 3 |

Day la tham so can benchmark lai theo RAM, jitter va latency cua phan cung Veetee.

## Dinh dang quan sat

- Microphone uplink: PCM mono 16-bit, encode Opus thuong o 16 kHz.
- Server downlink: Opus, hello upstream thuong cong bo 24 kHz.
- Frame duration: thuong 60 ms.
- Opus: VBR va DTX bat, FEC tat trong cau hinh encoder tham khao.
- Decoder co the doi sample rate theo tham so server va resample ve codec.

## Engine theo kha nang chip

Source tham khao co AFE engine cho nhom chip manh hon va lite engine cho nhom gioi han.
AFE co the ket hop wake word, VAD va device AEC; lite engine giam phu thuoc va RAM/CPU.
Veetee can chot target chip va bai toan acoustics truoc khi ke thua phan chia nay.

## AEC va listening mode

`AecMode` co ba lua chon: tat, tren thiet bi, tren server.

| Listening mode | Semantic |
| --- | --- |
| `auto` | Server/VAD tu ket thuc utterance |
| `manual` | Nguoi dung hoac thiet bi gui stop |
| `realtime` | Full-duplex, yeu cau AEC phu hop |

Server-side AEC can timestamp trong binary protocol de can chinh uplink va downlink.
Device-side AEC tang tai xu ly. Neu khong co AEC, can tranh thu microphone trong khi
speaker dang phat de han che loopback.

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
fatal_error -> (khong thoat)
```

Transition cung state la no-op hop le. Transition sai bi tu choi va ghi warning. Tat ca
code cap ung dung nen di qua state machine thay vi gan state truc tiep.

## Luong hoi thoai dien hinh

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
  -> listening hoac idle
```

Khi wake word xuat hien trong luc speaking, thiet bi co the gui abort voi reason
`wake_word_detected`, dung playback va mo luot noi moi.

## Rui ro can test tren phan cung

- Queue overflow va mat packet khi Wi-Fi jitter.
- Click/pop khi chuyen sample rate hoac reset decoder.
- Wake word false-positive do am thanh loa.
- VAD cat dau/cuoi cau va timeout trong moi truong on.
- Reconnect trong luc encode/decode dang chay.
- Playback drain, interruption va race khi state thay doi.
- AEC delay calibration theo codec, DMA va frame size.

## Source doi chieu

- `../references/xiaozhi-esp32/main/audio/README.md`
- `../references/xiaozhi-esp32/main/audio/audio_service.h`
- `../references/xiaozhi-esp32/main/device_state.h`
- `../references/xiaozhi-esp32/main/device_state_machine.cc`
- `../references/xiaozhi-esp32/main/application.cc`
