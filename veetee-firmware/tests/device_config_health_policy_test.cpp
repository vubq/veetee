#include <cassert>
#include <iostream>

#include "config/device_config_health_policy.h"

int main() {
    using veetee::config::DecideDeviceConfigHealth;
    using veetee::config::DeviceConfigHealthDecision;
    using veetee::config::DeviceConfigHealthInputs;

    assert(DecideDeviceConfigHealth({}) ==
           DeviceConfigHealthDecision::kWait);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = false,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = true,
               .resource_version_matches = true,
           }) == DeviceConfigHealthDecision::kWait);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = false,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = true,
               .resource_version_matches = true,
           }) == DeviceConfigHealthDecision::kWait);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = false,
               .partition_matches = false,
               .resource_version_matches = false,
           }) == DeviceConfigHealthDecision::kConfirm);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = true,
               .resource_version_matches = true,
           }) == DeviceConfigHealthDecision::kConfirm);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = false,
               .partition_required = false,
               .partition_matches = false,
               .resource_version_matches = false,
           }) == DeviceConfigHealthDecision::kRollback);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = false,
               .resource_version_matches = true,
           }) == DeviceConfigHealthDecision::kRollback);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = false,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = true,
               .resource_version_matches = true,
           }) == DeviceConfigHealthDecision::kRollback);
    assert(DecideDeviceConfigHealth({
               .transaction_pending = true,
               .health_window_due = true,
               .safe_boundary = true,
               .target_current = true,
               .runtime_healthy = true,
               .partition_required = true,
               .partition_matches = true,
               .resource_version_matches = false,
           }) == DeviceConfigHealthDecision::kRollback);

    using veetee::config::DeviceConfigResourceVersionMatches;
    assert(DeviceConfigResourceVersionMatches(false, nullptr, nullptr));
    assert(DeviceConfigResourceVersionMatches(true, "1.2.0", "1.2.0"));
    assert(!DeviceConfigResourceVersionMatches(true, "1.2.0", "1.3.0"));
    assert(!DeviceConfigResourceVersionMatches(true, "", "1.2.0"));
    assert(!DeviceConfigResourceVersionMatches(true, "1.2.0", nullptr));

    using veetee::config::DecideDeviceConfigBootResource;
    using veetee::config::DeviceConfigBootResourceDecision;
    assert(DecideDeviceConfigBootResource(false, false, false, true, true) ==
           DeviceConfigBootResourceDecision::kUseActive);
    assert(DecideDeviceConfigBootResource(true, true, false, true, true) ==
           DeviceConfigBootResourceDecision::kUseActive);
    assert(DecideDeviceConfigBootResource(true, false, true, true, true) ==
           DeviceConfigBootResourceDecision::kRollbackToMatchingPrevious);
    assert(DecideDeviceConfigBootResource(true, false, false, true, true) ==
           DeviceConfigBootResourceDecision::kRollbackTransactionButtonOnly);
    assert(DecideDeviceConfigBootResource(true, false, true, false, false) ==
           DeviceConfigBootResourceDecision::kButtonOnly);
    assert(DecideDeviceConfigBootResource(true, false, false, false, true) ==
           DeviceConfigBootResourceDecision::kButtonOnly);

    using veetee::config::EffectiveSendWakeAudio;
    using veetee::config::NextWakeAudioPrivacyRevoked;
    using veetee::config::WakeAudioCommittedRuntimeAllowed;
    using veetee::config::WakeAudioCommitRequiresRevocation;
    using veetee::config::WakeAudioRuntimeMatchesRequest;

    // A persisted opt-in is the only state that may clear the fail-closed
    // boot latch.
    bool privacy_revoked = true;
    privacy_revoked =
        NextWakeAudioPrivacyRevoked(privacy_revoked, true, true);
    assert(!privacy_revoked);
    assert(EffectiveSendWakeAudio(true, privacy_revoked));

    // A verified opt-out revokes pre-roll immediately. Reload failure,
    // supersede and resource rollback all restore an older opt-in through the
    // same effective-runtime guard and therefore remain disabled.
    privacy_revoked =
        NextWakeAudioPrivacyRevoked(privacy_revoked, false, false);
    assert(privacy_revoked);
    assert(!EffectiveSendWakeAudio(true, privacy_revoked));
    assert(!EffectiveSendWakeAudio(true, privacy_revoked));
    assert(!EffectiveSendWakeAudio(true, privacy_revoked));

    // Merely staging a later opt-in cannot undo an observed opt-out. Both the
    // direct-config and paired-resource health candidates must load with
    // pre-roll disabled until the same config is durably committed.
    privacy_revoked =
        NextWakeAudioPrivacyRevoked(privacy_revoked, true, false);
    assert(privacy_revoked);
    const bool direct_candidate_send_wake_audio =
        EffectiveSendWakeAudio(true, privacy_revoked);
    const bool paired_candidate_send_wake_audio =
        EffectiveSendWakeAudio(true, privacy_revoked);
    assert(!direct_candidate_send_wake_audio);
    assert(!paired_candidate_send_wake_audio);

    // Successful health plus durable persistence explicitly re-enables the
    // newly signed opt-in for future runtime restores.
    privacy_revoked =
        NextWakeAudioPrivacyRevoked(privacy_revoked, true, true);
    assert(!privacy_revoked);
    assert(EffectiveSendWakeAudio(true, privacy_revoked));
    assert(!WakeAudioCommitRequiresRevocation(true, true));

    // If allocation/activation fails after persistence, the transaction must
    // close the privacy latch again instead of leaving the committed opt-in in
    // an ambiguous runtime state.
    assert(WakeAudioCommitRequiresRevocation(true, false));
    privacy_revoked = true;
    assert(!EffectiveSendWakeAudio(true, privacy_revoked));
    assert(!WakeAudioCommitRequiresRevocation(false, false));
    // A later AlreadyActive/AlreadyApplied detector result cannot report the
    // config active while either RAM or durable revocation remains, nor while
    // the actual pre-roll state differs from the committed request.
    assert(!WakeAudioCommittedRuntimeAllowed(true, true, false));
    assert(!WakeAudioCommittedRuntimeAllowed(true, false, true));
    assert(WakeAudioCommittedRuntimeAllowed(true, false, false));
    assert(WakeAudioCommittedRuntimeAllowed(false, true, true));
    assert(!WakeAudioRuntimeMatchesRequest(true, false, false));
    assert(WakeAudioRuntimeMatchesRequest(true, true, true));
    assert(WakeAudioRuntimeMatchesRequest(false, false, false));
    assert(!WakeAudioRuntimeMatchesRequest(false, false, true));

    // A durable opt-out keeps the latch closed as well.
    privacy_revoked =
        NextWakeAudioPrivacyRevoked(privacy_revoked, false, true);
    assert(privacy_revoked);
    assert(!EffectiveSendWakeAudio(false, privacy_revoked));

    std::cout << "device_config_health_policy_test: passed\n";
    return 0;
}
