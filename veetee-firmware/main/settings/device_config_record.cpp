#include "settings/device_config_record.h"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<StoredDetectorConfig>);
static_assert(std::is_trivially_copyable_v<DeviceConfigRecord>);
static_assert(sizeof(StoredDetectorConfig) == 80,
              "Stored detector layout is a versioned NVS contract");
static_assert(sizeof(DeviceConfigRecord) == 348,
              "Device-config layout is a versioned NVS contract");

std::uint32_t Crc32(const void* data, std::size_t length) {
    std::uint32_t crc = 0xFFFFFFFFU;
    const auto* bytes = static_cast<const std::uint8_t*>(data);
    for (std::size_t index = 0; index < length; ++index) {
        crc ^= bytes[index];
        for (int bit = 0; bit < 8; ++bit) {
            const std::uint32_t mask = 0U - (crc & 1U);
            crc = (crc >> 1U) ^ (0xEDB88320U & mask);
        }
    }
    return ~crc;
}

template <std::size_t Size>
bool IsTerminated(const char (&value)[Size]) {
    return std::memchr(value, '\0', Size) != nullptr;
}

bool IsSafeIdentifier(const char* value) {
    if (value == nullptr || value[0] == '\0') return false;
    return std::all_of(value, value + std::strlen(value),
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.';
                       });
}

bool IsSafeVersion(const char* value) {
    if (value == nullptr || value[0] == '\0') return false;
    return std::all_of(value, value + std::strlen(value),
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.' || character == '+';
                       });
}

bool IsWakeNetModelId(const char* value) {
    if (value == nullptr || value[0] != 'w' || value[1] != 'n' ||
        value[2] == '\0') {
        return false;
    }
    return std::all_of(value + 2, value + std::strlen(value),
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.';
                       });
}

bool IsSafeEtag(const char* value) {
    if (value == nullptr || std::strlen(value) != 48 ||
        std::strncmp(value, "cfg1-", 5) != 0) {
        return false;
    }
    return std::all_of(value + 5, value + 48,
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_';
                       });
}

bool IsThresholdValid(std::uint32_t value) {
    return value == config::kThresholdPpmDefault ||
           (value >= config::kMinimumThresholdPpm &&
            value <= config::kMaximumThresholdPpm);
}

bool IsDetectorValid(const StoredDetectorConfig& detector,
                     bool allow_disabled, bool allow_while_speaking) {
    if (detector.enabled > 1 || detector.enabled_while_speaking > 1 ||
        !IsTerminated(detector.model_id)) {
        return false;
    }
    if (detector.enabled == 0) {
        return allow_disabled && detector.threshold_ppm == 0 &&
               detector.cooldown_ms == 0 && detector.model_id[0] == '\0' &&
               detector.enabled_while_speaking == 0;
    }
    return IsWakeNetModelId(detector.model_id) &&
           IsThresholdValid(detector.threshold_ppm) &&
           detector.cooldown_ms >= config::kMinimumDetectorCooldownMs &&
           detector.cooldown_ms <= config::kMaximumDetectorCooldownMs &&
           (allow_while_speaking || detector.enabled_while_speaking == 0);
}

void StoreDetector(const config::DetectorConfig& source,
                   StoredDetectorConfig* destination) {
    *destination = StoredDetectorConfig{};
    destination->enabled = source.enabled ? 1 : 0;
    destination->enabled_while_speaking =
        source.enabled_while_speaking ? 1 : 0;
    destination->threshold_ppm = source.threshold_ppm;
    destination->cooldown_ms = source.cooldown_ms;
    std::snprintf(destination->model_id, sizeof(destination->model_id), "%s",
                  source.model_id.data());
}

void LoadDetector(const StoredDetectorConfig& source,
                  config::DetectorConfig* destination) {
    *destination = config::DetectorConfig{};
    destination->enabled = source.enabled != 0;
    destination->enabled_while_speaking =
        source.enabled_while_speaking != 0;
    destination->threshold_ppm = source.threshold_ppm;
    destination->cooldown_ms = source.cooldown_ms;
    std::snprintf(destination->model_id.data(), destination->model_id.size(),
                  "%s", source.model_id);
}

}  // namespace

DeviceConfigRecord MakeDefaultDeviceConfigRecord(
    std::uint32_t minimum_security_epoch) {
    DeviceConfigRecord record{};
    InitializeDefaultDeviceConfigRecord(&record, minimum_security_epoch);
    return record;
}

bool InitializeDefaultDeviceConfigRecord(
    DeviceConfigRecord* record, std::uint32_t minimum_security_epoch) {
    if (record == nullptr || minimum_security_epoch == 0 ||
        minimum_security_epoch > config::kMaximumDeviceConfigVersion) {
        return false;
    }
    // This function is used on ESP-IDF's boot-only main task. Clear the caller's
    // persistent record directly so migration never adds another 348-byte
    // return-value/local object to the constrained initialization stack.
    auto* bytes = reinterpret_cast<std::uint8_t*>(record);
    for (std::size_t index = 0; index < sizeof(*record); ++index) {
        bytes[index] = 0;
    }
    record->record_version = kDeviceConfigRecordVersion;
    record->security_epoch_floor = minimum_security_epoch;
    record->wake_audio_privacy_revoked = 1;
    SealDeviceConfigRecord(record);
    return true;
}

void SealDeviceConfigRecord(DeviceConfigRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(DeviceConfigRecord, crc32));
}

bool IsValidDeviceConfigRecord(const DeviceConfigRecord& record) {
    if (record.record_version != kDeviceConfigRecordVersion ||
        record.applied_version > config::kMaximumDeviceConfigVersion ||
        record.security_epoch_floor == 0 ||
        record.security_epoch_floor > config::kMaximumDeviceConfigVersion ||
        record.wake_profile_version > config::kMaximumDeviceConfigVersion ||
        record.has_wake_profile > 1 ||
        record.send_wake_audio > 1 ||
        record.wake_audio_privacy_revoked > 1 || record.reserved != 0 ||
        !IsTerminated(record.etag) ||
        !IsTerminated(record.wake_profile_id) ||
        !IsTerminated(record.required_resource_version) ||
        !IsDetectorValid(record.activation, true, false) ||
        !IsDetectorValid(record.interrupt, true, true) ||
        record.crc32 !=
            Crc32(&record, offsetof(DeviceConfigRecord, crc32))) {
        return false;
    }
    if (record.applied_version == 0) {
        return record.etag[0] == '\0' && record.has_wake_profile == 0 &&
               record.wake_profile_version == 0 &&
               record.wake_profile_id[0] == '\0' &&
               record.required_resource_version[0] == '\0' &&
               record.activation.enabled == 0 && record.interrupt.enabled == 0 &&
               record.send_wake_audio == 0;
    }
    if (!IsSafeEtag(record.etag)) return false;
    if (record.has_wake_profile == 0) {
        return record.wake_profile_version == 0 &&
               record.wake_profile_id[0] == '\0' &&
               record.required_resource_version[0] == '\0' &&
               record.activation.enabled == 0 && record.interrupt.enabled == 0 &&
               record.send_wake_audio == 0;
    }
    return record.wake_profile_version > 0 &&
           IsSafeIdentifier(record.wake_profile_id) &&
           IsSafeVersion(record.required_resource_version) &&
           record.activation.enabled != 0 &&
           (record.interrupt.enabled == 0 ||
            std::strcmp(record.activation.model_id,
                        record.interrupt.model_id) != 0);
}

bool MarkWakeAudioPrivacyRevoked(DeviceConfigRecord* record) {
    if (record == nullptr || !IsValidDeviceConfigRecord(*record)) {
        return false;
    }
    if (record->wake_audio_privacy_revoked != 0) return true;
    record->wake_audio_privacy_revoked = 1;
    SealDeviceConfigRecord(record);
    return IsValidDeviceConfigRecord(*record);
}

bool DeviceConfigWakeAudioPrivacyRevoked(
    const DeviceConfigRecord& record) {
    return !IsValidDeviceConfigRecord(record) ||
           record.wake_audio_privacy_revoked != 0 ||
           record.send_wake_audio == 0;
}

DeviceConfigRecordMigration ReconcileDeviceConfigSecurityFloor(
    DeviceConfigRecord* record, std::uint32_t minimum_security_epoch) {
    if (record == nullptr || minimum_security_epoch == 0 ||
        minimum_security_epoch > config::kMaximumDeviceConfigVersion ||
        !IsValidDeviceConfigRecord(*record)) {
        return DeviceConfigRecordMigration::kInvalid;
    }
    if (record->security_epoch_floor >= minimum_security_epoch) {
        return DeviceConfigRecordMigration::kUnchanged;
    }
    if (!InitializeDefaultDeviceConfigRecord(record,
                                             minimum_security_epoch)) {
        return DeviceConfigRecordMigration::kInvalid;
    }
    return DeviceConfigRecordMigration::kResetForSecurityEpoch;
}

bool StageAppliedDeviceConfig(DeviceConfigRecord* record,
                              const config::DeviceConfig& applied,
                              const char* etag) {
    if (record == nullptr || etag == nullptr || !IsSafeEtag(etag) ||
        !IsValidDeviceConfigRecord(*record) || applied.version == 0 ||
        applied.version < record->applied_version ||
        applied.security_epoch < record->security_epoch_floor ||
        (applied.has_wake_profile &&
         (!applied.activation.enabled ||
          !IsSafeIdentifier(applied.wake_profile_id.data()) ||
          !IsSafeVersion(applied.required_resource_version.data()) ||
          applied.activation.enabled_while_speaking ||
          (applied.interrupt.enabled &&
           std::strcmp(applied.activation.model_id.data(),
                       applied.interrupt.model_id.data()) == 0)))) {
        return false;
    }
    if (applied.version == record->applied_version) {
        return std::strcmp(etag, record->etag) == 0;
    }

    DeviceConfigRecord next{};
    next.applied_version = applied.version;
    next.security_epoch_floor =
        std::max(record->security_epoch_floor, applied.security_epoch);
    next.wake_audio_privacy_revoked =
        applied.has_wake_profile && applied.send_wake_audio ? 0 : 1;
    std::snprintf(next.etag, sizeof(next.etag), "%s", etag);
    if (applied.has_wake_profile) {
        next.has_wake_profile = 1;
        next.send_wake_audio = applied.send_wake_audio ? 1 : 0;
        next.wake_profile_version = applied.wake_profile_version;
        std::snprintf(next.wake_profile_id, sizeof(next.wake_profile_id), "%s",
                      applied.wake_profile_id.data());
        std::snprintf(next.required_resource_version,
                      sizeof(next.required_resource_version), "%s",
                      applied.required_resource_version.data());
        StoreDetector(applied.activation, &next.activation);
        StoreDetector(applied.interrupt, &next.interrupt);
    }
    SealDeviceConfigRecord(&next);
    if (!IsValidDeviceConfigRecord(next)) return false;
    *record = next;
    return true;
}

bool LoadAppliedDeviceConfig(const DeviceConfigRecord& record,
                             config::DeviceConfig* output) {
    if (output == nullptr || !IsValidDeviceConfigRecord(record)) return false;
    config::DeviceConfig loaded{};
    loaded.version = record.applied_version;
    loaded.security_epoch = record.security_epoch_floor;
    loaded.has_wake_profile = record.has_wake_profile != 0;
    loaded.send_wake_audio = record.send_wake_audio != 0;
    loaded.wake_profile_version = record.wake_profile_version;
    std::snprintf(loaded.wake_profile_id.data(), loaded.wake_profile_id.size(),
                  "%s", record.wake_profile_id);
    std::snprintf(loaded.required_resource_version.data(),
                  loaded.required_resource_version.size(), "%s",
                  record.required_resource_version);
    LoadDetector(record.activation, &loaded.activation);
    LoadDetector(record.interrupt, &loaded.interrupt);
    *output = loaded;
    return true;
}

}  // namespace veetee::settings
