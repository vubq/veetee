# Signed firmware OTA và rollout

## 1. Phạm vi đã triển khai

Veetee dùng executable A/B `ota_0`/`ota_1`; resource/UI slots không chứa code native.
Bootstrap trả `firmware.manifest_url` optional cho device đã được chọn bởi rollout.
Device lấy manifest/content bằng device token, verify restricted JCS + Ed25519,
target N16R8, security epoch, size và SHA-256 trước khi đổi boot partition.
Khi device report đúng desired firmware version, bootstrap không trả lại
`manifest_url`; firmware cũng bỏ qua target bằng version đang chạy để tránh vòng
lặp tải A/B và reboot vô hạn.

Luồng device:

```text
checking -> downloading -> verifying -> staged -> rebooting
                                             -> pending_health -> active
                                                               -> rolled_back
```

`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`. Image mới chỉ được mark-valid sau khi
board/audio/resource/UI đã khởi tạo và qua health window. Nếu health fail hoặc reset
trước mark-valid, bootloader quay về image cũ. Namespace `veetee_fw_ota` chỉ giữ
security epoch; Wi-Fi profile, bootstrap URL và activation identity không bị sửa.

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
  .complete
```

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
- ESP-IDF compile với app rollback enabled và binary dưới OTA slot.
- Manager API validation, deterministic bucket, active ACK policy và DTO/typecheck.
- Manager Web schema/typecheck/build cho release/campaign controls.
- Prisma migration chỉ thêm enum/table/index/foreign key; không reset database.

## 5. Hardware gate còn phải test

Các bước sau không được suy ra từ host test và không tự chạy phá board đang dùng:

1. OTA từ `ota_0` sang `ota_1`, xác nhận Wi-Fi/bootstrap/activation còn nguyên.
2. Quan sát report `rebooting -> pending_health -> active`.
3. Ngắt nguồn ở nhiều offset download; image cũ vẫn boot được.
4. Manifest signature/hash/image lỗi bị từ chối và không đổi boot partition.
5. Force crash/watchdog trước mark-valid; bootloader rollback về image cũ.
6. Canary một device, pause, ACK active, resume theo percentage.
7. Rollback desired về previous signed release và xác nhận drift về 0.

Không cần domain; LAN IP hoặc Tailscale IP/DNS đều dùng được nếu bootstrap origin và
manifest payload URL cùng trỏ tới Manager API đang reachable từ ESP32.
