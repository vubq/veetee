import type { Device } from "../api/schemas";

export type DevicePresenceState = "online" | "stale" | "idle" | "offline";

export interface DevicePresence {
  state: DevicePresenceState;
  label: string;
  tone: "success" | "warning" | "danger";
}

const STALE_AFTER_MS = 15 * 60 * 1_000;

export function devicePresence(device: Device, now = Date.now()): DevicePresence {
  if (device.status === "offline") return { state: "offline", label: "Offline", tone: "danger" };
  if (device.status === "idle") return { state: "idle", label: "Đang rảnh", tone: "warning" };
  if (device.lastSeenAt) {
    const lastSeen = Date.parse(device.lastSeenAt);
    if (Number.isFinite(lastSeen) && now - lastSeen > STALE_AFTER_MS) {
      return { state: "stale", label: "Dữ liệu kết nối cũ", tone: "warning" };
    }
  }
  return { state: "online", label: "Online", tone: "success" };
}
