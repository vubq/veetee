#include "audio/wake_detector_policy.h"

namespace veetee::audio {

DetectorRole DetectorRoleForState(app::State state,
                                  bool activation_available,
                                  bool interrupt_available) {
    switch (state) {
        case app::State::kIdle:
        case app::State::kClosing:
            return activation_available ? DetectorRole::kActivation
                                        : DetectorRole::kDisabled;
        case app::State::kEvaluating:
        case app::State::kThinking:
        case app::State::kSpeaking:
            return interrupt_available ? DetectorRole::kInterrupt
                                       : DetectorRole::kDisabled;
        case app::State::kStarting:
        case app::State::kWifiConfiguring:
        case app::State::kNetworkConnecting:
        case app::State::kActivating:
        case app::State::kPairingRecovery:
        case app::State::kConnecting:
        case app::State::kListening:
        case app::State::kAborting:
            return DetectorRole::kDisabled;
    }
    return DetectorRole::kDisabled;
}

const char* ToString(DetectorRole role) {
    switch (role) {
        case DetectorRole::kDisabled: return "disabled";
        case DetectorRole::kActivation: return "activation";
        case DetectorRole::kInterrupt: return "interrupt";
    }
    return "unknown";
}

}  // namespace veetee::audio
