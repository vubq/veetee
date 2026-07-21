#include "settings/wifi_profile_record.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <limits>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<WifiProfileRecord>);
static_assert(sizeof(WifiProfile) == 100,
              "Wi-Fi profile layout is a versioned NVS contract");
static_assert(sizeof(WifiProfileRecord) == 512,
              "Wi-Fi record layout is a versioned NVS contract");

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

bool IsZeroProfile(const WifiProfile& profile) {
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(&profile);
    return std::all_of(bytes, bytes + sizeof(profile),
                       [](std::uint8_t value) { return value == 0; });
}

bool HasDuplicateSsid(const WifiProfileRecord& record, std::size_t index) {
    for (std::size_t previous = 0; previous < index; ++previous) {
        if (std::strcmp(record.profiles[previous].ssid,
                        record.profiles[index].ssid) == 0) {
            return true;
        }
    }
    return false;
}

void CopyString(char* destination, std::size_t capacity, const char* source) {
    std::snprintf(destination, capacity, "%s", source == nullptr ? "" : source);
}

std::size_t FindProfileIndex(const WifiProfileRecord& record, const char* ssid) {
    if (ssid == nullptr) return kMaxWifiProfiles;
    for (std::size_t index = 0; index < record.count; ++index) {
        if (std::strcmp(record.profiles[index].ssid, ssid) == 0) return index;
    }
    return kMaxWifiProfiles;
}

void MoveToFront(WifiProfileRecord* record, std::size_t index) {
    if (index == 0 || index >= record->count) return;
    const WifiProfile selected = record->profiles[index];
    for (std::size_t cursor = index; cursor > 0; --cursor) {
        record->profiles[cursor] = record->profiles[cursor - 1];
    }
    record->profiles[0] = selected;
}

}  // namespace

void SealWifiProfileRecord(WifiProfileRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(WifiProfileRecord, crc32));
}

bool IsValidWifiProfileRecord(const WifiProfileRecord& record) {
    if (record.version != kWifiProfileRecordVersion ||
        record.count > kMaxWifiProfiles ||
        record.crc32 != Crc32(&record, offsetof(WifiProfileRecord, crc32))) {
        return false;
    }
    for (std::size_t index = 0; index < record.count; ++index) {
        const WifiProfile& profile = record.profiles[index];
        if (!IsTerminated(profile.ssid) || !IsTerminated(profile.password) ||
            profile.ssid[0] == '\0' || HasDuplicateSsid(record, index)) {
            return false;
        }
    }
    for (std::size_t index = record.count; index < kMaxWifiProfiles; ++index) {
        if (!IsZeroProfile(record.profiles[index])) return false;
    }
    return true;
}

bool UpsertWifiProfile(WifiProfileRecord* record, const char* ssid,
                       const char* password) {
    if (record == nullptr || ssid == nullptr || password == nullptr ||
        ssid[0] == '\0' ||
        std::strlen(ssid) >= sizeof(WifiProfile{}.ssid) ||
        std::strlen(password) >= sizeof(WifiProfile{}.password)) {
        return false;
    }
    if (record->version != kWifiProfileRecordVersion ||
        record->count > kMaxWifiProfiles) {
        *record = WifiProfileRecord{};
    }

    const std::size_t existing = FindProfileIndex(*record, ssid);
    if (existing < record->count) {
        if (password[0] != '\0') {
            CopyString(record->profiles[existing].password,
                       sizeof(record->profiles[existing].password), password);
        }
        MoveToFront(record, existing);
    } else {
        const std::size_t new_count =
            std::min<std::size_t>(record->count + 1, kMaxWifiProfiles);
        for (std::size_t cursor = new_count - 1; cursor > 0; --cursor) {
            record->profiles[cursor] = record->profiles[cursor - 1];
        }
        record->profiles[0] = WifiProfile{};
        CopyString(record->profiles[0].ssid, sizeof(record->profiles[0].ssid),
                   ssid);
        CopyString(record->profiles[0].password,
                   sizeof(record->profiles[0].password), password);
        record->count = static_cast<std::uint8_t>(new_count);
    }
    SealWifiProfileRecord(record);
    return true;
}

bool MarkWifiProfileSuccessful(WifiProfileRecord* record, const char* ssid) {
    if (record == nullptr || ssid == nullptr) return false;
    const std::size_t index = FindProfileIndex(*record, ssid);
    if (index >= record->count) return false;
    if (record->profiles[index].success_count <
        std::numeric_limits<std::uint16_t>::max()) {
        ++record->profiles[index].success_count;
    }
    MoveToFront(record, index);
    SealWifiProfileRecord(record);
    return true;
}

const WifiProfile* FindWifiProfile(const WifiProfileRecord& record,
                                   const char* ssid) {
    const std::size_t index = FindProfileIndex(record, ssid);
    return index < record.count ? &record.profiles[index] : nullptr;
}

}  // namespace veetee::settings
