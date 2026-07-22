# Device UI và UI Pack

## 1. Quyết định sản phẩm

Veetee giữ ba giao diện built-in cho ST7789 dọc `240x320`:

| ID | Tên | Vai trò |
|---|---|---|
| `signal` | 01 / Signal | Mặc định; đồng bộ navy/lime/coral với Manager Web. |
| `monolith` | 02 / Monolith | Tương phản cao, thiên về thiết bị AI công nghiệp. |
| `quiet` | 03 / Quiet | Nền sáng, chuyển động nhẹ, phù hợp không gian sống. |

Manager Web có trang `Giao diện thiết bị` để preview cùng một state contract trên
cả ba theme. Việc chọn theme chỉ đổi presentation; không được thay TurnArbiter,
state machine, admission policy, MCP permission hoặc logic hội thoại.

Signal là fallback sản phẩm mặc định. Firmware vẫn phải chứa một fallback tối
thiểu để boot, provisioning, pairing code, pairing recovery và lỗi resource có
thể hiển thị ngay cả khi hai resource slot đều hỏng.

## 2. Bài học giữ lại từ Xiaozhi

Xiaozhi có partition `assets`, tải `assets.bin` qua HTTP, verify checksum, mmap và
đọc `index.json`. Gói có thể chứa ESP-SR model, font CBIN, emoji/image, màu theme,
background và một số layout resource. Cơ chế này chứng minh việc đổi font/theme/
wake model độc lập firmware là khả thi trên ESP32-S3.

Veetee giữ các ý tưởng tốt:

- binary data nằm ngoài executable;
- manifest/index versioned;
- tải qua HTTP thay vì WebSocket audio;
- kiểm size/integrity trước apply;
- font, hình và model có thể thay độc lập firmware;
- có built-in fallback khi asset không hợp lệ.

Veetee không copy nguyên luồng mutable single-slot. N16R8 đã có `resource_0` và
`resource_1`, mỗi slot 4 MiB. Mọi release đi qua inactive slot, SHA-256, Ed25519,
compatibility gate, health window, desired/reported state và rollback. Checksum
nhanh trong container chỉ phát hiện corruption; nó không thay chữ ký release.

## 3. Tên và phạm vi artifact

Tên sản phẩm là **UI Pack**, extension thân thiện là `.vtp`. Manager vẫn có thể
nhận `.bin` để import từ toolchain, nhưng không suy luận an toàn từ extension.
Content type dự kiến:

```text
application/vnd.veetee.ui-pack
```

UI Pack được phép chứa data-only:

```text
manifest.json
theme.json
fonts/*.bin
icons/*.bin
backgrounds/*.rgb565
strings/vi-VN.json
strings/en-US.json
sounds/*.opus
```

UI Pack không được chứa:

- native executable, shared library, WASM hoặc script chạy trên ESP32;
- GPIO/partition/driver configuration;
- prompt, persona, provider secret hoặc MCP permission;
- state transition hay expression quyết định từ semantic text;
- wake model hoặc admission model không khai báo đúng artifact kind.

Wake/model pack và UI Pack là hai logical artifact riêng dù có thể dùng chung một
physical resource slot. Nhờ vậy thay hình nền không buộc benchmark lại wake word,
và thay wake model không thể âm thầm thay UI.

## 4. UI Pack ABI dự kiến

Resource ABI V1 hiện chỉ nhận raw ESP-SR model pack. UI Pack cần resource ABI V2
multi-member. Header/index chỉ chứa offset, length, alignment, kind và hash; mọi
member vẫn bị giới hạn trong slot 4 MiB.

Manifest tối thiểu:

```json
{
  "manifest_version": 1,
  "kind": "ui_pack",
  "id": "ui:signal:1.0.0",
  "version": "1.0.0",
  "theme_id": "signal",
  "target": {
    "board": "veetee-s3-n16r8",
    "display": "st7789-240x320-rgb565"
  },
  "compatibility": {
    "resource_abi": 2,
    "ui_abi": 1,
    "min_firmware": "0.3.0",
    "max_firmware_exclusive": "0.4.0"
  },
  "locales": ["vi-VN", "en-US"],
  "fallback_theme_id": "signal",
  "apply": {
    "mode": "when_standby",
    "requires_reboot": false,
    "rollback_allowed": true
  },
  "payload": {
    "bytes": 734003,
    "sha256": "<64 hex>",
    "content_type": "application/vnd.veetee.ui-pack"
  },
  "signature": {
    "algorithm": "ed25519",
    "key_id": "veetee-release-2026-01",
    "security_epoch": 1,
    "value": "<base64>"
  }
}
```

`theme.json` chỉ được dùng token/layout primitive đã biết bởi firmware:

```json
{
  "ui_abi": 1,
  "palette": {
    "idle.background": "#112f3b",
    "idle.foreground": "#f4f2e8",
    "idle.accent": "#c9ed7c"
  },
  "states": {
    "idle": {"composition": "signal_orb", "motion": "breathe"},
    "listening": {"composition": "signal_orb", "motion": "listen_rings"},
    "speaking": {"composition": "signal_wave", "motion": "audio_level"}
  }
}
```

`composition` và `motion` là enum renderer đã compile và bounded, không phải code.
Biên độ animation khi listening/speaking lấy từ audio/VAD state thật, không phát
random để giả rằng robot đang nghe hoặc nói.

## 5. Upload và rollout trên Manager Web

Luồng đầy đủ:

```text
select/drop file
  -> local extension/size/hash inspection
  -> Manager API stream upload vào quarantine
  -> parse container + validate manifest/member bounds
  -> malware/data-only policy + compatibility validation
  -> release signer ký manifest immutable
  -> publish development/canary/stable
  -> desired state cho một hoặc nhiều device
  -> device Range download vào inactive resource slot
  -> SHA-256 + Ed25519 + member validation
  -> apply khi standby
  -> reported state + health window
  -> active hoặc rollback về slot cũ/built-in Signal
```

Trạng thái hiện tại của Web:

- đã tích hợp preview Signal/Monolith/Quiet;
- Signal là default;
- chọn hoặc drag/drop `.vtp`/`.bin`;
- kiểm extension, giới hạn 4 MiB và SHA-256 nếu Web Crypto khả dụng;
- staging bị khóa có chủ ý cho đến khi API/UI ABI V2 hoàn tất.

Không có domain không chặn phát triển LAN. Tuy nhiên Web Crypto có thể không khả
dụng trên origin HTTP dùng IP LAN; Manager API vẫn phải tự stream SHA-256 và không
bao giờ tin hash từ browser. Khi mở truy cập ngoài LAN, upload bắt buộc HTTPS,
authentication, RBAC, CSRF/origin policy, rate limit và audit log.

## 6. Những phần bắt buộc hard-code

AI-first không có nghĩa loại bỏ mọi invariant. Các phần sau phải compile-time hoặc
được ký bởi trust root:

- partition label/size và resource A/B journal;
- public key/trust epoch tối thiểu;
- parser/header/UI ABI và mọi bound chống overflow;
- mapping state machine sang stable state ID;
- renderer primitive được phép;
- boot/provisioning/pairing/recovery fallback tối thiểu;
- Signal fallback palette/font đủ hiển thị tiếng Việt cơ bản;
- nút vật lý vẫn hoạt động khi UI Pack lỗi.

Nội dung persona, câu trả lời AI, provider routing, theme token, locale pack, font,
icon, background và earcon sản phẩm không cần hard-code. Firmware không được phân
nhánh theo exact text như `dừng lại` để chọn animation; nó chỉ render state/event đã
được TurnArbiter và conversation pipeline xác nhận.

## 7. Kế hoạch triển khai

1. Freeze `ui_abi=1`, state token và budget RAM/PSRAM/SPI cho Signal.
2. Chuyển renderer sang LVGL hoặc retained lightweight scene graph; benchmark cả
   hai trước khi freeze. Full-frame không đặt mục tiêu 30 FPS ở SPI 10 MHz; chỉ
   redraw vùng động 15-30 FPS.
3. Thêm Resource ABI V2 multi-member và tool tạo `.vtp` reproducible.
4. Thêm Manager API streaming upload/quarantine/inspect/sign endpoints.
5. Thêm UI Pack catalog, preview, channel, canary, rollout và drift/report view.
6. Thêm firmware loader cho font/theme/icon/locale, apply ở standby và rollback.
7. Chạy corruption, power-loss, oversized member, signature, downgrade, font lỗi,
   missing locale và repeated rollout soak trên board thật.
