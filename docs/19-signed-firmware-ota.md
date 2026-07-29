# Signed firmware OTA và rollout

## 1. Phạm vi đã triển khai

Veetee dùng executable A/B `ota_0`/`ota_1`; resource/UI slots không chứa code native.
Bootstrap trả `firmware.manifest_url` optional cho device đã được chọn bởi rollout.
Device lấy manifest/content bằng device token, verify restricted JCS + Ed25519,
target N16R8, security epoch, size và SHA-256 trước khi đổi boot partition.
Hai URL phải là canonical artifact routes cùng exact bootstrap origin; redirect và
compressed transfer bị từ chối. Bearer token không được gửi sang origin khác.
Khi device report đúng desired firmware version, bootstrap không trả lại
`manifest_url`; firmware cũng bỏ qua target bằng version đang chạy để tránh vòng
lặp tải A/B và reboot vô hạn.

Luồng device:

```text
checking -> downloading -> verifying -> staged -> rebooting
                                             -> pending_health -> active
                                                               -> rolled_back
```

Firmware target khác version đang chạy có precedence tuyệt đối trong authenticated
bootstrap: firmware chỉ post target cho application task, không emit config/resource/
UI hoặc `activation_complete` trong lượt đó. Application task chuyển
`activating|idle -> upgrading`, tắt capture/wake, abort playback, đóng transport và
cancel các reconciler trước khi gọi updater. Worker chỉ download/verify/stage; nó
không đổi boot partition và không tự reboot. Sau `staged`, application task persist
report `rebooting`, commit boot partition rồi mới restart. Persist/commit lỗi restore
image đang chạy và đi qua terminal `failed` bền vững.

`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`. Sau reboot pending-verify, bootstrap vẫn
reconcile config/resource/UI và emit `activation_complete`, nhưng defer target firmware
mới cho tới khi attempt hiện tại kết thúc. Image mới chỉ được mark-valid trong cửa
sổ 30 giây bắt đầu từ `GOT_IP`, với overall bound tính từ boot bằng Wi-Fi connect
timeout 60 giây + bootstrap grace 30 giây, khi đồng thời có identity hợp lệ,
authenticated bootstrap hoàn tất,
state `idle`, capture/playback task đang chạy, wake resource và UI healthy, cùng wake
task nếu profile yêu cầu. Policy được poll 500 ms; thiếu bất kỳ gate nào tới deadline
thì application task yêu cầu bootloader rollback. Nếu health fail hoặc reset trước
mark-valid, bootloader quay về image cũ. `rebooting`, `pending_health` và terminal
result được persist với monotonic sequence/CRC để replay đúng thứ tự qua reboot; phase
terminal không bị outcome khác supersede và attempt chỉ clear sau khi terminal report
đã persist. `staged` là journal local; application không enqueue một report
intermediate ngay trước durable `rebooting`, tránh SMP cấp sequence `staged` cao hơn
boot boundary. Reporter cũng chặn/drop mọi intermediate khi đã có terminal pending.
Recovery giữ terminal journal immutable nhưng vẫn đối chiếu running slot/version:
mismatch report `failed` với `terminal_runtime_mismatch`, không bao giờ report
`active`/`rolled_back` sai image; pending image mismatch bị yêu cầu rollback. Journal
attempt riêng giữ from/to version, slot, epoch, expected bytes và
bounded error code. Namespace `veetee_fw_ota` cũng giữ security-epoch floor; Wi-Fi
profile, bootstrap URL và activation identity không bị sửa.

Executable downloader V1 luôn bắt đầu lại từ byte 0. Manager content route có thể hỗ
trợ Range cho resource clients, nhưng firmware updater chưa có Range/resume hoặc
download-offset journal; không được tính capability đó là đã triển khai cho OTA app.

## 2. Release

Private key phải nằm ngoài repository. Ví dụ LAN/Tailscale:

```bash
npm run firmware:release -- \
  --input ../veetee-firmware/build/veetee_firmware.bin \
  --artifact-id fw-0.4.0 \
  --version 0.4.0 \
  --channel canary \
  --public-base-url http://192.168.1.20:8001 \
  --private-key /secure/path/release-ed25519.pem
```

`--version` phải đúng với `CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION` của lần build
firmware đó; khi lên release mới, cập nhật Kconfig/sdkconfig rồi build lại trước
khi chạy lệnh release.

Output immutable:

```text
data/artifacts/fw-0.4.0/
  manifest.json
  content.bin
  provenance.json
  sbom.spdx.json
  .complete
```

`provenance.json` records only bounded release metadata: artifact/version/channel,
content and manifest hashes, repository commit/dirty marker and release-tool
version. `sbom.spdx.json` is a minimal SPDX 2.3 inventory for the immutable image
with its SHA-256; it deliberately does not claim a full transitive dependency SBOM
until the build pipeline exports one. Neither file contains a private key, token or
absolute source path. `.complete` lists hashes for all four release files so a
catalog worker can verify the complete directory before publish.

Release gate đọc marker `VEETEE_RELEASE_VERSION=<semver>` đã được firmware nhúng
từ `CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION`. `--version` khác marker bị từ chối,
kể cả binary vẫn có ESP image header hợp lệ.

Sau đó đăng ký artifact qua catalog, publish bằng
`POST /api/v1/firmware-releases/:id/publish`.

## 3. Rollout

Routes:

```text
GET  /api/v1/firmware-releases
POST /api/v1/firmware-releases/:id/publish
GET  /api/v1/firmware-rollouts
POST /api/v1/firmware-rollouts
POST /api/v1/firmware-rollouts/:id/pause
POST /api/v1/firmware-rollouts/:id/resume
POST /api/v1/firmware-rollouts/:id/rollback
```

Production/stable bắt buộc ít nhất một canary. Canary luôn nhận desired trước;
percentage selection dùng bucket ổn định
`SHA256(rolloutId + ":" + deviceId) mod 100`, không random lại khi refresh.
Percentage fleet chỉ được mở sau khi mọi canary report target version `active`.
Pause ngừng mở rộng desired state, không hạ image đang chạy. Rollback trỏ desired
về previous signed/published release cùng channel và device vẫn verify lại
manifest/image. Manager chỉ cho một firmware campaign `running`/`paused` mỗi
tenant; tập device đã nhận desired được lưu bất biến để rollback không bỏ sót
percentage target nếu trạng thái canary thay đổi sau đó.

## 4. Host/build gate

- Firmware manifest success/tamper/target/capacity/security downgrade.
- Bootstrap firmware precedence, UPGRADING state lockout, generation cancellation,
  canonical same-origin URL và boot-health gate từng điều kiện.
- ESP-IDF compile với app rollback enabled và binary dưới OTA slot.
- Manager API validation, deterministic bucket, active ACK policy và DTO/typecheck.
- Manager Web schema/typecheck/build cho release/campaign controls.
- Prisma migration chỉ thêm enum/table/index/foreign key; không reset database.

## 5. Hardware gate còn phải test

Các bước sau không được suy ra từ host test và không tự chạy phá board đang dùng:

1. OTA từ `ota_0` sang `ota_1`, xác nhận Wi-Fi/bootstrap/activation còn nguyên.
2. Quan sát report `rebooting -> pending_health -> active`.
3. Ngắt nguồn ở nhiều offset download, sau `staged`, sau commit `rebooting` và trong
   `pending_health`; journal phải recover đúng terminal outcome và image cũ vẫn boot.
4. Manifest signature/hash/image lỗi bị từ chối và không đổi boot partition.
5. Force crash/watchdog trước mark-valid; bootloader rollback về image cũ.
6. Canary một device, pause, ACK active, resume theo percentage.
7. Rollback desired về previous signed release và xác nhận drift về 0.

Không cần domain; LAN IP hoặc Tailscale IP/DNS đều dùng được nếu bootstrap origin và
manifest payload URL cùng trỏ tới Manager API đang reachable từ ESP32.
