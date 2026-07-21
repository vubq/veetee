# voice-server

Hot path WebSocket/Opus và conversation engines. App này không phụ thuộc manager API cho mỗi audio frame; config được tải theo immutable snapshot/version.

Milestone đầu: mock input-admission/VAD/ASR -> semantic gate -> OpenAI-compatible
LLM -> one Vietnamese TTS -> MCP timeout/cancellation/abort tests. Baseline local
được chốt trong `docs/14-model-and-provider-baseline.md`: Silero VAD, Zipformer
Vietnamese INT8 primary, ChunkFormer quality re-decode, VieNeu-TTS v3 Turbo và
`openai-compatible-9router` dev adapter.

Conversation mặc định là `mode=auto`: button/wake word chỉ mở assistant gate; VAD tự finalize, admission gate quyết định có gọi LLM/MCP, inactivity timeout phát goodbye rồi sleep.
