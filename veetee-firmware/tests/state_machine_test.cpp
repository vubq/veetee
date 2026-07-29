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
    const auto boot = first_boot.Handle(Event::kBootNeedsProvisioning);
    assert(boot.accepted);
    assert(boot.to == State::kWifiConfiguring);
    assert(!boot.network_lost);
    Expect(first_boot, Event::kRetryWifiProvisioning, State::kWifiConfiguring);
    Expect(first_boot, Event::kProvisioningSaved, State::kNetworkConnecting);
    Expect(first_boot, Event::kWifiConnected, State::kActivating);
    Expect(first_boot, Event::kActivationCodeAvailable, State::kActivating);
    Expect(first_boot, Event::kActivationComplete, State::kIdle);

    StateMachine timeout;
    Expect(timeout, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(timeout, Event::kWifiConnectionTimeout, State::kWifiConfiguring);
}

void TestWifiLossMarksTransportForAbortiveClose() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    const auto result = machine.Handle(Event::kWifiDisconnected);
    assert(result.accepted);
    assert(result.to == State::kNetworkConnecting);
    assert(result.network_lost);
}

void TestSessionLossInvalidatesQueuedSessionWork() {
    StateMachine connecting;
    ReachIdle(connecting);
    Expect(connecting, Event::kButtonShortPress, State::kConnecting);
    const std::uint32_t connecting_generation =
        connecting.cancellation_generation();
    Expect(connecting, Event::kTransportLost, State::kIdle);
    assert(connecting.cancellation_generation() ==
           connecting_generation + 1);

    StateMachine listening;
    ReachIdle(listening);
    Expect(listening, Event::kButtonShortPress, State::kConnecting);
    Expect(listening, Event::kTransportConnected, State::kListening);
    const std::uint32_t listening_generation =
        listening.cancellation_generation();
    Expect(listening, Event::kWifiDisconnected,
           State::kNetworkConnecting);
    assert(listening.cancellation_generation() ==
           listening_generation + 1);
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

void TestTurnFailureReturnsToListeningAndInvalidatesOutput() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionAccepted, State::kThinking);
    const std::uint32_t generation = machine.cancellation_generation();
    Expect(machine, Event::kTurnFailed, State::kListening);
    assert(machine.assistant_gate_open());
    assert(machine.cancellation_generation() == generation + 1);
}

void TestReconnectKeepsTheGateOpenAndInvalidatesActiveOutput() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kVadFinal, State::kEvaluating);
    Expect(machine, Event::kAdmissionAccepted, State::kThinking);
    Expect(machine, Event::kTtsStarted, State::kSpeaking);
    const std::uint32_t generation = machine.cancellation_generation();
    Expect(machine, Event::kTransportReconnectScheduled, State::kConnecting);
    assert(machine.assistant_gate_open());
    assert(machine.cancellation_generation() == generation + 1);
    Expect(machine, Event::kTransportConnected, State::kListening);
}

void TestReconnectIsRejectedAfterClosingStarts() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kTransportConnected, State::kListening);
    Expect(machine, Event::kInactivityTimeout, State::kClosing);
    assert(!machine.Handle(Event::kTransportReconnectScheduled).accepted);
}

void TestButtonCancelsAConnectingSession() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kButtonShortPress, State::kConnecting);
    Expect(machine, Event::kButtonShortPress, State::kIdle);
    assert(!machine.assistant_gate_open());
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

void TestRejectedIdentityRequiresPhysicalRecovery() {
    StateMachine machine;
    Expect(machine, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(machine, Event::kWifiConnected, State::kActivating);
    Expect(machine, Event::kDeviceIdentityRejected, State::kPairingRecovery);
    assert(!machine.Handle(Event::kButtonShortPress).accepted);
    Expect(machine, Event::kEnterWifiConfig, State::kWifiConfiguring);
}

void TestFirmwareUpdateOwnsTheActivationBoundary() {
    StateMachine machine;
    Expect(machine, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(machine, Event::kWifiConnected, State::kActivating);
    Expect(machine, Event::kFirmwareUpdateRequested, State::kUpgrading);
    assert(!machine.assistant_gate_open());
    assert(!machine.Handle(Event::kActivationComplete).accepted);
    assert(!machine.Handle(Event::kButtonShortPress).accepted);
    assert(!machine.Handle(Event::kButtonLongPress).accepted);
    assert(!machine.Handle(Event::kActivationWakeDetected).accepted);
    assert(!machine.Handle(Event::kTransportLost).accepted);
    assert(machine.state() == State::kUpgrading);
}

void TestFirmwareUpdateRecoveryTransitions() {
    StateMachine already_current;
    Expect(already_current, Event::kBootWithCredentials,
           State::kNetworkConnecting);
    Expect(already_current, Event::kWifiConnected, State::kActivating);
    Expect(already_current, Event::kFirmwareUpdateRequested, State::kUpgrading);
    Expect(already_current, Event::kFirmwareAlreadyCurrent, State::kIdle);

    StateMachine failed;
    Expect(failed, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(failed, Event::kWifiConnected, State::kActivating);
    Expect(failed, Event::kFirmwareUpdateRequested, State::kUpgrading);
    Expect(failed, Event::kFirmwareUpdateFailed, State::kActivating);

    StateMachine wifi_lost;
    Expect(wifi_lost, Event::kBootWithCredentials, State::kNetworkConnecting);
    Expect(wifi_lost, Event::kWifiConnected, State::kActivating);
    Expect(wifi_lost, Event::kFirmwareUpdateRequested, State::kUpgrading);
    const auto result = wifi_lost.Handle(Event::kWifiDisconnected);
    assert(result.accepted);
    assert(result.to == State::kNetworkConnecting);
    assert(result.network_lost);
}

void TestIdleCanEnterFirmwareUpdateAtSafeBoundary() {
    StateMachine machine;
    ReachIdle(machine);
    Expect(machine, Event::kFirmwareUpdateRequested, State::kUpgrading);
}

}  // namespace

int main() {
    TestBootAndProvisioningFlow();
    TestWifiLossMarksTransportForAbortiveClose();
    TestSessionLossInvalidatesQueuedSessionWork();
    TestAutoConversationDoesNotNeedSecondButtonPress();
    TestWakeAndButtonShareTheSamePath();
    TestAbortInvalidatesTheCurrentGeneration();
    TestButtonCanCancelPendingAsrWhileFirmwareStillListens();
    TestAdmissionRejectReturnsToListening();
    TestTurnFailureReturnsToListeningAndInvalidatesOutput();
    TestReconnectKeepsTheGateOpenAndInvalidatesActiveOutput();
    TestReconnectIsRejectedAfterClosingStarts();
    TestButtonCancelsAConnectingSession();
    TestLongPressClosesTheAssistantGate();
    TestWakeCancelsClosingGrace();
    TestAssistantSleepWaitsForGoodbyePlaybackDrain();
    TestRejectedIdentityRequiresPhysicalRecovery();
    TestFirmwareUpdateOwnsTheActivationBoundary();
    TestFirmwareUpdateRecoveryTransitions();
    TestIdleCanEnterFirmwareUpdateAtSafeBoundary();
    std::cout << "state_machine_test: passed\n";
    return 0;
}
