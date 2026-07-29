#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::settings {

inline constexpr char kDeviceConfigNvsNamespace[] = "veetee_config";
inline constexpr char kDeviceConfigNvsRecordKey[] = "state";

enum class DeviceConfigBlobStatus : std::uint8_t {
    kFound,
    kNotFound,
    kLengthMismatch,
    kTypeMismatch,
    kStorageError,
};

enum class DeviceConfigBlobAction : std::uint8_t {
    kLoadCurrent,
    kResetConfigOnly,
    kFail,
};

constexpr DeviceConfigBlobAction DecideDeviceConfigBlobAction(
    DeviceConfigBlobStatus status, std::size_t stored_bytes,
    std::size_t current_bytes) {
    switch (status) {
        case DeviceConfigBlobStatus::kFound:
            return stored_bytes == current_bytes
                       ? DeviceConfigBlobAction::kLoadCurrent
                       : DeviceConfigBlobAction::kResetConfigOnly;
        case DeviceConfigBlobStatus::kNotFound:
        case DeviceConfigBlobStatus::kLengthMismatch:
        case DeviceConfigBlobStatus::kTypeMismatch:
            return DeviceConfigBlobAction::kResetConfigOnly;
        case DeviceConfigBlobStatus::kStorageError:
            return DeviceConfigBlobAction::kFail;
    }
    return DeviceConfigBlobAction::kFail;
}

}  // namespace veetee::settings
