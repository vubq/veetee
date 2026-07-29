#include "ota/firmware_ota_recovery_policy.h"

#include <cstring>

namespace veetee::ota {

FirmwareOtaRecoveryDecision DecideFirmwareOtaRecovery(
    const FirmwareOtaRecoverySnapshot& snapshot) {
    const auto& attempt = snapshot.attempt;
    if (!settings::IsValidFirmwareOtaAttemptRecord(attempt) ||
        attempt.has_attempt == 0 || snapshot.running_version == nullptr ||
        snapshot.running_slot > 1) {
        return FirmwareOtaRecoveryDecision::kNone;
    }
    const bool running_target =
        snapshot.running_slot == attempt.to_slot &&
        std::strcmp(snapshot.running_version, attempt.to_version) == 0;
    const bool running_previous =
        snapshot.running_slot == attempt.from_slot &&
        std::strcmp(snapshot.running_version, attempt.from_version) == 0;
    // Once a terminal outcome has been journaled, reboot recovery must replay
    // that exact outcome until its durable reported-state ACK is staged and the
    // attempt is cleared.  It must still match the image actually running:
    // terminal journal immutability is not permission to report a false Active
    // or RolledBack state after NVS/otadata divergence.
    switch (attempt.phase) {
        case settings::FirmwareOtaAttemptPhase::kActive:
            return running_target &&
                           snapshot.image_state == FirmwareRunningImageState::kValid
                       ? FirmwareOtaRecoveryDecision::kActive
                       : FirmwareOtaRecoveryDecision::kInconsistent;
        case settings::FirmwareOtaAttemptPhase::kRolledBack:
            return running_previous
                       ? FirmwareOtaRecoveryDecision::kRolledBack
                       : FirmwareOtaRecoveryDecision::kInconsistent;
        case settings::FirmwareOtaAttemptPhase::kFailed:
            return running_previous
                       ? FirmwareOtaRecoveryDecision::kFailed
                       : FirmwareOtaRecoveryDecision::kInconsistent;
        default:
            break;
    }
    if (running_target) {
        if (snapshot.image_state == FirmwareRunningImageState::kPendingVerify) {
            return FirmwareOtaRecoveryDecision::kPendingHealth;
        }
        if (snapshot.image_state == FirmwareRunningImageState::kValid) {
            return FirmwareOtaRecoveryDecision::kActive;
        }
        return FirmwareOtaRecoveryDecision::kFailed;
    }
    if (running_previous) {
        if (attempt.phase == settings::FirmwareOtaAttemptPhase::kStaged ||
            attempt.phase == settings::FirmwareOtaAttemptPhase::kFailed) {
            return FirmwareOtaRecoveryDecision::kFailed;
        }
        return FirmwareOtaRecoveryDecision::kRolledBack;
    }
    return FirmwareOtaRecoveryDecision::kFailed;
}

FirmwareTerminalReplayStep NextFirmwareTerminalReplayStep(
    const FirmwareTerminalReplayProgress& progress) {
    if (progress.journal_transition_required && !progress.attempt_marked) {
        return FirmwareTerminalReplayStep::kMarkAttempt;
    }
    if (!progress.report_persisted) {
        return FirmwareTerminalReplayStep::kPersistReport;
    }
    if (!progress.attempt_cleared) {
        return FirmwareTerminalReplayStep::kClearAttempt;
    }
    return FirmwareTerminalReplayStep::kComplete;
}

bool FirmwareCancelCanContinueAfterRestore(bool restore_required,
                                           bool restore_succeeded) {
    return !restore_required || restore_succeeded;
}

bool FirmwareCancelCanReleaseStagedOwnership(
    bool terminal_transition_required, bool terminal_transition_persisted) {
    return !terminal_transition_required || terminal_transition_persisted;
}

const char* FirmwareOtaRecoveryDecisionName(
    FirmwareOtaRecoveryDecision decision) {
    switch (decision) {
        case FirmwareOtaRecoveryDecision::kNone: return "none";
        case FirmwareOtaRecoveryDecision::kPendingHealth:
            return "pending_health";
        case FirmwareOtaRecoveryDecision::kActive: return "active";
        case FirmwareOtaRecoveryDecision::kRolledBack: return "rolled_back";
        case FirmwareOtaRecoveryDecision::kFailed: return "failed";
        case FirmwareOtaRecoveryDecision::kInconsistent:
            return "inconsistent";
    }
    return "unknown";
}

}  // namespace veetee::ota
