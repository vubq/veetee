#include "ota/resource_manifest.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>

#include "cJSON.h"
#include "network/endpoint_url.h"
#include "security/signed_json.h"

namespace veetee::ota {
namespace {

constexpr std::size_t kMaximumManifestBytes = 32768;

bool HasOnlyProperties(const cJSON* object,
                       std::initializer_list<const char*> allowed) {
    if (!cJSON_IsObject(object)) return false;
    for (const cJSON* child = object->child; child != nullptr; child = child->next) {
        if (child->string == nullptr ||
            std::none_of(allowed.begin(), allowed.end(), [&](const char* key) {
                return std::strcmp(child->string, key) == 0;
            })) {
            return false;
        }
    }
    return true;
}

bool CopyString(const cJSON* object, const char* key, char* destination,
                std::size_t capacity) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsString(item) || item->valuestring == nullptr) return false;
    const std::size_t length = std::strlen(item->valuestring);
    if (length == 0 || length >= capacity) return false;
    std::memcpy(destination, item->valuestring, length + 1);
    return true;
}

bool ReadU64(const cJSON* object, const char* key, std::uint64_t* destination) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(item) || !std::isfinite(item->valuedouble) ||
        item->valuedouble < 0 || std::floor(item->valuedouble) != item->valuedouble ||
        item->valuedouble > 9007199254740991.0) {
        return false;
    }
    *destination = static_cast<std::uint64_t>(item->valuedouble);
    return true;
}

bool ReadU32(const cJSON* object, const char* key, std::uint32_t* destination) {
    std::uint64_t value = 0;
    if (!ReadU64(object, key, &value) || value > UINT32_MAX) return false;
    *destination = static_cast<std::uint32_t>(value);
    return true;
}

bool Equals(const cJSON* object, const char* key, const char* expected) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(item) && item->valuestring != nullptr &&
           std::strcmp(item->valuestring, expected) == 0;
}

bool IsSha256(const char* value) {
    if (value == nullptr || std::strlen(value) != 64) return false;
    return std::all_of(value, value + 64, [](char character) {
        return (character >= '0' && character <= '9') ||
               (character >= 'a' && character <= 'f');
    });
}

bool ParseSemver(const char* value, std::uint32_t parts[3]) {
    if (value == nullptr) return false;
    const char* cursor = value;
    for (std::size_t index = 0; index < 3; ++index) {
        if (*cursor < '0' || *cursor > '9') return false;
        std::uint64_t part = 0;
        while (*cursor >= '0' && *cursor <= '9') {
            part = part * 10U + static_cast<unsigned>(*cursor - '0');
            if (part > UINT32_MAX) return false;
            ++cursor;
        }
        parts[index] = static_cast<std::uint32_t>(part);
        if (index != 2) {
            if (*cursor != '.') return false;
            ++cursor;
        }
    }
    return *cursor == '\0';
}

int CompareSemver(const std::uint32_t left[3], const std::uint32_t right[3]) {
    for (std::size_t index = 0; index < 3; ++index) {
        if (left[index] < right[index]) return -1;
        if (left[index] > right[index]) return 1;
    }
    return 0;
}

const TrustedReleaseKey* FindKey(const TrustedReleaseKey* keys,
                                 std::size_t count, const char* key_id) {
    if (keys == nullptr || key_id == nullptr) return nullptr;
    for (std::size_t index = 0; index < count; ++index) {
        if (keys[index].key_id != nullptr &&
            std::strcmp(keys[index].key_id, key_id) == 0) {
            return &keys[index];
        }
    }
    return nullptr;
}

bool IsOneOf(const char* value, const char* first, const char* second,
             const char* third) {
    return value != nullptr &&
           (std::strcmp(value, first) == 0 || std::strcmp(value, second) == 0 ||
            std::strcmp(value, third) == 0);
}

bool IsSafeMemberName(const char* name) {
    if (name == nullptr || name[0] == '\0') return false;
    const std::size_t length = std::strlen(name);
    if (name[0] == '/' || name[length - 1] == '/') return false;

    const char* segment = name;
    for (const char* cursor = name;; ++cursor) {
        if (*cursor != '\0' && *cursor != '/' &&
            !((*cursor >= 'a' && *cursor <= 'z') ||
              (*cursor >= 'A' && *cursor <= 'Z') ||
              (*cursor >= '0' && *cursor <= '9') || *cursor == '-' ||
              *cursor == '_' || *cursor == '.')) {
            return false;
        }
        if (*cursor != '/' && *cursor != '\0') continue;
        const std::size_t segment_length = static_cast<std::size_t>(cursor - segment);
        if (segment_length == 0 ||
            (segment_length == 1 && segment[0] == '.') ||
            (segment_length == 2 && segment[0] == '.' && segment[1] == '.')) {
            return false;
        }
        if (*cursor == '\0') return true;
        segment = cursor + 1;
    }
}

bool IsSupportedRuntime(const DeviceResourceCapability& capability,
                        const char* kind, const char* runtime,
                        std::uint32_t runtime_abi) {
    if (capability.supported_runtimes == nullptr) return false;
    for (std::size_t index = 0; index < capability.supported_runtime_count; ++index) {
        const SupportedResourceRuntime& supported =
            capability.supported_runtimes[index];
        if (supported.kind != nullptr && supported.runtime != nullptr &&
            std::strcmp(supported.kind, kind) == 0 &&
            std::strcmp(supported.runtime, runtime) == 0 &&
            supported.runtime_abi == runtime_abi) {
            return true;
        }
    }
    return false;
}

ResourceManifestError ValidateMembers(
    const cJSON* members, std::uint64_t payload_bytes,
    const DeviceResourceCapability& capability) {
    if (!cJSON_IsArray(members) || cJSON_GetArraySize(members) <= 0) {
        return ResourceManifestError::kInvalidSchema;
    }
    std::vector<std::string> names;
    std::uint64_t member_bytes = 0;
    for (const cJSON* member = members->child; member != nullptr;
         member = member->next) {
        if (!cJSON_IsObject(member)) return ResourceManifestError::kInvalidSchema;
        char name[129] = {};
        char kind[33] = {};
        char runtime[65] = {};
        char hash[65] = {};
        std::uint32_t runtime_abi = 0;
        std::uint32_t format_version = 0;
        std::uint64_t bytes = 0;
        if (!CopyString(member, "name", name, sizeof(name)) ||
            !IsSafeMemberName(name) ||
            !CopyString(member, "kind", kind, sizeof(kind)) ||
            !IsOneOf(kind, "model_pack", "display_assets", "audio_assets") ||
            !CopyString(member, "runtime", runtime, sizeof(runtime)) ||
            !ReadU32(member, "runtime_abi", &runtime_abi) || runtime_abi == 0 ||
            !ReadU32(member, "format_version", &format_version) ||
            format_version == 0 ||
            !CopyString(member, "sha256", hash, sizeof(hash)) ||
            !IsSha256(hash) || !ReadU64(member, "bytes", &bytes) || bytes == 0 ||
            bytes > payload_bytes || member_bytes > payload_bytes - bytes) {
            return ResourceManifestError::kInvalidSchema;
        }
        if (!IsSupportedRuntime(capability, kind, runtime, runtime_abi)) {
            return ResourceManifestError::kUnsupportedRuntime;
        }
        member_bytes += bytes;
        if (std::find(names.begin(), names.end(), name) != names.end()) {
            return ResourceManifestError::kInvalidSchema;
        }
        names.emplace_back(name);
    }
    return ResourceManifestError::kOk;
}

cJSON* ParseExact(std::string_view document) {
    std::string terminated(document);
    const char* parse_end = nullptr;
    return cJSON_ParseWithLengthOpts(terminated.c_str(), terminated.size() + 1,
                                     &parse_end, true);
}

}  // namespace

ResourceManifestError VerifyResourceManifest(
    std::string_view document, const DeviceResourceCapability& capability,
    const TrustedReleaseKey* trusted_keys, std::size_t trusted_key_count,
    VerifiedResourceManifest* manifest) {
    if (manifest == nullptr || document.empty() || capability.board == nullptr ||
        capability.chip == nullptr || capability.firmware_version == nullptr ||
        document.size() > kMaximumManifestBytes) {
        return ResourceManifestError::kInvalidSchema;
    }

    cJSON* root = ParseExact(document);
    if (root == nullptr) return ResourceManifestError::kInvalidJson;
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return ResourceManifestError::kInvalidSchema;
    }

    VerifiedResourceManifest candidate{};
    ResourceManifestError error = ResourceManifestError::kInvalidSchema;
    const cJSON* target = cJSON_GetObjectItemCaseSensitive(root, "target");
    const cJSON* compatibility =
        cJSON_GetObjectItemCaseSensitive(root, "compatibility");
    const cJSON* payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    const cJSON* apply = cJSON_GetObjectItemCaseSensitive(root, "apply");
    const cJSON* members = cJSON_GetObjectItemCaseSensitive(root, "members");
    const cJSON* signature = cJSON_GetObjectItemCaseSensitive(root, "signature");

    std::uint32_t manifest_version = 0;
    std::uint32_t resource_abi = 0;
    std::uint64_t target_flash = 0;
    std::uint64_t target_psram = 0;
    char target_board[65] = {};
    char target_chip[17] = {};
    char minimum_firmware[33] = {};
    char maximum_firmware[33] = {};
    char content_type[65] = {};
    char apply_mode[33] = {};
    char channel[17] = {};
    char created_at[65] = {};
    char algorithm[17] = {};
    char key_id[65] = {};
    char signature_base64[89] = {};

    const bool schema_valid =
        HasOnlyProperties(root, {"manifest_version", "bundle_id", "kind",
                                 "version", "channel", "target",
                                 "compatibility", "payload", "apply", "members",
                                 "created_at", "signature"}) &&
        ReadU32(root, "manifest_version", &manifest_version) && manifest_version == 1 &&
        Equals(root, "kind", "resource_bundle") &&
        CopyString(root, "bundle_id", candidate.bundle_id,
                   sizeof(candidate.bundle_id)) &&
        CopyString(root, "version", candidate.version, sizeof(candidate.version)) &&
        CopyString(root, "channel", channel, sizeof(channel)) &&
        IsOneOf(channel, "development", "canary", "stable") &&
        CopyString(root, "created_at", created_at, sizeof(created_at)) &&
        HasOnlyProperties(target, {"board", "chip", "flash_bytes", "psram_bytes"}) &&
        CopyString(target, "board", target_board, sizeof(target_board)) &&
        CopyString(target, "chip", target_chip, sizeof(target_chip)) &&
        ReadU64(target, "flash_bytes", &target_flash) &&
        ReadU64(target, "psram_bytes", &target_psram) &&
        HasOnlyProperties(compatibility,
                          {"min_firmware", "max_firmware_exclusive",
                           "resource_abi"}) &&
        CopyString(compatibility, "min_firmware", minimum_firmware,
                   sizeof(minimum_firmware)) &&
        CopyString(compatibility, "max_firmware_exclusive", maximum_firmware,
                   sizeof(maximum_firmware)) &&
        ReadU32(compatibility, "resource_abi", &resource_abi) &&
        HasOnlyProperties(payload, {"url", "size", "sha256", "content_type"}) &&
        CopyString(payload, "url", candidate.payload_url,
                   sizeof(candidate.payload_url)) &&
        ReadU64(payload, "size", &candidate.payload_bytes) &&
        candidate.payload_bytes > 0 &&
        CopyString(payload, "sha256", candidate.payload_sha256,
                   sizeof(candidate.payload_sha256)) &&
        IsSha256(candidate.payload_sha256) &&
        CopyString(payload, "content_type", content_type, sizeof(content_type)) &&
        std::strcmp(content_type, "application/vnd.veetee.resource-pack") == 0 &&
        HasOnlyProperties(apply,
                          {"mode", "requires_reboot", "rollback_allowed"}) &&
        CopyString(apply, "mode", apply_mode, sizeof(apply_mode)) &&
        cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(apply, "requires_reboot")) &&
        cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(apply, "rollback_allowed")) &&
        HasOnlyProperties(signature,
                          {"algorithm", "key_id", "security_epoch", "value"}) &&
        CopyString(signature, "algorithm", algorithm, sizeof(algorithm)) &&
        CopyString(signature, "key_id", key_id, sizeof(key_id)) &&
        ReadU32(signature, "security_epoch", &candidate.security_epoch) &&
        CopyString(signature, "value", signature_base64,
                   sizeof(signature_base64));
    if (!schema_valid || std::strcmp(algorithm, "ed25519") != 0) {
        cJSON_Delete(root);
        return error;
    }
    const ResourceManifestError member_error =
        ValidateMembers(members, candidate.payload_bytes, capability);
    if (member_error != ResourceManifestError::kOk) {
        cJSON_Delete(root);
        return member_error;
    }
    candidate.requires_reboot =
        cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(apply, "requires_reboot"));
    if (!IsOneOf(apply_mode, "immediate", "when_standby", "on_reboot")) {
        cJSON_Delete(root);
        return ResourceManifestError::kInvalidSchema;
    }

    const TrustedReleaseKey* key = FindKey(trusted_keys, trusted_key_count, key_id);
    if (key == nullptr) {
        cJSON_Delete(root);
        return ResourceManifestError::kUntrustedKey;
    }
    if (candidate.security_epoch < key->minimum_security_epoch) {
        cJSON_Delete(root);
        return ResourceManifestError::kSecurityDowngrade;
    }

    std::string canonical;
    if (!security::CanonicalizeManifestForSignature(document, &canonical) ||
        !security::VerifyEd25519Base64(key->public_key.data(), canonical,
                                      signature_base64)) {
        cJSON_Delete(root);
        return ResourceManifestError::kInvalidSignature;
    }
    if (std::strcmp(target_board, capability.board) != 0 ||
        std::strcmp(target_chip, capability.chip) != 0 ||
        target_flash != capability.flash_bytes ||
        target_psram > capability.psram_bytes) {
        cJSON_Delete(root);
        return ResourceManifestError::kTargetMismatch;
    }
    if (resource_abi != capability.resource_abi) {
        cJSON_Delete(root);
        return ResourceManifestError::kResourceAbiMismatch;
    }
    if (candidate.payload_bytes > capability.resource_slot_bytes) {
        cJSON_Delete(root);
        return ResourceManifestError::kCapacityExceeded;
    }
    if (!network::IsHttpEndpointUrl(candidate.payload_url)) {
        cJSON_Delete(root);
        return ResourceManifestError::kInvalidPayloadUrl;
    }

    std::uint32_t current[3] = {};
    std::uint32_t minimum[3] = {};
    std::uint32_t maximum[3] = {};
    if (!ParseSemver(capability.firmware_version, current) ||
        !ParseSemver(minimum_firmware, minimum) ||
        !ParseSemver(maximum_firmware, maximum) ||
        CompareSemver(current, minimum) < 0 ||
        CompareSemver(current, maximum) >= 0) {
        cJSON_Delete(root);
        return ResourceManifestError::kFirmwareIncompatible;
    }

    cJSON_Delete(root);
    *manifest = candidate;
    return ResourceManifestError::kOk;
}

const char* ResourceManifestErrorName(ResourceManifestError error) {
    switch (error) {
        case ResourceManifestError::kOk: return "ok";
        case ResourceManifestError::kInvalidJson: return "invalid_json";
        case ResourceManifestError::kInvalidSchema: return "invalid_schema";
        case ResourceManifestError::kInvalidSignature: return "invalid_signature";
        case ResourceManifestError::kUntrustedKey: return "untrusted_key";
        case ResourceManifestError::kSecurityDowngrade: return "security_downgrade";
        case ResourceManifestError::kTargetMismatch: return "target_mismatch";
        case ResourceManifestError::kFirmwareIncompatible: return "firmware_incompatible";
        case ResourceManifestError::kResourceAbiMismatch: return "resource_abi_mismatch";
        case ResourceManifestError::kUnsupportedRuntime: return "unsupported_runtime";
        case ResourceManifestError::kCapacityExceeded: return "capacity_exceeded";
        case ResourceManifestError::kInvalidPayloadUrl: return "invalid_payload_url";
    }
    return "unknown";
}

}  // namespace veetee::ota
