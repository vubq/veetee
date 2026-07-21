#include "network/wifi_candidate_order.h"

#include <algorithm>
#include <array>
#include <cstring>

namespace veetee::network {
namespace {

// A recent success may beat a slightly stronger alternative, while a network
// that is much stronger still wins. This avoids pinning the robot to weak Wi-Fi.
constexpr int kMruSignalBonus[] = {12, 6, 3, 1, 0};

struct Candidate {
    std::size_t profile_index = 0;
    bool visible = false;
    int score = -256;
};

int BestVisibleRssi(const char* ssid, const VisibleWifiNetwork* visible_networks,
                    std::size_t visible_count, bool* visible) {
    int best = -128;
    *visible = false;
    for (std::size_t index = 0; index < visible_count; ++index) {
        if (std::strcmp(ssid, visible_networks[index].ssid) == 0) {
            *visible = true;
            best = std::max(best, static_cast<int>(visible_networks[index].rssi));
        }
    }
    return best;
}

}  // namespace

WifiCandidateOrder BuildWifiCandidateOrder(
    const settings::WifiProfileRecord& profiles,
    const VisibleWifiNetwork* visible_networks, std::size_t visible_count) {
    WifiCandidateOrder result{};
    if (profiles.count == 0 ||
        (visible_networks == nullptr && visible_count != 0)) {
        return result;
    }

    std::array<Candidate, settings::kMaxWifiProfiles> candidates{};
    for (std::size_t index = 0; index < profiles.count; ++index) {
        bool visible = false;
        const int rssi = BestVisibleRssi(profiles.profiles[index].ssid,
                                         visible_networks, visible_count,
                                         &visible);
        candidates[index] = Candidate{
            .profile_index = index,
            .visible = visible,
            .score = visible ? rssi + kMruSignalBonus[index] : -256,
        };
    }
    std::stable_sort(candidates.begin(), candidates.begin() + profiles.count,
                     [](const Candidate& left, const Candidate& right) {
                         if (left.visible != right.visible) return left.visible;
                         if (left.score != right.score) return left.score > right.score;
                         return left.profile_index < right.profile_index;
                     });
    result.count = profiles.count;
    for (std::size_t index = 0; index < result.count; ++index) {
        result.profile_indices[index] = candidates[index].profile_index;
    }
    return result;
}

}  // namespace veetee::network
