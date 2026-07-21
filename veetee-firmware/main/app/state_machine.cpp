#include "app/state_machine.h"

namespace veetee::app {

TransitionResult StateMachine::Handle(Event event) {
    const State from = state_;
    bool accepted = false;

    switch (event) {
        case Event::kBootCompleted:
            if (state_ == State::kStarting) {
                state_ = State::kIdle;
                accepted = true;
            }
            break;

        case Event::kEnterWifiConfig:
            if (state_ == State::kStarting || state_ == State::kIdle) {
                assistant_gate_open_ = false;
                state_ = State::kWifiConfiguring;
                accepted = true;
            }
            break;

        case Event::kWifiConfigured:
            if (state_ == State::kWifiConfiguring) {
                state_ = State::kActivating;
                accepted = true;
            }
            break;

        case Event::kActivationComplete:
            if (state_ == State::kActivating) {
                state_ = State::kIdle;
                accepted = true;
            }
            break;

        case Event::kButtonShortPress:
            if (state_ == State::kIdle) {
                assistant_gate_open_ = true;
                state_ = State::kConnecting;
                accepted = true;
            } else if (state_ == State::kEvaluating || state_ == State::kThinking ||
                       state_ == State::kSpeaking || state_ == State::kClosing) {
                BeginAbort();
                accepted = true;
            }
            break;

        case Event::kButtonLongPress:
            if (state_ == State::kConnecting || state_ == State::kEvaluating ||
                state_ == State::kThinking || state_ == State::kSpeaking ||
                state_ == State::kClosing) {
                assistant_gate_open_ = false;
                BeginAbort();
                accepted = true;
            } else if (state_ == State::kListening) {
                assistant_gate_open_ = false;
                state_ = State::kIdle;
                accepted = true;
            }
            break;

        case Event::kActivationWakeDetected:
            if (state_ == State::kIdle) {
                assistant_gate_open_ = true;
                state_ = State::kConnecting;
                accepted = true;
            } else if (state_ == State::kClosing) {
                assistant_gate_open_ = true;
                BeginAbort();
                accepted = true;
            }
            break;

        case Event::kInterruptDetected:
            if (state_ == State::kEvaluating || state_ == State::kThinking ||
                state_ == State::kSpeaking || state_ == State::kClosing) {
                BeginAbort();
                accepted = true;
            }
            break;

        case Event::kTransportConnected:
            if (state_ == State::kConnecting && assistant_gate_open_) {
                state_ = State::kListening;
                accepted = true;
            }
            break;

        case Event::kTransportLost:
            if (state_ != State::kStarting && state_ != State::kWifiConfiguring &&
                state_ != State::kActivating && state_ != State::kIdle) {
                if (state_ == State::kEvaluating || state_ == State::kThinking ||
                    state_ == State::kSpeaking || state_ == State::kClosing ||
                    state_ == State::kAborting) {
                    ++cancellation_generation_;
                }
                assistant_gate_open_ = false;
                state_ = State::kIdle;
                accepted = true;
            }
            break;

        case Event::kVadFinal:
            if (state_ == State::kListening) {
                state_ = State::kEvaluating;
                accepted = true;
            }
            break;

        case Event::kAdmissionAccepted:
            if (state_ == State::kEvaluating) {
                state_ = State::kThinking;
                accepted = true;
            }
            break;

        case Event::kAdmissionRejected:
            if (state_ == State::kEvaluating) {
                state_ = State::kListening;
                accepted = true;
            }
            break;

        case Event::kTtsStarted:
            if (state_ == State::kThinking) {
                state_ = State::kSpeaking;
                accepted = true;
            }
            break;

        case Event::kTtsStopped:
            if (state_ == State::kSpeaking) {
                state_ = assistant_gate_open_ ? State::kListening : State::kIdle;
                accepted = true;
            }
            break;

        case Event::kInactivityTimeout:
            if (state_ == State::kListening) {
                state_ = State::kClosing;
                accepted = true;
            }
            break;

        case Event::kGoodbyeComplete:
            if (state_ == State::kClosing) {
                assistant_gate_open_ = false;
                state_ = State::kIdle;
                accepted = true;
            }
            break;

        case Event::kAbortComplete:
            if (state_ == State::kAborting) {
                state_ = assistant_gate_open_ ? State::kListening : State::kIdle;
                accepted = true;
            }
            break;
    }

    return Result(accepted, from);
}

TransitionResult StateMachine::Result(bool accepted, State from) const {
    return TransitionResult{
        accepted,
        from,
        state_,
        assistant_gate_open_,
        cancellation_generation_,
    };
}

void StateMachine::BeginAbort() {
    ++cancellation_generation_;
    state_ = State::kAborting;
}

const char* ToString(State state) {
    switch (state) {
        case State::kStarting: return "starting";
        case State::kWifiConfiguring: return "wifi_configuring";
        case State::kActivating: return "activating";
        case State::kIdle: return "idle";
        case State::kConnecting: return "connecting";
        case State::kListening: return "listening";
        case State::kEvaluating: return "evaluating";
        case State::kThinking: return "thinking";
        case State::kSpeaking: return "speaking";
        case State::kAborting: return "aborting";
        case State::kClosing: return "closing";
    }
    return "unknown";
}

const char* ToString(Event event) {
    switch (event) {
        case Event::kBootCompleted: return "boot_completed";
        case Event::kEnterWifiConfig: return "enter_wifi_config";
        case Event::kWifiConfigured: return "wifi_configured";
        case Event::kActivationComplete: return "activation_complete";
        case Event::kButtonShortPress: return "button_short_press";
        case Event::kButtonLongPress: return "button_long_press";
        case Event::kActivationWakeDetected: return "activation_wake_detected";
        case Event::kInterruptDetected: return "interrupt_detected";
        case Event::kTransportConnected: return "transport_connected";
        case Event::kTransportLost: return "transport_lost";
        case Event::kVadFinal: return "vad_final";
        case Event::kAdmissionAccepted: return "admission_accepted";
        case Event::kAdmissionRejected: return "admission_rejected";
        case Event::kTtsStarted: return "tts_started";
        case Event::kTtsStopped: return "tts_stopped";
        case Event::kInactivityTimeout: return "inactivity_timeout";
        case Event::kGoodbyeComplete: return "goodbye_complete";
        case Event::kAbortComplete: return "abort_complete";
    }
    return "unknown";
}

}  // namespace veetee::app
