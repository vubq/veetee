#pragma once

#include <cstdint>

#include "config/device_config.h"

namespace veetee::settings {

// The flashed prototype used version 1 for a different 508-byte A/B journal.
// This compact signed-config record is a distinct on-flash schema.
constexpr std::uint32_t kDeviceConfigRecordVersion = 2;

struct StoredDetectorConfig {
    std::uint32_t threshold_ppm = 0;
    std::uint32_t cooldown_ms = 0;
    std::uint8_t enabled = 0;
    std::uint8_t enabled_while_speaking = 0;
    std::uint8_t reserved[2] = {};
    char model_id[65] = {};
};

struct DeviceConfigRecord {
    std::uint32_t record_version = kDeviceConfigRecordVersion;
    std::uint32_t applied_version = 0;
    std::uint32_t security_epoch_floor = 0;
    std::uint32_t wake_profile_version = 0;
    std::uint8_t has_wake_profile = 0;
    std::uint8_t send_wake_audio = 0;
    std::uint8_t wake_audio_privacy_revoked = 0;
    std::uint8_t reserved = 0;
    char etag[65] = {};
    char wake_profile_id[65] = {};
    char required_resource_version[33] = {};
    StoredDetectorConfig activation{};
    StoredDetectorConfig interrupt{};
    std::uint32_t crc32 = 0;
};

enum class DeviceConfigRecordMigration : std::uint8_t {
    kInvalid,
    kUnchanged,
    kResetForSecurityEpoch,
};

DeviceConfigRecord MakeDefaultDeviceConfigRecord(
    std::uint32_t minimum_security_epoch);
bool InitializeDefaultDeviceConfigRecord(
    DeviceConfigRecord* record, std::uint32_t minimum_security_epoch);
void SealDeviceConfigRecord(DeviceConfigRecord* record);
bool IsValidDeviceConfigRecord(const DeviceConfigRecord& record);
bool MarkWakeAudioPrivacyRevoked(DeviceConfigRecord* record);
bool DeviceConfigWakeAudioPrivacyRevoked(
    const DeviceConfigRecord& record);
DeviceConfigRecordMigration ReconcileDeviceConfigSecurityFloor(
    DeviceConfigRecord* record, std::uint32_t minimum_security_epoch);
bool StageAppliedDeviceConfig(DeviceConfigRecord* record,
                              const config::DeviceConfig& applied,
                              const char* etag);
bool LoadAppliedDeviceConfig(const DeviceConfigRecord& record,
                             config::DeviceConfig* config);

}  // namespace veetee::settings
