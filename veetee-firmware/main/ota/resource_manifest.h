#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace veetee::ota {

enum class ResourceManifestError : std::uint8_t {
    kOk,
    kInvalidJson,
    kInvalidSchema,
    kInvalidSignature,
    kUntrustedKey,
    kSecurityDowngrade,
    kTargetMismatch,
    kFirmwareIncompatible,
    kResourceAbiMismatch,
    kUnsupportedRuntime,
    kCapacityExceeded,
    kInvalidPayloadUrl,
};

struct SupportedResourceRuntime {
    const char* kind;
    const char* runtime;
    std::uint32_t runtime_abi;
};

struct DeviceResourceCapability {
    const char* manifest_kind;
    const char* content_type;
    const char* board;
    const char* chip;
    const char* firmware_version;
    std::uint32_t resource_abi;
    std::uint32_t ui_abi;
    std::uint64_t flash_bytes;
    std::uint64_t psram_bytes;
    std::uint64_t resource_slot_bytes;
    const SupportedResourceRuntime* supported_runtimes = nullptr;
    std::size_t supported_runtime_count = 0;
};

struct TrustedReleaseKey {
    const char* key_id;
    std::uint32_t minimum_security_epoch;
    std::array<std::uint8_t, 32> public_key;
};

struct VerifiedResourceManifest {
    char bundle_id[65] = {};
    char version[33] = {};
    char payload_url[257] = {};
    char payload_sha256[65] = {};
    std::uint64_t payload_bytes = 0;
    std::uint32_t security_epoch = 0;
    bool requires_reboot = false;
};

ResourceManifestError VerifyResourceManifest(
    std::string_view document, const DeviceResourceCapability& capability,
    const TrustedReleaseKey* trusted_keys, std::size_t trusted_key_count,
    VerifiedResourceManifest* manifest);

const char* ResourceManifestErrorName(ResourceManifestError error);

}  // namespace veetee::ota
