#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "settings/wifi_profile_record.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::settings::FindWifiProfile;
    using veetee::settings::IsValidWifiProfileRecord;
    using veetee::settings::MarkWifiProfileSuccessful;
    using veetee::settings::SealWifiProfileRecord;
    using veetee::settings::UpsertWifiProfile;
    using veetee::settings::WifiProfileRecord;
    using veetee::settings::kMaxWifiProfiles;

    WifiProfileRecord record{};
    SealWifiProfileRecord(&record);
    Expect(IsValidWifiProfileRecord(record), "sealed empty record is valid");

    // This is the pure part of the V2 -> V3 migration from legacy keys.
    Expect(UpsertWifiProfile(&record, "Home", "secret"),
           "legacy credential becomes a profile");
    Expect(record.count == 1 && std::strcmp(record.profiles[0].ssid, "Home") == 0,
           "legacy profile is active");
    Expect(IsValidWifiProfileRecord(record), "migrated record is sealed");

    Expect(UpsertWifiProfile(&record, "Office", "work-pass"),
           "second network added");
    Expect(std::strcmp(record.profiles[0].ssid, "Office") == 0,
           "newly selected network moves to front");
    Expect(UpsertWifiProfile(&record, "Home", ""),
           "known network accepts blank password");
    Expect(std::strcmp(record.profiles[0].password, "secret") == 0,
           "blank password preserves saved password");

    Expect(MarkWifiProfileSuccessful(&record, "Office"),
           "successful network can be promoted");
    Expect(std::strcmp(record.profiles[0].ssid, "Office") == 0 &&
               record.profiles[0].success_count == 1,
           "success updates MRU and counter");

    for (std::size_t index = 0; index < kMaxWifiProfiles + 2; ++index) {
        char ssid[16] = {};
        std::snprintf(ssid, sizeof(ssid), "Extra-%u",
                      static_cast<unsigned>(index));
        Expect(UpsertWifiProfile(&record, ssid, "password"),
               "bounded profile insert");
    }
    Expect(record.count == kMaxWifiProfiles, "profile store stays bounded");
    Expect(FindWifiProfile(record, "Extra-6") != nullptr,
           "newest profile retained");
    Expect(FindWifiProfile(record, "Home") == nullptr,
           "least-recent profile evicted");

    WifiProfileRecord corrupt = record;
    corrupt.profiles[0].ssid[0] = 'X';
    Expect(!IsValidWifiProfileRecord(corrupt), "CRC rejects corrupt record");

    WifiProfileRecord duplicate{};
    Expect(UpsertWifiProfile(&duplicate, "Same", "one"), "first duplicate seed");
    duplicate.count = 2;
    duplicate.profiles[1] = duplicate.profiles[0];
    SealWifiProfileRecord(&duplicate);
    Expect(!IsValidWifiProfileRecord(duplicate), "duplicate SSIDs rejected");

    std::cout << "wifi profile record tests passed\n";
    return 0;
}
