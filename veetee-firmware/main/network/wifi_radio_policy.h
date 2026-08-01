#pragma once

#include <cstdint>

namespace veetee::network {

enum class WifiRadioMode : std::uint8_t {
    kRealtimeStation,
    kProvisioningPortal,
};

struct WifiRadioPolicy {
    bool power_save_enabled = false;
    std::uint16_t listen_interval = 1;
};

constexpr WifiRadioPolicy RadioPolicyFor(WifiRadioMode mode) {
    switch (mode) {
        case WifiRadioMode::kRealtimeStation:
        case WifiRadioMode::kProvisioningPortal:
            return {};
    }
    return {};
}

}  // namespace veetee::network
