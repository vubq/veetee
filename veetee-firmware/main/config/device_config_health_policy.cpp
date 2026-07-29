#include "config/device_config_health_policy.h"

#include <cstring>

namespace veetee::config {

DeviceConfigHealthDecision DecideDeviceConfigHealth(
    const DeviceConfigHealthInputs& inputs) {
    if (!inputs.transaction_pending || !inputs.health_window_due ||
        !inputs.safe_boundary) {
        return DeviceConfigHealthDecision::kWait;
    }
    if (!inputs.target_current || !inputs.runtime_healthy ||
        (inputs.partition_required &&
         (!inputs.partition_matches || !inputs.resource_version_matches))) {
        return DeviceConfigHealthDecision::kRollback;
    }
    return DeviceConfigHealthDecision::kConfirm;
}

DeviceConfigBootResourceDecision DecideDeviceConfigBootResource(
    bool wake_profile_required, bool active_version_matches,
    bool previous_version_matches, bool transaction_pending,
    bool previous_slot_distinct) {
    if (!wake_profile_required || active_version_matches) {
        return DeviceConfigBootResourceDecision::kUseActive;
    }
    if (previous_slot_distinct && previous_version_matches) {
        return DeviceConfigBootResourceDecision::kRollbackToMatchingPrevious;
    }
    if (transaction_pending) {
        return DeviceConfigBootResourceDecision::kRollbackTransactionButtonOnly;
    }
    return DeviceConfigBootResourceDecision::kButtonOnly;
}

bool DeviceConfigResourceVersionMatches(bool has_wake_profile,
                                        const char* required_version,
                                        const char* active_version) {
    if (!has_wake_profile) return true;
    return required_version != nullptr && active_version != nullptr &&
           required_version[0] != '\0' && active_version[0] != '\0' &&
           std::strcmp(required_version, active_version) == 0;
}

bool NextWakeAudioPrivacyRevoked(bool currently_revoked,
                                 bool send_wake_audio,
                                 bool config_persisted) {
    if (!send_wake_audio) return true;
    return config_persisted ? false : currently_revoked;
}

bool EffectiveSendWakeAudio(bool send_wake_audio, bool privacy_revoked) {
    return send_wake_audio && !privacy_revoked;
}

bool WakeAudioCommitRequiresRevocation(bool send_wake_audio,
                                       bool runtime_enabled) {
    return send_wake_audio && !runtime_enabled;
}

bool WakeAudioRuntimeMatchesRequest(bool send_wake_audio,
                                    bool runtime_send_wake_audio,
                                    bool pre_roll_configured) {
    return send_wake_audio
               ? runtime_send_wake_audio && pre_roll_configured
               : !runtime_send_wake_audio && !pre_roll_configured;
}

bool WakeAudioCommittedRuntimeAllowed(bool send_wake_audio,
                                      bool runtime_privacy_revoked,
                                      bool durable_privacy_revoked) {
    return !send_wake_audio ||
           (!runtime_privacy_revoked && !durable_privacy_revoked);
}

}  // namespace veetee::config
