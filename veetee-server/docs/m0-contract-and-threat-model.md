# M0.6 Contract, threat model và parity backlog

## 1. Module boundary

M0 khóa boundary, không tạo production implementation:

```text
device gateway
  -> protocol codec + schema validation
  -> session/turn state machine
  -> audio ingress/egress + backpressure
  -> speech pipeline (VAD -> ASR -> intent -> LLM -> TTS)
  -> provider adapters (OmniRoute / Gemini native / local models)
  -> tools/MCP + policy
  -> persistence/control plane/observability
```

Quy tắc ownership:

| Module | Được biết | Không được biết |
| --- | --- | --- |
| Protocol codec | wire version, headers, JSON/binary envelope | provider, DB schema, prompt |
| Session state | device/session/turn/generation lifecycle | raw provider credentials |
| Audio pipeline | PCM/Opus, queue, pacing, cancellation | REST/admin persistence details |
| Provider adapter | typed request/event/error, deadline, cancellation | device socket, direct tool authority |
| Tool/MCP policy | allowlist, auth, confirmation, audit | model internals, unrestricted network |
| Persistence | tenant/device/session metadata, retention | raw API key, unredacted audio by default |
| Observability | stable metric/event/correlation fields | secret, raw prompt/audio/payload |

## 2. Provider event contract

Adapter stream phát event typed theo thứ tự có thể kiểm tra:

```text
started -> delta* -> tool_call* -> usage? -> completed
                     \-> failed
cancel_requested -> cancelled
```

- `started`: provider/model, request ID nội bộ, timestamp; không có prompt/secret.
- `delta`: text hoặc reasoning đã được phân loại; reasoning không đi vào TTS.
- `tool_call`: tool name + JSON fragment, phải merge/validate trước execute.
- `usage`: prompt/output/reasoning token nếu provider trả.
- `completed`: final normalized text và finish reason.
- `failed`: typed code, retryability, provider status; message redacted.
- `cancelled`: xác nhận transport đã đóng; không tuyên bố upstream compute đã hủy nếu chưa
  có bằng chứng.

## 3. Error taxonomy

| Code | Retry | Action |
| --- | --- | --- |
| `veetee_invalid_input` | No | reject schema/size/version |
| `veetee_auth_failed` | No/scope | close or re-auth, audit |
| `veetee_timeout` | bounded | cancel children, maybe retry idempotent |
| `veetee_client_gone` | No | cleanup session/stream |
| `veetee_provider_rate_limited` | After cooldown | honor Retry-After/backoff |
| `veetee_provider_unavailable` | bounded | fallback/circuit breaker |
| `veetee_provider_malformed` | No immediate | fail closed, record safe metric |
| `veetee_tool_denied` | No | user/policy decision |
| `veetee_ota_rejected` | No | signature/version/target mismatch |
| `veetee_internal` | No public detail | correlation + redacted log |

## 4. Cancellation and cleanup

`abort`, disconnect, total deadline hoặc provider cancellation đều phải đi qua một
`CancellationScope` của turn:

1. Mark generation stale atomically.
2. Cancel ASR/LLM/TTS/provider/tool children.
3. Stop accepting audio for stale generation.
4. Drain/drop stale token and audio queues.
5. Close provider response and release key/model semaphore in `finally`.
6. Emit one terminal event and retain correlation metadata theo retention policy.

## 5. Threat model

| Asset | Threat | Boundary/control |
| --- | --- | --- |
| Device credential | theft/replay/impersonation | TLS, scoped opaque credential, rotation, nonce, rate limit |
| WebSocket | malformed/oversized binary, slow client, session confusion | schema/size limits, bounded queue, session binding, timeout |
| Audio/transcript | privacy leakage and retention overreach | no raw log, explicit retention, delete/export, tenant isolation |
| OmniRoute | key exposure, prompt leakage, outage, cache confusion | env/secret ref, redaction, timeout, cache key includes options, breaker |
| Gemini keys | quota theft, invalid key, concurrent exhaustion | local secret source, least-in-flight, cooldown, circuit breaker |
| Tool/MCP | prompt injection, SSRF, destructive action | allowlist, schema, auth, confirmation, egress policy, audit |
| OTA | unsigned/downgrade/replay artifact | signature, target/version policy, hash, staged rollout, rollback |
| PostgreSQL | injection, tenant escape, accidental retention | parameterized access, tenant predicates, migrations, backup/restore |
| Control plane | admin takeover and abuse | RBAC, CSRF/session policy, rate limit, audit, safe error |
| Logs/metrics | secret or PII leakage | structured redaction, cardinality limits, access control |

Security tests must include malformed JSON, truncated audio header, invalid URL/scheme,
oversized upload/frame, wrong device/session ID, duplicate abort, stale generation, tool
argument injection, provider 401/403/429/5xx and secret-pattern scan.

## 6. Parity backlog với dependency

| ID | Capability | Mốc | Dependency | Acceptance |
| --- | --- | --- | --- | --- |
| P0 | Namespace + device WS/OTA contract | M0 | M0.1-M0.4 | approved paths, golden vectors, scan |
| P1 | Fake provider/session E2E | M1 | P0 | hello/listen/audio/STT/TTS/abort |
| P2 | Opus codec/backpressure | M1 | P0 | malformed/slow-client tests |
| P3 | Minimal OTA responder | M1 | P0 | device discovery and safe no-update |
| P4 | Local VAD/ASR | M2 | P1, M0.5 | latency + real audio evaluation |
| P5 | OmniRoute LLM adapter | M2 | P1, M0.3 | streaming/tool/usage/cancel |
| P6 | Gemini TTS key pool | M2 | P1, M0.4 | fake fault matrix + no replay |
| P7 | Prompt/intent/tool registry | M3 | P5/P6 | policy, confirmation, audit |
| P8 | Device MCP | M3 | P7 | JSON-RPC pagination/correlation |
| P9 | PostgreSQL persistence | M4 | P1/P7 | migration, tenant, retention |
| P10 | Console/control plane | M4 | P9 | RBAC, device/model/agent management |
| P11 | Activation/binding/OTA rollout | M5 | P3/P9/P10 | signature, rotation, rollback |
| P12 | Parity expansion | M6 | P7-P11 | each feature has explicit decision |
| P13 | Hardening/release | M7 | P0-P12 | fuzz/load/soak/backup/E2E |

## 7. Quyết định còn mở

- PhoWhisper WER/CER và calibration VAD trên audio có quyền sử dụng.
- Voice/style prompt và PCM metadata Gemini chính thức.
- Secret manager source production và key rotation mechanism.
- Python package/ORM/migration tool sau khi source M1 được tạo.
- Chính sách transcript/audio retention mặc định.
- Có đưa MQTT/UDP vào parity hay chỉ WebSocket.
- Embedding/retrieval implementation và memory retention.
