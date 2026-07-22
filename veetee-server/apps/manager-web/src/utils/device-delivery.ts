import type { Device } from "../api/schemas";

export type DeliveryState = "unmanaged" | "synced" | "pending" | "drift" | "failed" | "rolled_back";

export interface DeliverySubsystem {
  id: "resource" | "ui";
  label: string;
  desiredVersion?: string;
  currentVersion?: string;
  phase?: string;
  state: DeliveryState;
  message: string;
}

export interface DeviceDeliverySummary {
  state: DeliveryState;
  title: string;
  description: string;
  subsystems: DeliverySubsystem[];
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function subsystem(
  id: DeliverySubsystem["id"],
  label: string,
  desiredVersion: string | undefined,
  reportValue: unknown,
): DeliverySubsystem {
  const report = record(reportValue);
  const phase = stringValue(report?.phase);
  const currentVersion = stringValue(report?.currentVersion);

  if (!desiredVersion) {
    return {
      id,
      label,
      ...(currentVersion ? { currentVersion } : {}),
      ...(phase ? { phase } : {}),
      state: "unmanaged",
      message: "Chưa có phiên bản desired cho hạng mục này.",
    };
  }

  if (phase === "failed" || phase === "rolled_back") {
    return {
      id,
      label,
      desiredVersion,
      ...(currentVersion ? { currentVersion } : {}),
      phase,
      state: phase,
      message: phase === "failed"
        ? "Firmware báo áp dụng thất bại; thiết bị vẫn giữ bản an toàn trước đó."
        : "Firmware đã rollback về bản an toàn trước đó.",
    };
  }

  if (phase === "active" && currentVersion === desiredVersion) {
    return {
      id,
      label,
      desiredVersion,
      currentVersion,
      phase,
      state: "synced",
      message: "Firmware đã verify, kích hoạt và report đúng phiên bản desired.",
    };
  }

  if (phase === "active" && currentVersion && currentVersion !== desiredVersion) {
    return {
      id,
      label,
      desiredVersion,
      currentVersion,
      phase,
      state: "drift",
      message: "Thiết bị đang active một phiên bản khác với desired.",
    };
  }

  return {
    id,
    label,
    desiredVersion,
    ...(currentVersion ? { currentVersion } : {}),
    ...(phase ? { phase } : {}),
    state: "pending",
    message: report
      ? "Đang chờ firmware hoàn tất download, verify, apply và report active."
      : "Thiết bị chưa report tiến trình cho phiên bản desired này.",
  };
}

export function summarizeDeviceDelivery(device: Device): DeviceDeliverySummary {
  const desired = device.desiredState.state;
  const reported = device.reportedState.state;
  const subsystems = [
    subsystem(
      "resource",
      "Wake / model",
      stringValue(desired.resourceBundleVersion),
      reported.resource,
    ),
    subsystem(
      "ui",
      "Display / UI Pack",
      stringValue(desired.uiPackVersion),
      reported.ui,
    ),
  ];
  const managed = subsystems.filter((item) => item.state !== "unmanaged");

  if (!managed.length) {
    return {
      state: "unmanaged",
      title: "Chưa có delivery cần đối chiếu",
      description: "Desired revision và report sequence là hai bộ đếm độc lập; chúng không được dùng để suy ra drift.",
      subsystems,
    };
  }
  if (managed.some((item) => item.state === "failed")) {
    return {
      state: "failed",
      title: "Có delivery áp dụng thất bại",
      description: "Mở hạng mục lỗi để xem phiên bản thiết bị giữ lại và thực hiện reconcile khi thiết bị sẵn sàng.",
      subsystems,
    };
  }
  if (managed.some((item) => item.state === "rolled_back")) {
    return {
      state: "rolled_back",
      title: "Thiết bị đã rollback một delivery",
      description: "Bản desired chưa được áp dụng; firmware đang chạy bản an toàn trước đó.",
      subsystems,
    };
  }
  if (managed.some((item) => item.state === "drift")) {
    return {
      state: "drift",
      title: "Phiên bản active khác desired",
      description: "Thiết bị đã report active nhưng phiên bản thực tế không khớp yêu cầu hiện tại.",
      subsystems,
    };
  }
  if (managed.some((item) => item.state === "pending")) {
    return {
      state: "pending",
      title: "Đang chờ thiết bị áp dụng delivery",
      description: "Rollout mới chỉ cập nhật desired state; hoàn tất khi firmware report phase active đúng phiên bản.",
      subsystems,
    };
  }
  return {
    state: "synced",
    title: "Các delivery đang đồng bộ",
    description: "Mọi hạng mục có desired version đều đã được firmware report active đúng phiên bản.",
    subsystems,
  };
}

export function deliveryTone(state: DeliveryState): "success" | "warning" | "danger" | "neutral" {
  if (state === "synced") return "success";
  if (["failed", "rolled_back"].includes(state)) return "danger";
  if (["pending", "drift"].includes(state)) return "warning";
  return "neutral";
}

export function deliveryLabel(state: DeliveryState): string {
  return {
    unmanaged: "Chưa quản lý",
    synced: "Đã áp dụng",
    pending: "Đang chờ",
    drift: "Lệch phiên bản",
    failed: "Thất bại",
    rolled_back: "Đã rollback",
  }[state];
}
