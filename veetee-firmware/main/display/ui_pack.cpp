#include "display/ui_pack.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "cJSON.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "psa/crypto.h"

namespace veetee::display {
namespace {

constexpr char kTag[] = "veetee_ui_pack";
constexpr std::array<std::uint8_t, 8> kMagic = {
    'V', 'T', 'P', 'A', 'C', 'K', '1', 0,
};
constexpr std::uint32_t kMaximumPackBytes = 2U * 1024U * 1024U;
constexpr std::uint16_t kHeaderBytes = 64;
constexpr std::uint16_t kEntryBytes = 128;
constexpr std::uint16_t kMaximumEntries = 32;
constexpr std::uint32_t kMemberAlignment = 16;
constexpr std::size_t kHashBufferBytes = 4096;
constexpr std::size_t kMaximumJsonBytes = 64U * 1024U;

constexpr std::array<const char*, 13> kStateNames = {
    "starting",          "wifi_configuring", "network_connecting",
    "activating",        "pairing_recovery", "idle",
    "connecting",        "listening",        "evaluating",
    "thinking",          "speaking",         "aborting",
    "closing",
};

struct PackEntry {
    char name[64] = {};
    std::uint16_t kind = 0;
    std::uint32_t offset = 0;
    std::uint32_t bytes = 0;
    std::array<std::uint8_t, 32> sha256{};
};

std::uint16_t ReadU16(const std::uint8_t* value) {
    return static_cast<std::uint16_t>(value[0]) |
           (static_cast<std::uint16_t>(value[1]) << 8U);
}

std::uint32_t ReadU32(const std::uint8_t* value) {
    return static_cast<std::uint32_t>(value[0]) |
           (static_cast<std::uint32_t>(value[1]) << 8U) |
           (static_cast<std::uint32_t>(value[2]) << 16U) |
           (static_cast<std::uint32_t>(value[3]) << 24U);
}

std::uint32_t Crc32(const std::uint8_t* data, std::size_t bytes) {
    std::uint32_t crc = 0xffffffffU;
    for (std::size_t index = 0; index < bytes; ++index) {
        crc ^= data[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1U) ^ (0xedb88320U &
                                 (0U - static_cast<std::uint32_t>(crc & 1U)));
        }
    }
    return crc ^ 0xffffffffU;
}

bool EndsWith(const char* value, const char* suffix) {
    const std::size_t value_length = std::strlen(value);
    const std::size_t suffix_length = std::strlen(suffix);
    return value_length >= suffix_length &&
           std::memcmp(value + value_length - suffix_length, suffix,
                       suffix_length) == 0;
}

bool IsSafeName(const char* name) {
    if (name == nullptr || name[0] == '\0' || name[0] == '/' ||
        name[std::strlen(name) - 1] == '/') {
        return false;
    }
    const char* segment = name;
    for (const char* cursor = name;; ++cursor) {
        const char character = *cursor;
        if (character != '\0' && character != '/' &&
            !(std::isalnum(static_cast<unsigned char>(character)) != 0 ||
              character == '-' || character == '_' || character == '.')) {
            return false;
        }
        if (character != '/' && character != '\0') continue;
        const std::size_t length = static_cast<std::size_t>(cursor - segment);
        if (length == 0 || (length == 1 && segment[0] == '.') ||
            (length == 2 && segment[0] == '.' && segment[1] == '.')) {
            return false;
        }
        if (character == '\0') return true;
        segment = cursor + 1;
    }
}

std::uint16_t MemberKind(const char* name) {
    if (std::strcmp(name, "manifest.json") == 0) return 1;
    if (std::strcmp(name, "theme.json") == 0) return 2;
    if (std::strncmp(name, "strings/", 8) == 0 && EndsWith(name, ".json")) {
        return 3;
    }
    if (std::strncmp(name, "fonts/", 6) == 0 && EndsWith(name, ".vfont")) {
        return 4;
    }
    if (std::strncmp(name, "icons/", 6) == 0 && EndsWith(name, ".vicon")) {
        return 5;
    }
    if (std::strncmp(name, "backgrounds/", 12) == 0 &&
        EndsWith(name, ".rgb565")) {
        return 6;
    }
    if (std::strncmp(name, "sounds/", 7) == 0 && EndsWith(name, ".opus")) {
        return 7;
    }
    return 0;
}

bool IsZero(const std::uint8_t* data, std::size_t bytes) {
    return std::all_of(data, data + bytes,
                       [](std::uint8_t value) { return value == 0; });
}

bool ParseHexColor(const cJSON* object, const char* key,
                   std::uint16_t* output) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsString(item) || item->valuestring == nullptr ||
        std::strlen(item->valuestring) != 7 || item->valuestring[0] != '#') {
        return false;
    }
    std::uint32_t rgb = 0;
    for (int index = 1; index < 7; ++index) {
        const char character = item->valuestring[index];
        const int nibble = character >= '0' && character <= '9'
                               ? character - '0'
                           : character >= 'a' && character <= 'f'
                               ? character - 'a' + 10
                           : character >= 'A' && character <= 'F'
                               ? character - 'A' + 10
                               : -1;
        if (nibble < 0) return false;
        rgb = (rgb << 4U) | static_cast<std::uint32_t>(nibble);
    }
    const std::uint16_t red = static_cast<std::uint16_t>((rgb >> 19U) & 0x1fU);
    const std::uint16_t green = static_cast<std::uint16_t>((rgb >> 10U) & 0x3fU);
    const std::uint16_t blue = static_cast<std::uint16_t>((rgb >> 3U) & 0x1fU);
    *output = static_cast<std::uint16_t>((red << 11U) | (green << 5U) | blue);
    return true;
}

bool ReadMember(const esp_partition_t* partition, const PackEntry& entry,
                char** output) {
    if (entry.bytes == 0 || entry.bytes > kMaximumJsonBytes || output == nullptr) {
        return false;
    }
    auto* data = static_cast<char*>(std::malloc(entry.bytes + 1U));
    if (data == nullptr) return false;
    if (esp_partition_read(partition, entry.offset, data, entry.bytes) != ESP_OK) {
        std::free(data);
        return false;
    }
    data[entry.bytes] = '\0';
    if (std::memchr(data, '\0', entry.bytes) != nullptr) {
        std::free(data);
        return false;
    }
    *output = data;
    return true;
}

cJSON* ParseJsonMember(const esp_partition_t* partition, const PackEntry& entry) {
    char* document = nullptr;
    if (!ReadMember(partition, entry, &document)) return nullptr;
    const char* parse_end = nullptr;
    cJSON* root = cJSON_ParseWithLengthOpts(
        document, static_cast<std::size_t>(entry.bytes) + 1U, &parse_end, true);
    std::free(document);
    return root;
}

bool VerifyMemberHash(const esp_partition_t* partition, const PackEntry& entry) {
    auto* buffer = static_cast<std::uint8_t*>(std::malloc(kHashBufferBytes));
    if (buffer == nullptr) return false;
    psa_hash_operation_t operation = PSA_HASH_OPERATION_INIT;
    bool valid = psa_hash_setup(&operation, PSA_ALG_SHA_256) == PSA_SUCCESS;
    for (std::uint32_t offset = 0; valid && offset < entry.bytes;) {
        const std::uint32_t bytes = std::min<std::uint32_t>(
            kHashBufferBytes, entry.bytes - offset);
        valid = esp_partition_read(partition, entry.offset + offset, buffer,
                                   bytes) == ESP_OK &&
                psa_hash_update(&operation, buffer, bytes) == PSA_SUCCESS;
        offset += bytes;
    }
    std::array<std::uint8_t, 32> digest{};
    std::size_t digest_bytes = 0;
    valid = valid &&
            psa_hash_finish(&operation, digest.data(), digest.size(),
                            &digest_bytes) == PSA_SUCCESS &&
            digest_bytes == digest.size() && digest == entry.sha256;
    psa_hash_abort(&operation);
    std::free(buffer);
    return valid;
}

const PackEntry* FindEntry(const std::array<PackEntry, kMaximumEntries>& entries,
                           std::uint16_t count, const char* name) {
    for (std::uint16_t index = 0; index < count; ++index) {
        if (std::strcmp(entries[index].name, name) == 0) return &entries[index];
    }
    return nullptr;
}

bool ParseManifest(const cJSON* root, char theme_id[33]) {
    if (!cJSON_IsObject(root)) return false;
    const cJSON* schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON* kind = cJSON_GetObjectItemCaseSensitive(root, "kind");
    const cJSON* id = cJSON_GetObjectItemCaseSensitive(root, "id");
    const cJSON* version = cJSON_GetObjectItemCaseSensitive(root, "version");
    const cJSON* theme = cJSON_GetObjectItemCaseSensitive(root, "theme_id");
    const cJSON* target = cJSON_GetObjectItemCaseSensitive(root, "target");
    const cJSON* compatibility =
        cJSON_GetObjectItemCaseSensitive(root, "compatibility");
    const cJSON* locales = cJSON_GetObjectItemCaseSensitive(root, "locales");
    const cJSON* board = cJSON_GetObjectItemCaseSensitive(target, "board");
    const cJSON* display = cJSON_GetObjectItemCaseSensitive(target, "display");
    const cJSON* resource_abi =
        cJSON_GetObjectItemCaseSensitive(compatibility, "resource_abi");
    const cJSON* ui_abi =
        cJSON_GetObjectItemCaseSensitive(compatibility, "ui_abi");
    if (!cJSON_IsNumber(schema) || schema->valueint != 1 ||
        !cJSON_IsString(kind) || std::strcmp(kind->valuestring, "ui_pack") != 0 ||
        !cJSON_IsString(id) || !cJSON_IsString(version) ||
        !cJSON_IsString(theme) || std::strlen(theme->valuestring) == 0 ||
        std::strlen(theme->valuestring) >= 33 || !cJSON_IsObject(target) ||
        !cJSON_IsString(board) ||
        std::strcmp(board->valuestring, "veetee-s3-n16r8") != 0 ||
        !cJSON_IsString(display) ||
        std::strcmp(display->valuestring, "st7789-240x280-rgb565") != 0 ||
        !cJSON_IsObject(compatibility) || !cJSON_IsNumber(resource_abi) ||
        resource_abi->valueint != 2 || !cJSON_IsNumber(ui_abi) ||
        ui_abi->valueint != 1 || !cJSON_IsArray(locales)) {
        return false;
    }
    bool has_vietnamese = false;
    for (const cJSON* locale = locales->child; locale != nullptr;
         locale = locale->next) {
        if (cJSON_IsString(locale) && locale->valuestring != nullptr &&
            std::strcmp(locale->valuestring, "vi-VN") == 0) {
            has_vietnamese = true;
        }
    }
    if (!has_vietnamese) return false;
    std::snprintf(theme_id, 33, "%s", theme->valuestring);
    return true;
}

bool ParseTheme(const cJSON* root, const char* expected_theme_id,
                UiTheme* theme) {
    if (!cJSON_IsObject(root) || expected_theme_id == nullptr || theme == nullptr) {
        return false;
    }
    const cJSON* schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON* ui_abi = cJSON_GetObjectItemCaseSensitive(root, "ui_abi");
    const cJSON* theme_id = cJSON_GetObjectItemCaseSensitive(root, "theme_id");
    const cJSON* composition =
        cJSON_GetObjectItemCaseSensitive(root, "composition");
    const cJSON* palette = cJSON_GetObjectItemCaseSensitive(root, "palette");
    if (!cJSON_IsNumber(schema) || schema->valueint != 1 ||
        !cJSON_IsNumber(ui_abi) || ui_abi->valueint != 1 ||
        !cJSON_IsString(theme_id) ||
        std::strcmp(theme_id->valuestring, expected_theme_id) != 0 ||
        !cJSON_IsString(composition) || !cJSON_IsObject(palette)) {
        return false;
    }
    if (std::strcmp(composition->valuestring, "signal") == 0) {
        theme->composition = UiComposition::kSignal;
    } else if (std::strcmp(composition->valuestring, "monolith") == 0) {
        theme->composition = UiComposition::kMonolith;
    } else if (std::strcmp(composition->valuestring, "quiet") == 0) {
        theme->composition = UiComposition::kQuiet;
    } else {
        return false;
    }
    for (std::size_t index = 0; index < kStateNames.size(); ++index) {
        const cJSON* colors =
            cJSON_GetObjectItemCaseSensitive(palette, kStateNames[index]);
        if (!cJSON_IsObject(colors) ||
            !ParseHexColor(colors, "background",
                           &theme->states[index].background) ||
            !ParseHexColor(colors, "foreground",
                           &theme->states[index].foreground) ||
            !ParseHexColor(colors, "accent", &theme->states[index].accent)) {
            return false;
        }
    }
    std::snprintf(theme->theme_id, sizeof(theme->theme_id), "%s",
                  expected_theme_id);
    theme->external = true;
    return true;
}

bool ParseVietnameseStrings(const cJSON* root) {
    if (!cJSON_IsObject(root)) return false;
    const cJSON* schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON* locale = cJSON_GetObjectItemCaseSensitive(root, "locale");
    const cJSON* states = cJSON_GetObjectItemCaseSensitive(root, "states");
    if (!cJSON_IsNumber(schema) || schema->valueint != 1 ||
        !cJSON_IsString(locale) ||
        std::strcmp(locale->valuestring, "vi-VN") != 0 ||
        !cJSON_IsObject(states)) {
        return false;
    }
    for (const char* state_name : kStateNames) {
        const cJSON* state = cJSON_GetObjectItemCaseSensitive(states, state_name);
        const cJSON* kicker = cJSON_GetObjectItemCaseSensitive(state, "kicker");
        const cJSON* title = cJSON_GetObjectItemCaseSensitive(state, "title");
        const cJSON* hint = cJSON_GetObjectItemCaseSensitive(state, "hint");
        if (!cJSON_IsObject(state) || !cJSON_IsString(kicker) ||
            !cJSON_IsString(title) || !cJSON_IsString(hint)) {
            return false;
        }
    }
    return true;
}

}  // namespace

UiTheme BuiltInSignalTheme() {
    UiTheme theme{};
    const std::array<UiStateStyle, 13> styles = {{
        {0x1167, 0xF79D, 0xCF6F}, {0x11C8, 0xF79D, 0x9EFC},
        {0x11C8, 0xF79D, 0x9EFC}, {0x21A7, 0xF79D, 0xA6DC},
        {0x38C3, 0xFF9D, 0xFB4A}, {0x1167, 0xF79D, 0xCF6F},
        {0x11C8, 0xF79D, 0x9EFC}, {0x09C6, 0xF79D, 0xCF6F},
        {0x3164, 0xF79D, 0xF5EA}, {0x3164, 0xF79D, 0xF5EA},
        {0x4925, 0xFF9D, 0xFC4F}, {0x38C3, 0xFF9D, 0xFB4A},
        {0x19A7, 0xF79D, 0x8576},
    }};
    theme.states = styles;
    return theme;
}

bool IsValidUiTheme(const UiTheme& theme) {
    if (theme.theme_id[0] == '\0' ||
        std::strlen(theme.theme_id) >= sizeof(theme.theme_id)) {
        return false;
    }
    return std::all_of(theme.states.begin(), theme.states.end(),
                       [](const UiStateStyle& style) {
                           return style.background != style.foreground;
                       });
}

esp_err_t LoadUiPackPartition(const char* partition_label, UiTheme* theme) {
    if (partition_label == nullptr || theme == nullptr) return ESP_ERR_INVALID_ARG;
    const esp_partition_t* partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, partition_label);
    if (partition == nullptr || partition->size < kHeaderBytes) {
        return ESP_ERR_NOT_FOUND;
    }
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;

    std::array<std::uint8_t, kHeaderBytes> header{};
    if (esp_partition_read(partition, 0, header.data(), header.size()) != ESP_OK ||
        !std::equal(kMagic.begin(), kMagic.end(), header.begin())) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    const std::uint16_t format_version = ReadU16(header.data() + 8);
    const std::uint16_t header_bytes = ReadU16(header.data() + 10);
    const std::uint16_t ui_abi = ReadU16(header.data() + 12);
    const std::uint16_t entry_count = ReadU16(header.data() + 14);
    const std::uint32_t index_offset = ReadU32(header.data() + 16);
    const std::uint32_t index_bytes = ReadU32(header.data() + 20);
    const std::uint32_t payload_offset = ReadU32(header.data() + 24);
    const std::uint32_t total_bytes = ReadU32(header.data() + 28);
    const std::uint32_t index_crc = ReadU32(header.data() + 32);
    const std::uint32_t flags = ReadU32(header.data() + 36);
    const std::uint32_t expected_payload =
        (kHeaderBytes + entry_count * kEntryBytes + 15U) & ~15U;
    if (format_version != 1 || header_bytes != kHeaderBytes || ui_abi != 1 ||
        entry_count < 3 || entry_count > kMaximumEntries ||
        index_offset != kHeaderBytes || index_bytes != entry_count * kEntryBytes ||
        payload_offset != expected_payload || total_bytes > kMaximumPackBytes ||
        total_bytes > partition->size || total_bytes < payload_offset || flags != 0 ||
        !IsZero(header.data() + 40, 24)) {
        return ESP_ERR_INVALID_SIZE;
    }

    auto* index = static_cast<std::uint8_t*>(std::malloc(index_bytes));
    if (index == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = esp_partition_read(partition, index_offset, index,
                                         index_bytes);
    if (error != ESP_OK || Crc32(index, index_bytes) != index_crc) {
        std::free(index);
        return error == ESP_OK ? ESP_ERR_INVALID_CRC : error;
    }

    std::array<PackEntry, kMaximumEntries> entries{};
    std::uint32_t previous_end = payload_offset;
    for (std::uint16_t item = 0; item < entry_count && error == ESP_OK; ++item) {
        const std::uint8_t* raw = index + item * kEntryBytes;
        const void* terminator = std::memchr(raw, 0, 64);
        if (terminator == nullptr || terminator == raw) {
            error = ESP_ERR_INVALID_RESPONSE;
            break;
        }
        const std::size_t name_bytes =
            static_cast<const std::uint8_t*>(terminator) - raw;
        std::memcpy(entries[item].name, raw, name_bytes);
        entries[item].name[name_bytes] = '\0';
        entries[item].kind = ReadU16(raw + 64);
        const std::uint16_t entry_flags = ReadU16(raw + 66);
        entries[item].offset = ReadU32(raw + 68);
        entries[item].bytes = ReadU32(raw + 72);
        std::copy_n(raw + 76, 32, entries[item].sha256.begin());
        const std::uint32_t alignment = ReadU32(raw + 108);
        const std::uint16_t expected_kind = MemberKind(entries[item].name);
        const std::uint64_t member_end =
            static_cast<std::uint64_t>(entries[item].offset) + entries[item].bytes;
        if (!IsSafeName(entries[item].name) || expected_kind == 0 ||
            entries[item].kind != expected_kind || entry_flags != 0 ||
            entries[item].bytes == 0 || alignment != kMemberAlignment ||
            entries[item].offset % alignment != 0 ||
            entries[item].offset < payload_offset ||
            entries[item].offset < previous_end || member_end > total_bytes ||
            !IsZero(raw + 112, 16)) {
            error = ESP_ERR_INVALID_RESPONSE;
            break;
        }
        for (std::uint16_t previous = 0; previous < item; ++previous) {
            if (std::strcmp(entries[previous].name, entries[item].name) == 0) {
                error = ESP_ERR_INVALID_RESPONSE;
            }
        }
        previous_end = static_cast<std::uint32_t>(member_end);
    }
    std::free(index);
    if (error != ESP_OK) return error;

    const PackEntry* manifest_entry =
        FindEntry(entries, entry_count, "manifest.json");
    const PackEntry* theme_entry = FindEntry(entries, entry_count, "theme.json");
    const PackEntry* strings_entry =
        FindEntry(entries, entry_count, "strings/vi-VN.json");
    if (manifest_entry == nullptr || theme_entry == nullptr ||
        strings_entry == nullptr) {
        return ESP_ERR_NOT_FOUND;
    }
    for (std::uint16_t item = 0; item < entry_count; ++item) {
        if (!VerifyMemberHash(partition, entries[item])) {
            return ESP_ERR_INVALID_CRC;
        }
    }

    cJSON* manifest_json = ParseJsonMember(partition, *manifest_entry);
    cJSON* theme_json = ParseJsonMember(partition, *theme_entry);
    cJSON* strings_json = ParseJsonMember(partition, *strings_entry);
    char theme_id[33] = {};
    UiTheme candidate = BuiltInSignalTheme();
    const bool valid = ParseManifest(manifest_json, theme_id) &&
                       ParseTheme(theme_json, theme_id, &candidate) &&
                       ParseVietnameseStrings(strings_json) &&
                       IsValidUiTheme(candidate);
    cJSON_Delete(manifest_json);
    cJSON_Delete(theme_json);
    cJSON_Delete(strings_json);
    if (!valid) return ESP_ERR_INVALID_RESPONSE;

    *theme = candidate;
    ESP_LOGI(kTag, "UI Pack loaded partition=%s theme=%s composition=%u",
             partition_label, theme->theme_id,
             static_cast<unsigned>(theme->composition));
    return ESP_OK;
}

}  // namespace veetee::display
