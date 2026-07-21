# Veetee project instructions

## Scope

This workspace contains the Veetee blueprint, source scaffolds, contract fixtures and manager-web prototype. `references/xiaozhi-esp32` and `references/xiaozhi-esp32-server` are read-only comparison sources.

## Required reading

- `README.md` for project boundaries.
- `docs/01-xiaozhi-audit.md` before porting behavior.
- `docs/03-firmware-spec.md` for ESP32-S3 hardware and state rules.
- `docs/04-protocol-compatibility.md` for wire contract changes.
- `docs/05-realtime-conversation.md` for auto conversation, assistant gate, cancellation, wake and AEC.
- `docs/11-ai-first-design-principles.md` before adding intent, behavior, routing or hard-coded product rules.
- `docs/12-dynamic-config-and-artifacts.md` before changing wake profiles, device config, models, assets, OTA resources or artifact rollout.
- `docs/08-roadmap.md` for milestone order.
- `skills/veetee-development/SKILL.md` for AI task workflow.

## Hard rules

- Do not edit either directory under `references/`.
- Keep WebSocket/OTA/MCP wire compatibility unless a versioned contract change is explicit.
- Ship only canonical `/veetee/...` product routes. Reference-client migration belongs in an optional external gateway rewrite, not a branded route in Veetee source.
- Keep manager-api off the frame-by-frame audio path.
- Keep `listen.mode=auto` as the default experience: VAD finalizes speech and AI responds without a second button press. Manual/PTT is compatibility-only.
- Treat button wake and activation wake word as two inputs to the same conversation flow. Button/interrupt profile must share one abort/cancellation path.
- Require a general input-admission/relevance decision before LLM or MCP; do not hard-code source-specific audio cases.
- Keep inactivity timeout, closing grace and provider/MCP deadlines explicit and independently tested.
- Manage device config as immutable desired/reported versions. WebSocket may invalidate, but firmware pulls signed config/artifacts over HTTP(S).
- Never place arbitrary native executable/plugin code in a resource bundle. Runtime/operator changes require signed firmware OTA.
- Keep an active resource slot until the inactive bundle is fully verified and health-checked; rollback must survive power loss.
- Do not hard-code semantic intent, phrases, persona, provider selection or locale behavior. Keep deterministic code only for hardware, protocol, safety, security, resource bounds and recovery.
- Treat the pin map in `docs/03-firmware-spec.md` as provisional until the physical board is measured.
- Do not require a purchased domain for local development; use configurable LAN URLs and bootstrap discovery.
- Never commit API keys, activation secrets, transcript/audio dumps or production certificates.
- Add tests and docs with every behavior/contract change.
- Report hardware validation separately from host/build validation.

## Preferred task shape

Implement one vertical slice with a small diff, a fixture or test, and a short handoff. Avoid wholesale repository ports and unrelated formatting.
