import type { ResourceRollout, UiPackRollout } from "../api/schemas";

export type DeliveryRolloutKind = "wake" | "ui";
export type DeliveryRolloutStatus = ResourceRollout["status"];

export interface DeliveryRollout {
  id: string;
  kind: DeliveryRolloutKind;
  deviceId: string;
  artifactId: string;
  desiredStateVersion: number;
  status: DeliveryRolloutStatus;
  createdAt: string;
  wakeProfileVersion?: number;
}

export function normalizeRollouts(
  resourceRollouts: ResourceRollout[],
  uiPackRollouts: UiPackRollout[],
): DeliveryRollout[] {
  return [
    ...resourceRollouts.map((rollout) => ({ ...rollout, kind: "wake" as const })),
    ...uiPackRollouts.map((rollout) => ({ ...rollout, kind: "ui" as const })),
  ].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
}

export function rolloutKindLabel(kind: DeliveryRolloutKind): string {
  return kind === "wake" ? "Wake / model" : "UI Pack";
}

export function rolloutStatusLabel(status: DeliveryRolloutStatus): string {
  return {
    active: "Chờ thiết bị",
    complete: "Đã áp dụng",
    failed: "Thất bại",
    rolled_back: "Đã rollback",
  }[status];
}
