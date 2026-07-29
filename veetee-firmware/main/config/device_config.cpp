#include "config/device_config.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <initializer_list>
#include <string>

#include "cJSON.h"
#include "security/signed_json.h"

namespace veetee::config {
namespace {

constexpr std::size_t kMaximumDeviceConfigBytes = 8192;

bool HasOnlyProperties(const cJSON* object,
                       std::initializer_list<const char*> allowed) {
    if (!cJSON_IsObject(object)) return false;
    std::size_t count = 0;
    for (const cJSON* child = object->child; child != nullptr;
         child = child->next) {
        ++count;
        const bool found = std::any_of(
            allowed.begin(), allowed.end(), [child](const char* name) {
                return child->string != nullptr &&
                       std::strcmp(child->string, name) == 0;
            });
        if (!found) return false;
    }
    return count == allowed.size();
}

bool ReadU32(const cJSON* object, const char* key, std::uint32_t* output) {
    if (output == nullptr) return false;
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) ||
        value->valuedouble < 0 || value->valuedouble > UINT32_MAX ||
        std::floor(value->valuedouble) != value->valuedouble) {
        return false;
    }
    *output = static_cast<std::uint32_t>(value->valuedouble);
    return true;
}

template <std::size_t Size>
bool CopyString(const cJSON* object, const char* key,
                std::array<char, Size>* output) {
    if (output == nullptr) return false;
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsString(value) || value->valuestring == nullptr) return false;
    const std::size_t length = std::strlen(value->valuestring);
    if (length == 0 || length >= output->size()) return false;
    std::memcpy(output->data(), value->valuestring, length + 1);
    return true;
}

template <std::size_t Size>
bool IsSafeIdentifier(const std::array<char, Size>& value) {
    return value[0] != '\0' &&
           std::all_of(value.data(), value.data() + std::strlen(value.data()),
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.';
                       });
}

bool IsWakeNetModelId(const std::array<char, 65>& value) {
    const std::size_t length = std::strlen(value.data());
    if (length < 3 || value[0] != 'w' || value[1] != 'n') return false;
    return std::all_of(value.data() + 2, value.data() + length,
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.';
                       });
}

bool IsSafeVersion(const std::array<char, 33>& value) {
    return value[0] != '\0' &&
           std::all_of(value.data(), value.data() + std::strlen(value.data()),
                       [](unsigned char character) {
                           return (character >= 'a' && character <= 'z') ||
                                  (character >= 'A' && character <= 'Z') ||
                                  (character >= '0' && character <= '9') ||
                                  character == '-' || character == '_' ||
                                  character == '.' || character == '+';
                       });
}

bool UsesIntegerNumberSyntax(std::string_view document) {
    bool in_string = false;
    bool escaped = false;
    for (std::size_t index = 0; index < document.size(); ++index) {
        const char character = document[index];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (character == '\\') {
                escaped = true;
            } else if (character == '"') {
                in_string = false;
            }
            continue;
        }
        if (character == '"') {
            in_string = true;
            continue;
        }
        if (character != '-' && (character < '0' || character > '9')) {
            continue;
        }
        std::size_t cursor = index;
        if (document[cursor] == '-') ++cursor;
        const std::size_t digits = cursor;
        while (cursor < document.size() && document[cursor] >= '0' &&
               document[cursor] <= '9') {
            ++cursor;
        }
        if (cursor == digits ||
            (cursor - digits > 1 && document[digits] == '0') ||
            (cursor < document.size() &&
             (document[cursor] == '.' || document[cursor] == 'e' ||
              document[cursor] == 'E'))) {
            return false;
        }
        index = cursor - 1;
    }
    return !in_string && !escaped;
}

cJSON* ParseExact(std::string_view document) {
    if (document.empty() || document.size() > kMaximumDeviceConfigBytes ||
        std::find(document.begin(), document.end(), '\0') != document.end() ||
        !UsesIntegerNumberSyntax(document)) {
        return nullptr;
    }
    std::string terminated(document);
    const char* parse_end = nullptr;
    return cJSON_ParseWithLengthOpts(terminated.c_str(), terminated.size() + 1,
                                     &parse_end, true);
}

bool IsThresholdValid(std::uint32_t value) {
    return value == kThresholdPpmDefault ||
           (value >= kMinimumThresholdPpm && value <= kMaximumThresholdPpm);
}

bool ParseDetector(const cJSON* object, bool interrupt,
                   DetectorConfig* output) {
    if (output == nullptr ||
        !HasOnlyProperties(
            object,
            interrupt
                ? std::initializer_list<const char*>{
                      "model_id", "threshold_ppm", "cooldown_ms",
                      "enabled_while_speaking"}
                : std::initializer_list<const char*>{
                      "model_id", "threshold_ppm", "cooldown_ms"})) {
        return false;
    }
    DetectorConfig parsed{};
    parsed.enabled = true;
    if (!CopyString(object, "model_id", &parsed.model_id) ||
        !IsWakeNetModelId(parsed.model_id) ||
        !ReadU32(object, "threshold_ppm", &parsed.threshold_ppm) ||
        !IsThresholdValid(parsed.threshold_ppm) ||
        !ReadU32(object, "cooldown_ms", &parsed.cooldown_ms) ||
        parsed.cooldown_ms < kMinimumDetectorCooldownMs ||
        parsed.cooldown_ms > kMaximumDetectorCooldownMs) {
        return false;
    }
    if (interrupt) {
        const cJSON* while_speaking = cJSON_GetObjectItemCaseSensitive(
            object, "enabled_while_speaking");
        if (!cJSON_IsBool(while_speaking)) return false;
        parsed.enabled_while_speaking = cJSON_IsTrue(while_speaking);
    }
    *output = parsed;
    return true;
}

const ota::TrustedReleaseKey* FindKey(
    const ota::TrustedReleaseKey* keys, std::size_t count,
    const char* key_id) {
    if (keys == nullptr || key_id == nullptr) return nullptr;
    for (std::size_t index = 0; index < count; ++index) {
        if (keys[index].key_id != nullptr &&
            std::strcmp(keys[index].key_id, key_id) == 0) {
            return &keys[index];
        }
    }
    return nullptr;
}

}  // namespace

DeviceConfigError VerifyDeviceConfig(
    std::string_view document, const char* expected_device_id,
    std::uint32_t expected_version,
    const ota::TrustedReleaseKey* trusted_keys,
    std::size_t trusted_key_count, DeviceConfig* config) {
    if (document.empty() || expected_device_id == nullptr ||
        expected_device_id[0] == '\0' || expected_version == 0 ||
        expected_version > kMaximumDeviceConfigVersion ||
        config == nullptr) {
        return DeviceConfigError::kInvalidSchema;
    }

    cJSON* root = ParseExact(document);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return DeviceConfigError::kInvalidJson;
    }

    DeviceConfig parsed{};
    std::array<char, 65> device_id{};
    const cJSON* wake_profile =
        cJSON_GetObjectItemCaseSensitive(root, "wake_profile");
    const cJSON* signature =
        cJSON_GetObjectItemCaseSensitive(root, "signature");
    std::uint32_t schema_version = 0;
    std::array<char, 65> key_id{};
    std::array<char, 89> signature_value{};
    std::array<char, 16> algorithm{};

    bool valid = HasOnlyProperties(
                     root, {"schema_version", "device_id", "version",
                            "wake_profile", "signature"}) &&
                 ReadU32(root, "schema_version", &schema_version) &&
                 schema_version == kDeviceConfigSchemaVersion &&
                 CopyString(root, "device_id", &device_id) &&
                 IsSafeIdentifier(device_id) &&
                 ReadU32(root, "version", &parsed.version) &&
                 parsed.version > 0 &&
                 parsed.version <= kMaximumDeviceConfigVersion &&
                 HasOnlyProperties(signature,
                                   {"algorithm", "key_id",
                                    "security_epoch", "value"}) &&
                 CopyString(signature, "algorithm", &algorithm) &&
                 std::strcmp(algorithm.data(), "ed25519") == 0 &&
                 CopyString(signature, "key_id", &key_id) &&
                 IsSafeIdentifier(key_id) &&
                 ReadU32(signature, "security_epoch",
                         &parsed.security_epoch) &&
                 parsed.security_epoch > 0 &&
                 parsed.security_epoch <= kMaximumDeviceConfigVersion &&
                 CopyString(signature, "value", &signature_value);

    if (valid && cJSON_IsNull(wake_profile)) {
        parsed.has_wake_profile = false;
    } else if (valid && cJSON_IsObject(wake_profile)) {
        parsed.has_wake_profile = true;
        valid = HasOnlyProperties(
                    wake_profile,
                    {"id", "version", "required_resource_version",
                     "activation", "interrupt", "send_wake_audio"}) &&
                CopyString(wake_profile, "id", &parsed.wake_profile_id) &&
                IsSafeIdentifier(parsed.wake_profile_id) &&
                ReadU32(wake_profile, "version",
                        &parsed.wake_profile_version) &&
                parsed.wake_profile_version > 0 &&
                parsed.wake_profile_version <= kMaximumDeviceConfigVersion &&
                CopyString(wake_profile, "required_resource_version",
                           &parsed.required_resource_version) &&
                IsSafeVersion(parsed.required_resource_version) &&
                ParseDetector(cJSON_GetObjectItemCaseSensitive(
                                  wake_profile, "activation"),
                              false, &parsed.activation);
        const cJSON* interrupt =
            cJSON_GetObjectItemCaseSensitive(wake_profile, "interrupt");
        if (valid && cJSON_IsNull(interrupt)) {
            parsed.interrupt = DetectorConfig{};
        } else if (valid) {
            valid = ParseDetector(interrupt, true, &parsed.interrupt);
        }
        const cJSON* send_wake_audio =
            cJSON_GetObjectItemCaseSensitive(wake_profile, "send_wake_audio");
        valid = valid && cJSON_IsBool(send_wake_audio);
        if (valid) parsed.send_wake_audio = cJSON_IsTrue(send_wake_audio);
    } else {
        valid = false;
    }

    if (!valid) {
        cJSON_Delete(root);
        return DeviceConfigError::kInvalidSchema;
    }
    if (parsed.interrupt.enabled &&
        std::strcmp(parsed.activation.model_id.data(),
                    parsed.interrupt.model_id.data()) == 0) {
        cJSON_Delete(root);
        return DeviceConfigError::kUnsupportedFeature;
    }
    if (std::strcmp(device_id.data(), expected_device_id) != 0) {
        cJSON_Delete(root);
        return DeviceConfigError::kDeviceMismatch;
    }
    if (parsed.version != expected_version) {
        cJSON_Delete(root);
        return DeviceConfigError::kVersionMismatch;
    }

    const ota::TrustedReleaseKey* key = FindKey(
        trusted_keys, trusted_key_count, key_id.data());
    if (key == nullptr) {
        cJSON_Delete(root);
        return DeviceConfigError::kUntrustedKey;
    }
    if (parsed.security_epoch < key->minimum_security_epoch) {
        cJSON_Delete(root);
        return DeviceConfigError::kSecurityDowngrade;
    }
    cJSON_Delete(root);

    std::string canonical;
    if (!security::CanonicalizeManifestForSignature(document, &canonical) ||
        !security::VerifyEd25519Base64(key->public_key.data(), canonical,
                                      signature_value.data())) {
        return DeviceConfigError::kInvalidSignature;
    }
    *config = parsed;
    return DeviceConfigError::kOk;
}

const char* DeviceConfigErrorName(DeviceConfigError error) {
    switch (error) {
        case DeviceConfigError::kOk: return "ok";
        case DeviceConfigError::kInvalidJson: return "invalid_json";
        case DeviceConfigError::kInvalidSchema: return "invalid_schema";
        case DeviceConfigError::kInvalidSignature: return "invalid_signature";
        case DeviceConfigError::kUntrustedKey: return "untrusted_key";
        case DeviceConfigError::kSecurityDowngrade: return "security_downgrade";
        case DeviceConfigError::kDeviceMismatch: return "device_mismatch";
        case DeviceConfigError::kVersionMismatch: return "version_mismatch";
        case DeviceConfigError::kUnsupportedFeature:
            return "unsupported_feature";
    }
    return "unknown";
}

}  // namespace veetee::config
