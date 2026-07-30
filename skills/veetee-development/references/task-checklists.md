# Task checklists

## Firmware

- Verify board pins and target before editing.
- Use bounded queues and avoid repeated large allocations in audio paths.
- Schedule application mutations from callbacks.
- Validate JSON/binary input and preserve ownership/lifetime.
- Test boot, Wi-Fi fallback, activation, reconnect, auto conversation, assistant gate, abort and wake flow.
- Verify button wake and activation wake word enter the same auto state path.
- Verify interrupt profile and button abort evaluating/LLM/TTS/MCP through the same cancellation path.
- Verify VAD finalization starts the AI turn without a second button press; keep manual/PTT compatibility-only.
- Report what still needs physical hardware.

## Voice server

- Isolate per-session mutable state.
- Add cancellation/deadline to ASR, LLM, TTS and tools.
- Prevent late turn output after abort.
- Keep semantic intents model-driven and schema-validated; do not add exact transcript string branches.
- Require general input admission before LLM/MCP; do not add rules tied to named noise/media sources.
- Test non-actionable/not-addressed/unclear input, inactivity goodbye and closing-grace cancellation.
- Cancel MCP with the turn and reject stale results after abort.
- For config/artifact tasks, validate target/size/hash/signature/ABI, desired-vs-reported state and rollback/power-loss behavior.
- Keep provider SDK behind an adapter/capability interface.
- Redact secrets/transcripts and keep metric labels bounded.
- Test malformed frames, provider timeout and connection drop.
- Before host Voice startup, sync env and verify `OPENBLAS_NUM_THREADS=1` exists in the
  live process; distinguish it from the ONNX TTS thread setting.
- For long-speech acceptance, test at least 300 seconds of representative PCM plus at
  least three normal follow-up turns; 300--600 seconds is a soak window, not a product
  cap, so output over 600 seconds must not be truncated while progress continues. Also
  run a synthetic progressive stream equivalent to over 10 minutes with bounded
  queue/context, and report interval CPU, RSS/thread plateau, gaps/errors and the
  separate physical browser/ESP32 listening gap.
- Preserve the LLM terminal `finish_reason`: `length`/`max_tokens` is incomplete,
  must be reported after partial TTS drains, and must not commit partial context/memory.
  Use resumable segments for generated output beyond one provider request and stream
  source text through the chunker/TTS for arbitrarily long files.

## Manager API

- Apply tenant ownership guard.
- Validate DTO and return stable error codes.
- Use transaction/idempotency for pairing, publish and rollout.
- Encrypt provider credentials and never return raw secrets.
- Audit privileged mutations.
- Add migration and integration test for data changes.
- Never buffer large artifacts in API memory; use scoped object-store upload/download URLs.
- Publish only immutable signed manifests; capability and rollout checks run before publish.

## Manager Web

- Use generated API types and server-state query cache.
- Keep all user text in locale resources.
- Make pairing, provider test and privileged tool states explicit.
- Confirm destructive/user-only operations.
- Verify desktop and mobile with keyboard/accessibility flows.
- Cover success, loading, empty, expired and error states.
- Show artifact scan/signature/compatibility, flash budget, desired-vs-reported drift and canary/rollback state.
