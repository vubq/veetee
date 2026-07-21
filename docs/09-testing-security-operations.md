# Testing, security và vận hành

## 1. Test pyramid

### Firmware host tests

- JSON hello/message parser.
- Binary v2/v3 length/endian validation.
- State transition table.
- NVS migration/defaults.
- MCP schema/range/pagination.
- OTA manifest/version/signature parser.
- Config snapshot schema/ETag/desired-reported reconciliation.
- Resource manifest/member hash/ABI/capability/partition budget validation.
- Apply journal and power-loss recovery parser.

### Firmware hardware tests

- GPIO pin smoke; ST7789 color/offset/mirror.
- INMP441 noise floor, clipping, sample rate/slot.
- MAX98357A playback, volume curve, no underrun.
- AP -> station -> AP fallback.
- Button debounce/hold/interrupt.
- Activation wake accuracy in standby and interrupt-profile accuracy in standby/thinking; speaking interrupt is best-effort until AEC gate passes.
- 30 phút conversation loop và heap watermark.

### Server tests

- Contract fixture tests với firmware messages.
- Provider adapter conformance suite dùng fake server.
- Local model benchmark: Silero endpoint/admission, Zipformer primary, ChunkFormer
  conditional re-decode, VieNeu first-audio/RTF và 9router stream/tool/cancel.
- Turn cancellation/race tests.
- Input admission conformance: accepted/rejected/unclear/interrupt/end, reason bounded; dialogue-act tests riêng cho follow-up/confirmation/correction.
- Inactivity timeout, closing grace and wake-during-goodbye race tests.
- MCP cancellation/stale-result tests, including side effect completed after abort.
- WebSocket reconnect/timeout/malformed/flood.
- MCP authorization/SSRF/schema.
- Manager API tenant isolation/idempotency/migration.
- Playwright pairing/provider/publish/OTA flows.

## 2. End-to-end scenarios

```text
E2E-01 blank flash -> AP -> Wi-Fi -> activation -> idle
E2E-02 enable assistant -> Vietnamese speech -> VAD final -> streaming reply, không bấm lần hai
E2E-03 button wake and activation wake word -> same auto conversation flow
E2E-04 non-actionable/not-addressed/low-confidence input -> no LLM/MCP call
E2E-05 click or interrupt profile during ASR/LLM/TTS/MCP -> silence/cancel -> new turn
E2E-06 wake word -> conversation -> semantic “tạm biệt” -> close
E2E-07 inactivity -> localized goodbye -> standby; wake in closing grace cancels close
E2E-08 provider ASR/LLM/TTS/MCP timeout and fallback
E2E-09 Wi-Fi/server drop and reconnect
E2E-10 MCP regular vs user-only policy
E2E-11 signed OTA canary and rollback
E2E-12 config/resource publish -> device pull -> verify -> stage -> apply -> report
E2E-13 invalid signature/oversize/incompatible bundle -> keep active resource and report failure
E2E-14 power loss during resource download/apply -> recover active slot or rollback
E2E-15 Zipformer low-confidence -> ChunkFormer re-decode -> accepted transcript, cùng turn deadline
E2E-16 Zipformer stable -> không khởi chạy ChunkFormer
E2E-17 VieNeu batch/stream capability được phản ánh đúng, abort không phát audio stale
E2E-18 9router abort -> không còn token/tool/TTS stale; backup adapter chạy được khi health fail
```

Mỗi scenario lưu trace id, firmware log, voice-server events và manager audit.

Admission test corpus phải đa dạng về môi trường, speaker, media playback, khoảng cách, âm lượng, utterance hợp lệ/không hợp lệ và self-TTS echo. Các nguồn cụ thể chỉ là dataset examples; acceptance dựa trên admission decision/false-accept/false-reject, không dựa trên rule nhận diện tên nguồn.

## 3. Security baseline

### Device

- Không hard-code provider key hoặc manager admin token.
- Unique client UUID + device identity; secure boot/flash encryption khi production.
- Validate URL scheme/host cho OTA và MCP user-only action.
- Signed firmware; anti-rollback policy theo release channel.
- Signed config/resource manifest; SHA-256 payload và signature theo JCS + detached algorithm đã freeze.
- Resource bundle chỉ chứa data/model/assets; executable/runtime change phải qua signed firmware OTA.
- Không overwrite active resource slot trước khi inactive slot verify và health check xong.
- Capability, size, ABI và minimum firmware được kiểm tra cả API-side lẫn firmware-side.
- Rate limit activation/bootstrap và exponential backoff.

### Server/API

- CSPRNG activation code, TTL 5-10 phút, max attempt, atomic consume.
- JWT scoped theo device/client; rotate server secret không làm outage dài.
- Argon2id, refresh token rotation, RBAC + tenant guard.
- Encrypt provider credentials bằng KMS/master key ngoài database.
- Redact `Authorization`, API key, transcript/audio và tool secrets.
- Strict upload MIME/size/hash; MinIO bucket private.
- SSRF protection cho remote MCP, image/OTA URL và plugin HTTP.
- Object-store upload dùng short-lived scoped URL; scan magic/size/path traversal/zip bomb/model index.
- Signing private key nằm ngoài database, ưu tiên offline signer/HSM/Vault; có key rotation và revocation.

### Privacy

- Mặc định không lưu raw audio.
- Transcript retention configurable, có consent và delete API.
- Voiceprint/voice clone là opt-in, tách encryption/access policy.
- Audit xem/download/export dữ liệu nhạy cảm.

## 4. Observability

### Metrics tối thiểu

- `active_device_connections`.
- `turns_total{result,locale,engine}`.
- `first_audio_latency_ms` histogram.
- `abort_to_silence_ms` histogram.
- `input_admission_total{decision,reason}` với reason bounded.
- `input_false_accept_rate` và `input_false_reject_rate` từ benchmark corpus.
- `conversation_timeout_total{stage}`.
- `wake_events_total{profile,result}`.
- `device_config_drift_total`.
- `config_reconcile_duration_ms`.
- `artifact_download_bytes_total`.
- `artifact_apply_total{kind,result}`.
- `artifact_rollback_total{reason}`.
- `wake_model_false_accept_rate` và `wake_model_false_reject_rate`.
- `provider_request_duration_ms{kind,adapter,result}`.
- `audio_frames_dropped_total{direction,reason}`.
- `mcp_calls_total{tool,result,actor}`.
- `activation_attempts_total{result}`.
- firmware heap watermark, RSSI, reconnect, watchdog reset.

### Trace span

```text
conversation.turn
  ├── vad.finalize
  ├── asr.stream
  ├── intent.route
  ├── tool.call
  ├── llm.first_token
  ├── tts.first_audio
  └── device.playback
```

Không dùng transcript làm span name/label vì cardinality và privacy.

## 5. LAN deployment không domain

Docker Compose binding gợi ý:

```text
0.0.0.0:8000 voice WebSocket
0.0.0.0:8002 manager API
0.0.0.0:8003 OTA/bootstrap HTTP
0.0.0.0:8081 manager web
127.0.0.1:5432 Postgres
127.0.0.1:6379 Redis
127.0.0.1:9000 MinIO
```

Dev LAN có thể bắt đầu bằng HTTP/WS, nhưng token vẫn phải bật, firewall chỉ cho subnet cần thiết và tuyệt đối không dùng profile này cho public. LAN release nên dùng HTTPS/WSS với local CA/SPKI pinning hoặc IP SAN; public bắt buộc TLS qua tunnel/reverse proxy. Firmware bootstrap nhận URL từ cấu hình nên không cần rebuild khi đổi IP/tunnel/domain.

## 6. Release gates

- Contract tests pass cả native và Xiaozhi compatibility path.
- Migration up/down strategy được review và backup restore test.
- Không có secret trong Git/traces/build artifacts.
- Firmware build reproducible, artifact có SHA-256/signature/SBOM.
- Config/resource bundle immutable, signed, capability-compatible và rollout qua canary.
- Desired/reported state, apply journal và rollback evidence truy vấn được theo device.
- Physical hardware validation được ghi rõ; build pass không thay thế test board.
- Rollout canary 1-5 thiết bị trước khi mở rộng.
