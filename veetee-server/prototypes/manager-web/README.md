# Veetee Manager prototype

Prototype HTML/CSS/JS thuần để duyệt product direction trước khi chuyển sang Vue 3.

## Mở prototype

Mở trực tiếp `index.html` trong trình duyệt hoặc chạy static server:

```bash
python3 -m http.server 4173 --directory veetee-server/prototypes/manager-web
```

Các interaction mẫu: đổi trang bằng sidebar, mở modal ghép thiết bị 6 số, test provider, bắt đầu/dừng realtime lab và command palette.

## Visual direction

- “Friendly control room”: nền giấy ấm, navy kỹ thuật, coral cho action và acid green cho health.
- Typography ưu tiên Be Vietnam Pro + Space Grotesk; có fallback nếu offline.
- Không dùng layout admin template dạng bảng xám mặc định.
- Responsive: sidebar chuyển thành header/mobile dock dưới 760 px.

Prototype chỉ dùng fake data; không chứa credential hoặc API call thật.

## Trước khi chuyển sang Vue 3

Prototype V2 phải bổ sung hoặc có wireframe được duyệt cho:

- Wake/interrupt detector profiles và ESP-SR model pack;
- resource bundle, flash budget, signature và rollout;
- desired vs reported device state;
- conversation trace gồm admission/dialogue act/plan/cancellation;
- MCP safety class/confirmation;
- security/privacy/retention;
- offline/self-hosted fonts cho LAN không Internet.

Các quyết định UI còn mở nằm trong `../../../docs/13-decision-register.md`.
