// Device contract mirrored from firmware and the standard UI Packs.
//
// Sources of truth (keep in sync, do not diverge silently):
//   - veetee-firmware/main/display/st7789_display.cpp  -> kScreenCopy
//   - veetee-server/ui-packs/<theme>/theme.json        -> palette
//   - veetee-server/ui-packs/<theme>/strings/vi-VN.json -> localized copy
//   - veetee-server/apps/manager-web/src/device-ui/firmware-contract.ts

export const PANEL = Object.freeze({
  board: "veetee-s3-n16r8",
  display: "st7789-240x280-rgb565",
  width: 240,
  height: 280,
  resourceAbi: 2,
  uiAbi: 1,
  // Bounded animation cadence used by the shipped 0.3.1 renderer.
  firmwareFrameMs: 500,
});

export const STATE_IDS = Object.freeze([
  "starting",
  "wifi_configuring",
  "network_connecting",
  "activating",
  "pairing_recovery",
  "idle",
  "connecting",
  "listening",
  "evaluating",
  "thinking",
  "speaking",
  "aborting",
  "closing",
]);

// Verbatim mirror of kScreenCopy. UI ABI 1 renders this operational ASCII copy
// from the executable so boot/recovery never depends on a pack font.
export const SCREEN_COPY = Object.freeze({
  starting: { number: "00", kicker: "SYSTEM / BOOT", title: "VEE TEE", hint: "INITIALIZING HARDWARE", label: "Khởi động" },
  wifi_configuring: { number: "01", kicker: "NETWORK / CONFIG", title: "WI-FI SETUP", hint: "OPEN 192.168.4.1", label: "Cấu hình Wi-Fi" },
  network_connecting: { number: "02", kicker: "NETWORK / LINK", title: "CONNECTING", hint: "TRYING SAVED NETWORKS", label: "Kết nối mạng" },
  activating: { number: "03", kicker: "DEVICE / PAIR", title: "PAIRING", hint: "ENTER CODE IN MANAGER", label: "Ghép thiết bị" },
  pairing_recovery: { number: "04", kicker: "DEVICE / RECOVERY", title: "PAIRING LOST", hint: "HOLD BUTTON FOR RECOVERY", label: "Mất ghép nối" },
  idle: { number: "05", kicker: "ASSISTANT / READY", title: "HEY VEETEE", hint: "BUTTON OR WAKE WORD", label: "Chờ" },
  connecting: { number: "06", kicker: "SESSION / OPEN", title: "CONNECTING", hint: "OPENING VOICE CHANNEL", label: "Mở trợ lý" },
  listening: { number: "07", kicker: "AUDIO / INPUT", title: "LISTENING", hint: "SPEAK NATURALLY", label: "Đang nghe" },
  evaluating: { number: "08", kicker: "INPUT / ADMISSION", title: "EVALUATING", hint: "SIGNAL AND INTENT CHECK", label: "Đánh giá" },
  thinking: { number: "09", kicker: "AI / EXECUTION", title: "THINKING", hint: "MODEL AND MCP TOOLS", label: "Xử lý" },
  speaking: { number: "10", kicker: "AUDIO / OUTPUT", title: "SPEAKING", hint: "PRESS TO INTERRUPT", label: "Đang nói" },
  aborting: { number: "11", kicker: "TURN / CANCEL", title: "STOPPING", hint: "CLEARING CURRENT TURN", label: "Đang hủy" },
  closing: { number: "12", kicker: "SESSION / CLOSE", title: "GOODBYE", hint: "READY TO WAKE AGAIN", label: "Kết thúc" },
});

// strings/vi-VN.json from each standard pack. Rendering these needs a .vfont
// member in the pack; that is an extension of the same data-only UI ABI 1.
const VI_SIGNAL = {
  starting: { kicker: "ĐANG KHỞI ĐỘNG", title: "VeeTee.", hint: "Đang kiểm tra hệ thống" },
  wifi_configuring: { kicker: "CẤU HÌNH WI-FI", title: "Kết nối mạng.", hint: "Mở 192.168.4.1 trên điện thoại" },
  network_connecting: { kicker: "ĐANG KẾT NỐI", title: "Một chút nhé.", hint: "Đang thử các mạng đã lưu" },
  activating: { kicker: "GHÉP THIẾT BỊ", title: "Nhập mã này.", hint: "Mở Manager Web · mã chỉ dùng một lần" },
  pairing_recovery: { kicker: "MẤT GHÉP NỐI", title: "Cần kết nối lại.", hint: "Giữ nút 5 giây để phục hồi" },
  idle: { kicker: "SẴN SÀNG", title: "Chào bạn.", hint: "Bấm nút hoặc nói “Hey VeeTee”" },
  connecting: { kicker: "ĐANG MỞ TRỢ LÝ", title: "Tôi ở đây.", hint: "Đang mở phiên hội thoại" },
  listening: { kicker: "ĐANG NGHE", title: "Bạn cứ nói.", hint: "Nói tự nhiên · bấm để ngắt" },
  evaluating: { kicker: "ĐANG ĐÁNH GIÁ", title: "Tôi đang nghe rõ.", hint: "Kiểm tra chất lượng và ý định" },
  thinking: { kicker: "ĐANG XỬ LÝ", title: "Một chút nhé.", hint: "AI có thể gọi công cụ khi cần" },
  speaking: { kicker: "ĐANG TRẢ LỜI", title: "Tôi đang nói.", hint: "Bấm nút để ngắt ngay" },
  aborting: { kicker: "ĐANG DỪNG", title: "Đã hiểu.", hint: "Đang hủy lượt hiện tại" },
  closing: { kicker: "SẮP NGỦ", title: "Hẹn gặp lại.", hint: "Bấm hoặc gọi để tiếp tục" },
};

const VI_MONOLITH = {
  starting: { kicker: "SYSTEM / BOOT", title: "VEE/TEE", hint: "Khởi tạo phần cứng" },
  wifi_configuring: { kicker: "NETWORK / CONFIG", title: "WI-FI", hint: "192.168.4.1" },
  network_connecting: { kicker: "NETWORK / LINK", title: "CONNECT", hint: "Đang thử profile mạng" },
  activating: { kicker: "DEVICE / PAIR", title: "PAIRING", hint: "Nhập mã trên Manager" },
  pairing_recovery: { kicker: "DEVICE / RECOVERY", title: "PAIRING LOST", hint: "Giữ nút 5 giây" },
  idle: { kicker: "ASSISTANT / READY", title: "STANDBY", hint: "BUTTON / HEY VEETEE" },
  connecting: { kicker: "SESSION / OPEN", title: "CONNECT", hint: "Đang mở kênh thoại" },
  listening: { kicker: "AUDIO / INPUT", title: "LISTEN", hint: "Bấm để ngắt" },
  evaluating: { kicker: "INPUT / ADMISSION", title: "EVALUATE", hint: "Kiểm tra tín hiệu và ý định" },
  thinking: { kicker: "AI / EXECUTION", title: "THINK", hint: "MODEL + MCP" },
  speaking: { kicker: "AUDIO / OUTPUT", title: "SPEAK", hint: "Bấm để ngắt ngay" },
  aborting: { kicker: "TURN / CANCEL", title: "ABORT", hint: "Đang xóa audio cũ" },
  closing: { kicker: "SESSION / CLOSE", title: "STANDBY", hint: "Có thể gọi lại trong lúc chào" },
};

const VI_QUIET = {
  starting: { kicker: "ĐANG THỨC DẬY", title: "VeeTee", hint: "" },
  wifi_configuring: { kicker: "KẾT NỐI", title: "Chọn Wi-Fi", hint: "Mở 192.168.4.1" },
  network_connecting: { kicker: "KẾT NỐI", title: "Đợi một chút", hint: "" },
  activating: { kicker: "GHÉP THIẾT BỊ", title: "Mã của bạn", hint: "Mở Manager Web" },
  pairing_recovery: { kicker: "CẦN KẾT NỐI LẠI", title: "Giữ nút 5 giây", hint: "" },
  idle: { kicker: "SẴN SÀNG", title: "Chào bạn", hint: "Bấm nút hoặc nói Hey VeeTee" },
  connecting: { kicker: "TÔI Ở ĐÂY", title: "Đang kết nối", hint: "" },
  listening: { kicker: "ĐANG NGHE", title: "Bạn cứ nói", hint: "Bấm để ngắt" },
  evaluating: { kicker: "ĐANG HIỂU", title: "Tôi đang nghe rõ", hint: "" },
  thinking: { kicker: "ĐANG NGHĨ", title: "Một chút nhé", hint: "" },
  speaking: { kicker: "ĐANG TRẢ LỜI", title: "Tôi đang nói", hint: "Bấm để ngắt" },
  aborting: { kicker: "ĐÃ DỪNG", title: "Tôi đang nghe", hint: "" },
  closing: { kicker: "HẸN GẶP LẠI", title: "Ngủ ngon", hint: "" },
};

const PALETTE_SIGNAL = {
  starting: { background: "#102C33", foreground: "#FBFBF7", accent: "#C8F36B" },
  wifi_configuring: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
  network_connecting: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
  activating: { background: "#102C33", foreground: "#FBFBF7", accent: "#C8F36B" },
  pairing_recovery: { background: "#102C33", foreground: "#FFF3EB", accent: "#F2643C" },
  idle: { background: "#102C33", foreground: "#FBFBF7", accent: "#C8F36B" },
  connecting: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
  listening: { background: "#102C33", foreground: "#FBFBF7", accent: "#C8F36B" },
  evaluating: { background: "#102C33", foreground: "#FBFBF7", accent: "#F0BD54" },
  thinking: { background: "#102C33", foreground: "#FBFBF7", accent: "#F0BD54" },
  speaking: { background: "#102C33", foreground: "#FFF3EB", accent: "#C8F36B" },
  aborting: { background: "#102C33", foreground: "#FFF3EB", accent: "#F2643C" },
  closing: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
};

const PALETTE_MONOLITH = {
  ...PALETTE_SIGNAL,
  evaluating: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
  thinking: { background: "#102C33", foreground: "#FBFBF7", accent: "#7DE2D1" },
};

const PALETTE_QUIET = Object.fromEntries(
  STATE_IDS.map((state) => [
    state,
    {
      background: "#102C33",
      foreground: state === "pairing_recovery" || state === "speaking" || state === "aborting" ? "#FFF3EB" : "#FBFBF7",
      accent: "#C8F36B",
    },
  ]),
);

// Cột Dark của bảng semantic color trong docs/22-veetee-interface-language.md §2.
// §7 nói rõ giá trị màu semantic là thứ được chia sẻ *nguyên văn* giữa các runtime,
// nên đây là chép đúng giá trị chứ không phải phối lại cho giống.
export const WEB_TOKENS = Object.freeze({
  canvas: "#0d1719",
  surface: "#142225",
  surfaceRaised: "#1a2b2f",
  surfaceInset: "#0f1d20",
  text: "#f1f4ee",
  textSecondary: "#c2d0ce",
  textMuted: "#91a6a5",
  border: "#33484b",
  technical: "#b9dcdf",
  action: "#ff7651",
  health: "#b5e95a",
  success: "#56c7a4",
  warning: "#f0bd55",
  danger: "#ff806f",
});

// Một accent duy nhất cho mọi trạng thái bình thường: cam Action #ff7651, cũng
// chính là màu dấu nhận diện Veetee. Đổi tông màu chủ đạo mỗi lần đổi state là
// thứ khiến bảng màu không thể hài hoà, nên chỉ cảnh báo thật mới đổi tông — và
// ngay cả khi đó, hình dạng mới là tín hiệu chính, đúng như docs/22 §2 yêu cầu
// "màu không phải tín hiệu duy nhất".
const WEB_ACCENT_ROLE = Object.freeze({
  pairing_recovery: "danger",
  aborting: "danger",
});
const WEB_ACCENT_DEFAULT = "action";

export const PALETTE_SOURCES = Object.freeze([
  { id: "web", label: "Manager Web (đề xuất)" },
  { id: "pack", label: "UI Pack đang ship" },
]);

export const COMPOSITIONS = Object.freeze([
  {
    id: "signal",
    index: "01",
    product: "Mobile",
    demoName: "OS",
    note: "Bảng thông tin kiểu điện thoại · built-in failsafe",
    technique: "vector",
    palette: PALETTE_SIGNAL,
    localized: VI_SIGNAL,
  },
  {
    id: "monolith",
    index: "02",
    product: "Companion",
    demoName: "Hiyori Momose",
    note: "Clip nhân vật decode từ UI Pack · HUD do firmware vẽ đè",
    technique: "clip",
    palette: PALETTE_MONOLITH,
    localized: VI_MONOLITH,
  },
  {
    id: "quiet",
    index: "03",
    product: "Robot Face",
    demoName: "Đôi mắt",
    note: "Hai mắt navy/lime, anti-aliased, partial-flush hai vùng nhỏ",
    technique: "vector",
    palette: PALETTE_QUIET,
    localized: VI_QUIET,
  },
]);

export function compositionById(id) {
  return COMPOSITIONS.find((composition) => composition.id === id) ?? COMPOSITIONS[0];
}

// `pack` phối màu bề mặt từ background/foreground, đúng cách st7789_display.cpp
// đang làm (chỉ khác là dùng alpha liên tục thay vì 1/8). `web` thì không phối
// gì cả — nó lấy thẳng token bề mặt của Manager Web, nên hai runtime dùng đúng
// cùng một giá trị màu thay vì hai màu "trông giống nhau".
export function tokensFor(composition, state, source = "web") {
  if (source === "web") {
    const accent = WEB_TOKENS[WEB_ACCENT_ROLE[state] ?? WEB_ACCENT_DEFAULT];
    return {
      background: rgb(WEB_TOKENS.canvas),
      foreground: rgb(WEB_TOKENS.text),
      accent: rgb(accent),
      panel: rgb(WEB_TOKENS.surface),
      panelRaised: rgb(WEB_TOKENS.surfaceRaised),
      inset: rgb(WEB_TOKENS.surfaceInset),
      hairline: rgb(WEB_TOKENS.border),
      secondary: rgb(WEB_TOKENS.textSecondary),
      muted: rgb(WEB_TOKENS.textMuted),
      accentSoft: mix(WEB_TOKENS.canvas, accent, 0.22),
      accentDim: mix(WEB_TOKENS.canvas, accent, 0.48),
      accentBright: mix(accent, WEB_TOKENS.text, 0.45),
    };
  }
  const base = composition.palette[state] ?? composition.palette.idle;
  return {
    background: rgb(base.background),
    foreground: rgb(base.foreground),
    accent: rgb(base.accent),
    panel: mix(base.background, base.foreground, 0.07),
    panelRaised: mix(base.background, base.foreground, 0.12),
    inset: mix(base.background, base.foreground, 0.04),
    hairline: mix(base.background, base.foreground, 0.2),
    secondary: mix(base.background, base.foreground, 0.75),
    muted: mix(base.background, base.foreground, 0.55),
    accentSoft: mix(base.background, base.accent, 0.24),
    accentDim: mix(base.background, base.accent, 0.5),
    accentBright: mix(base.accent, base.foreground, 0.45),
  };
}

function rgb(hex) {
  return mix(hex, hex, 0);
}

export function copyFor(composition, state, locale) {
  const ascii = SCREEN_COPY[state];
  if (locale !== "vi-VN") return ascii;
  const localized = composition.localized[state];
  return {
    number: ascii.number,
    label: ascii.label,
    kicker: localized.kicker || ascii.kicker,
    title: localized.title || ascii.title,
    hint: localized.hint || ascii.hint,
  };
}

export function parseHex(hex) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

export function mix(fromHex, toHex, amount) {
  const from = parseHex(fromHex);
  const to = parseHex(toHex);
  const weight = Math.max(0, Math.min(1, amount));
  const channel = (index) => Math.round(from[index] * (1 - weight) + to[index] * weight);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

export function withAlpha(color, alpha) {
  const [red, green, blue] = color.startsWith("#")
    ? parseHex(color)
    : color.match(/\d+/g).slice(0, 3).map(Number);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}
