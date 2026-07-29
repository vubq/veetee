#include "ota/firmware_boot_health_policy.h"

namespace veetee::ota {

FirmwareBootHealthDecision EvaluateFirmwareBootHealth(
    const FirmwareBootHealthSnapshot& snapshot) {
    if (!snapshot.pending_verify) {
        return FirmwareBootHealthDecision::kWait;
    }

    const bool ready =
        snapshot.identity_valid && snapshot.authenticated_bootstrap_complete &&
        snapshot.app_idle && snapshot.capture_task_running &&
        snapshot.playback_task_running && snapshot.wake_resource_healthy &&
        snapshot.ui_pack_healthy &&
        (!snapshot.wake_task_required || snapshot.wake_task_running);
    if (ready) {
        return FirmwareBootHealthDecision::kConfirm;
    }
    return snapshot.deadline_expired
               ? FirmwareBootHealthDecision::kRollback
               : FirmwareBootHealthDecision::kWait;
}

bool FirmwareBootHealthDeadlineExpired(
    std::int64_t now_us, std::int64_t overall_deadline_us,
    std::int64_t post_wifi_deadline_us) {
    if (now_us < 0) return false;
    return (overall_deadline_us > 0 && now_us >= overall_deadline_us) ||
           (post_wifi_deadline_us > 0 && now_us >= post_wifi_deadline_us);
}

bool FirmwareHealthPollFailureRequiresRollback(
    bool timer_armed, std::uint8_t consecutive_failures,
    std::uint8_t failure_limit) {
    return !timer_armed && failure_limit != 0 &&
           consecutive_failures >= failure_limit;
}

const char* ToString(FirmwareBootHealthDecision decision) {
    switch (decision) {
        case FirmwareBootHealthDecision::kWait: return "wait";
        case FirmwareBootHealthDecision::kConfirm: return "confirm";
        case FirmwareBootHealthDecision::kRollback: return "rollback";
    }
    return "unknown";
}

}  // namespace veetee::ota
