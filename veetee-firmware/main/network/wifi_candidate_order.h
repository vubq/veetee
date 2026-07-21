#pragma once

#include <cstddef>
#include <cstdint>

#include "settings/wifi_profile_record.h"

namespace veetee::network {

struct VisibleWifiNetwork {
    char ssid[33] = {};
    std::int8_t rssi = -127;
};

struct WifiCandidateOrder {
    std::size_t profile_indices[settings::kMaxWifiProfiles] = {};
    std::size_t count = 0;
};

WifiCandidateOrder BuildWifiCandidateOrder(
    const settings::WifiProfileRecord& profiles,
    const VisibleWifiNetwork* visible_networks, std::size_t visible_count);

}  // namespace veetee::network
