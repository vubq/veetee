#pragma once

#include <cstdint>

#include "settings/firmware_ota_attempt_record.h"

namespace veetee::ota {

enum class FirmwareRunningImageState : std::uint8_t {
    kUnknown,
    kPendingVerify,
    kValid,
};

enum class FirmwareOtaRecoveryDecision : std::uint8_t {
    kNone,
    kPendingHealth,
    kActive,
    kRolledBack,
    kFailed,
    kInconsistent,
};

enum class FirmwareTerminalReplayStep : std::uint8_t {
    kMarkAttempt,
    kPersistReport,
    kClearAttempt,
    kComplete,
};

struct FirmwareTerminalReplayProgress {
    bool journal_transition_required = true;
    bool attempt_marked = false;
    bool report_persisted = false;
    bool attempt_cleared = false;
};

struct FirmwareOtaRecoverySnapshot {
    settings::FirmwareOtaAttemptRecord attempt{};
    const char* running_version = nullptr;
    std::uint8_t running_slot = 0;
    FirmwareRunningImageState image_state = FirmwareRunningImageState::kUnknown;
};

FirmwareOtaRecoveryDecision DecideFirmwareOtaRecovery(
    const FirmwareOtaRecoverySnapshot& snapshot);
FirmwareTerminalReplayStep NextFirmwareTerminalReplayStep(
    const FirmwareTerminalReplayProgress& progress);
bool FirmwareCancelCanContinueAfterRestore(bool restore_required,
                                           bool restore_succeeded);
bool FirmwareCancelCanReleaseStagedOwnership(
    bool terminal_transition_required, bool terminal_transition_persisted);
const char* FirmwareOtaRecoveryDecisionName(
    FirmwareOtaRecoveryDecision decision);

}  // namespace veetee::ota
