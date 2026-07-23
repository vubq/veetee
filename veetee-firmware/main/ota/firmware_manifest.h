#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

#include "ota/resource_manifest.h"

namespace veetee::ota {

enum class FirmwareManifestError : std::uint8_t {
    kOk,
    kInvalidJson,
    kInvalidSchema,
    kInvalidSignature,
    kUntrustedKey,
    kSecurityDowngrade,
    kTargetMismatch,
    kCapacityExceeded,
    kInvalidPayloadUrl,
};

struct DeviceFirmwareCapability {
    const char* board;
    const char* chip;
    std::uint64_t flash_bytes;
    std::uint64_t psram_bytes;
    std::uint64_t slot_bytes;
};

struct VerifiedFirmwareManifest {
    char bundle_id[65] = {};
    char version[33] = {};
    char payload_url[257] = {};
    char payload_sha256[65] = {};
    std::uint64_t payload_bytes = 0;
    std::uint32_t security_epoch = 0;
};

FirmwareManifestError VerifyFirmwareManifest(
    std::string_view document, const DeviceFirmwareCapability& capability,
    const TrustedReleaseKey* trusted_keys, std::size_t trusted_key_count,
    VerifiedFirmwareManifest* manifest);

const char* FirmwareManifestErrorName(FirmwareManifestError error);

}  // namespace veetee::ota
