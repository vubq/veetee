#include <cstdlib>
#include <iostream>

#include "network/wifi_radio_policy.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::network::RadioPolicyFor;
    using veetee::network::WifiRadioMode;

    const auto station = RadioPolicyFor(WifiRadioMode::kRealtimeStation);
    Expect(!station.power_save_enabled,
           "realtime station keeps beacon and audio transport awake");
    Expect(station.listen_interval == 1,
           "realtime station listens to every beacon interval");

    const auto portal = RadioPolicyFor(WifiRadioMode::kProvisioningPortal);
    Expect(!portal.power_save_enabled,
           "provisioning portal remains responsive while configuring");

    std::cout << "wifi radio policy tests passed\n";
    return 0;
}
