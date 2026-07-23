#pragma once

#include <cstdint>

namespace veetee::app {

enum class State : std::uint8_t {
    kStarting,
    kWifiConfiguring,
    kNetworkConnecting,
    kActivating,
    kPairingRecovery,
    kIdle,
    kConnecting,
    kListening,
    kEvaluating,
    kThinking,
    kSpeaking,
    kAborting,
    kClosing,
};

enum class Event : std::uint8_t {
    kBootNeedsProvisioning,
    kBootWithCredentials,
    kEnterWifiConfig,
    kRetryWifiProvisioning,
    kProvisioningSaved,
    kWifiConnected,
    kWifiConnectionTimeout,
    kWifiDisconnected,
    kActivationCodeAvailable,
    kActivationComplete,
    kDeviceIdentityRejected,
    kButtonShortPress,
    kButtonLongPress,
    kActivationWakeDetected,
    kInterruptDetected,
    kTransportConnected,
    kTransportLost,
    kVadFinal,
    kAdmissionAccepted,
    kAdmissionRejected,
    kTtsStarted,
    kTtsStopped,
    kAssistantSleepRequested,
    kInactivityTimeout,
    kGoodbyeComplete,
    kAbortComplete,
};

struct TransitionResult {
    bool accepted;
    State from;
    State to;
    bool assistant_gate_open;
    std::uint32_t cancellation_generation;
    bool network_lost;
};

class StateMachine {
public:
    TransitionResult Handle(Event event);

    [[nodiscard]] State state() const { return state_; }
    [[nodiscard]] bool assistant_gate_open() const { return assistant_gate_open_; }
    [[nodiscard]] std::uint32_t cancellation_generation() const {
        return cancellation_generation_;
    }

private:
    TransitionResult Result(bool accepted, State from, Event event) const;
    void BeginAbort();

    State state_ = State::kStarting;
    bool assistant_gate_open_ = false;
    std::uint32_t cancellation_generation_ = 0;
};

const char* ToString(State state);
const char* ToString(Event event);

}  // namespace veetee::app
