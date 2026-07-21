#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "display/state_visual.h"

namespace {

void Expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::app::State;
    constexpr std::array<State, 13> states = {
        State::kStarting,        State::kWifiConfiguring,
        State::kNetworkConnecting, State::kActivating,
        State::kPairingRecovery, State::kIdle,
        State::kConnecting,      State::kListening,
        State::kEvaluating,      State::kThinking,
        State::kSpeaking,        State::kAborting,
        State::kClosing,
    };
    for (const State state : states) {
        const auto& visual = veetee::display::VisualForState(state);
        Expect(visual.label != nullptr && std::strlen(visual.label) > 0,
               "state label is required");
        Expect(visual.detail != nullptr && std::strlen(visual.detail) > 0,
               "state detail is required");
        Expect(visual.background != visual.foreground,
               "state foreground must contrast with background");
    }
    const auto& recovery =
        veetee::display::VisualForState(State::kPairingRecovery);
    Expect(std::strstr(recovery.detail, "5S") != nullptr,
           "pairing recovery must advertise the physical reset hold");
    std::cout << "state visual tests passed\n";
    return 0;
}
