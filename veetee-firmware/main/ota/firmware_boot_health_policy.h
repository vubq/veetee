#pragma once

#include <cstdint>

namespace veetee::ota {

enum class FirmwareBootHealthDecision : std::uint8_t {
    kWait,
    kConfirm,
    kRollback,
};

struct FirmwareBootHealthSnapshot {
    bool pending_verify = false;
    bool deadline_expired = false;
    bool identity_valid = false;
    bool authenticated_bootstrap_complete = false;
    bool app_idle = false;
    bool capture_task_running = false;
    bool playback_task_running = false;
    bool wake_resource_healthy = false;
    bool ui_pack_healthy = false;
    bool wake_task_required = false;
    bool wake_task_running = false;
};

FirmwareBootHealthDecision EvaluateFirmwareBootHealth(
    const FirmwareBootHealthSnapshot& snapshot);

bool FirmwareBootHealthDeadlineExpired(
    std::int64_t now_us, std::int64_t overall_deadline_us,
    std::int64_t post_wifi_deadline_us);

bool FirmwareHealthPollFailureRequiresRollback(
    bool timer_armed, std::uint8_t consecutive_failures,
    std::uint8_t failure_limit);

const char* ToString(FirmwareBootHealthDecision decision);

}  // namespace veetee::ota
