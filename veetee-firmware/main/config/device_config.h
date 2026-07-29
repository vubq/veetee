#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

#include "ota/resource_manifest.h"

namespace veetee::config {

constexpr std::uint32_t kDeviceConfigSchemaVersion = 1;
constexpr std::uint32_t kMaximumDeviceConfigVersion = 2147483647U;
constexpr std::uint32_t kThresholdPpmDefault = 0;
constexpr std::uint32_t kMinimumThresholdPpm = 400000;
constexpr std::uint32_t kMaximumThresholdPpm = 999900;
constexpr std::uint32_t kMinimumDetectorCooldownMs = 250;
constexpr std::uint32_t kMaximumDetectorCooldownMs = 10000;

struct DetectorConfig {
    bool enabled = false;
    std::array<char, 65> model_id{};
    std::uint32_t threshold_ppm = kThresholdPpmDefault;
    std::uint32_t cooldown_ms = 0;
    bool enabled_while_speaking = false;
};

struct DeviceConfig {
    std::uint32_t version = 0;
    std::uint32_t security_epoch = 0;
    bool has_wake_profile = false;
    std::array<char, 65> wake_profile_id{};
    std::uint32_t wake_profile_version = 0;
    std::array<char, 33> required_resource_version{};
    DetectorConfig activation{};
    DetectorConfig interrupt{};
    bool send_wake_audio = false;
};

enum class DeviceConfigError : std::uint8_t {
    kOk,
    kInvalidJson,
    kInvalidSchema,
    kInvalidSignature,
    kUntrustedKey,
    kSecurityDowngrade,
    kDeviceMismatch,
    kVersionMismatch,
    kUnsupportedFeature,
};

DeviceConfigError VerifyDeviceConfig(
    std::string_view document, const char* expected_device_id,
    std::uint32_t expected_version,
    const ota::TrustedReleaseKey* trusted_keys,
    std::size_t trusted_key_count, DeviceConfig* config);

const char* DeviceConfigErrorName(DeviceConfigError error);

}  // namespace veetee::config
