# Local AI runtime and speed baseline

This document records the local speech stack actually installed on the Veetee
development machine. It is deliberately separate from the provider contract: a
model can be replaced without changing the ESP32 wire protocol or conversation
state machine.

## Host profile

The benchmark host has an Intel i5-10300H (4 physical / 8 logical CPU threads),
15 GiB RAM and an NVIDIA GTX 1650 Ti with 4 GiB VRAM. VAD/ASR and the MOSS codec
use ONNX Runtime CPU; the active host TTS uses VieNeu native C++ CPU. A separate
CUDA 12 / ONNX Runtime GPU environment was also measured against the same models
and fixed random seeds; it did not improve this workload on the GTX 1650 Ti.

All model workers run directly in the voice-server virtual environment. Docker is
not required for speech inference. Model files are under `veetee-server/models/`
and are ignored by Git; pinned preparation scripts verify SHA-256 before a worker
uses them.

## Selected runtime

| Stage | Runtime | Profile | Why |
| --- | --- | --- | --- |
| VAD/endpoint | Silero VAD ONNX | CPU, one recurrent state per session | small, deterministic endpoint signal; not semantic admission |
| ASR primary | Sherpa-ONNX Zipformer Vietnamese 30M INT8 | CPU, 2 threads | very low RTF and suitable for final/streaming decode |
| ASR quality fallback | ChunkFormer-CTC-Large-Vie | not installed by default | 614 MiB-class checkpoint, heavy dependencies and CC BY-NC restriction; enable only after quality benchmark |
| TTS active host | VieNeu-TTS.cpp native CPU | 4 threads, llama.cpp SIMD + ONNX MOSS codec, 24/40 lead and 48/72 steady batch bounds | Small lead chunk lowers first audio; exact configured tempo, measured headroom and bounded playback queue |
| TTS compatibility default | VieNeu-TTS v3 Turbo ONNX INT8 | CPU, 2 threads, Trúc Ly at neutral 1.0x tempo with 16-frame lead-in | clone-safe default when native library/model pack is absent |

Repository examples retain ONNX as the portable default, while this development
host selects `VEETEE_TTS_BACKEND=native`. The native C ABI is batch-only, so the
adapter releases a 24/40-character lead chunk and bounds steady batches at 48/72
characters, buffers five seconds of paced device audio, schedules browser PCM
ahead, and serializes the native worker. Abort clears
speaker/browser audio and rejects the generation immediately; an in-flight C call
may finish silently under its worker lock because native code cannot be force-killed.

## Measured results

The latest measurements use the same Vietnamese sentence, a warmed model, five
runs per TTS profile and a fixed NumPy seed per run. The fixed seeds ensure every
profile generates equivalent acoustic-token sequences instead of comparing
different random samples.

### ASR

| Model | Audio | Warm decode | RTF | Output |
| --- | ---: | ---: | ---: | --- |
| Zipformer Vietnamese 30M INT8 | 1.55 s | 38.06 ms median / 44.37 ms p95 at 2 threads | 0.025 median | `ÂM LƯỢNG TV GIẢM` |

This is comfortably below the V1 ASR-final latency budget. Keep the recognizer
resident and do not load ChunkFormer on the normal path.

### TTS

The CPU/GPU comparison below uses neutral tempo `1.0` so it measures inference
placement rather than post-processing. Production uses the same CPU provider with
Trúc Ly and a 16-acoustic-frame stream lead-in. A faster playback tempo shortens
the audio buffer without making inference faster, so it is not the default.

| Backend | Threads | First audio median / p95 | Complete median / p95 | RTF median / p95 |
| --- | ---: | ---: | ---: | ---: |
| VieNeu ONNX INT8 CPU, stream lead-in 16 | 2 | 1.56 / 1.72 s | 3.98 / 4.24 s | 1.148 / 1.205 |
| VieNeu ONNX INT8 CUDA with CPU fallback | 2 | 696 / 1,365 ms | 4.06 / 5.05 s | 1.303 / 1.804 |
| VieNeu native C++ CPU, 75-character probe | 4 | batch complete in 3.22 s | 3.22 s | 0.745 |
| VieNeu native C++ CPU, 190-character probe | 4 | batch complete in 9.65 s | 9.65 s | 0.957 |

The CUDA graph produced many CPU/GPU copy boundaries, used only about 4--10% GPU,
and peaked near 1 GiB VRAM during the sampled run. The current VieNeu INT8 export
is therefore kept on CPU. The host currently also has an NVIDIA driver/library
version mismatch, so CUDA cannot be enabled safely without a maintenance reboot.
Revisit CUDA only with a GPU-oriented FP16/FP32 export or a newer engine that
keeps the recurrent decode path on the GPU.

A post-fix ten-run check measured first audio at 1.56 s median/1.72 s p95,
complete synthesis at 3.98 s median and zero estimated playback starvation in all
ten runs. The lead-in trades a few hundred milliseconds of first audio for a
continuous stream; the old 1.5x profile shortened playback but caused gaps because
the autoregressive generator remained slower than the speaker clock.

The current native run uses the 630 MiB model pack and peaked near 1.68 GiB RSS
including initialization. The 190-character one-shot CLI took 13.92 s wall time,
of which 9.65 s was synthesis, so production keeps the context resident. A live
Groq + native Lab run produced 854 characters/37.41 s PCM without provider errors,
then answered a separate `1 + 1` turn in 1.82 s. A 2026-07-24 direct native probe
measured 24 characters in 1.07--1.44 s, 50 characters in 1.95--2.13 s and
82 characters in 3.32--3.79 s across Trúc Ly/Ngọc Linh, with RTF 0.72--0.75.
Those measurements drive the smaller lead/steady bounds. Tempo is no longer
adaptively reduced: `effective_speed` equals the published request, while
`realtime_speed_ceiling` reports whether a long response may drain its playback
buffer.

### Thread sweep for local speech

The ASR sweep used 20 warmed runs. The historical TTS thread sweep on 2026-07-22
used five warmed, fixed-seed runs with the production watermark enabled and the
old four-frame lead-in; it remains useful for thread selection, not current
first-audio expectations:

| Threads | ASR median / p95 | TTS first audio median / p95 | TTS RTF median / p95 |
| ---: | ---: | ---: | ---: |
| 1 | 46.59 / 53.84 ms | not selected | not selected |
| 2 | 38.06 / 44.37 ms | 521 / 596 ms | 1.124 / 1.202 |
| 4 | 61.83 / 81.26 ms | 533 / 625 ms | 1.215 / 1.297 |
| 6 | 82.32 / 141.58 ms | 516 / 654 ms | 1.239 / 1.348 |
| 8 | 108.28 / 158.63 ms | not selected | not selected |

The ONNX compatibility profile uses two ASR threads and two TTS threads. Six ONNX
TTS threads had a similar median first-audio result but worse p95, worse complete
RTF and much higher sustained CPU temperature. The active native profile uses four
threads, matching the native benchmark on this four-core host.

## Runtime controls

```env
VEETEE_MODELS_ROOT=models
VEETEE_ASR_THREADS=2
VEETEE_TTS_THREADS=4
VEETEE_TTS_BACKEND=native
VEETEE_TTS_VOICE="Trúc Ly"
VEETEE_TTS_STYLE=tu_nhien
VEETEE_TTS_SPEED=1.0
VEETEE_TTS_STREAM_LEADIN_FRAMES=16
VEETEE_TTS_OUTPUT_SAMPLE_RATE=24000
VEETEE_TTS_APPLY_WATERMARK=true
VEETEE_TTS_NATIVE_MODEL_DIR=models/vieneu-v3-turbo-native
VEETEE_TTS_NATIVE_LIBRARY_PATH=.cache/local-ai/VieNeu-TTS.cpp/build-cpu/libvieneu-tts.so
VEETEE_TTS_NATIVE_REALTIME_HEADROOM=1.15
VEETEE_TTS_PLAYBACK_QUEUE_SECONDS=5
VEETEE_LLM_PREWARM=true
VEETEE_LLM_PREWARM_SECONDS=12
VEETEE_PLANNER_SECONDS=8
VEETEE_9ROUTER_MODEL=cx/gpt-5.6-terra
VEETEE_DEFAULT_PERSONA="You are Veetee, a natural voice assistant. Reply in the user's language with as much detail as the request needs. Start speaking promptly, continue until the answer is complete, and do not use Markdown or expose hidden reasoning."
```

Prepare the default stack:

```bash
cd veetee-server
npm run env:voice:sync
npm run models:prepare
npm run models:benchmark
```

The sync command writes only the ignored voice runtime environment with mode
`0600`. It copies the Manager internal service token and the active 9Router API key
without printing either value; Codex OAuth/session credentials remain owned by
9Router and never enter Veetee configuration.

The benchmark accepts separate controls, for example:

```bash
uv run --project apps/voice-server python scripts/benchmark_local_ai.py \
  --asr-threads 2 --tts-threads 2 --voice "Trúc Ly" --speed 1.0 \
  --tts-stream-leadin-frames 16 \
  --watermark --runs 5 --seed 20260722
```

`VEETEE_DEFAULT_PERSONA` is only the configurable fallback when Manager auth is
disabled or no agent config exists. A published agent persona replaces it; no
persona or locale behavior is compiled into firmware.

## Local full-loop validation

The host WebSocket client exercises the real wire path rather than calling
providers directly. `npm run test:voice:local-e2e` starts an isolated voice-server
on a random loopback port with device auth disabled only for that process, runs the
client and always stops the temporary process. It does not restart or weaken the
LAN service on port 8000. The MCP commands below use an untracked local WAV
containing the Vietnamese request to set the volume to 55 percent; replace the path
with an equivalent test utterance when reproducing the run:

```bash
cd veetee-server
npm run test:voice:local-e2e
npm run test:voice:local-e2e -- \
  --abort-on-first-audio
npm run test:voice:local-e2e -- \
  --wav /tmp/veetee-mcp-volume.wav \
  --expect-tool self.audio_speaker.set_volume \
  --expected-volume 55
npm run test:voice:local-e2e -- \
  --wav /tmp/veetee-mcp-volume.wav \
  --abort-on-tool-call \
  --expect-tool self.audio_speaker.set_volume \
  --expected-volume 55
```

The 2026-07-22 run passed `Opus uplink -> Silero -> Zipformer -> fused semantic
admission/plan -> 9Router -> VieNeu -> paced Opus downlink`. The fused structured
call returns admission, dialogue act and plan together. Direct short responses go
straight to TTS without a second model call; MCP turns keep a second prose pass so
the spoken result is grounded in the actual tool response. The post-provider-routing
regression run reached first downlink audio at about 2.75 s for direct clarification
and 3.87 s for the tool/abort path. Earlier cold or slower 9Router samples reached
about 4.0--6.5 s; none of these small smoke samples is the final p95 gate.

The same run verified semantic no-response for incidental speech, first-audio
button abort, abort while MCP was pending with a late result, first-input goodbye,
and interrupt during goodbye. No stale MCP/text/TTS output was observed after
generation cancellation.

A clean 9Router upstream can take about 4.3 seconds for its first structured call,
while the same call is about 1.3 seconds after warmup. Voice-server therefore
prewarms the configured default LLM during startup and reports it as a required
readiness component. Prewarm failure is logged without leaking credentials; the
server stays live for diagnostics but `/health/ready` remains not ready while the
LLM endpoint is unavailable. A later readiness probe retries the bounded prewarm,
so a transient startup outage can recover without restarting voice-server.

The local development planner hard deadline is 8 seconds because the Codex-backed
9Router route can occasionally exceed 4 seconds even after prewarm. This is a
safety ceiling, not a latency target: the p95 planner target remains much lower,
and successful responses are forwarded immediately rather than waiting for the
deadline.

Semantic output uses a forced internal structured function with a bounded JSON
Schema instead of trusting free-form JSON text. This function returns admission,
dialogue act and `ConversationPlan`; it is not an MCP/device action. The
policy/parser normalizes cross-field invariants, then the MCP broker independently
validates the selected live tool name, schema, safety class and arguments.

For a direct `respond` plan, the same structured call may include a short,
directly speakable `response_text`; voice-server sends it to TTS without a second
LLM request. Tool plans deliberately keep the second LLM pass because the spoken
answer must be grounded in the real MCP result. This hybrid removes one sequential
provider call from common knowledge/social turns without fabricating tool success.
Static planner responses, clarification and goodbye text still pass through the
same Vietnamese sentence chunker as streamed prose. Each sentence receives a
refreshed synthesis-idle deadline while the device sees one continuous
`tts:start`/audio/`tts:stop` lifecycle. Native VieNeu emits an initial chunk with
24/40 target/maximum characters, then coalesces steady chunks toward 48 characters
with a 72-character maximum and reserves its single worker for the complete speech
turn. Queue wait is cancellation-aware but does not consume the synthesis-idle
deadline, so concurrent Lab/device sessions cannot alternate the model after every
sentence or fail merely because another response is still speaking.

The latest cancellation run sent abort on the first downlink frame. `tts:stop` and
the next `listen:start` arrived in about 6.4 ms on loopback (earlier warm samples
were about 0.5--2.1 ms). At most two more
frames from the three-frame prebuffer were already on the wire; firmware closes
its local playback generation before sending abort, so those stale frames do not
reach the speaker. The paced sender now runs independently from TTS inference,
allowing synthesis and playback to overlap while keeping a configurable, bounded
five-second server queue. Lab sends PCM ahead but delays normal `tts.stop` until
the scheduled PCM duration has elapsed; cancelled stop clears browser playback
immediately.

The MCP full-loop run discovered the regular device catalog, mapped the Vietnamese
request to `self.audio_speaker.set_volume({"volume":55})`, normalized the device
result for the prose model and spoke back `55%`. The MCP cancellation run withheld
the device result, sent a button abort while `tools/call` was pending, then injected
the late result after `listen:start`. Loopback returned to listening in about 0.45
ms and emitted no stale LLM text, TTS or follow-up tool call. No `tts:stop` is
expected in this scenario because playback had not started; an abort during active
playback still follows the `tts:stop` contract above.

Prepare the native model pack (about 630 MiB; still ignored by Git):

```bash
cd veetee-server
npm run models:prepare-native
```

The native build cache and ONNX Runtime C++ SDK are intentionally kept under
`veetee-server/.cache/local-ai/`. They are not application dependencies and are
not copied into firmware or committed to the repository. Native mode fails
readiness when the configured library or assets are missing; it never silently
falls back to another voice backend.

## Production decision and next optimization

1. Keep Zipformer + Silero on ONNX CPU; use VieNeu native CPU on this host and
   retain VieNeu ONNX as the portable compatibility backend.
2. Prewarm all three sessions during voice-server startup and expose each state
   through `/health/ready`.
3. Stream sentence-sized TTS chunks, clear the playback queue on arbiter abort,
   and never wait for a full LLM answer before starting the first sentence.
4. Upstream a native stream/progress callback with cooperative cancellation. The
   current bounded-batch adapter is realtime-safe through generation rejection,
   worker serialization and playback buffering, but cannot terminate C inference
   in the middle of a batch.
5. Install ChunkFormer in a separate environment only when a labeled Vietnamese
   noise/name/number corpus shows a meaningful WER gain. Its CC BY-NC license
   must remain visible in Manager before any redistribution or commercial use.

The speed target is measured end to end: VAD final -> ASR final <= 600 ms,
ASR final -> first LLM token <= 800 ms, first LLM token -> first TTS audio <=
700 ms, and user stop -> speaker silence <= 250 ms. A faster isolated model is
not an optimization if it breaks these cancellation and streaming gates.
