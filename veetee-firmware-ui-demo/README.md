# Veetee Firmware UI Demo

Demo độc lập, không cần build, cho giao diện thiết bị **ESP32-S3 + ST7789 240×280**.
Mục đích là chốt hướng hình ảnh **trước** khi viết renderer firmware, và giữ hướng đó
kiểm chứng được với ràng buộc panel thật.

Ba giao diện, giữ nguyên ba composition ID của UI ABI 1 làm khóa tương thích:

| ID | Tên sản phẩm | Tên trong demo | Kỹ thuật vẽ |
|---|---|---|---|
| `signal` | 01 · Mobile | **OS** | Vector anti-aliased + `.vfont` |
| `monolith` | 02 · Companion | **Hiyori Momose** | Phát clip VTCLIP1 + HUD vector đè lên |
| `quiet` | 03 · Robot Face | **Đôi mắt** | Vector anti-aliased, partial flush |

## Chạy

ES modules cần HTTP, không mở bằng `file://`.

```bash
cd veetee-firmware-ui-demo
./serve.sh          # hoặc serve.bat trên Windows, hoặc: python -m http.server 8080
```

Mở `http://localhost:8080`.

Self-check không cần cài gì (contract mirror, roundtrip VTCLIP1, từ chối container hỏng,
giới hạn panel/ngân sách, 546 frame của ba màn hình, đường PNG → `.vclip`, id HTML/JS):

```bash
node tools/check.mjs
```

## Vì sao nó "giống y hệt ESP32"

Demo không phải mockup phóng to. Mọi pixel đi qua đúng đường mà firmware đi:

- Vẽ **một lần** vào canvas đúng `240 × 280` đơn vị panel. Không có toạ độ CSS, không
  có `devicePixelRatio` trong đường vẽ.
- **Lượng tử hóa RGB565** (5/6/5) sau khi vẽ, đúng như framebuffer ghi vào PSRAM. Bật/tắt
  được để thấy dải màu bị mất; có tuỳ chọn dither 4×4 Bayer.
- Phóng to bằng **nearest-neighbour**, nên preview không bịa ra chi tiết subpixel mà panel
  không hiển thị được.
- Chỉ dùng primitive mà renderer firmware tái tạo được: rounded-rect / circle / arc /
  capsule bằng distance field có anti-alias, một alpha ramp theo hàng cho scrim, và text
  blit alpha 8-bit từ atlas `.vfont`. Không blur, không shadow, không filter ảnh.
- Nhịp khung hình chọn được: `500 ms` (đúng renderer `0.3.1` đang ship) hoặc 30/15/12 fps
  (mục tiêu partial flush).
- Ô **ngân sách** tính bytes và thời gian SPI thật cho toàn khung và cho vùng bẩn, theo
  `CONFIG_VEETEE_LCD_SPI_CLOCK_HZ`. Ở 10 MHz mặc định trong `sdkconfig.defaults`, một lần
  flush toàn khung `240×280×2 = 134 400 B` mất ~107 ms — đây là lý do giao diện 01 và 03
  chỉ vẽ lại vùng nhỏ, còn giao diện 02 cần cân nhắc fps và nâng xung SPI.

Nguồn contract được mirror trong `src/contract.js`, phải giữ đồng bộ với:

- `veetee-firmware/main/display/st7789_display.cpp` → `kScreenCopy` (13 state, copy ASCII);
- `veetee-server/ui-packs/<theme>/theme.json` → palette theo state;
- `veetee-server/ui-packs/<theme>/strings/vi-VN.json` → bản chữ tiếng Việt;
- `veetee-server/apps/manager-web/src/device-ui/firmware-contract.ts` → software twin.

Chuyển ô *Bản chữ* sang `vi-VN` để xem yêu cầu thực tế của tiếng Việt có dấu — phần này
cần `.vfont` trong UI Pack trước khi quảng bá là đã render đủ dấu.

## Bảng màu

Mặc định demo dùng **token semantic của Manager Web** (`docs/22-veetee-interface-language.md` §2,
cột Dark) chứ không phối lại cho "trông giống". §7 nói rõ giá trị màu semantic là thứ được
chia sẻ *nguyên văn* giữa các runtime, nên đây là chép đúng giá trị:

| Vai trò | Giá trị | Dùng ở đâu trên thiết bị |
|---|---|---|
| Canvas | `#0d1719` | nền toàn màn |
| Surface | `#142225` | thẻ hero, tile, khuôn mặt |
| Surface raised | `#1a2b2f` | ô mã ghép, rãnh meter |
| Border | `#33484b` | mọi nét viền 1px |
| Text / secondary / muted | `#f1f4ee` / `#c2d0ce` / `#91a6a5` | ba bậc chữ |
| Action | `#ff7651` | ghép thiết bị |
| Health | `#b5e95a` | sẵn sàng, đang nghe, đang nói |
| Warning | `#f0bd55` | đánh giá, xử lý |
| Danger | `#ff806f` | mất ghép nối, đang hủy |

Accent chọn theo **vai trò của state**, không theo tên component — cùng quy tắc đặt màu như
Manager Web, nên hai runtime nói cùng một ngôn ngữ màu.

> **Đây là đề xuất, chưa phải thứ đang ship.** `veetee-server/ui-packs/*/theme.json` vẫn
> mang palette cũ (`#102C33` / `#FBFBF7` / `#C8F36B`). Chọn *UI Pack đang ship* trong ô
> **Bảng màu** để so sánh trực tiếp. Khi chốt hướng thì phải cập nhật ba file `theme.json`
> đó — demo cố tình không tự sửa, vì đổi chúng là đổi thứ thiết bị thật đang hiển thị.

## Mật độ và dấu riêng

Bản đầu của hai màn vector có **~124 lệnh vẽ** (OS) trong khung 240×280 — ngoặc góc kiểu HUD,
hàng tick, số chìm, chấm peak-hold, thanh accent. Đó là lý do nó đọc ra rối và cũ chứ không
nét. Giao diện OS hiện đại ở kích thước này chỉ dùng 10–20 phần tử. Hướng đã chốt là **bớt
đi**: tách khối bằng khoảng trắng, không bằng nét kẻ — ở RGB565 một nét 1px tương phản thấp
gần như biến mất, nên viền vừa tốn vừa bẩn.

| Giao diện | Trước | Sau |
|---|---|---|
| OS (`signal`) | ~124 lệnh vẽ | **28** |
| Đôi mắt (`quiet`) | ~34 lệnh vẽ | **15** |

Màu: một accent duy nhất **`#ff7651`** (Action của Manager Web, cũng là màu dấu nhận diện)
cho mọi trạng thái bình thường. Trước đó demo đổi tông theo state qua 5 màu khác nhau — đổi
màu chủ đạo mỗi lần đổi trạng thái thì không thể hài hoà. Chỉ hai trạng thái cảnh báo mới
lệch tông, và ngay cả khi đó **hình dạng mới là tín hiệu chính** (rãnh đứt đoạn, chân mày
cau), đúng yêu cầu "màu không phải tín hiệu duy nhất" của `docs/22` §2.

Dấu riêng của Veetee nằm trong cấu trúc chứ không phải logo dán thêm:

- **Hai chấm lệch chéo** của dấu nhận diện lặp lại trước dòng kicker trên màn OS.
- **Hai con mắt chính là hai chấm đó**: mắt phải đặt thấp hơn mắt trái đúng 2px, theo đúng
  độ lệch của hai chấm sáng trong dấu. Khuôn mặt là dấu nhận diện phóng to, không phải một
  con robot chung chung gắn logo ở góc.
- **Thanh tín hiệu là dãy hình vuông bo góc** theo đúng tỉ lệ bo `0.32` của dấu, không phải
  cột thẳng như mọi equalizer khác.

## Cái giá của animation

| Giao diện | Vùng bẩn/frame | So với toàn khung | Trần fps @10 MHz |
|---|---|---|---|
| OS (`signal`) | 11.3 KiB | 9% | 108 fps |
| Đôi mắt (`quiet`) | 28.1 KiB | 21% | 43 fps |
| Hiyori (`monolith`) | 99.4 KiB | 76% | 12 fps |

Bỏ khung mặt ở màn Đôi mắt cũng bỏ luôn hai vòng quầng sáng — thứ ngốn SPI nhất mà không
mang thông tin nào chỗ khác chưa nói. Cả hai màn vector giờ dư dả ở 30 fps ngay tại xung SPI
mặc định 10 MHz.

## Chỗ demo khác firmware hiện tại

Đây là chủ đích, không phải sai lệch cần sửa ngược:

- Renderer đang ship dùng font bitmap 5×7 và khối `FillRectangle`, đọc ra như pixel art.
  Demo là **mục tiêu**: hình học anti-aliased, chữ sắc nét, giữ nhận diện Veetee.
- Vẽ chữ trong demo dùng font hệ thống, nên metric có thể lệch nhẹ giữa các máy. Bản
  firmware sẽ khoá bằng một `.vfont` cụ thể trong UI Pack.
- Giao diện 02 trong firmware `0.3.1` là nhân vật vẽ bằng primitive; demo thay bằng đường
  ống clip mô tả bên dưới.

## Giao diện 02: đường ống Live2D → ESP32

Live2D **không** chạy trên ESP32-S3. Nhân vật được render trên PC rồi đóng thành frame:

```text
Hiyori.cmo3
  └─ Live2D Cubism Editor / Cubism Web runtime trên PC
       └─ PNG sequence từng animation
            └─ crop + resize về 240 × 280, composite lên nền đục
                 └─ RGB565 + PackBits RLE  →  VTCLIP1 (.vclip)
                      └─ ESP32-S3 giải nén span thẳng vào framebuffer
```

Hai cách tạo `.vclip`, cùng một encoder (`src/clip-codec.js`):

```bash
# A. Trực tiếp từ Live2D trong trình duyệt (không cần Cubism Editor)
#    mở http://localhost:8080/tools/capture-hiyori.html → "Xuất clip" → giải nén zip
#    vào assets/hiyori/

# B. Từ PNG sequence do Cubism Editor xuất
node tools/pack-clip.mjs pack ./assets/hiyori/png/idle ./assets/hiyori/idle.vclip \
  --fps=12 --width=240 --height=280 --fit=cover --background=#102C33
node tools/pack-clip.mjs inspect ./assets/hiyori/idle.vclip
```

Sau đó bấm **Nạp assets/hiyori** trong demo, hoặc chọn thẳng file `.vclip` +
`manifest.json` bằng nút *Chọn file*.

### Nguồn Hiyori và runtime (đã kiểm chứng online)

| Thứ | Nguồn | Ghi chú |
|---|---|---|
| Model Hiyori | [`Live2D/CubismWebSamples`](https://github.com/Live2D/CubismWebSamples) → `Samples/Resources/Hiyori/Hiyori.model3.json` | Đã xác nhận file tồn tại: `Hiyori.moc3`, `Hiyori.2048/texture_0{0,1}.png`, physics3/pose3/userdata3/cdi3, motion `Idle[0..8]` + `TapBody[0]` |
| Cubism Core | `https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js` | Phần mềm độc quyền, không đóng gói lại. Live2D khuyến cáo không dùng direct link cho production — chạy lặp lại thì tải SDK for Web về local |
| Runtime WebGL | [`pixi-live2d-display@0.4.0`](https://github.com/guansss/pixi-live2d-display) + `pixi.js@6.5.10` | **Phải ghim đúng cặp này.** 0.4.0 chạy PixiJS **v6**; PixiJS v7 chỉ được hỗ trợ từ `0.5.0-beta` |

Hai chi tiết dễ sai khi tự dựng lại tool capture:

1. **Ghép nhầm PixiJS v7 với `pixi-live2d-display@0.4.0`** thì runtime không khởi tạo được.
2. **Ghi tham số vào sai hook.** `Cubism4InternalModel.update()` chạy theo thứ tự
   `beforeMotionUpdate → motion → afterMotionUpdate → saveParameters → expression →
   eyeBlink → focus → naturalMovements → physics → pose → beforeModelUpdate →
   coreModel.update()`. Chỉ `beforeModelUpdate` nằm sau mọi bộ ghi tự động, nên đó là hook
   duy nhất giữ được pose tới frame render. Ghi ở `afterMotionUpdate` sẽ bị eyeBlink và
   naturalMovements đè, khiến hai lần chụp cùng một pha ra hai kết quả khác nhau.
   Kèm theo đó phải đặt `autoUpdate: false` để shared ticker không tự đẩy thời gian.

Điều kiện giấy phép — gồm **giới hạn thương mại theo ngưỡng doanh thu 10.000.000 JPY** và
quy định không được sửa thiết kế nhân vật — nằm trong `NOTICE.md`. Đọc trước khi đưa clip
Hiyori vào UI Pack phân phối cho người dùng cuối.

Điểm quan trọng của thiết kế: **chữ không nướng vào frame**. Firmware blit clip rồi vẽ đè
kicker / title / hint / mã ghép thiết bị bằng chính primitive vector của hai giao diện kia.
Nhờ vậy đổi locale, hiển thị mã pairing và copy lỗi không cần export lại nhân vật.

Lip-sync dùng overlay `mouth.vclip` chỉ phủ hình chữ nhật quanh miệng, nên một lượt nói
không tốn thêm frame toàn khung. Hai ràng buộc dễ làm sai:

- **Clip nền `speaking` phải để miệng đóng.** Nếu vừa bake chuyển động miệng vào clip nền
  vừa blit overlay thì hai nguồn đánh nhau, và miệng mấp máy theo nhịp cố định bất kể
  thiết bị đang nói gì.
- **Overlay phải chụp cho từng frame nền, không phải một lần.** Đầu nhân vật cử động suốt
  clip `speaking`, nên một khung miệng chụp ở đúng một pose sẽ lệch ở mọi frame khác và
  hiện thành một mảng chữ nhật sai chỗ trên mặt. Vì vậy overlay có
  `frame_nền × 4 mức` frame, phẳng hoá theo `baseIndex * levels + level`, khai báo bằng
  `per_frame: true` và `base` trong manifest. Loader từ chối overlay có số frame không
  khớp `base.frameCount × levels`.

### VTCLIP1

Little-endian, header 32 byte, bảng offset `frame_count × u32`, rồi payload:

| Offset | Kiểu | Ý nghĩa |
|---|---|---|
| 0 | `char[8]` | magic `VTCLIP1\0` |
| 8 / 10 | `u16` | width / height |
| 12 / 14 | `u16` | frame_count / fps (1..60) |
| 16 | `u32` | flags — bit 0 = delta, các bit khác phải bằng 0 |
| 20 | `u32` | payload_len |
| 24 | `u32` | CRC-32/ISO-HDLC của payload |
| 28 | `u32` | reserved — phải bằng 0 |

**Keyframe** (frame 0, và mọi frame khi bit delta tắt) là PackBits trên pixel RGB565:
`op & 0x80` là run `(op & 0x7F) + 1` pixel theo sau là một pixel; ngược lại là literal
`op + 1` pixel.

**Delta frame** (các frame sau frame 0 khi bit delta bật) so với frame ngay trước:

| Op | Ý nghĩa |
|---|---|
| `0x00..0x7F` | literal, `op + 1` pixel (1..128) |
| `0x80..0xBF` | run, `(op & 0x3F) + 1` pixel (1..64), theo sau 1 pixel |
| `0xC0..0xFE` | skip, `(op & 0x3F) + 1` pixel (1..63) — giữ nguyên frame trước |
| `0xFF` | skip dài, theo sau `u16` LE (1..65535) |

Mọi frame phải giải nén ra đúng `width × height` pixel.

Hệ quả cần nhớ khi tích hợp:

- Phát **tuần tự** từ frame 0. Đổi state thì nhảy về frame 0 — đó là keyframe nên phát
  được ngay, không phải tua.
- `SKIP` không ghi gì cả, nên firmware **không được xoá vùng clip giữa hai frame**. Đó cũng
  chính là chỗ tiết kiệm SPI: chỉ những pixel thật sự đổi mới phải đẩy ra panel.
- Overlay miệng **cố tình không delta**. Biên độ TTS nhảy tự do nên đó là truy cập ngẫu
  nhiên, mà chuỗi delta thì phải dựng lại từ keyframe. Overlay bé nên giữ frame độc lập
  gần như không tốn thêm gì.

Ngân sách thực tế: một UI slot là **2 MiB**, và tỉ lệ nén phụ thuộc hoàn toàn vào độ phẳng
của frame. Hai mốc đã đo bằng `pack-clip.mjs`:

Số đo thật trên Hiyori, và bài học rút ra:

| Bước | Mỗi frame | Tỉ lệ nén | 100 frame |
|---|---|---|---|
| RLE, full panel | ~39 KiB | ~3.4× | 3.81 MiB |
| RLE, đã crop theo bóng nhân vật | ~39.6 KiB | **~1.6×** | ~4.1 MiB |
| **Keyframe + delta, đã crop** | **~9 KiB** *(ước tính)* | — | **~1.1 MiB** |

Con số ở giữa là chỗ dễ hiểu nhầm nhất: crop **không** cải thiện tỉ lệ nén, nó còn làm tỉ
lệ tệ đi. Lý do là RLE chỉ ăn vùng phẳng, mà crop vứt đi đúng phần nền phẳng đó — phần
còn lại gần như toàn nhân vật có shading mềm, gần như không có run nào. Crop vẫn đáng làm
vì nó giảm **số pixel tuyệt đối** (và giảm byte đẩy qua SPI), nhưng nó không cứu được
ngân sách.

Thứ cứu được ngân sách là **delta frame**: nén *giữa* các frame thay vì *trong* một frame.
Giữa hai frame liên tiếp của một vòng lặp, đại đa số pixel giống hệt nhau, nên op `SKIP`
nuốt gọn. Đây cũng là cách khớp phần cứng nhất: framebuffer vốn đã chứa frame trước, nên
delta chỉ vá lên đó — không tốn thêm RAM, và chỉ pixel thật sự đổi mới phải đẩy ra panel.

### 2 MiB từ đâu ra, và nó có cứng thật không

Từ `veetee-firmware/partitions/veetee_16mb.csv`: `ui_0` và `ui_1` mỗi cái `0x200000`.
Nhưng dòng đầu chính file đó ghi:

```text
# Provisional N16R8 layout. Freeze only after ESP-SR, Opus, TLS and UI size probes.
```

Nghĩa là 2 MiB là **chỗ giữ chỗ đang chờ đúng phép đo này**, không phải giới hạn vật lý.
Bố cục 16 MiB hiện tại:

| Vùng | Kích thước | Ghi chú |
|---|---|---|
| `ota_0` + `ota_1` | 3.625 MiB × 2 | ảnh app A/B — pool lớn nhất có thể thu nếu app nhỏ hơn |
| `resource_0` + `resource_1` | 2 MiB × 2 | wake model A/B |
| `ui_0` + `ui_1` | 2 MiB × 2 | UI Pack A/B |
| `coredump` | 256 KiB | |
| chưa cấp phát | **384 KiB** | phần đuôi còn trống |

Cái **không** đổi được là A/B: `AGENTS.md` yêu cầu giữ slot active cho tới khi bundle mới
verify xong, và rollback phải sống sót qua mất điện. Nên gộp hai slot thành một để lấy
4 MiB là không được phép.

Vì vậy tool capture cho chọn ngân sách 2 / 3 / 4 MiB, kèm ghi chú thay đổi phân vùng mà
mỗi mức đòi hỏi — để dùng chính nó làm cái "UI size probe" mà file phân vùng đang chờ.
Demo báo cáo theo mốc 2 MiB hiện tại và cho biết bộ clip cần slot bao nhiêu, chỉ từ chối
khi vượt cả mốc 4 MiB.

Tool capture làm ba việc:

1. **Cắt theo bóng nhân vật.** Mọi pixel ngoài bóng đúng bằng màu nền phẳng mà renderer đã
   tô sẵn, nên cắt không mất gì về hình ảnh. Toạ độ crop đi vào `x`/`y` trong manifest.
2. **Mã hoá delta** cho clip nền (giữ keyframe ở frame 0 để đổi state phát được ngay).
3. **Tự cắt cho vừa ngân sách** nếu vẫn quá — bỏ bớt một nửa frame của clip nặng nhất và
   giảm fps tương ứng để giữ nguyên tốc độ chuyển động. Với delta thì bước này hiếm khi
   phải chạy. Nếu vẫn không vừa, tool **không** tải file về để bạn khỏi mất công giải nén
   rồi bị demo từ chối.

Hãy đo bằng
`node tools/pack-clip.mjs inspect` thay vì ước lượng; cả CLI lẫn tool capture đều in phần
trăm slot đã dùng, và demo từ chối bộ clip vượt 2 MiB đúng như parser thiết bị phải làm.
Muốn giảm dung lượng: bớt frame mỗi clip, giảm fps, hoặc giữ nền phẳng và ít nhiễu khi
export từ Cubism.

## Điều kiện tích hợp

Demo này **không** được inject vào firmware hay Manager Web dưới dạng HTML/JS runtime
(`docs/22-veetee-interface-language.md` §7). Nó là bản dựng hình để duyệt hướng.

Phần chuyển được sang firmware ngay, không đụng ABI:

- hình học và layout của giao diện 01 và 03 (`src/screens/os.js`, `src/screens/eyes.js`);
- quy tắc dẫn xuất token màu từ palette pack (`tokensFor` trong `src/contract.js`);
- danh sách vùng bẩn để partial flush (`DIRTY_REGIONS` trong `app.js`).

Phần **cần thay đổi ABI có phiên bản** trước khi ship, không được làm âm thầm:

- `clips/*.vclip` chưa nằm trong member allowlist của UI Pack
  (`docs/16-device-ui-and-ui-packs.md` §3). Thêm nó là chuyển `ui_abi` 1 → 2, kèm cập nhật
  `compatibility` trong manifest và gate tương thích ở Manager.
- `fonts/*.vfont` đã được allowlist nhưng renderer chưa dùng; bật lên là phần mở rộng của
  cùng ABI data-only, vẫn phải giữ copy ASCII tích hợp cho boot/recovery.
- Parser phải từ chối clip có `width`/`height` lớn hơn panel và clip vượt ngân sách slot.

`integration/vclip.c` + `integration/vclip.h` là bản decode tham chiếu viết theo đúng ràng
buộc firmware: không cấp phát, kiểm biên trên từng span, không tin header so với độ dài
buffer thật. File nằm trong demo và **không** thuộc build firmware; nó chỉ chứng minh định
dạng giải nén được trong ngân sách và sẵn sàng chuyển sang `main/display/` khi ABI mở.

## Cấu trúc

```text
index.html app.js styles.css     vỏ demo và bảng điều khiển
src/contract.js                  mirror 13 state, copy, palette, token
src/panel.js                     canvas 240×280 + lượng tử RGB565 + phóng nearest
src/draw.js                      primitive firmware phải tái tạo được
src/clip.js                      reader VTCLIP1 + nạp clip set
src/clip-codec.js                encoder VTCLIP1 dùng chung cho browser và Node
src/screens/os.js                giao diện 01
src/screens/companion.js         giao diện 02
src/screens/eyes.js              giao diện 03
tools/capture-hiyori.html|.js    Live2D → .vclip trong trình duyệt
tools/pack-clip.mjs              PNG sequence → .vclip, và inspect
tools/check.mjs                  self-check, không phụ thuộc package nào
integration/vclip.h|.c           decoder tham chiếu cho firmware
assets/hiyori/                   đích của clip đã đóng gói (không commit)
```

## Ghi chú

- Muốn thử pose và lip-sync Live2D thời gian thực thì dùng `tools/capture-hiyori.html`:
  nó chạy đúng runtime đó, chỉ khác là kết thúc bằng file `.vclip` thay vì xem cho vui.
  Demo chính có mục đích khác — cho thấy **thiết bị thật sẽ hiển thị gì**.
- `veetee-firmware/prototypes/device-ui/` là concept cũ đã bị thay thế, không dùng làm
  tham chiếu.
- Bản dựng trên host không thay cho nghiệm thu phần cứng. Orientation, màu, độ sáng,
  và độ trễ SPI phải kiểm trên board thật rồi báo cáo riêng.
- Xem `NOTICE.md` về điều kiện sử dụng sample data Live2D trước khi phân phối clip.
