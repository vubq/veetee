# Device UI và UI Pack

## 1. Quyết định sản phẩm

Veetee giữ ba visual direction cho ST7789 dọc `240x280`:

| ID | Tên | Phân phối | Vai trò |
|---|---|---|---|
| `signal` | 01 / Mobile | Có sẵn trong firmware và có standard pack | Bảng thông tin dạng điện thoại, mặc định và failsafe. |
| `monolith` | 02 / Companion | Standard UI Pack | Nhân vật Vee hoạt ảnh theo state hội thoại. |
| `quiet` | 03 / Robot Face | Standard UI Pack | Hai mắt hoạt ảnh, navy/lime đồng bộ robot trên Manager Web. |

Ba ID `signal`, `monolith`, `quiet` được giữ như khóa tương thích của UI ABI 1;
tên và presentation sản phẩm từ firmware `0.3.1` lần lượt là Mobile, Companion và
Robot Face. UI và API không suy luận behavior từ tên legacy này.

Manager Web preview cả ba bằng cùng state contract. Theme chỉ được đổi
presentation; UI Pack không có quyền thay TurnArbiter, state machine, admission
policy, provider routing, MCP permission hoặc executable behavior.

Mobile tối thiểu luôn nằm trong executable dưới composition ID `signal` để boot, provisioning, pairing code,
pairing recovery và lỗi artifact vẫn hiển thị nếu cả hai UI slot đều hỏng. Ba
composition hình học `signal`, `monolith`, `quiet` đều compile sẵn như một
allowlist an toàn; đo trên ELF hiện tại, ba hàm renderer chỉ chiếm khoảng 1.3 KiB
text tổng cộng. Palette, locale và asset của từng giao diện không nhúng cả ba vào
app image mà nằm trong pack tương ứng. Cách này nhẹ hơn nhiều so với ảnh/font/audio,
đồng thời tránh OTA toàn firmware hoặc thực thi layout code động khi đổi UI. Source
chuẩn của ba pack nằm trong `veetee-server/ui-packs/` để build reproducible.

## 2. Kiến trúc lưu trữ

Ý tưởng data asset tải độc lập firmware được giữ từ Xiaozhi, nhưng Veetee không dùng
mutable single-slot và không ghép wake model với giao diện. Partition N16R8 hiện là:

```text
resource_0 / resource_1    wake/model A/B, 2 MiB mỗi slot
ui_0 / ui_1                UI Pack A/B, 2 MiB mỗi slot
```

Hai artifact class có NVS namespace, apply journal, active pointer, health window và
rollback riêng. Thay giao diện không restart hoặc rollback WakeNet; thay wake model
không thể âm thầm đổi UI. Mọi release vẫn đi qua signed external manifest, SHA-256,
compatibility gate, inactive-slot staging và desired/reported state.

## 3. Container VTPACK1

Tên sản phẩm là **UI Pack**, extension chuẩn `.vtp`, content type:

```text
application/vnd.veetee.ui-pack
```

Manager có thể nhận tên `.bin` để hỗ trợ toolchain, nhưng an toàn luôn được quyết
định bằng magic/parser, không bằng extension. Resource ABI 2/UI ABI 1 đã freeze:

- magic `VTPACK1\0`;
- header cố định 64 byte;
- entry cố định 128 byte;
- tối đa 32 member và 2 MiB/container;
- payload/member căn 16 byte;
- CRC32 cho index;
- SHA-256 riêng cho mọi member;
- offset tăng đơn điệu, không overlap, không path traversal, không duplicate;
- flags/reserved byte bắt buộc bằng 0 để giữ forward compatibility rõ ràng.

Member allowlist data-only:

```text
manifest.json
theme.json
strings/*.json
fonts/*.vfont
icons/*.vicon
backgrounds/*.rgb565
sounds/*.opus
```

`manifest.json`, `theme.json` và `strings/vi-VN.json` là bắt buộc. Script, WASM,
ELF, shared library, native operator, GPIO/partition config, prompt, provider secret,
MCP policy và state-transition expression đều bị từ chối.

Manifest trong container hiện dùng schema:

```json
{
  "schema_version": 1,
  "kind": "ui_pack",
  "id": "ui-signal-1.2.0",
  "version": "1.2.0",
  "theme_id": "signal",
  "channel": "stable",
  "license": "MIT",
  "target": {
    "board": "veetee-s3-n16r8",
    "display": "st7789-240x280-rgb565"
  },
  "compatibility": {
    "resource_abi": 2,
    "ui_abi": 1,
    "min_firmware": "0.3.1",
    "max_firmware_exclusive": "0.4.0"
  },
  "locales": ["vi-VN", "en-US"],
  "fallback_theme_id": "signal"
}
```

External artifact manifest do Manager ký vẫn chứa target flash/PSRAM, firmware
range, payload URL/size/hash/content type, apply policy, member runtime và Ed25519
signature/security epoch. Container CRC/hash không thay thế release signature.

## 4. UI ABI 1

`theme.json` chỉ chọn một composition đã compile và palette theo 13 state ổn định:

```json
{
  "schema_version": 1,
  "ui_abi": 1,
  "theme_id": "signal",
  "composition": "signal",
  "palette": {
    "idle": {
      "background": "#112f3b",
      "foreground": "#f4f2e8",
      "accent": "#c9ed7c"
    }
  }
}
```

Composition allowlist là `signal`, `monolith`, `quiet`. Firmware dùng framebuffer
RGB565 trong PSRAM và DMA stripe buffer để flush ST7789. Renderer Mobile dùng card
thông tin kiểu điện thoại; Companion dùng nhân vật vector primitive; Robot Face
dùng hai mắt navy/lime. Cả ba dùng frame hoạt ảnh bounded 500 ms, state number,
copy vận hành và activation-code layout; không chạy layout script từ pack.

UI ABI 1 hiện áp dụng composition và palette từ pack. Parser bắt buộc kiểm cấu trúc
chuỗi `vi-VN`, nhưng renderer firmware vẫn dùng operational ASCII copy tích hợp để
không phụ thuộc font pack trong boot/recovery. Áp dụng localized strings, `.vfont`,
icon/background và product earcon là phần mở rộng kế tiếp của cùng data-only ABI;
không được quảng bá là đã render đầy đủ tiếng Việt có dấu trước hardware/font test.

## 5. Manager chọn, tạo pack, publish và rollout

Luồng chính cho ba giao diện chuẩn đã triển khai:

```text
chọn Mobile / Companion / Robot Face trên software twin
  -> POST /api/v1/ui-packs/standard/:theme/stage
  -> server build deterministic VTPACK1 từ source chuẩn
  -> chạy cùng quarantine/parser/hash/signature pipeline như file upload
  -> publish immutable artifact
  -> rollout explicit device
  -> desired state.ui
  -> device download content.bin vào inactive ui slot
  -> verify/apply/report
```

Người dùng không phải tự tạo hoặc chọn file. Extension chuẩn của artifact nguồn là
`.vtp`; Manager lưu payload phân phối dưới tên `content.bin`, còn firmware quyết
định hợp lệ bằng magic `VTPACK1`, ABI và chữ ký chứ không dựa vào đuôi file.

Upload thủ công vẫn tồn tại như luồng nâng cao:

```text
select/drop file
  -> browser kiểm extension/2 MiB/hash nếu Web Crypto khả dụng
  -> POST /api/v1/ui-packs/uploads
  -> stream bounded vào private quarantine
  -> parse container/member/hash/data-only/compatibility
  -> tạo signed external manifest
  -> atomic rename vào immutable local artifact store
  -> POST /api/v1/artifacts/:id/publish
  -> POST /api/v1/ui-packs/:id/rollout với explicit device UUID
  -> desired state.ui
  -> device download/apply/report
  -> GET /api/v1/ui-packs/rollouts hiển thị tiến độ
```

Manager API không buffer toàn pack trong RAM và không tin hash browser. Upload yêu
cầu ADMIN; rollout yêu cầu OPERATOR; mutation có tenant scope và audit/request ID.
Publish không có nghĩa device đã apply. Rollout chỉ terminal khi reported `state.ui`
xác nhận `active`, `failed` hoặc `rolled_back`.

Ba standard template `ui-signal-1.2.0`, `ui-monolith-1.2.0` và
`ui-quiet-1.2.0` đã build/inspect deterministic; binary generated không commit Git.
Manager có thể tạo, ký, publish và rollout lại từ Web qua endpoint chuẩn mà không
buffer pack upload tùy ý trong browser.
Không có domain không chặn luồng LAN. HTTP/IP có thể làm Web Crypto browser không
khả dụng, nhưng server-side stream/hash/signature vẫn là authority.

## 6. Firmware apply lifecycle

```text
bootstrap desired ui
  -> verify signed external manifest
  -> download inactive ui slot, có Range/resume
  -> verify payload hash và VTPACK1 member hashes
  -> parse manifest/theme/vi-VN structure
  -> chờ standby
  -> synchronous render smoke test
  -> activate UI journal
  -> health window
  -> confirm hoặc rollback previous slot
  -> built-in Mobile nếu cả hai slot đều fail
```

Firmware report `state.resource` và `state.ui` độc lập; một request reported-state
chỉ chứa đúng một artifact subsystem để retry/idempotency không trộn hai journal.
Firmware compatibility quảng bá cho bootstrap/reported state là `0.3.1`, không dùng
Git describe `*-dirty` làm SemVer capability.

## 7. Invariant bắt buộc hard-code

AI-first không có nghĩa loại bỏ invariant an toàn. Các phần sau phải nằm trong code
hoặc trust root:

- partition label/size, parser bounds, ABI và safe path rules;
- public key, minimum security epoch và signature algorithm;
- mapping state machine sang stable state ID;
- renderer primitive/composition allowlist;
- boot/provisioning/pairing/recovery copy tối thiểu;
- Mobile fallback palette/font tối thiểu dưới stable ID `signal`;
- nút vật lý và cancellation vẫn hoạt động khi UI Pack lỗi.

Persona, câu trả lời AI, provider routing, theme token, locale pack, font, icon,
background và earcon sản phẩm có thể cấu hình hoặc phân phối dưới dạng data đã ký.
Firmware không phân nhánh theo exact phrase như `dừng lại` để chọn UI hay behavior.

## 8. Validation và phần còn lại

Đã pass trên host/build:

- deterministic build/inspect cho Mobile, Companion và Robot Face;
- Web chọn standard theme -> server build/stage -> publish -> desired rollout;
- test chống drift giữa Web software twin, C++ renderer, state order và Mobile RGB565;
- corruption, executable member và path traversal rejection;
- Manager upload/publish/rollout E2E;
- simulated firmware `0.3.1` bootstrap + `state.ui` complete;
- firmware host tests và ESP-IDF 6.0.2 production build;
- built-in Mobile render path kế thừa safe fallback; renderer animation `0.3.1`
  đã pass host/build và còn cần nghiệm thu trên LCD thật.

Vẫn cần người dùng nghiệm thu trên board:

- orientation, color, brightness và visual Mobile thực tế;
- rollout Companion -> Robot Face -> Mobile qua hai UI slot;
- reboot persistence, corrupt-pack rollback và built-in Mobile fallback;
- SPI/render soak không tăng button abort latency;
- font/chuỗi tiếng Việt có dấu trước khi mở dynamic localized-copy apply;
- power-loss matrix trong download, activate và health window.

Sau V1 mới bổ sung percentage rollout, pause/resume/operator rollback, MinIO/multi-
node, `.vfont`/icon/background/earcon renderer và animation bounded theo benchmark.

## 9. Demo hướng hình ảnh

`veetee-firmware-ui-demo/` ở gốc repo là bản dựng độc lập, không build, để duyệt hướng
hình ảnh trước khi viết renderer mới. Nó vẽ ở đúng `240x280` đơn vị panel, lượng tử hóa
RGB565 và in ngân sách SPI theo `CONFIG_VEETEE_LCD_SPI_CLOCK_HZ`. Ba giao diện giữ nguyên
composition ID `signal` / `monolith` / `quiet`; copy và palette mirror từ `kScreenCopy` và
`veetee-server/ui-packs/`, phải giữ đồng bộ khi một trong hai đổi.

Demo là mục tiêu, không phải mô tả renderer đang ship: hướng đã chốt là hình học
anti-aliased và chữ `.vfont`, không phải font bitmap 5x7. Nó không được inject vào firmware
hay Manager Web dưới dạng HTML/JS runtime (`docs/22-veetee-interface-language.md` §7), và
khác với `veetee-firmware/prototypes/device-ui/` vốn là concept cũ đã bị thay thế.

Giao diện Companion trong demo dùng đường ống clip: nhân vật render trên PC, xuất frame
sequence, crop về `240x280`, nén RGB565-RLE thành container `VTCLIP1` và thiết bị chỉ giải
nén span vào framebuffer. Chữ không nướng vào frame mà firmware vẽ đè, để locale, mã ghép
thiết bị và copy lỗi không cần export lại nhân vật. Phát clip trên thiết bị **chưa** hợp lệ
theo UI ABI 1: member allowlist ở §3 chưa có `clips/*.vclip`, nên đây là thay đổi ABI có
phiên bản (`ui_abi` 1 -> 2) phải quyết định riêng, kèm giới hạn parser cho kích thước clip
và ngân sách slot. Bản decode tham chiếu nằm ở `veetee-firmware-ui-demo/integration/vclip.c`
và không thuộc build firmware.
