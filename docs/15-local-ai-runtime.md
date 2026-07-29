# Local AI runtime and speed baseline

This document records the local speech stack installed for Veetee. It is separate from
the provider contract: models and providers can change without changing the ESP32
WebSocket/Opus protocol or conversation state machine.

## Host and selected runtime

The benchmark host has an Intel i5-10300H (4 physical / 8 logical CPU threads), 15 GiB
RAM and an NVIDIA GTX 1650 Ti with 4 GiB VRAM. VAD/ASR, the MOSS codec and the current
host TTS use ONNX Runtime CPU. VieNeu native C++ CPU is an optional batch-only profile.
The measured CUDA environment did not improve this workload on the GTX 1650 Ti.

All model workers run directly in the voice-server virtual environment. Docker is not
required for speech inference. Model files are under `veetee-server/models/` and are
ignored by Git; preparation scripts verify their hashes before a worker uses them.

| Stage | Runtime | Baseline | Rationale |
| --- | --- | --- | --- |
| VAD/endpoint | Silero VAD ONNX | CPU, one recurrent state per session | deterministic endpoint signal |
| ASR primary | Sherpa-ONNX Zipformer Vietnamese 30M INT8 | CPU, 2 threads | low RTF and Vietnamese streaming decode |
| ASR fallback | ChunkFormer-CTC-Large-Vie | not installed by default | heavy checkpoint and license gate |
| TTS current baseline | VieNeu-TTS v3 Turbo ONNX INT8 | CPU, 2 threads, Trúc Ly, neutral 1.0x, 16-frame lead-in | best portable/intelligibility profile on this host |
| TTS optional | VieNeu-TTS.cpp native CPU | batch-only, serialized | use only after explicit native benchmark |

The repository example and known-good runtime baseline use `OPENBLAS_NUM_THREADS=1`,
`VEETEE_TTS_BACKEND=onnx` and `VEETEE_TTS_THREADS=2`. The OpenBLAS cap is a process
environment setting that must exist before NumPy is imported; the TTS setting only
limits ONNX Runtime and does not constrain NumPy/OpenBLAS workers. Do not infer the
active backend from this document alone: the ignored `apps/voice-server/.env` and the
published Manager agent profile determine the effective session behavior.

For both VieNeu backends, complete short sentences are grouped into natural TTS batches
(ONNX 160, native 72 characters). Normal boundaries remain confirmed sentence
terminators. The final unpunctuated remainder joins the pending batch when it fits.
Pathological punctuation-free output uses an emergency bound (ONNX 256, native 72) and
prefers a whitespace boundary. This avoids splitting a phrase such as `khó khăn` at a
character pacing threshold. Native C inference remains serial and cannot be force-killed
mid-batch; a cancelled output can therefore leave a worker using CPU briefly.

## Measured results

Measurements use warmed models, five runs per profile and fixed seeds.

### ASR

| Model | Audio | Warm decode | RTF | Output |
| --- | ---: | ---: | ---: | --- |
| Zipformer Vietnamese 30M INT8 | 1.55 s | 38.06 ms median / 44.37 ms p95 at 2 threads | 0.025 median | `ÂM LƯỢNG TV GIẢM` |

Keep the recognizer resident; do not load ChunkFormer on the normal path.

### TTS

| Backend | Threads | First audio median / p95 | Complete median / p95 | RTF median / p95 |
| --- | ---: | ---: | ---: | ---: |
| VieNeu ONNX INT8 CPU, stream lead-in 16 | 2 | 1.56 / 1.72 s | 3.98 / 4.24 s | 1.148 / 1.205 |
| VieNeu ONNX INT8 CUDA with CPU fallback | 2 | 696 / 1,365 ms | 4.06 / 5.05 s | 1.303 / 1.804 |
| VieNeu native C++ CPU, 75-character probe | 4 | batch complete in 3.22 s | 3.22 s | 0.745 |
| VieNeu native C++ CPU, 190-character probe | 4 | batch complete in 9.65 s | 9.65 s | 0.957 |

A post-fix ten-run ONNX check measured first audio at 1.56 s median / 1.72 s p95,
complete synthesis at 3.98 s median and no estimated playback starvation. The old 1.5x
profile shortened the output buffer but caused gaps because autoregressive generation
remained slower than the speaker clock. Tempo post-processing does not make inference
faster.

Native initialization can peak near 1.68 GiB RSS with the 630 MiB model pack. The
native 190-character one-shot probe took 9.65 s in synthesis, so native uses the smaller
72-character bound and a resident context.

### Thread selection

| Threads | ASR median / p95 | TTS first audio median / p95 | TTS RTF median / p95 |
| ---: | ---: | ---: | ---: |
| 1 | 46.59 / 53.84 ms | not selected | not selected |
| 2 | 38.06 / 44.37 ms | 521 / 596 ms | 1.124 / 1.202 |
| 4 | 61.83 / 81.26 ms | 533 / 625 ms | 1.215 / 1.297 |
| 6 | 82.32 / 141.58 ms | 516 / 654 ms | 1.239 / 1.348 |
| 8 | 108.28 / 158.63 ms | not selected | not selected |

Six ONNX TTS threads had similar median first audio but worse p95/RTF and much higher
sustained CPU temperature. Keep ONNX at two TTS threads. Select native four threads
only after confirming its model pack and measuring sustained host CPU.

This table controls ONNX Runtime threads only. NumPy matrix work uses OpenBLAS and has a
separate process-wide budget; without `OPENBLAS_NUM_THREADS=1`, the two-thread ONNX row
can still consume nearly all logical CPUs. Always record both values in a benchmark.

### Host power profile

The CPU power profile is part of the measured local runtime, not a TTS thread setting.
On 2026-07-29 this host was plugged into AC but manually left in `power-saver`, with all
logical CPUs observed near 900 MHz. A fresh real-prewarm plus three-request run then had
first audio 3.908 s median, RTF 2.695 and 2.746 s estimated starvation. Under
`performance`, the same ONNX/2-thread/Trúc Ly/1.0x process measured first audio 1.047 s
median, RTF 0.804 and zero estimated starvation. That short comparison isolated a power
profile problem; it did not test long-run OpenBLAS oversubscription.

A same-profile load-only control under `performance` also measured RTF 0.805. Therefore
real prewarm is a functional readiness/observability check, not a way to make inherently
slow inference faster; the active power profile was the cause of this measured
slow/high-CPU incident. CPU percentage alone is misleading: `performance` used more
aggregate CPU while active but completed each request much sooner. For realtime
acceptance on this host, record `powerprofilesctl get` and intentionally use
`performance` while on AC. Do not compensate for `power-saver` by increasing TTS threads,
lead-in, chunk size or playback buffer.

A later five-minute soak on the same host found a second, independent runtime problem:
without an explicit OpenBLAS cap, NumPy opened about seven additional BLAS workers even
though ONNX was configured for two threads. The Voice process then used nearly all eight
logical CPUs, heated the package into the 92--95 C range and thermally throttled. Keep the
power profile as a recorded benchmark precondition, but always verify
`OPENBLAS_NUM_THREADS=1` before attributing slow/high CPU only to the power profile.

## Runtime controls

The checked-in example and known-good portable baseline use the following values:

```env
OPENBLAS_NUM_THREADS=1
VEETEE_MODELS_ROOT=models
VEETEE_ASR_THREADS=2
VEETEE_VAD_THREADS=1
VEETEE_TTS_THREADS=2
VEETEE_TTS_BACKEND=onnx
VEETEE_TTS_VOICE="Trúc Ly"
VEETEE_TTS_STYLE=tu_nhien
VEETEE_TTS_SPEED=1.0
VEETEE_TTS_STREAM_LEADIN_FRAMES=16
VEETEE_TTS_OUTPUT_SAMPLE_RATE=24000
VEETEE_TTS_APPLY_WATERMARK=true
VEETEE_TTS_NATIVE_MODEL_DIR=models/vieneu-v3-turbo-native
VEETEE_TTS_NATIVE_LIBRARY_PATH=.cache/local-ai/VieNeu-TTS.cpp/build-cpu/libvieneu-tts.so
VEETEE_TTS_NATIVE_REALTIME_HEADROOM=1.15
VEETEE_TTS_NATIVE_USE_REF_CODES=true
VEETEE_TTS_PLAYBACK_QUEUE_SECONDS=5
VEETEE_LLM_PREWARM=true
VEETEE_LLM_PREWARM_SECONDS=12
VEETEE_PLANNER_SECONDS=15
VEETEE_CLIPROXY_BASE_URL=http://127.0.0.1:8317/v1
VEETEE_CLIPROXY_MODEL=gpt-5.6-terra
```

Prepare the default stack:

```bash
cd veetee-server
npm run env:voice:sync
npm run models:prepare
npm run models:benchmark
```

`env:voice:sync` renders the ignored voice environment from `.env.example`; it may
reset local backend/thread edits. Run it only after the existing Manager `.env` is valid,
then verify the OpenBLAS cap, effective backend, thread count, speed and model paths with
a non-secret allowlist rather than printing the file. The generated file is mode `0600`
and the sync command does not print credentials. `npm run dev:voice` pins
`OPENBLAS_NUM_THREADS=1` in the command and passes this file to `uv --env-file`, so the
cap exists before Python imports NumPy even if the parent shell contains another value.
The sync reads the CLIProxyAPI client key from the existing trusted local config, writes
only `VEETEE_CLIPROXY_*` gateway settings and does not read or require a 9Router store.

The active local profile selected on 2026-07-29 keeps 9Router paused and publishes the
agent LLM chain as `openai-compatible-cliproxyapi` on `http://127.0.0.1:8317/v1`, then
`groq-cloud` as fallback. After sync, start Voice directly from `veetee-server`:

```bash
npm run dev:voice
```

The bare `dev:voice`, `test:voice:local-e2e` and `models:benchmark` scripts all pin this
baseline before Python starts. A deliberate OpenBLAS A/B must invoke the underlying
`uv run ...` command with one explicit candidate at a time and report that it bypassed
the bare-command default; do not edit the accepted default just to collect a probe.

Do not pass the gateway key on the command line and do not expose port `8317` through
LAN, Tailscale Serve/Funnel or public ingress. CLIProxyAPI currently authenticates
clients even though its host process listens on more than loopback; Veetee calls it only
through `127.0.0.1`. Port `20128` is not part of this startup profile.

To deliberately test native:

```bash
cd veetee-server
npm run models:prepare-native
# Set VEETEE_TTS_BACKEND=native and VEETEE_TTS_THREADS=4 in apps/voice-server/.env.
npm run dev:voice
```

Native mode fails readiness when the configured model or shared library is missing; it
does not silently fall back to ONNX.

The current provider quality guard warns at speed `>= 1.2x`: WSOLA can reduce Vietnamese
consonant/tone clarity and cannot compensate for an inference rate slower than playback.
PCM volume above `1.0` can clip. Manager-published voice rate/style/volume settings may
override the process defaults for a session, but do not change the process-wide backend
or thread count.

## Startup, readiness and reboot behavior

Voice-server startup prewarms ASR, TTS and (when enabled) LLM concurrently before ready.
VieNeu readiness now executes one bounded fixed-phrase synthesis through the normal
phonemization, inference, codec, watermark, resampling and PCM conversion path. The PCM
is fully drained and discarded inside the provider; it is never emitted to Lab,
WebSocket, Opus, browser, device or speaker. Startup fails if that synthesis produces no
audio. A successful process emits one `vieneu_tts_prewarm_complete` event containing
bounded profile/timing/count metadata, never the phrase or audio.

High CPU before `vieneu_tts_prewarm_complete` is expected because startup is executing a
complete synthesis and loading model state. The event proves the path produced PCM but
does not by itself prove realtime throughput; inspect its RTF and run the fixed benchmark.
Sustained idle CPU after `/health/ready` is `200`, a first user request that repeats RTF
`2--3` / first-audio `3--4` seconds, or a delayed `vieneu_tts_completed` after cancelled
playback indicates an inference/deadline or host-performance issue rather than normal
reboot cost. On the baseline host, check the power profile before changing application
settings.

The repository does not provide a voice-server boot service. Voice startup is a
foreground command and relative `.env`/model paths require `veetee-server` as the
working directory. Docker Compose starts only PostgreSQL/Redis (and optional MinIO),
not voice-server, Manager API/Web, CLIProxyAPI, ASR, VAD or TTS. CLIProxyAPI is an
external dependency and must pass authenticated model/inference prewarm before Voice
readiness can pass. Plain `npm run dev:voice` uses the `VEETEE_CLIPROXY_*` values rendered
by `env:voice:sync`; 9Router remains paused.

Published Manager agent settings are loaded again after process restart. An effective
`totalTurnSeconds > 0` is an absolute parent deadline and can stop a progressing turn;
keep it at `0` unless a product policy explicitly requires a ceiling. Low TTS first-audio
or idle deadlines can also cancel output when CPU contention or a large batch delays the
next audio event.

Use `docs/21-local-development-runbook.md` for the complete cold-start order, health
checks, safe process inspection, authorized live Lab probe design and AI handoff prompt.

## Benchmark and full-loop validation

The benchmark accepts separate controls:

```bash
uv run --project apps/voice-server python scripts/benchmark_local_ai.py \
  --asr-threads 2 --tts-threads 2 --voice "Trúc Ly" --speed 1.0 \
  --tts-stream-leadin-frames 16 --watermark --runs 5 --seed 20260722
```

The host E2E helper exercises an isolated loopback server and always stops it; it does
not restart or weaken the LAN service on port 8000:

```bash
cd veetee-server
npm run test:voice:local-e2e
```

This helper uses the device `/veetee/v1/` path with temporary auth disabled. The
authorized live Realtime Lab path is different: Manager login issues a one-use token,
then the client connects to `/veetee/lab/v1/` and sends a `lab.auth` frame. The runbook
describes that path and its unavoidable audit/rate-limit side effects.

A clean LLM gateway can still take several seconds for its first structured call.
Readiness remains false while the required endpoint is unavailable; a later readiness
probe retries bounded prewarm without restarting the server. The active CLIProxyAPI
route has a five-second first-token budget for the published agent, so an occasional
`provider_deadline` is an upstream-cycle failure, not evidence that TTS should receive
more threads or buffering.

The first-audio watchdog applies only until the speech turn emits its first non-empty
PCM. Later provider batches use the synthesis-idle watchdog while the device sees one
continuous `tts:start`/audio/`tts:stop` lifecycle. Queue wait is cancellation-aware and
does not consume the synthesis-idle deadline. The bounded five-second server queue and
three-frame prebuffer protect pacing but cannot repair a TTS generator slower than the
speaker clock.

The long 2,352-character fixed-text diagnostic compared all-160 sentence batching with
first-160/steady-256. Hybrid reduced outer requests and actual internal starts from 23 to
12 and first audio from 1.544 s to 1.415 s, but aggregate RTF regressed from 0.858 to
0.870; both estimated zero schedule gaps. Therefore steady-256 did not pass the rollout
gate and was removed. ONNX remains at a 160-character natural sentence-batch cap with a
256-character emergency bound only for pathological punctuation-free output.

### Dated acceptance evidence

Historical 2026-07-22 device-loopback E2E covered authenticated/canonical Opus uplink,
Silero, Zipformer, structured admission/planning, 9Router, VieNeu, paced Opus downlink,
first-audio abort, MCP pending/late result cancellation, inactivity goodbye and wake
during goodbye. No stale MCP/text/TTS output was observed. This is host wire evidence,
not a physical speaker/AEC pass. The 2026-07-24 native probes ranged about 1.07--1.44 s
for 24 characters, 1.95--2.13 s for 50 and 3.32--3.79 s for 82, with RTF about
0.72--0.75; native still remains optional because the C call is batch-only and cannot
cooperatively stop mid-inference.

On 2026-07-29, after real synthesis prewarm and with the host intentionally set to
`performance`, a fresh three-request ONNX/2-thread run measured first audio 1.047 s
median / 1.114 s p95, RTF 0.804 / 0.814 p95 and zero estimated starvation. A separate
2,374-character fixture (`sha256[:16]=ff611923af5ccce5`) used 22 natural batches, reached
first audio in 1.260 s, synthesized 136.56 s of audio at aggregate RTF 0.812 and estimated
zero starvation. The isolated canonical Opus E2E also completed one `tts.start`/`tts.stop`
lifecycle with VieNeu request RTF 0.817. Its configured Codex route was unavailable and
the conversation used the bounded semantic fallback, so that run is transport/TTS
evidence, not canonical 9Router/LLM acceptance.

The same-day long-run A/B isolated OpenBLAS. With the cap absent, 324 s of generated
audio took 416.3 s wall time; Voice CPU average/p95/peak was 584/690/714%, the process
had 34 threads, and the Lab scheduler estimated 112 gaps totalling 62.4 s. With
`OPENBLAS_NUM_THREADS=1`, a comparable 306.96 s run took 313.7 s, CPU fell to
124/174/208%, the process held 27 threads, and the scheduler estimated zero gaps. CPU
work per audio second fell by about 83% while wall/audio improved from 1.285 to 1.022.
This A/B is the reason the cap is part of the checked-in runtime baseline; increasing
`VEETEE_TTS_THREADS` or changing the power profile does not substitute for it.

An end-to-end CLIProxyAPI -> VieNeu Lab soak then produced 308.56 s (5 min 8.6 s) of
24 kHz mono PCM in 323 frames with zero schedule gaps and no turn error. Planner and
prose both returned HTTP 200 from local CLIProxyAPI `gpt-5.6-terra`; 9Router remained
stopped and no Groq fallback was used. Voice CPU average/p95/peak was
120.9/174/188.3%; RSS moved from 1055.5 to 1058.8 MiB and flattened at the tail. The
first attempt had a prose `provider_deadline` at 4.999 s and is recorded as a failed
cycle; one controlled retry passed. Never hide a retry or average a failed provider
cycle into the passing soak.

The 2026-07-28 accepted live HTTPS Realtime Lab session used the current ONNX/2-thread,
Trúc Ly, `tu_nhien`, 1.0x profile. Two natural user turns completed with one
`tts.start`/`tts.stop` lifecycle each, no deadline, turn error or stale output. Their
turn-first-audio observations were about 1.18--1.72 s and both
`lab_playback_schedule_summary` records had `schedule_gap_count=0`, with estimated
low-water about 0.76--0.94 s. The user listening check judged the result acceptable.
This is subjective browser PCM acceptance, not a Vietnamese MOS score or ESP32 speaker
validation.

Two deliberately short headless text probes exercised the same HTTPS one-use Lab token,
WSS route and mobile-audio unlock logic. Both completed admission -> streaming LLM ->
`tts.start` -> first audio -> `tts.stop` -> listening with no deadline/error/stale output,
but exposed short-request variability: first audio 3.031/2.507 s, request wall RTF
1.953/1.695, and estimated schedule gaps 1.278 s/53 ms. The second warm probe measured
admission 661 ms and LLM first token 336 ms. These values are retained rather than
cherry-picked: schedule estimates can expose insufficient generation headroom, but a
headless browser cannot hear intelligibility and does not acknowledge real speaker
playback.

## Cutoff and CPU diagnosis

Prefer structured Voice Server logs and wire events over bounded telemetry. Correlate:

- startup `vieneu_tts_prewarm_complete` (`backend`, threads, profile, counts, duration,
  RTF); high CPU before this event is startup work, not a user turn
- `conversation_tts_text_chunk_ready` (`reason`, `text_characters`)
- `conversation_tts_request`, turn-level `conversation_tts_first_audio`, and
  `conversation_tts_batch_first_audio` (`reason`, character count, duration)
- `conversation_provider_deadline`
- `tts:start`, `tts:stop` with cancellation state, and `listen:start`
- `vieneu_tts_completed` (`request_wall_rtf`, normalized chunks, actual internal starts,
  clipping and backend profile)
- Lab `lab_playback_schedule_summary` and device `tts.paced_sender_summary`; these are
  schedule/starvation diagnostics, not measured speaker underruns
- device transport loss/abort and firmware playback queue diagnostics

Do not use the `%CPU` column from a single `ps` snapshot as instantaneous CPU. On Linux
it is a process-lifetime average and can decay slowly after a large synthesis burst. Use
`pidstat -p <voice-pid> 1` or another interval/delta sampler for the post-synthesis tail.
In the accepted long soak, instantaneous Voice CPU returned to approximately zero in
less than 0.5 s even while the lifetime average remained visibly high.

RSS also need not return to its prewarm value: resident model pages and the ONNX allocator
retain a high-water mark. Treat a flat 30--60 s tail or a plateau across repeated turns
as the expected resident state; diagnose a leak only when repeated comparable turns keep
raising the plateau. Likewise, the last binary PCM frame may precede terminal `tts.stop`
while Lab/device playback drains buffered audio. That interval is not continued inference
when interval CPU is idle and synthesis-complete events have already arrived.

A typical deadline signature is first audio, then `conversation_provider_deadline`, then
cancelled `tts.stop`; native work can continue briefly under its worker lock. A device
playback queue drop can truncate the speaker tail but cannot explain high host CPU.
Browser AudioContext failure can stop Lab playback while the server completes normally;
that is browser-side, not ESP32 speaker validation.

Conversation telemetry is bounded and may drop events under backpressure. Do not paste
transcript/audio payloads, Authorization headers, API keys or Manager tokens into issue
reports. Report the active branch/commit, process PID/CPU/RSS, ports, readiness bodies
with sensitive fields removed, event names/timings and the hardware-only gap separately.

## Production decision

1. Start Python with `OPENBLAS_NUM_THREADS=1`; keep ONNX TTS at two threads unless a
   same-host A/B benchmark replaces this baseline.
2. Keep Zipformer/Silero and baseline VieNeu ONNX on CPU; use native only after explicit
   host benchmark and model validation.
3. Prewarm model sessions and expose component state through `/health/ready`.
4. Keep sentence-sized TTS batching, bounded playback and generation rejection.
5. Add cooperative progress/cancellation to native C inference before making it the
   default realtime backend.

The end-to-end target is VAD final -> ASR final <= 600 ms, ASR final -> first LLM token
<= 800 ms, first LLM token -> first TTS audio <= 700 ms, and user stop -> speaker
silence <= 250 ms. A faster isolated model is not an optimization if it breaks
cancellation or streaming gates.
