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

export function preferredDevice(devices: Device[], now = Date.now()): Device | undefined {
  const rank: Record<DevicePresenceState, number> = { online: 4, idle: 3, stale: 2, offline: 1 };
  return [...devices].sort((left, right) => {
    const stateDelta = rank[devicePresence(right, now).state] - rank[devicePresence(left, now).state];
    if (stateDelta) return stateDelta;
    return Date.parse(right.lastSeenAt ?? right.pairedAt) - Date.parse(left.lastSeenAt ?? left.pairedAt);
  })[0];
}
