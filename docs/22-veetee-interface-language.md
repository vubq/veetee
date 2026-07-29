# Veetee Interface Language

Tài liệu này là visual, copy, accessibility và responsive contract chung cho
Manager Web và captive Wi-Fi portal. Nó không thay đổi API, wire protocol, NVS,
activation hay desired/reported semantics.

## 1. Product character

Veetee là một trợ lý gia đình thông minh và một công cụ vận hành đáng tin cậy:
ấm áp, bình tĩnh, rõ ràng và có năng lực kỹ thuật. Giao diện không mô phỏng trực
tiếp Apple Home, Linear, Vercel hay Material; cũng không dùng admin template xám
mặc định.

- Giữ dấu nhận diện hình vuông cam bo tròn, hơi nghiêng, có hai chấm sáng.
- Dùng `Veetee` trong văn xuôi và tên sản phẩm; lockup có thể dùng `veetee`.
- `VEETEE` chỉ dùng cho technical eyebrow rất ngắn.
- Decorative gradient/dot field chỉ dùng có chủ đích ở login, onboarding, hero và
  empty state. Màn hình vận hành ưu tiên khả năng đọc và scan dữ liệu.

Manager Web đóng gói Be Vietnam Pro cục bộ. Captive portal dùng system font và
không tải font/asset từ Internet.

## 2. Semantic color

Màu được đặt theo vai trò, không theo tên component. Mỗi runtime triển khai token
riêng nhưng phải giữ cùng ý nghĩa.

| Role | Light | Dark | Use |
|---|---|---|---|
| Canvas | `#f3f3ed` | `#0d1719` | Page background |
| Surface | `#fbfbf7` | `#142225` | Primary content surface |
| Surface raised | `#ffffff` | `#1a2b2f` | Dialog/card/control |
| Surface inset | `#eef2ed` | `#0f1d20` | Code/data inset |
| Text primary | `#13272c` | `#f1f4ee` | Headings/body |
| Text secondary | `#284047` | `#c2d0ce` | Supporting copy |
| Text muted | `#687b7f` | `#91a6a5` | Metadata only |
| Border | `#d9ded8` | `#33484b` | Default boundary |
| Technical | `#102c33` | `#b9dcdf` | Technical context |
| Action | `#f2643c` | `#ff7651` | Primary action/focus accent |
| Health | `#c8f36b` | `#b5e95a` | Healthy state, never alone |
| Information | `#dceeee` | `#183a41` | Informational surface |
| Success | `#18745e` | `#56c7a4` | Confirmed success |
| Warning | `#9a6500` | `#f0bd55` | Attention/retry |
| Danger | `#b9382b` | `#ff806f` | Error/destructive action |

Dark mode is not a mechanical inversion. Manager Web offers `Sáng | Hệ thống |
Tối`, persists the browser preference locally and uses `Hệ thống` by default; the
captive portal continues to follow the operating system without storing a separate
preference. Text and interactive boundary contrast must satisfy WCAG 2.2 AA. Status
always includes a label and icon/shape; color is not the only signal.

## 3. Type, space and density

- Page title: strong hierarchy and short Vietnamese copy; avoid all caps.
- Body and form copy: minimum 14 px on Web, 15 px in captive mobile WebView.
- Supporting text: minimum 12 px; 10 px is reserved for bounded technical labels.
- Controls use at least 44 px height on touch layouts.
- Spacing follows a 4 px base with 8/12/16/24/32/48 px primary steps.
- Radius tiers: 10 px control, 16 px card, 24 px shell/hero.

Container density:

- `airy`: overview, login, onboarding, pairing and empty state.
- `comfortable`: agent/provider forms and Realtime Lab.
- `compact`: fleet, resources, rollout, audit, telemetry and diagnostics.

Compact means less vertical whitespace, not smaller tap targets or illegible type.
At narrow widths, tables become bounded cards/rows or use an internal scroll region;
the whole page must not overflow horizontally.

## 4. Motion and interaction

- Motion explains route, hierarchy, progress or save state; no ambient looping.
- Route/surface transitions use short opacity and at most a few pixels translation.
- Drawer/dialog transitions are short and non-springy.
- Loading has a textual state and bounded skeleton/progress treatment.
- `prefers-reduced-motion: reduce` disables transforms, smooth scrolling and
  nonessential animation.
- Every keyboard-operated dialog/drawer traps focus, supports Escape and restores
  focus to the trigger.
- Route changes update document title and move focus to the new main content target.

## 5. Copy and localization

`vi-VN` is the default and `en-US` is the fallback. Every new Vietnamese locale key
must have an English counterpart.

Preferred terms:

| Product concept | Vietnamese UI |
|---|---|
| Device | Thiết bị |
| Agent | Trợ lý |
| AI provider | Nhà cung cấp AI |
| Publish immutable version | Xuất bản |
| Rollout | Phân phối |
| Desired state | Trạng thái mong muốn |
| Reported/applied state | Trạng thái thiết bị báo cáo / Đã áp dụng trên thiết bị |
| Pairing | Ghép thiết bị |

Do not translate route names, hashes, model IDs, provider IDs, request IDs or raw
protocol fields. Never describe desired/published state as active until reported
state confirms it. Error copy is calm, identifies the failed step, gives the next
action and exposes a bounded request ID when useful; it does not dump raw exceptions.

## 6. Captive Wi-Fi portal

The normal mobile flow is deliberately short:

1. Select or enter Wi-Fi.
2. Enter a password when required.
3. Connect and observe progress until station DHCP succeeds.
4. Open Veetee Manager and enter the six-digit code shown on the robot.

Bootstrap URL, locale, IANA time zone, wake profile and hidden-network controls live
under Advanced unless required to complete the request. The browser never receives
saved passwords, activation code, challenge or device token.

The setup SoftAP is currently open and served over HTTP. Copy must say that the
password goes directly to the nearby robot and is not returned/logged/sent to the
Internet; it must not imply encrypted radio transport. Portal assets are fully
embedded, use system fonts and obey firmware byte/stack/chunk budgets.

“Wi-Fi connected” means `IP_EVENT_STA_GOT_IP`. It does not mean Manager bootstrap,
activation or pairing has completed. If the captive WebView closes, the robot display
remains the authoritative activation-code handoff.

## 7. Runtime boundaries

Shared literally where practical:

- semantic color values;
- mark geometry;
- product casing/terminology;
- named loading/scanning/saving/connecting/connected/failed states;
- accessibility and status conventions.

Shared conceptually, not literally:

- information hierarchy, spacing rhythm, density and copy voice;
- responsive goals and success/recovery flow.

Never share literally:

- Vue components, Headless UI, TanStack Query or vue-i18n with firmware;
- bundled Web fonts;
- Web breakpoints/complex layouts;
- prototype HTML/JavaScript through runtime injection.
