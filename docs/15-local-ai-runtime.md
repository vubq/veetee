# Local AI runtime and speed baseline

This document records the local speech stack actually installed on the Veetee
development machine. It is deliberately separate from the provider contract: a
model can be replaced without changing the ESP32 wire protocol or conversation
state machine.

## Host profile

The benchmark host has an Intel i5-10300H (4 physical / 8 logical CPU threads),
15 GiB RAM and an NVIDIA GTX 1650 Ti with 4 GiB VRAM. The production environment
uses ONNX Runtime CPU. A separate CUDA 12 / ONNX Runtime GPU environment was also
measured against the same models and fixed random seeds; it did not improve this
INT8 workload on the GTX 1650 Ti.

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
| TTS default | VieNeu-TTS v3 Turbo ONNX INT8 | CPU, 2 threads, streaming codec | best measured p95/RTF balance, lower thermal load, incremental audio and cancellation |
| TTS benchmark option | VieNeu-TTS.cpp native CPU | llama.cpp native SIMD + ONNX MOSS codec | faster complete synthesis on this host, but the current C ABI is batch-only |

The default TTS remains the ONNX path even though the native benchmark is faster
for complete audio. Realtime conversation requires incremental audio and a clear
barge-in cancellation boundary; selecting a batch-only native call would make
that behavior worse. Native C++ is therefore an opt-in worker profile until its
stream callback and cancellation API are implemented and benchmarked.

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

| Backend | Threads | First audio median / p95 | Complete median / p95 | RTF median / p95 |
| --- | ---: | ---: | ---: | ---: |
| VieNeu ONNX INT8 CPU | 2 | 521 / 596 ms | 3.68 / 3.94 s | 1.124 / 1.202 |
| VieNeu ONNX INT8 CUDA with CPU fallback | 2 | 696 / 1,365 ms | 4.06 / 5.05 s | 1.303 / 1.804 |
| VieNeu native C++ CPU | 4 | batch-only | 2.22 s historical run | 0.75 historical run |

The CUDA graph produced many CPU/GPU copy boundaries, used only about 4--10% GPU,
and peaked near 1 GiB VRAM during the sampled run. The current VieNeu INT8 export
is therefore kept on CPU. Revisit CUDA only with a GPU-oriented FP16/FP32 export
or a newer engine that keeps the recurrent decode path on the GPU.

The native run used a 630 MiB model pack and peaked at about 959 MiB RSS. A
one-shot CLI process took about 5.4 s wall time because model loading dominates;
production must keep the engine resident. The native profile currently exposes
`vieneu_synthesize_v3()` only, so it cannot meet the same first-audio and
user-stop guarantees as the ONNX streaming profile.

### Thread sweep for local speech

The ASR sweep used 20 warmed runs. The TTS sweep on 2026-07-22 used five warmed,
fixed-seed runs with the production watermark enabled:

| Threads | ASR median / p95 | TTS first audio median / p95 | TTS RTF median / p95 |
| ---: | ---: | ---: | ---: |
| 1 | 46.59 / 53.84 ms | not selected | not selected |
| 2 | 38.06 / 44.37 ms | 521 / 596 ms | 1.124 / 1.202 |
| 4 | 61.83 / 81.26 ms | 533 / 625 ms | 1.215 / 1.297 |
| 6 | 82.32 / 141.58 ms | 516 / 654 ms | 1.239 / 1.348 |
| 8 | 108.28 / 158.63 ms | not selected | not selected |

The selected profile uses two ASR threads and two TTS threads. Six TTS threads had
a similar median first-audio result but worse p95, worse complete RTF and much
higher sustained CPU temperature. More logical threads do not improve this
recurrent INT8 workload on the four-core host.

## Runtime controls

```env
VEETEE_MODELS_ROOT=models
VEETEE_ASR_THREADS=2
VEETEE_TTS_THREADS=2
VEETEE_TTS_VOICE="Trúc Ly"
VEETEE_TTS_OUTPUT_SAMPLE_RATE=24000
VEETEE_TTS_APPLY_WATERMARK=true
VEETEE_LLM_PREWARM=true
VEETEE_LLM_PREWARM_SECONDS=12
VEETEE_PLANNER_SECONDS=8
VEETEE_9ROUTER_MODEL=cx/gpt-5.6-terra
VEETEE_DEFAULT_PERSONA="You are Veetee, a concise voice assistant. Reply in the user's language using one to three short spoken sentences. Do not use Markdown or expose hidden reasoning."
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
  --asr-threads 2 --tts-threads 2 --watermark --runs 5 --seed 20260722
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
bounded TTS operation context while the device sees one continuous
`tts:start`/audio/`tts:stop` lifecycle.

The latest cancellation run sent abort on the first downlink frame. `tts:stop` and
the next `listen:start` arrived in about 6.4 ms on loopback (earlier warm samples
were about 0.5--2.1 ms). At most two more
frames from the three-frame prebuffer were already on the wire; firmware closes
its local playback generation before sending abort, so those stale frames do not
reach the speaker. The paced sender now runs independently from TTS inference,
allowing synthesis and playback to overlap while keeping a bounded 12-frame
server queue.

The MCP full-loop run discovered the regular device catalog, mapped the Vietnamese
request to `self.audio_speaker.set_volume({"volume":55})`, normalized the device
result for the prose model and spoke back `55%`. The MCP cancellation run withheld
the device result, sent a button abort while `tools/call` was pending, then injected
the late result after `listen:start`. Loopback returned to listening in about 0.45
ms and emitted no stale LLM text, TTS or follow-up tool call. No `tts:stop` is
expected in this scenario because playback had not started; an abort during active
playback still follows the `tts:stop` contract above.

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
