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

void ReachIdle(StateMachine& machine) {
    Expect(machine, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(machine, Event::kWifiConnected, State::kActivating);
    Expect(machine, Event::kActivationComplete, State::kIdle);
}

void TestBootAndProvisioningFlow() {
    StateMachine first_boot;
    Expect(first_boot, Event::kBootNeedsProvisioning, State::kWifiConfiguring);
    Expect(first_boot, Event::kRetryWifiProvisioning, State::kWifiConfiguring);
    Expect(first_boot, Event::kProvisioningSaved, State::kNetworkConnecting);
    Expect(first_boot, Event::kWifiConnected, State::kActivating);
    Expect(first_boot, Event::kActivationCodeAvailable, State::kActivating);
    Expect(first_boot, Event::kActivationComplete, State::kIdle);

    StateMachine timeout;
    Expect(timeout, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(timeout, Event::kWifiConnectionTimeout, State::kWifiConfiguring);
}

void TestAutoConversationDoesNotNeedSecondButtonPress() {
    StateMachine machine;
    ReachIdle(machine);
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
    ReachIdle(button_machine);
    ReachIdle(wake_machine);
    Expect(button_machine, Event::kButtonShortPress, State::kConnecting);
    Expect(wake_machine, Event::kActivationWakeDetected, State::kConnecting);
    Expect(button_machine, Event::kTransportConnected, State::kListening);
    Expect(wake_machine, Event::kTransportConnected, State::kListening);
}

void TestAbortInvalidatesTheCurrentGeneration() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionAccepted, State::kThinking);
    const std::uint32_t generation = machine.cancellation_generation();
    Expect(machine, Event::kButtonShortPress, State::kAborting);
    assert(machine.cancellation_generation() == generation + 1);
    Expect(machine, Event::kAbortComplete, State::kListening);
}

void TestButtonCanCancelPendingAsrWhileFirmwareStillListens() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    const std::uint32_t generation = machine.cancellation_generation();
    Expect(machine, Event::kButtonShortPress, State::kAborting);
    assert(machine.cancellation_generation() == generation + 1);
    Expect(machine, Event::kAbortComplete, State::kListening);
}

void TestAdmissionRejectReturnsToListening() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionRejected, State::kListening);
}

void TestLongPressClosesTheAssistantGate() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kButtonLongPress, State::kIdle);
    assert(!machine.assistant_gate_open());
}

void TestWakeCancelsClosingGrace() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kInactivityTimeout, State::kClosing);
    Expect(machine, Event::kActivationWakeDetected, State::kAborting);
    Expect(machine, Event::kAbortComplete, State::kListening);
}

void TestAssistantSleepWaitsForGoodbyePlaybackDrain() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kTtsStarted, State::kSpeaking);
    Expect(machine, Event::kAssistantSleepRequested, State::kClosing);
    Expect(machine, Event::kTtsStopped, State::kIdle);
    assert(!machine.assistant_gate_open());
}

}  // namespace

int main() {
    TestBootAndProvisioningFlow();
    TestAutoConversationDoesNotNeedSecondButtonPress();
    TestWakeAndButtonShareTheSamePath();
    TestAbortInvalidatesTheCurrentGeneration();
    TestButtonCanCancelPendingAsrWhileFirmwareStillListens();
    TestAdmissionRejectReturnsToListening();
    TestLongPressClosesTheAssistantGate();
    TestWakeCancelsClosingGrace();
    TestAssistantSleepWaitsForGoodbyePlaybackDrain();
    std::cout << "state_machine_test: passed\n";
    return 0;
}
