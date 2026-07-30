#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace veetee::maintenance {

// The executor owns one blocking HTTP/TLS stack. Jobs keep their own parser,
// generation, journal and outbox state; this enum only defines scheduling lanes.
enum class MaintenanceJobKind : std::uint8_t {
    kReporter,
    kFirmware,
    kBootstrap,
    kDeviceConfig,
    kWakeResource,
    kUiPack,
    kCount,
};

inline constexpr std::size_t kMaintenanceJobCount =
    static_cast<std::size_t>(MaintenanceJobKind::kCount);
inline constexpr std::uint32_t kRealtimeMaintenanceBarrierMs = 150;

constexpr bool IsRealtimeMaintenanceBarrierWithinBudget(
    std::uint32_t elapsed_ms) {
    return elapsed_ms <= kRealtimeMaintenanceBarrierMs;
}

constexpr std::uint32_t RemainingRealtimeMaintenanceBarrierMs(
    std::uint32_t elapsed_ms) {
    return elapsed_ms >= kRealtimeMaintenanceBarrierMs
               ? 0
               : kRealtimeMaintenanceBarrierMs - elapsed_ms;
}

inline constexpr std::array<MaintenanceJobKind, kMaintenanceJobCount>
    kMaintenancePriority = {
        MaintenanceJobKind::kReporter,
        MaintenanceJobKind::kFirmware,
        MaintenanceJobKind::kBootstrap,
        MaintenanceJobKind::kDeviceConfig,
        MaintenanceJobKind::kWakeResource,
        MaintenanceJobKind::kUiPack,
};

constexpr std::size_t MaintenanceJobIndex(MaintenanceJobKind kind) {
    return static_cast<std::size_t>(kind);
}

constexpr bool IsValidMaintenanceJob(MaintenanceJobKind kind) {
    return MaintenanceJobIndex(kind) < kMaintenanceJobCount;
}

// Voice is always admitted ahead of control-plane HTTP work. Once executable
// OTA crosses the upgrading boundary, only its worker and durable reporting may
// run until the application task releases exclusivity.
constexpr bool CanRunMaintenanceJob(MaintenanceJobKind kind,
                                    bool realtime_active,
                                    bool firmware_exclusive) {
    if (!IsValidMaintenanceJob(kind) || realtime_active) return false;
    if (!firmware_exclusive) return true;
    return kind == MaintenanceJobKind::kFirmware ||
           kind == MaintenanceJobKind::kReporter;
}

// A blocking HTTP request may be interrupted when a higher-priority runtime
// boundary closes its scheduling lane. Executable OTA is the sole exception
// after the application has entered its exclusive upgrading boundary.
constexpr bool CanPreemptMaintenanceHttp(MaintenanceJobKind kind,
                                         bool firmware_exclusive) {
    return IsValidMaintenanceJob(kind) &&
           !(kind == MaintenanceJobKind::kFirmware && firmware_exclusive);
}

// A job selected before a gate transition must not be dispatched using the
// stale decision. The executor also rechecks the live gate before an HTTP
// client can be registered, closing the final handler-entry race.
constexpr bool IsMaintenanceDispatchCurrent(MaintenanceJobKind kind,
                                            std::uint32_t selected_epoch,
                                            std::uint32_t current_epoch,
                                            bool realtime_active,
                                            bool firmware_exclusive) {
    return selected_epoch == current_epoch &&
           CanRunMaintenanceJob(kind, realtime_active, firmware_exclusive);
}

constexpr std::size_t MaintenancePriorityRank(MaintenanceJobKind kind) {
    for (std::size_t rank = 0; rank < kMaintenancePriority.size(); ++rank) {
        if (kMaintenancePriority[rank] == kind) return rank;
    }
    return kMaintenancePriority.size();
}

}  // namespace veetee::maintenance
