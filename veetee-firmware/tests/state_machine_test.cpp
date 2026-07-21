#include <cassert>
#include <cstdint>
#include <iostream>

#include "app/state_machine.h"

namespace {

using veetee::app::Event;
using veetee::app::State;
using veetee::app::StateMachine;

void Expect(StateMachine& machine, Event event, State expected) {
    const auto result = machine.Handle(event);
    assert(result.accepted);
    assert(result.to == expected);
}

void TestAutoConversationDoesNotNeedSecondButtonPress() {
    StateMachine machine;
    Expect(machine, Event::kBootCompleted, State::kIdle);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionAccepted, State::kThinking);
    Expect(machine, Event::kTtsStarted, State::kSpeaking);
    Expect(machine, Event::kTtsStopped, State::kListening);
    assert(machine.assistant_gate_open());
}

void TestWakeAndButtonShareTheSamePath() {
    StateMachine button_machine;
    StateMachine wake_machine;
    Expect(button_machine, Event::kBootCompleted, State::kIdle);
    Expect(wake_machine, Event::kBootCompleted, State::kIdle);
    Expect(button_machine, Event::kButtonShortPress, State::kConnecting);
    Expect(wake_machine, Event::kActivationWakeDetected, State::kConnecting);
    Expect(button_machine, Event::kTransportConnected, State::kListening);
    Expect(wake_machine, Event::kTransportConnected, State::kListening);
}

void TestAbortInvalidatesTheCurrentGeneration() {
    StateMachine machine;
    Expect(machine, Event::kBootCompleted, State::kIdle);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionAccepted, State::kThinking);
    const std::uint32_t generation = machine.cancellation_generation();
    Expect(machine, Event::kButtonShortPress, State::kAborting);
    assert(machine.cancellation_generation() == generation + 1);
    Expect(machine, Event::kAbortComplete, State::kListening);
}

void TestAdmissionRejectReturnsToListening() {
    StateMachine machine;
    Expect(machine, Event::kBootCompleted, State::kIdle);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionRejected, State::kListening);
}

void TestLongPressClosesTheAssistantGate() {
    StateMachine machine;
    Expect(machine, Event::kBootCompleted, State::kIdle);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kButtonLongPress, State::kIdle);
    assert(!machine.assistant_gate_open());
}

void TestWakeCancelsClosingGrace() {
    StateMachine machine;
    Expect(machine, Event::kBootCompleted, State::kIdle);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kInactivityTimeout, State::kClosing);
    Expect(machine, Event::kActivationWakeDetected, State::kAborting);
    Expect(machine, Event::kAbortComplete, State::kListening);
}

}  // namespace

int main() {
    TestAutoConversationDoesNotNeedSecondButtonPress();
    TestWakeAndButtonShareTheSamePath();
    TestAbortInvalidatesTheCurrentGeneration();
    TestAdmissionRejectReturnsToListening();
    TestLongPressClosesTheAssistantGate();
    TestWakeCancelsClosingGrace();
    std::cout << "state_machine_test: passed\n";
    return 0;
}
