#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::settings {

constexpr std::uint32_t kWifiProfileRecordVersion = 1;
constexpr std::size_t kMaxWifiProfiles = 5;

struct WifiProfile {
    char ssid[33] = {};
    char password[65] = {};
    std::uint16_t success_count = 0;
};

struct WifiProfileRecord {
    std::uint32_t version = kWifiProfileRecordVersion;
    std::uint8_t count = 0;
    std::uint8_t reserved[3] = {};
    WifiProfile profiles[kMaxWifiProfiles] = {};
    std::uint32_t crc32 = 0;
};

void SealWifiProfileRecord(WifiProfileRecord* record);
bool IsValidWifiProfileRecord(const WifiProfileRecord& record);

// Upsert moves the selected network to the front. An empty password keeps the
// existing password for an already known SSID, which makes portal re-selection safe.
bool UpsertWifiProfile(WifiProfileRecord* record, const char* ssid,
                       const char* password);
bool MarkWifiProfileSuccessful(WifiProfileRecord* record, const char* ssid);
const WifiProfile* FindWifiProfile(const WifiProfileRecord& record,
                                   const char* ssid);

}  // namespace veetee::settings
