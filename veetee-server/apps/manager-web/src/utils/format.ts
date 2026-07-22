export function formatDate(value?: string): string {
  if (!value) return "Chưa có";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

export function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (["online", "healthy", "ready", "complete", "published", "closed"].includes(status)) {
    return "success";
  }
  if (["idle", "unknown", "active", "validated", "half_open"].includes(status)) {
    return "warning";
  }
  if (["offline", "degraded", "failed", "rolled_back", "revoked", "open"].includes(status)) {
    return "danger";
  }
  return "neutral";
}

export function shortId(value: string, length = 10): string {
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}
