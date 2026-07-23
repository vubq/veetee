#include "ota/firmware_manifest.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <initializer_list>
#include <string>

#include "cJSON.h"
#include "network/endpoint_url.h"
#include "security/signed_json.h"

namespace veetee::ota {
namespace {
constexpr std::size_t kMaximumManifestBytes = 32768;

bool only(const cJSON* object, std::initializer_list<const char*> allowed) {
    if (!cJSON_IsObject(object)) return false;
    for (const cJSON* child = object->child; child != nullptr; child = child->next) {
        if (child->string == nullptr ||
            std::none_of(allowed.begin(), allowed.end(), [&](const char* key) {
                return std::strcmp(key, child->string) == 0;
            })) return false;
    }
    return true;
}
bool stringValue(const cJSON* object, const char* key, char* output, std::size_t size) {
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsString(value) || value->valuestring == nullptr) return false;
    const std::size_t length = std::strlen(value->valuestring);
    if (length == 0 || length >= size) return false;
    std::memcpy(output, value->valuestring, length + 1);
    return true;
}
bool u64(const cJSON* object, const char* key, std::uint64_t* output) {
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) ||
        value->valuedouble < 0 || std::floor(value->valuedouble) != value->valuedouble ||
        value->valuedouble > 9007199254740991.0) return false;
    *output = static_cast<std::uint64_t>(value->valuedouble);
    return true;
}
bool u32(const cJSON* object, const char* key, std::uint32_t* output) {
    std::uint64_t value = 0;
    if (!u64(object, key, &value) || value > UINT32_MAX) return false;
    *output = static_cast<std::uint32_t>(value);
    return true;
}
bool equal(const cJSON* object, const char* key, const char* expected) {
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(value) && value->valuestring != nullptr &&
           std::strcmp(value->valuestring, expected) == 0;
}
bool sha256(const char* value) {
    if (value == nullptr || std::strlen(value) != 64) return false;
    return std::all_of(value, value + 64, [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}
const TrustedReleaseKey* keyFor(const TrustedReleaseKey* keys, std::size_t count,
                                const char* keyId) {
    for (std::size_t index = 0; keys != nullptr && index < count; ++index) {
        if (keys[index].key_id != nullptr && std::strcmp(keys[index].key_id, keyId) == 0) {
            return &keys[index];
        }
    }
    return nullptr;
}
cJSON* parse(std::string_view document) {
    std::string copy(document);
    const char* end = nullptr;
    return cJSON_ParseWithLengthOpts(copy.c_str(), copy.size() + 1, &end, true);
}
}  // namespace

FirmwareManifestError VerifyFirmwareManifest(
    std::string_view document, const DeviceFirmwareCapability& capability,
    const TrustedReleaseKey* trusted_keys, std::size_t trusted_key_count,
    VerifiedFirmwareManifest* manifest) {
    if (manifest == nullptr || document.empty() || document.size() > kMaximumManifestBytes ||
        capability.board == nullptr || capability.chip == nullptr ||
        capability.slot_bytes == 0) return FirmwareManifestError::kInvalidSchema;
    cJSON* root = parse(document);
    if (root == nullptr) return FirmwareManifestError::kInvalidJson;
    const cJSON* target = cJSON_GetObjectItemCaseSensitive(root, "target");
    const cJSON* compatibility = cJSON_GetObjectItemCaseSensitive(root, "compatibility");
    const cJSON* payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    const cJSON* apply = cJSON_GetObjectItemCaseSensitive(root, "apply");
    const cJSON* signature = cJSON_GetObjectItemCaseSensitive(root, "signature");
    std::uint32_t manifestVersion = 0;
    std::uint32_t minimumSecurityEpoch = 0;
    std::uint64_t flash = 0, psram = 0;
    char board[65] = {}, chip[17] = {}, contentType[65] = {}, algorithm[17] = {};
    char keyId[65] = {}, signatureValue[89] = {};
    char applyMode[33] = {};
    VerifiedFirmwareManifest candidate{};
    bool valid =
        only(root, {"manifest_version", "bundle_id", "kind", "version", "channel",
                    "target", "compatibility", "payload", "apply", "created_at", "signature"}) &&
        u32(root, "manifest_version", &manifestVersion) && manifestVersion == 1 &&
        equal(root, "kind", "firmware") &&
        stringValue(root, "bundle_id", candidate.bundle_id, sizeof(candidate.bundle_id)) &&
        stringValue(root, "version", candidate.version, sizeof(candidate.version)) &&
        only(target, {"board", "chip", "flash_bytes", "psram_bytes"}) &&
        stringValue(target, "board", board, sizeof(board)) &&
        stringValue(target, "chip", chip, sizeof(chip)) &&
        u64(target, "flash_bytes", &flash) && u64(target, "psram_bytes", &psram) &&
        only(compatibility, {"min_bootloader", "min_security_epoch"}) &&
        stringValue(compatibility, "min_bootloader", applyMode, sizeof(applyMode)) &&
        u32(compatibility, "min_security_epoch", &minimumSecurityEpoch) &&
        only(payload, {"url", "size", "sha256", "content_type"}) &&
        stringValue(payload, "url", candidate.payload_url, sizeof(candidate.payload_url)) &&
        u64(payload, "size", &candidate.payload_bytes) && candidate.payload_bytes > 0 &&
        stringValue(payload, "sha256", candidate.payload_sha256, sizeof(candidate.payload_sha256)) &&
        sha256(candidate.payload_sha256) &&
        stringValue(payload, "content_type", contentType, sizeof(contentType)) &&
        std::strcmp(contentType, "application/vnd.veetee.esp32s3-firmware") == 0 &&
        only(apply, {"mode", "requires_reboot", "rollback_allowed"}) &&
        stringValue(apply, "mode", applyMode, sizeof(applyMode)) &&
        std::strcmp(applyMode, "when_standby") == 0 &&
        cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(apply, "requires_reboot")) &&
        cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(apply, "rollback_allowed")) &&
        only(signature, {"algorithm", "key_id", "security_epoch", "value"}) &&
        stringValue(signature, "algorithm", algorithm, sizeof(algorithm)) &&
        std::strcmp(algorithm, "ed25519") == 0 &&
        stringValue(signature, "key_id", keyId, sizeof(keyId)) &&
        u32(signature, "security_epoch", &candidate.security_epoch) &&
        stringValue(signature, "value", signatureValue, sizeof(signatureValue));
    if (!valid || candidate.security_epoch < minimumSecurityEpoch) {
        cJSON_Delete(root);
        return FirmwareManifestError::kInvalidSchema;
    }
    const TrustedReleaseKey* trusted = keyFor(trusted_keys, trusted_key_count, keyId);
    if (trusted == nullptr) {
        cJSON_Delete(root);
        return FirmwareManifestError::kUntrustedKey;
    }
    if (candidate.security_epoch < trusted->minimum_security_epoch) {
        cJSON_Delete(root);
        return FirmwareManifestError::kSecurityDowngrade;
    }
    std::string canonical;
    if (!security::CanonicalizeManifestForSignature(document, &canonical) ||
        !security::VerifyEd25519Base64(trusted->public_key.data(), canonical, signatureValue)) {
        cJSON_Delete(root);
        return FirmwareManifestError::kInvalidSignature;
    }
    if (std::strcmp(board, capability.board) != 0 || std::strcmp(chip, capability.chip) != 0 ||
        flash != capability.flash_bytes || psram > capability.psram_bytes) {
        cJSON_Delete(root);
        return FirmwareManifestError::kTargetMismatch;
    }
    if (candidate.payload_bytes > capability.slot_bytes) {
        cJSON_Delete(root);
        return FirmwareManifestError::kCapacityExceeded;
    }
    if (!network::IsHttpEndpointUrl(candidate.payload_url)) {
        cJSON_Delete(root);
        return FirmwareManifestError::kInvalidPayloadUrl;
    }
    cJSON_Delete(root);
    *manifest = candidate;
    return FirmwareManifestError::kOk;
}

const char* FirmwareManifestErrorName(FirmwareManifestError error) {
    switch (error) {
        case FirmwareManifestError::kOk: return "ok";
        case FirmwareManifestError::kInvalidJson: return "invalid_json";
        case FirmwareManifestError::kInvalidSchema: return "invalid_schema";
        case FirmwareManifestError::kInvalidSignature: return "invalid_signature";
        case FirmwareManifestError::kUntrustedKey: return "untrusted_key";
        case FirmwareManifestError::kSecurityDowngrade: return "security_downgrade";
        case FirmwareManifestError::kTargetMismatch: return "target_mismatch";
        case FirmwareManifestError::kCapacityExceeded: return "capacity_exceeded";
        case FirmwareManifestError::kInvalidPayloadUrl: return "invalid_payload_url";
    }
    return "unknown";
}
}  // namespace veetee::ota
