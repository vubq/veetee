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
