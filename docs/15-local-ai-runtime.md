# Local AI runtime and speed baseline

This document records the local speech stack actually installed on the Veetee
development machine. It is deliberately separate from the provider contract: a
model can be replaced without changing the ESP32 wire protocol or conversation
state machine.

## Host profile

The benchmark host has an Intel i5-10300H (4 physical / 8 logical CPU threads),
15 GiB RAM and an NVIDIA GTX 1650 Ti with 4 GiB VRAM. CUDA toolkit (`nvcc`) is
not installed, so the reproducible baseline uses CPU execution. The NVIDIA driver
does not by itself make the current ONNX or native C++ pipeline a CUDA pipeline.

All model workers run directly in the voice-server virtual environment. Docker is
not required for speech inference. Model files are under `veetee-server/models/`
and are ignored by Git; pinned preparation scripts verify SHA-256 before a worker
uses them.

## Selected runtime

| Stage | Runtime | Profile | Why |
| --- | --- | --- | --- |
| VAD/endpoint | Silero VAD ONNX | CPU, one recurrent state per session | small, deterministic endpoint signal; not semantic admission |
| ASR primary | Sherpa-ONNX Zipformer Vietnamese 30M INT8 | CPU, 4 threads | very low RTF and suitable for final/streaming decode |
| ASR quality fallback | ChunkFormer-CTC-Large-Vie | not installed by default | 614 MiB-class checkpoint, heavy dependencies and CC BY-NC restriction; enable only after quality benchmark |
| TTS default | VieNeu-TTS v3 Turbo ONNX INT8 | CPU, 4 threads, streaming codec | first audio is available before the utterance finishes and cancellation can stop the stream |
| TTS benchmark option | VieNeu-TTS.cpp native CPU | llama.cpp native SIMD + ONNX MOSS codec | faster complete synthesis on this host, but the current C ABI is batch-only |

The default TTS remains the ONNX path even though the native benchmark is faster
for complete audio. Realtime conversation requires incremental audio and a clear
barge-in cancellation boundary; selecting a batch-only native call would make
that behavior worse. Native C++ is therefore an opt-in worker profile until its
stream callback and cancellation API are implemented and benchmarked.

## Measured results

The measurements below use the same Vietnamese sentence and a warmed model. TTS
audio duration varies slightly because sampling is enabled, so compare ranges and
not a single run as an absolute promise.

### ASR

| Model | Audio | Warm decode | RTF | Output |
| --- | ---: | ---: | ---: | --- |
| Zipformer Vietnamese 30M INT8 | 1.55 s | about 81 ms | 0.018--0.030 | `ÂM LƯỢNG TV GIẢM` |

This is comfortably below the V1 ASR-final latency budget. Keep the recognizer
resident and do not load ChunkFormer on the normal path.

### TTS

| Backend | Threads | First audio | Complete synthesis | Audio | RTF |
| --- | ---: | ---: | ---: | ---: | ---: |
| VieNeu ONNX INT8 | 4 | 430--560 ms | about 3.0--3.7 s | 2.8--3.3 s | about 0.99--1.21 |
| VieNeu native C++ CPU | 4 | batch-only | 2.22 s | 2.96 s | 0.75 |

The native run used a 630 MiB model pack and peaked at about 959 MiB RSS. A
one-shot CLI process took about 5.4 s wall time because model loading dominates;
production must keep the engine resident. The native profile currently exposes
`vieneu_synthesize_v3()` only, so it cannot meet the same first-audio and
user-stop guarantees as the ONNX streaming profile.

### Thread sweep for ONNX TTS

On this CPU, 1, 2, 4, 6 and 8 threads all landed near RTF 1.0. Four threads is
the selected default because it leaves headroom for VAD, ASR, WebSocket and the
LLM adapter. `VEETEE_TTS_THREADS` can be changed for a measured deployment; do
not assume that maxing logical threads improves latency under concurrent sessions.

## Runtime controls

```env
VEETEE_MODELS_ROOT=models
VEETEE_ASR_THREADS=4
VEETEE_TTS_THREADS=4
VEETEE_TTS_VOICE=Ngọc Linh
VEETEE_TTS_OUTPUT_SAMPLE_RATE=24000
VEETEE_TTS_APPLY_WATERMARK=true
```

Prepare the default stack:

```bash
cd veetee-server
npm run models:prepare
npm run models:benchmark
```

Prepare the optional native benchmark pack (about 630 MiB; still ignored by
Git):

```bash
cd veetee-server
npm run models:prepare-native
```

The native build cache and ONNX Runtime C++ SDK are intentionally kept under
`veetee-server/.cache/local-ai/`. They are not application dependencies and are
not copied into firmware or committed to the repository.

## Production decision and next optimization

1. Keep Zipformer + Silero + VieNeu ONNX INT8 as the V1 local speech baseline.
2. Prewarm all three sessions during voice-server startup and expose each state
   through `/health/ready`.
3. Stream sentence-sized TTS chunks, clear the playback queue on arbiter abort,
   and never wait for a full LLM answer before starting the first sentence.
4. Add a native TTS stream callback that emits decoded PCM per text/audio chunk,
   accepts a cancellation token, and preserves the same `OperationContext` as
   the Python provider. Only then can the native 0.75 RTF result replace ONNX.
5. Install ChunkFormer in a separate environment only when a labeled Vietnamese
   noise/name/number corpus shows a meaningful WER gain. Its CC BY-NC license
   must remain visible in Manager before any redistribution or commercial use.

The speed target is measured end to end: VAD final -> ASR final <= 600 ms,
ASR final -> first LLM token <= 800 ms, first LLM token -> first TTS audio <=
700 ms, and user stop -> speaker silence <= 250 ms. A faster isolated model is
not an optimization if it breaks these cancellation and streaming gates.
