#include <cassert>
#include <iostream>

#include "ota/firmware_ota_recovery_policy.h"

namespace {

veetee::settings::FirmwareOtaAttemptRecord Attempt(
    veetee::settings::FirmwareOtaAttemptPhase phase) {
    auto record = veetee::settings::MakeDefaultFirmwareOtaAttemptRecord();
    assert(veetee::settings::BeginFirmwareOtaAttempt(
        &record, "0.3.1", "0.4.0", 0, 1, 2, 1532480));
    if (phase == veetee::settings::FirmwareOtaAttemptPhase::kStaged) {
        return record;
    }
    assert(veetee::settings::AdvanceFirmwareOtaAttempt(
        &record, veetee::settings::FirmwareOtaAttemptPhase::kRebooting));
    if (phase == veetee::settings::FirmwareOtaAttemptPhase::kPendingHealth) {
        assert(veetee::settings::AdvanceFirmwareOtaAttempt(
            &record,
            veetee::settings::FirmwareOtaAttemptPhase::kPendingHealth));
    }
    return record;
}

void TestTerminalReplayOrder() {
    using veetee::ota::FirmwareTerminalReplayProgress;
    using veetee::ota::FirmwareTerminalReplayStep;
    using veetee::ota::NextFirmwareTerminalReplayStep;

    FirmwareTerminalReplayProgress progress{};
    assert(NextFirmwareTerminalReplayStep(progress) ==
           FirmwareTerminalReplayStep::kMarkAttempt);
    progress.attempt_marked = true;
    assert(NextFirmwareTerminalReplayStep(progress) ==
           FirmwareTerminalReplayStep::kPersistReport);
    // A failed report persist retries the same step and must never advance to
    // clearing the only durable OTA attempt.
    assert(NextFirmwareTerminalReplayStep(progress) ==
           FirmwareTerminalReplayStep::kPersistReport);
    progress.report_persisted = true;
    assert(NextFirmwareTerminalReplayStep(progress) ==
           FirmwareTerminalReplayStep::kClearAttempt);
    progress.attempt_cleared = true;
    assert(NextFirmwareTerminalReplayStep(progress) ==
           FirmwareTerminalReplayStep::kComplete);

    FirmwareTerminalReplayProgress inconsistent{};
    inconsistent.journal_transition_required = false;
    assert(NextFirmwareTerminalReplayStep(inconsistent) ==
           FirmwareTerminalReplayStep::kPersistReport);
}

void TestCancelKeepsOwnershipUntilRestoreAndJournalSucceed() {
    using veetee::ota::FirmwareCancelCanContinueAfterRestore;
    using veetee::ota::FirmwareCancelCanReleaseStagedOwnership;

    assert(!FirmwareCancelCanContinueAfterRestore(true, false));
    assert(FirmwareCancelCanContinueAfterRestore(true, true));
    assert(FirmwareCancelCanContinueAfterRestore(false, false));
    assert(!FirmwareCancelCanReleaseStagedOwnership(true, false));
    assert(FirmwareCancelCanReleaseStagedOwnership(true, true));
    assert(FirmwareCancelCanReleaseStagedOwnership(false, false));
}

}  // namespace

int main() {
    using veetee::ota::DecideFirmwareOtaRecovery;
    using veetee::ota::FirmwareOtaRecoveryDecision;
    using veetee::ota::FirmwareOtaRecoverySnapshot;
    using veetee::ota::FirmwareRunningImageState;
    using veetee::settings::FirmwareOtaAttemptPhase;

    const auto rebooting = Attempt(FirmwareOtaAttemptPhase::kRebooting);
    assert(DecideFirmwareOtaRecovery({
               .attempt = rebooting,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kPendingVerify,
           }) == FirmwareOtaRecoveryDecision::kPendingHealth);
    assert(DecideFirmwareOtaRecovery({
               .attempt = rebooting,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kActive);
    assert(DecideFirmwareOtaRecovery({
               .attempt = rebooting,
               .running_version = "0.3.1",
               .running_slot = 0,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kRolledBack);

    const auto staged = Attempt(FirmwareOtaAttemptPhase::kStaged);
    assert(DecideFirmwareOtaRecovery({
               .attempt = staged,
               .running_version = "0.3.1",
               .running_slot = 0,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kFailed);
    // Power can fail after otadata commit but before the rebooting phase is
    // persisted. The running target and ESP image state remain authoritative.
    assert(DecideFirmwareOtaRecovery({
               .attempt = staged,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kPendingVerify,
           }) == FirmwareOtaRecoveryDecision::kPendingHealth);
    assert(DecideFirmwareOtaRecovery({
               .attempt = rebooting,
               .running_version = "9.9.9",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kFailed);

    auto active = rebooting;
    assert(veetee::settings::AdvanceFirmwareOtaAttempt(
        &active, FirmwareOtaAttemptPhase::kActive));
    assert(DecideFirmwareOtaRecovery({
               .attempt = active,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kActive);
    assert(DecideFirmwareOtaRecovery({
               .attempt = active,
               .running_version = "0.3.1",
               .running_slot = 0,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kInconsistent);
    assert(DecideFirmwareOtaRecovery({
               .attempt = active,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kPendingVerify,
           }) == FirmwareOtaRecoveryDecision::kInconsistent);

    auto rolled_back = rebooting;
    assert(veetee::settings::AdvanceFirmwareOtaAttempt(
        &rolled_back, FirmwareOtaAttemptPhase::kRolledBack,
        "bootloader_rollback"));
    assert(DecideFirmwareOtaRecovery({
               .attempt = rolled_back,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kInconsistent);
    assert(DecideFirmwareOtaRecovery({
               .attempt = rolled_back,
               .running_version = "0.3.1",
               .running_slot = 0,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kRolledBack);

    auto failed = staged;
    assert(veetee::settings::AdvanceFirmwareOtaAttempt(
        &failed, FirmwareOtaAttemptPhase::kFailed, "reboot_cancelled"));
    assert(DecideFirmwareOtaRecovery({
               .attempt = failed,
               .running_version = "0.4.0",
               .running_slot = 1,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kInconsistent);
    assert(DecideFirmwareOtaRecovery({
               .attempt = failed,
               .running_version = "0.3.1",
               .running_slot = 0,
               .image_state = FirmwareRunningImageState::kValid,
           }) == FirmwareOtaRecoveryDecision::kFailed);

    TestTerminalReplayOrder();
    TestCancelKeepsOwnershipUntilRestoreAndJournalSucceed();

    std::cout << "firmware_ota_recovery_policy_test: passed\n";
    return 0;
}
