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
- UI Pack VTPACK1 header/index/member bounds, CRC32, SHA-256, required locale/theme,
  executable/path-traversal rejection và built-in Signal fallback.
- Restricted JCS canonicalization, detached Ed25519 vector, duplicate/NUL/trailing
  JSON rejection và trusted-key/security-epoch downgrade.
- Apply journal and power-loss recovery parser.
- Reported-state sequence/CRC, durable terminal retry và latest-state coalescing.

### Firmware hardware tests

- GPIO pin smoke; ST7789 color/offset/mirror.
- ST7789 state sequence, activation code và pairing-recovery screen; redraw không
  làm chậm button abort hoặc gây watchdog.
- INMP441 noise floor, clipping, sample rate/slot.
- MAX98357A playback, volume curve, no underrun; idle 10 phút không pop/chirp lặp
  lại khi zero-PCM clock mitigation bật.
- AP -> station -> AP fallback.
- Button debounce/hold/interrupt.
- Stored identity bị Manager từ chối -> pairing-recovery; short press không xóa gì,
  hold 5 giây mới clear identity/provisioning và mở AP.
- Activation wake accuracy in standby and interrupt-profile accuracy in standby/thinking; speaking interrupt is best-effort until AEC gate passes.
- 30 phút conversation loop và heap watermark.

### Hardware validation pending user interaction

Các bước dưới đây cần người ở cạnh board/điện thoại thực hiện; host không tự
thay Wi-Fi đang dùng và không được tự xóa NVS:

1. Từ trạng thái idle, giữ GPIO0 khoảng 5 giây để vào Wi-Fi config mode.
2. Kết nối điện thoại vào `VeeTee-9D1C`; kiểm tra DHCP, captive popup và toàn bộ
   HTML/CSS/JS, scan SSID, lưu cấu hình, chuyển AP -> station rồi reconnect.
3. Bấm nút ngắn, nói tiếng Việt và xác nhận WebSocket/ASR/LLM/TTS cùng loa thật;
   không cần bấm lần hai để gửi câu.
4. Kiểm tra wake word và button interrupt khi robot đang thinking/speaking.
5. Kiểm tra trực quan hướng/độ sáng LCD, trạng thái activation/idle và nghe loa
   xem có pop/chirp lặp trong 10 phút.
6. Chạy conversation/heap soak dài hơn và ma trận mất điện, payload hỏng,
   rollback resource/UI trên board.

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
- Manager integration suite phải dùng `VEETEE_INTEGRATION_DATABASE_URL` trỏ tới
  database riêng có hậu tố `_test`; runner từ chối chạy nếu trùng database dev.
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
E2E-19 "Hey VeeTee" corpus -> FAR/FRR/latency gate; `Hi ESP` bring-up không được tính là product pass
E2E-20 reported-state equal retry -> no mutation; lower sequence -> 409; canonical Veetee route only
E2E-21 provider secret rotate/clear -> admin response và audit không chứa raw secret
E2E-22 retryable LLM failure trước output -> fallback; sau output/abort -> không fallback
E2E-23 revoked/stale device identity -> pairing recovery -> physical hold -> code mới
E2E-24 10 phút speaker idle/reconnect/bootstrap retry -> không startup chime lặp hoặc pop/chirp
E2E-25 UI Pack upload -> publish -> desired `state.ui` -> inactive `ui_*` slot -> render health -> complete
E2E-26 corrupt/incompatible UI Pack -> rollback UI journal hoặc built-in Signal, wake resource không đổi
E2E-27 goodbye TTS slow/fail -> vẫn đóng assistant gate; button trong goodbye -> cancel và quay lại listening
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
- Firmware V1 verify detached Ed25519 bằng vendored Monocypher 4.0.3. Public key
  development trong source chỉ dùng fixture/LAN bring-up; production release phải
  build với trust root/key ID khác và private signer luôn nằm ngoài repository.
- Firmware dùng restricted RFC 8785 profile phù hợp manifest V1: JSON number phải
  là integer biểu diễn chính xác trong IEEE-754, property name ASCII, value string
  UTF-8 hợp lệ; duplicate key, NUL/`\u0000`, float và trailing content bị từ chối.
- Resource bundle chỉ chứa data/model/assets; executable/runtime change phải qua signed firmware OTA.
- Không overwrite active resource slot trước khi inactive slot verify và health check xong.
- Capability, size, ABI và minimum firmware được kiểm tra cả API-side lẫn firmware-side.
- Rate limit activation/bootstrap và exponential backoff.
- Server từ chối identity không được tự động factory-reset từ xa; recovery xóa
  credential chỉ sau physical hold trên thiết bị.

### Server/API

- CSPRNG activation code, TTL 5-10 phút, max attempt, atomic consume.
- Pairing bootstrap idempotent theo hardware trong TTL; activation retry trả cùng
  device token dẫn xuất bằng HMAC server-side, database chỉ lưu token hash.
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

Conversation timeline vận hành mặc định chỉ lưu metadata đã redact; event UUID
idempotent, retention 7 ngày (configurable 1-30), payload được bound ở producer và
không tạo event theo từng audio frame/token delta.

## 5. LAN deployment không domain

Native single-node development binding đang dùng:

```text
0.0.0.0:8000 voice WebSocket
0.0.0.0:8001 manager API + OTA/bootstrap/artifact routes
0.0.0.0:8081 manager web
127.0.0.1:5432 Postgres
127.0.0.1:6379 Redis
```

MinIO `127.0.0.1:9000` chỉ bật khi cần object-storage profile. Production có thể
tách admin `8002` và device-edge `8003` ở reverse proxy mà không tách business data.

Dev LAN có thể bắt đầu bằng HTTP/WS, nhưng token vẫn phải bật, firewall chỉ cho subnet cần thiết và tuyệt đối không dùng profile này cho public. LAN release nên dùng HTTPS/WSS với local CA/SPKI pinning hoặc IP SAN; public bắt buộc TLS qua tunnel/reverse proxy. Firmware bootstrap nhận URL từ cấu hình nên không cần rebuild khi đổi IP/tunnel/domain.

## 6. Release gates

- Contract tests pass canonical Veetee routes và các wire fixtures tương thích; runtime không publish namespace của source tham chiếu.
- Migration up/down strategy được review và backup restore test.
- Không có secret trong Git/traces/build artifacts.
- Firmware build reproducible, artifact có SHA-256/signature/SBOM.
- Config/resource bundle immutable, signed, capability-compatible và rollout qua canary.
- Wake resource và UI Pack dùng partition/journal độc lập; release evidence phải chỉ
  rõ artifact nào được apply, rollback và reported-state xác nhận.
- Desired/reported state, apply journal và rollback evidence truy vấn được theo device.
- Physical hardware validation được ghi rõ; build pass không thay thế test board.
- Release evidence phải tách rõ host/build/serial pass với nghiệm thu nghe/nhìn:
  LCD orientation/độ sáng và speaker idle noise luôn cần người kiểm tra trực tiếp.
- Rollout canary 1-5 thiết bị trước khi mở rộng.
