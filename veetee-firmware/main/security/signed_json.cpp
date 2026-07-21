#include "security/signed_json.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstring>
#include <limits>
#include <vector>

#include "cJSON.h"
#include "monocypher-ed25519.h"

namespace veetee::security {
namespace {

constexpr std::size_t kMaximumDocumentBytes = 32768;
constexpr std::int64_t kMaximumExactJsonInteger = 9007199254740991LL;

bool HasForbiddenNull(std::string_view document) {
    bool in_string = false;
    for (std::size_t index = 0; index < document.size(); ++index) {
        const char character = document[index];
        if (character == '\0') return true;
        if (!in_string) {
            if (character == '"') in_string = true;
            continue;
        }
        if (character == '"') {
            in_string = false;
            continue;
        }
        if (character != '\\' || index + 1 >= document.size()) continue;

        const char escape = document[++index];
        if (escape != 'u' || index + 4 >= document.size()) continue;
        if (document[index + 1] == '0' && document[index + 2] == '0' &&
            document[index + 3] == '0' && document[index + 4] == '0') {
            return true;
        }
        index += 4;
    }
    return false;
}

bool IsValidUtf8(std::string_view value) {
    std::size_t index = 0;
    while (index < value.size()) {
        const auto first = static_cast<unsigned char>(value[index]);
        if (first <= 0x7f) {
            ++index;
            continue;
        }
        std::size_t continuation = 0;
        std::uint32_t codepoint = 0;
        if ((first & 0xe0U) == 0xc0U) {
            continuation = 1;
            codepoint = first & 0x1fU;
        } else if ((first & 0xf0U) == 0xe0U) {
            continuation = 2;
            codepoint = first & 0x0fU;
        } else if ((first & 0xf8U) == 0xf0U) {
            continuation = 3;
            codepoint = first & 0x07U;
        } else {
            return false;
        }
        if (index + continuation >= value.size()) return false;
        for (std::size_t offset = 1; offset <= continuation; ++offset) {
            const auto byte = static_cast<unsigned char>(value[index + offset]);
            if ((byte & 0xc0U) != 0x80U) return false;
            codepoint = (codepoint << 6U) | (byte & 0x3fU);
        }
        const std::uint32_t minimum =
            continuation == 1 ? 0x80U : continuation == 2 ? 0x800U : 0x10000U;
        if (codepoint < minimum || codepoint > 0x10ffffU ||
            (codepoint >= 0xd800U && codepoint <= 0xdfffU)) {
            return false;
        }
        index += continuation + 1;
    }
    return true;
}

bool AppendEscapedString(const char* value, std::string* output) {
    if (value == nullptr || output == nullptr) return false;
    const std::string_view text(value);
    if (!IsValidUtf8(text)) return false;
    output->push_back('"');
    constexpr char kHex[] = "0123456789abcdef";
    for (unsigned char character : text) {
        switch (character) {
            case '"': output->append("\\\""); break;
            case '\\': output->append("\\\\"); break;
            case '\b': output->append("\\b"); break;
            case '\t': output->append("\\t"); break;
            case '\n': output->append("\\n"); break;
            case '\f': output->append("\\f"); break;
            case '\r': output->append("\\r"); break;
            default:
                if (character < 0x20U) {
                    output->append("\\u00");
                    output->push_back(kHex[character >> 4U]);
                    output->push_back(kHex[character & 0x0fU]);
                } else {
                    output->push_back(static_cast<char>(character));
                }
                break;
        }
    }
    output->push_back('"');
    return true;
}

bool IsAsciiKey(const char* value) {
    if (value == nullptr || value[0] == '\0') return false;
    for (const unsigned char* cursor =
             reinterpret_cast<const unsigned char*>(value);
         *cursor != '\0'; ++cursor) {
        if (*cursor > 0x7fU || *cursor < 0x20U) return false;
    }
    return true;
}

bool CanonicalizeNode(const cJSON* node, std::string* output) {
    if (node == nullptr || output == nullptr) return false;
    if (cJSON_IsNull(node)) {
        output->append("null");
        return true;
    }
    if (cJSON_IsBool(node)) {
        output->append(cJSON_IsTrue(node) ? "true" : "false");
        return true;
    }
    if (cJSON_IsString(node)) return AppendEscapedString(node->valuestring, output);
    if (cJSON_IsNumber(node)) {
        if (!std::isfinite(node->valuedouble) ||
            std::floor(node->valuedouble) != node->valuedouble ||
            std::fabs(node->valuedouble) >
                static_cast<double>(kMaximumExactJsonInteger)) {
            return false;
        }
        const auto integer = static_cast<std::int64_t>(node->valuedouble);
        std::array<char, 32> encoded{};
        const auto result = std::to_chars(encoded.data(),
                                          encoded.data() + encoded.size(), integer);
        if (result.ec != std::errc()) return false;
        output->append(encoded.data(), result.ptr);
        return true;
    }
    if (cJSON_IsArray(node)) {
        output->push_back('[');
        bool first = true;
        for (const cJSON* child = node->child; child != nullptr; child = child->next) {
            if (!first) output->push_back(',');
            first = false;
            if (!CanonicalizeNode(child, output)) return false;
        }
        output->push_back(']');
        return true;
    }
    if (cJSON_IsObject(node)) {
        std::vector<const cJSON*> properties;
        for (const cJSON* child = node->child; child != nullptr; child = child->next) {
            if (!IsAsciiKey(child->string)) return false;
            properties.push_back(child);
        }
        std::sort(properties.begin(), properties.end(), [](const cJSON* left,
                                                           const cJSON* right) {
            return std::strcmp(left->string, right->string) < 0;
        });
        for (std::size_t index = 1; index < properties.size(); ++index) {
            if (std::strcmp(properties[index - 1]->string,
                            properties[index]->string) == 0) {
                return false;
            }
        }
        output->push_back('{');
        for (std::size_t index = 0; index < properties.size(); ++index) {
            if (index != 0) output->push_back(',');
            if (!AppendEscapedString(properties[index]->string, output)) return false;
            output->push_back(':');
            if (!CanonicalizeNode(properties[index], output)) return false;
        }
        output->push_back('}');
        return true;
    }
    return false;
}

bool CanonicalizeOwned(cJSON* root, std::string* canonical) {
    if (root == nullptr || canonical == nullptr) return false;
    canonical->clear();
    canonical->reserve(2048);
    const bool valid = CanonicalizeNode(root, canonical);
    if (!valid) canonical->clear();
    return valid;
}

cJSON* ParseExact(std::string_view document) {
    std::string terminated(document);
    const char* parse_end = nullptr;
    return cJSON_ParseWithLengthOpts(terminated.c_str(), terminated.size() + 1,
                                     &parse_end, true);
}

int Base64Value(unsigned char character) {
    if (character >= 'A' && character <= 'Z') return character - 'A';
    if (character >= 'a' && character <= 'z') return character - 'a' + 26;
    if (character >= '0' && character <= '9') return character - '0' + 52;
    if (character == '+') return 62;
    if (character == '/') return 63;
    return -1;
}

bool DecodeSignature(std::string_view encoded, std::uint8_t output[64]) {
    if (encoded.size() != 88 || encoded[86] != '=' || encoded[87] != '=') {
        return false;
    }
    std::size_t output_index = 0;
    for (std::size_t index = 0; index < encoded.size(); index += 4) {
        const bool final_block = index + 4 == encoded.size();
        const int first = Base64Value(static_cast<unsigned char>(encoded[index]));
        const int second = Base64Value(static_cast<unsigned char>(encoded[index + 1]));
        const int third = encoded[index + 2] == '='
                              ? 0
                              : Base64Value(static_cast<unsigned char>(encoded[index + 2]));
        const int fourth = encoded[index + 3] == '='
                               ? 0
                               : Base64Value(static_cast<unsigned char>(encoded[index + 3]));
        if (first < 0 || second < 0 || third < 0 || fourth < 0) return false;
        if (!final_block && (encoded[index + 2] == '=' || encoded[index + 3] == '=')) {
            return false;
        }
        if (final_block && (second & 0x0f) != 0) return false;
        const std::uint32_t packed = (static_cast<std::uint32_t>(first) << 18U) |
                                     (static_cast<std::uint32_t>(second) << 12U) |
                                     (static_cast<std::uint32_t>(third) << 6U) |
                                     static_cast<std::uint32_t>(fourth);
        if (output_index < 64) output[output_index++] = packed >> 16U;
        if (encoded[index + 2] != '=' && output_index < 64) {
            output[output_index++] = packed >> 8U;
        }
        if (encoded[index + 3] != '=' && output_index < 64) {
            output[output_index++] = packed;
        }
    }
    return output_index == 64;
}

}  // namespace

bool CanonicalizeJson(std::string_view document, std::string* canonical) {
    if (canonical == nullptr || document.empty() ||
        document.size() > kMaximumDocumentBytes || HasForbiddenNull(document)) {
        return false;
    }
    cJSON* root = ParseExact(document);
    if (root == nullptr) return false;
    const bool valid = CanonicalizeOwned(root, canonical);
    cJSON_Delete(root);
    return valid;
}

bool CanonicalizeManifestForSignature(std::string_view document,
                                      std::string* canonical) {
    if (canonical == nullptr || document.empty() ||
        document.size() > kMaximumDocumentBytes || HasForbiddenNull(document)) {
        return false;
    }
    cJSON* root = ParseExact(document);
    if (root == nullptr || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return false;
    }
    cJSON* signature = cJSON_GetObjectItemCaseSensitive(root, "signature");
    if (!cJSON_IsObject(signature) ||
        cJSON_GetObjectItemCaseSensitive(signature, "value") == nullptr) {
        cJSON_Delete(root);
        return false;
    }
    cJSON_DeleteItemFromObjectCaseSensitive(signature, "value");
    const bool valid = CanonicalizeOwned(root, canonical);
    cJSON_Delete(root);
    return valid;
}

bool VerifyEd25519Base64(const std::uint8_t public_key[32],
                         std::string_view message,
                         std::string_view signature_base64) {
    if (public_key == nullptr || message.empty()) return false;
    std::array<std::uint8_t, 64> signature{};
    if (!DecodeSignature(signature_base64, signature.data())) return false;
    return crypto_ed25519_check(signature.data(), public_key,
                                reinterpret_cast<const std::uint8_t*>(message.data()),
                                message.size()) == 0;
}

}  // namespace veetee::security
