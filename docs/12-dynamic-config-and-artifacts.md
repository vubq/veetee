# Cấu hình động và phân phối artifact

## 1. Mục tiêu

Veetee phải thay đổi được behavior, wake word, model và tài nguyên mà không build lại toàn bộ firmware cho mỗi lần chỉnh cấu hình. Kiến trúc dùng mô hình `desired state` + immutable version + signed artifact:

```text
Manager Web
   -> Manager API tạo desired config version
   -> Artifact service validate/sign/store bundle
   -> Publish rollout/canary
   -> Device nhận invalidation hoặc kiểm tra bootstrap
   -> Pull manifest bằng HTTP(S)
   -> Download -> verify -> stage -> activate -> report
```

WebSocket chỉ gửi invalidation/version nhỏ. Firmware luôn tự pull config/artifact qua HTTP(S); không đẩy binary lớn qua voice socket.

## 2. Bốn lớp thay đổi

| Lớp | Ví dụ | Cách cập nhật | Reboot |
|---|---|---|---|
| Dynamic config | timeout, locale, sensitivity, policy, profile id | signed config snapshot | không |
| Resource/model data | wake model, interrupt model, font, icon, sound | signed resource bundle | thường không, có thể restart subsystem |
| Firmware executable | ESP-IDF app, driver, model runtime/operator | signed A/B OTA | có |
| Hardware contract | GPIO, I2S/SPI wiring, flash layout | board build/ADR | build + flash |

Chỉ hardware, executable, security root và safe bounds là compile-time. Behavior sản phẩm còn lại phải nằm trong config hoặc artifact versioned.

## 3. Desired state và effective state

Manager API không mutate trực tiếp thiết bị. JSON dưới đây là manager/internal representation nên có thể dùng `camelCase`; snapshot/config/manifest gửi tới device phải dùng canonical `snake_case`. Nó quản lý hai trạng thái:

- `desired_state`: version mà tenant muốn device sử dụng;
- `reported_state`: version device đã verify và đang chạy thực tế.

```json
{
  "deviceId": "AA:BB:CC:DD:EE:FF",
  "desired": {
    "agentConfigVersion": 13,
    "resourceBundleVersion": "1.4.0",
    "firmwareVersion": "0.2.0"
  },
  "reported": {
    "agentConfigVersion": 12,
    "resourceBundleVersion": "1.3.2",
    "firmwareVersion": "0.1.5",
    "status": "downloading_resources"
  }
}
```

UI phải hiển thị drift thay vì giả định publish là đã áp dụng. Rollback chỉ đổi desired pointer sang version cũ đã ký; không chỉnh sửa object đã publish.

## 4. Wake profile cấu hình từ Web/API

Wake configuration tách detector profile và model pack artifact:

```text
DetectorProfile
  locale
  model pack reference
  detector id/role
  sensitivity/cooldown/policy
  expected audio format
  config version

ModelPackArtifact
  runtime/format/opset (V1: ESP-SR + srmodels.bin)
  binary payload
  memory/flash requirement
  compatibility metadata
  hash/signature/license
```

Ví dụ profile:

```json
{
  "id": "wake-vi-home-v4",
  "version": 4,
  "locale": "vi-VN",
  "activation": {
    "examples": ["Hey VeeTee"],
    "pronunciation_hints": {"vi-VN": ["hây vi ti"]},
    "model_pack_artifact_id": "model:esp-sr-hey-veetee:1.0.0",
    "detector_id": "wakenet:hey_veetee",
    "sensitivity": 0.64,
    "cooldown_ms": 1200
  },
  "interrupt": {
    "examples": ["dừng lại", "không nói nữa"],
    "model_pack_artifact_id": "model:esp-sr-vi-home:3.0.1",
    "detector_id": "multinet:interrupt_stop",
    "sensitivity": 0.71,
    "enabled_while_speaking": true
  },
  "send_wake_audio": false
}
```

`examples` là metadata/training/test data, không phải exact-string branch trong firmware.
`pronunciation_hints` cũng chỉ phục vụ dataset, review và UI theo locale; firmware chọn detector đã compile bằng ID bất biến. Manager không chuyển chuỗi này thành command grammar tại runtime.

### Wake word tùy chỉnh

Có hai capability khác nhau:

1. Model pack đã có keyword/grammar/detector: API chỉ cần tạo profile và config mới.
2. Model cần train/compile theo phrase: Manager tạo build job, sinh model artifact mới, benchmark rồi mới cho publish.

Không hứa rằng nhập một chuỗi bất kỳ trên Web sẽ lập tức thành wake word tốt. V1 hỗ trợ chọn/upload model đã build; service generate/train custom wake model là phase sau. Model không được train trên ESP32.

Wake phrase sản phẩm đã chốt là `Hey VeeTee`, cách đọc `hây vi ti` (`heɪ viː tiː`). Trước publish, model phải có benchmark versioned gồm người nói/giới tính/vùng giọng Việt khác nhau, khoảng cách/góc nói, âm lượng, phòng vang, TV/nhạc/quạt/đường phố và các từ gần âm. Gate khởi đầu đề xuất: false accept không quá `0.5/device-day` trong soak, false reject không quá `10%` ở tập near-field mục tiêu, p95 detect không quá `250 ms` sau cuối phrase và không làm mất frame capture. Đây là release gate có thể siết theo dữ liệu thực, không phải rule phân loại âm thanh hard-code.

ESP-SR `2.4.7` chỉ có `Hi ESP` phù hợp làm bring-up sẵn có cho board; không được gắn nhãn nó là `Hey VeeTee`. Production ưu tiên model custom ESP-SR nếu quyền sử dụng/toolchain và benchmark đạt gate. Nếu không đạt, Veetee thêm một KWS runtime đã benchmark bằng firmware OTA rồi vẫn phân phối model qua signed `model_pack`.

### Quy tắc apply

- Thay sensitivity/cooldown: apply khi device ở standby hoặc giữa hai turn.
- `sensitivity` là giá trị sản phẩm chuẩn hóa; adapter runtime map sang threshold/mode cụ thể trong safe range, không truyền mù quáng vào SDK.
- Thay model cùng runtime/format: stage rồi restart wake subsystem, không bắt buộc reboot.
- Thay runtime/operator hoặc vượt memory requirement: yêu cầu firmware tương thích trước.
- Apply lỗi: giữ model/profile cũ, report reason và không làm mất button wake.

Button wake luôn là recovery path dù wake model/config bị lỗi.

## 5. Artifact taxonomy

Không dùng một tên `framework.bin` mơ hồ. Artifact có type rõ:

| Type | Nội dung |
|---|---|
| `firmware` | executable ESP-IDF OTA image |
| `resource_bundle` | release logic được manifest ký; V1 map tới một raw model pack |
| `model_pack` | ESP-SR model pack (`srmodels.bin`) hoặc runtime pack đã được firmware hỗ trợ |
| `wake_model` | logical activation detector reference trong model pack |
| `interrupt_model` | logical interrupt detector reference trong model pack |
| `display_assets` | font, icon, animation, locale resources |
| `audio_assets` | prompt, chime, offline sound |
| `admission_model` | optional local signal/input classifier |

`assets.bin` và `model.bin` có thể tồn tại như payload logic. Với ESP-SR V1, payload
vật lý là raw `srmodels.bin` bắt đầu tại offset 0; manifest ký bên ngoài chứa toàn bộ
metadata/hash và dùng MIME `application/vnd.veetee.esp-sr-model-pack`. Cách này cho
phép `esp_srmodel_init(partition_label)` load trực tiếp mà không cần copy hoặc bỏ qua
container header.

Native executable code, shared library hoặc arbitrary plugin không được đưa vào resource bundle. Nếu model cần operator/runtime mới, cập nhật `firmware` trước. MCP mở rộng behavior qua server/tool registry; không dùng dynamic native code trên ESP32.

### Resource payload ABI V1 và hướng V2

V1 chỉ có một member `model_pack/esp-sr`, member bytes/hash bằng payload bytes/hash.
Không đặt header trước `srmodels.bin`. Multi-member container chỉ được thêm ở
resource ABI V2, khi đó format dự kiến mới gồm:

```text
header: magic, pack_version, abi, index_offset, index_bytes, payload_bytes
index:  path/name, kind, runtime, runtime_abi, offset, bytes, sha256, alignment
payload: immutable binary members (không executable)
footer: pack_sha256 hoặc CRC phục vụ phát hiện write dang dở
```

V1 không nén `srmodels.bin`. Compression/container chỉ thêm khi có capability flag,
loader và benchmark. Path traversal, duplicate name, overflow và member vượt slot
đều bị từ chối. Firmware load smoke test model pack trước khi xác nhận active pointer.

## 6. Artifact manifest

Manifest là nguồn chuẩn cho compatibility và integrity:

```json
{
  "manifest_version": 1,
  "bundle_id": "01JRESOURCE0000000000000000",
  "kind": "resource_bundle",
  "version": "1.4.0",
  "channel": "stable",
  "target": {
    "board": "veetee-s3-n16r8",
    "chip": "esp32s3",
    "flash_bytes": 16777216,
    "psram_bytes": 8388608
  },
  "compatibility": {
    "min_firmware": "0.2.0",
    "max_firmware_exclusive": "0.3.0",
    "resource_abi": 1
  },
  "payload": {
    "url": "http://192.168.1.20:8003/veetee/artifacts/01JRESOURCE/content",
    "size": 125943,
    "sha256": "<64 hex chars>",
    "content_type": "application/vnd.veetee.esp-sr-model-pack"
  },
  "apply": {
    "mode": "when_standby",
    "requires_reboot": false,
    "rollback_allowed": true
  },
  "members": [
    {
      "name": "speech/esp-sr-models",
      "kind": "model_pack",
      "runtime": "esp-sr",
      "runtime_abi": 1,
      "format_version": 1,
      "sample_rate": 16000,
      "detectors": [
        {"id": "wakenet:veetee_vi", "role": "activation"},
        {"id": "multinet:interrupt_stop", "role": "interrupt"}
      ],
      "sha256": "<64 hex chars>",
      "bytes": 125943
    }
  ],
  "created_at": "2026-07-21T12:00:00Z",
  "signature": {
    "algorithm": "ed25519",
    "key_id": "veetee-release-2026-01",
    "security_epoch": 1,
    "value": "<base64 signature>"
  }
}
```

Signature bao phủ RFC 8785 JCS canonical manifest không gồm `signature.value`. Payload hash bao phủ toàn bộ binary. Member hashes giúp validate/index và debug, không thay thế bundle hash. `signature.value` dùng Base64; `key_id` và `security_epoch` phục vụ rotation/anti-downgrade. Algorithm V1 đã freeze là detached Ed25519 sau khi crypto spike pass trên ESP-IDF bằng Monocypher 4.0.3. Firmware dùng restricted JCS profile: integer-only trong khoảng biểu diễn chính xác, ASCII property name và UTF-8 string value; duplicate key, NUL/`\u0000`, float, invalid UTF-8 và trailing content đều bị reject. Hạn chế này là contract V1 và phải được signer/contract fixture giữ đồng nhất.

## 7. Device capability contract

Firmware report capability để Manager không publish artifact không tương thích:

```json
{
  "board": "veetee-s3-n16r8",
  "firmware_version": "0.2.0",
  "resource_abi": 1,
  "runtimes": {
    "esp-sr": [1],
    "lvgl-assets": [1]
  },
  "free_resource_slot_bytes": 3932160,
  "psram_bytes": 8388608,
  "hot_reload": ["model_pack", "display_assets", "audio_assets"]
}
```

Manager API validate capability khi publish, rollout và reconcile. Device vẫn phải validate lại vì server data không được coi là trusted.

## 8. Sync protocol

### Bootstrap

OTA/bootstrap response giữ field Xiaozhi cũ và thêm optional Veetee fields:

```json
{
  "config": {
    "version": 13,
    "etag": "agent-config-13",
    "url": "http://192.168.1.20:8003/veetee/config/v1/devices/AA-BB"
  },
  "resources": {
    "version": "1.4.0",
    "manifest_url": "http://192.168.1.20:8003/veetee/artifacts/manifests/01JRESOURCE"
  }
}
```

Xiaozhi-compatible firmware bỏ qua unknown field. Veetee firmware dùng `If-None-Match`/ETag để tránh tải lại config.

### Invalidation

Khi device đang online, voice server gửi event nhỏ:

```json
{
  "type": "system",
  "session_id": "01J...",
  "command": "config_changed",
  "config_version": 13,
  "resource_version": "1.4.0"
}
```

Device không áp dụng payload từ event. Nó chỉ schedule reconcile qua HTTP(S) khi standby hoặc theo policy.

### Report

Device report state sau mỗi phase:

```text
checking -> downloading -> verifying -> staged -> applying -> active
                                            └-> failed/rolled_back
```

Firmware V1 gửi:

```http
PUT /veetee/devices/:deviceId/reported-state
Authorization: Bearer <device-token>
Device-Id: <hardware-id>
Content-Type: application/json
```

```json
{
  "version": 12,
  "bootId": "95eff5a6-3dcf-4cb4-a6d9-e31cd6d82f63",
  "state": {
    "schemaVersion": 1,
    "firmware": {"version": "0.2.0"},
    "resource": {
      "phase": "downloading",
      "currentVersion": "factory-bringup",
      "desiredVersion": "1.0.0",
      "activeSlot": 0,
      "targetSlot": 1,
      "expectedBytes": 125943,
      "downloadedBytes": 65536,
      "securityEpoch": 1
    }
  }
}
```

`errorCode` chỉ xuất hiện ở `failed/rolled_back`. Version lớn hơn atomically thay
state; cùng version là retry idempotent và không mutate; version thấp hơn trả `409`.
Sequence được firmware persist trước khi gửi. Terminal state đang chờ được persist
để retry qua reboot; intermediate state latest-wins để không làm nghẽn main/audio.
Endpoint lấy từ bootstrap origin nên đổi IP/tunnel/domain không cần rebuild firmware.
V1 chưa đưa duration/trace vào snapshot; hai field này sẽ thuộc apply-event timeline
khi Manager có rollout/event model riêng. Không gửi secret, transcript hoặc signed
URL đầy đủ vào body/log.

## 9. Firmware storage cho ESP32-S3 N16R8

N16R8 có 16 MB flash và 8 MB PSRAM. Layout chính xác chỉ freeze sau khi đo app size, nhưng strategy V1 ưu tiên là:

- `ota_0` + `ota_1` cho executable A/B;
- `resource_0` + `resource_1` cho signed resource bundle A/B;
- NVS chỉ lưu identity, desired/current version, slot pointer và apply journal;
- một vùng nhỏ cho coredump/diagnostic nếu đủ chỗ.

Không lưu model lớn trong NVS. Resource slot inactive được ghi, verify toàn bộ rồi mới atomically đổi active pointer. Nếu boot/apply health check fail, rollback pointer về slot cũ.

Layout V1 của prototype N16R8:

```text
metadata/NVS/otadata/coredump    ~0.5 MB
ota_0                            ~3.5 MB
ota_1                            ~3.5 MB
resource_0                        4.0 MB
resource_1                        4.0 MB
```

Nếu app thực tế lớn hơn, ưu tiên giữ executable A/B. Nếu resource bundle vượt slot sau khi đã chốt asset scope, phải mở ADR chuyển sang resource store 8 MB với immutable blobs + dual manifest journal; không tự overwrite active slot. Manager phải từ chối bundle vượt `free_resource_slot_bytes` trong device capability.

## 10. Apply transaction và rollback

Source hiện đã triển khai các bước 1-10 cho single-member ESP-SR V1: device-authenticated
manifest/content pull, strict verify, Range/resume, streaming hash/write, CRC journal,
safe-boundary reload, pending-health và rollback. Manager API stream file immutable
trực tiếp, trả `206`/`Content-Range`, không buffer artifact trong process. Chưa được
coi production-ready cho tới khi power-loss/corruption/rollback matrix pass trên board
và firmware report apply result về Manager.

Device áp dụng config/resource theo transaction:

1. Fetch manifest/snapshot với device token.
2. Validate schema, target, version, safe bounds và URL policy.
3. Download vào inactive slot với resume/range nếu server hỗ trợ.
4. Verify size, SHA-256 và detached signature theo canonicalization/algorithm đã freeze.
5. Validate member index/runtime/memory requirement.
6. Stage và chạy subsystem smoke check.
7. Chờ `when_standby`/session boundary.
8. Atomically activate pointer.
9. Start health window và report.
10. Rollback nếu crash, watchdog, load failure hoặc detector health fail.

Release local dùng `npm run resources:release -- ...`; private Ed25519 key nằm trong
đường dẫn ignored ngoài source. Output immutable mặc định ở `data/artifacts/:id/`
với `manifest.json`, `content.bin` và marker hoàn tất. Bootstrap có thể dùng LAN IP;
không cần domain.

Không overwrite active slot trước khi verify xong. Mất điện ở mọi bước phải khởi động lại được từ apply journal.

## 11. Manager API và artifact lifecycle

Data model bổ sung:

```text
wake_profile
model_pack_artifact
resource_bundle
resource_bundle_member
artifact_manifest
artifact_signature
device_desired_state
device_reported_state
config_rollout
artifact_rollout
artifact_apply_event
```

Lifecycle:

```text
draft -> validating -> ready -> canary -> stable
                   └-> rejected
stable -> deprecated -> revoked
```

API groups đề xuất:

```text
GET/POST/PATCH  /api/v1/wake-profiles
POST            /api/v1/wake-profiles/:id/publish
POST            /api/v1/artifacts/uploads
GET             /api/v1/artifacts/:id
POST            /api/v1/artifacts/:id/validate
POST            /api/v1/resource-bundles
POST            /api/v1/resource-bundles/:id/publish
POST            /api/v1/resource-bundles/:id/rollout
GET             /api/v1/devices/:id/effective-config
POST            /api/v1/devices/:id/reconcile
GET             /api/v1/devices/:id/apply-events
```

Device-facing endpoints:

```text
GET  /veetee/config/v1/devices/:deviceId
GET  /veetee/artifacts/manifests/:manifestId
GET  /veetee/artifacts/:artifactId/content
PUT  /veetee/devices/:deviceId/reported-state
```

Upload đi thẳng vào private MinIO/local object store bằng short-lived signed upload URL; Manager API không giữ file lớn trong RAM. Worker validate magic/size/hash/schema/license, build bundle và ký release.

## 12. Security và supply chain

- SHA-256 cho payload integrity; manifest V1 dùng restricted JCS + detached
  Ed25519, verify bằng Monocypher 4.0.3 đã vendored và giữ nguyên license/provenance.
- Public verification key/rotation metadata nằm trong firmware trust store; private signing key nằm ngoài database, ưu tiên HSM/Vault/offline signer.
- Device token scoped theo device và chỉ đọc artifact được rollout cho chính nó.
- Artifact URL có TTL; URL scheme/host phải qua allowlist/bootstrap trust.
- Chống downgrade bằng minimum accepted version/security epoch; rollback chỉ tới version đã ký và còn policy cho phép.
- Scan MIME/magic/size, zip bomb/path traversal và malformed model index.
- Ghi SBOM/license/provenance cho firmware, runtime và model artifact.
- Revocation không xóa binary đang active ngay; rollout safe replacement rồi mới garbage collect.
- Không cho LLM/MCP tự publish config, artifact, signing key hoặc rollout firmware.

Trong LAN dev chưa có domain có thể dùng HTTP/WS, nhưng token phải bật và artifact vẫn hash/sign. LAN release nên dùng HTTPS/WSS với local CA/SPKI pinning hoặc IP SAN; public deployment bắt buộc TLS. Domain không phải điều kiện của firmware vì endpoint nằm trong bootstrap config.

## 13. Manager Web UX

Manager Web cần các màn hình:

- Wake profiles: activation/interrupt, locale, model, sensitivity, test corpus result.
- Artifact library: type, size, hash, signature, compatibility, license, scan status.
- Resource bundle composer: chọn model/assets, xem flash budget trước publish.
- Device effective state: desired vs reported, drift và lỗi apply.
- Rollout: canary, percentage, pause/resume/rollback.
- Device logs: state transition và error code đã redact.

UI không cho publish nếu:

- model/runtime không tương thích firmware;
- bundle vượt resource slot;
- artifact chưa ký/validate;
- wake benchmark chưa đạt threshold đã cấu hình;
- rollout không có ít nhất một canary device ở production channel.

## 14. Observability

Metrics tối thiểu:

- `device_config_drift_total`;
- `config_reconcile_duration_ms`;
- `artifact_download_bytes_total`;
- `artifact_apply_total{kind,result}`;
- `artifact_rollback_total{reason}`;
- `wake_profile_load_total{result}`;
- `wake_model_false_accept_rate` và `wake_model_false_reject_rate` từ benchmark;
- flash slot usage, apply journal recovery và resource load time.

Audit event phải ghi actor, tenant, config/artifact version, hash, rollout, device scope và result. Không ghi raw binary/token/signed URL.

## 15. Definition of Done

- Đổi sensitivity/timeout/profile qua Manager Web không cần build firmware.
- Publish wake model/resource bundle theo immutable version và signed manifest.
- Device pull, resume, verify, stage, activate và report desired/reported state.
- Mất điện/download lỗi/signature lỗi không phá active resource.
- Bundle/model không tương thích hoặc quá lớn bị từ chối ở cả Manager API và firmware.
- Button wake vẫn hoạt động khi wake model mới load fail.
- Session đang chạy không bị đổi config giữa turn; apply ở safe boundary.
- Rollout canary/rollback và audit chạy end-to-end bằng IP LAN, không cần domain.
