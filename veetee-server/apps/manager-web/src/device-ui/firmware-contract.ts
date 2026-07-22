import monolithTheme from "../../../../ui-packs/monolith/theme.json";
import quietTheme from "../../../../ui-packs/quiet/theme.json";
import signalTheme from "../../../../ui-packs/signal/theme.json";

export const DEVICE_UI_TARGET = {
  board: "veetee-s3-n16r8",
  display: "st7789-240x280-rgb565",
  width: 240,
  height: 280,
  resourceAbi: 2,
  uiAbi: 1,
} as const;

export const FIRMWARE_STATE_IDS = [
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
] as const;

export type FirmwareStateId = (typeof FIRMWARE_STATE_IDS)[number];
export type FirmwareComposition = "signal" | "monolith" | "quiet";

export interface FirmwareScreenCopy {
  number: string;
  kicker: string;
  title: string;
  hint: string;
  label: string;
}

// UI ABI 1 intentionally uses the same operational ASCII copy as st7789_display.cpp.
export const FIRMWARE_SCREEN_COPY: Record<FirmwareStateId, FirmwareScreenCopy> = {
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
};

type ThemeSource = typeof signalTheme;

export interface FirmwareTheme {
  id: FirmwareComposition;
  index: string;
  name: string;
  note: string;
  composition: FirmwareComposition;
  palette: Record<FirmwareStateId, { background: string; foreground: string; accent: string }>;
}

function firmwareTheme(source: ThemeSource, index: string, name: string, note: string): FirmwareTheme {
  return {
    id: source.theme_id as FirmwareComposition,
    index,
    name,
    note,
    composition: source.composition as FirmwareComposition,
    palette: source.palette as FirmwareTheme["palette"],
  };
}

export const FIRMWARE_THEMES: FirmwareTheme[] = [
  firmwareTheme(signalTheme, "01", "Signal", "Built-in default và failsafe trong firmware"),
  firmwareTheme(monolithTheme, "02", "Monolith", "Standard UI Pack · composition compile sẵn"),
  firmwareTheme(quietTheme, "03", "Quiet", "Standard UI Pack · composition compile sẵn"),
];
