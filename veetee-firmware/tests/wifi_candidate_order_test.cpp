#include <cstdio>
#include <cstdlib>
#include <iostream>

#include "network/wifi_candidate_order.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

veetee::network::VisibleWifiNetwork Visible(const char* ssid, int rssi) {
    veetee::network::VisibleWifiNetwork result{};
    std::snprintf(result.ssid, sizeof(result.ssid), "%s", ssid);
    result.rssi = static_cast<std::int8_t>(rssi);
    return result;
}

}  // namespace

int main() {
    using veetee::network::BuildWifiCandidateOrder;
    using veetee::network::VisibleWifiNetwork;
    using veetee::settings::UpsertWifiProfile;
    using veetee::settings::WifiProfileRecord;

    WifiProfileRecord profiles{};
    UpsertWifiProfile(&profiles, "Hidden", "hidden-pass");
    UpsertWifiProfile(&profiles, "Cafe", "cafe-pass");
    UpsertWifiProfile(&profiles, "Home", "home-pass");
    // MRU order is Home, Cafe, Hidden.

    const VisibleWifiNetwork strong_alternative[] = {
        Visible("Home", -75),
        Visible("Cafe", -55),
    };
    auto order = BuildWifiCandidateOrder(profiles, strong_alternative, 2);
    Expect(order.count == 3, "hidden profile remains a fallback candidate");
    Expect(order.profile_indices[0] == 1,
           "much stronger visible network beats MRU bonus");
    Expect(order.profile_indices[1] == 0,
           "last-success candidate remains ahead of hidden profile");
    Expect(order.profile_indices[2] == 2,
           "not-visible profile is tried last");

    const VisibleWifiNetwork close_signals[] = {
        Visible("Home", -60),
        Visible("Cafe", -55),
    };
    order = BuildWifiCandidateOrder(profiles, close_signals, 2);
    Expect(order.profile_indices[0] == 0,
           "MRU wins when signal strengths are close");

    const VisibleWifiNetwork duplicate_ap[] = {
        Visible("Cafe", -80),
        Visible("Cafe", -52),
        Visible("Home", -70),
    };
    order = BuildWifiCandidateOrder(profiles, duplicate_ap, 3);
    Expect(order.profile_indices[0] == 1,
           "strongest BSSID contributes to profile order");

    order = BuildWifiCandidateOrder(profiles, nullptr, 0);
    Expect(order.profile_indices[0] == 0 && order.profile_indices[1] == 1 &&
               order.profile_indices[2] == 2,
           "scan failure falls back to MRU order");

    std::cout << "wifi candidate order tests passed\n";
    return 0;
}
