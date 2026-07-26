---
name: voice-server-review
description: Review or validate Veetee voice-server Python changes involving FastAPI, WebSocket/Opus transport, conversation state, cancellation, VAD/ASR, LLM providers, MCP or TTS. Use for code review, debugging and focused validation under veetee-server/apps/voice-server.
---

# Voice Server Review

## Read first

- `../../docs/02-system-architecture.md`
- `../../docs/04-protocol-compatibility.md`
- `../../docs/05-realtime-conversation.md`
- `../../docs/06-provider-and-mcp.md`
- `../../docs/14-model-and-provider-baseline.md`
- The closest implementation and tests.

## Review

1. Trace the turn from transport input through admission, optional MCP, LLM, TTS and paced output.
2. Check every provider operation has a deadline and cancellation path.
3. Verify abort increments or respects generation and all late output is dropped.
4. Keep Manager API off frame-by-frame audio and preserve WebSocket/MCP envelopes.
5. Reject exact-string semantic routing and central provider condition chains; use structured output and registries.
6. Check bounded queues, backpressure, readiness, redaction and stable client-facing error codes.
7. Report concurrency limits or native inference locks that affect session capacity.

## Validate

From `veetee-server/`:

```bash
npm run lint:voice
npm run test:voice
```

Run the smallest relevant test first, for example:

```bash
uv run --project apps/voice-server pytest \
  apps/voice-server/tests/test_conversation_engine.py -q
```

Run `npm run test:voice:local-e2e` only when the local model/runtime contract is affected and its prerequisites are available.

Report findings, commands run, results and any live-audio or hardware validation still missing. Do not edit providers, start model workers or expose credentials during a review unless explicitly requested.
