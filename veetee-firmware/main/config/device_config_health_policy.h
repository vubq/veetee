#pragma once

#include <cstdint>

namespace veetee::config {

enum class DeviceConfigHealthDecision : std::uint8_t {
    kWait,
    kConfirm,
    kRollback,
};

enum class DeviceConfigBootResourceDecision : std::uint8_t {
    kUseActive,
    kRollbackToMatchingPrevious,
    kRollbackTransactionButtonOnly,
    kButtonOnly,
};

struct DeviceConfigHealthInputs {
    bool transaction_pending = false;
    bool health_window_due = false;
    bool safe_boundary = false;
    bool target_current = false;
    bool runtime_healthy = false;
    bool partition_required = false;
    bool partition_matches = false;
    bool resource_version_matches = false;
};

DeviceConfigHealthDecision DecideDeviceConfigHealth(
    const DeviceConfigHealthInputs& inputs);

DeviceConfigBootResourceDecision DecideDeviceConfigBootResource(
    bool wake_profile_required, bool active_version_matches,
    bool previous_version_matches, bool transaction_pending,
    bool previous_slot_distinct);

bool DeviceConfigResourceVersionMatches(bool has_wake_profile,
                                        const char* required_version,
                                        const char* active_version);

bool NextWakeAudioPrivacyRevoked(bool currently_revoked,
                                 bool send_wake_audio,
                                 bool config_persisted);

bool EffectiveSendWakeAudio(bool send_wake_audio, bool privacy_revoked);

bool WakeAudioCommitRequiresRevocation(bool send_wake_audio,
                                       bool runtime_enabled);

bool WakeAudioRuntimeMatchesRequest(bool send_wake_audio,
                                    bool runtime_send_wake_audio,
                                    bool pre_roll_configured);

bool WakeAudioCommittedRuntimeAllowed(bool send_wake_audio,
                                      bool runtime_privacy_revoked,
                                      bool durable_privacy_revoked);

}  // namespace veetee::config
