#include <cassert>
#include <iostream>

#include "audio/wake_detector_policy.h"

namespace {

using veetee::app::State;
using veetee::audio::DetectorRole;
using veetee::audio::DetectorRoleForState;

void TestActivationOnlyRunsAtTheAssistantBoundary() {
    assert(DetectorRoleForState(State::kIdle, true, false) ==
           DetectorRole::kActivation);
    assert(DetectorRoleForState(State::kClosing, true, false) ==
           DetectorRole::kActivation);
    assert(DetectorRoleForState(State::kListening, true, false) ==
           DetectorRole::kDisabled);
    assert(DetectorRoleForState(State::kSpeaking, true, false) ==
           DetectorRole::kDisabled);
}

void TestInterruptRequiresItsOwnValidatedProfile() {
    assert(DetectorRoleForState(State::kEvaluating, true, true) ==
           DetectorRole::kInterrupt);
    assert(DetectorRoleForState(State::kThinking, true, true) ==
           DetectorRole::kInterrupt);
    assert(DetectorRoleForState(State::kSpeaking, true, true) ==
           DetectorRole::kInterrupt);
    assert(DetectorRoleForState(State::kSpeaking, true, false) ==
           DetectorRole::kDisabled);
}

void TestProvisioningAndCancellationNeverRunTheDetector() {
    assert(DetectorRoleForState(State::kWifiConfiguring, true, true) ==
           DetectorRole::kDisabled);
    assert(DetectorRoleForState(State::kActivating, true, true) ==
           DetectorRole::kDisabled);
    assert(DetectorRoleForState(State::kConnecting, true, true) ==
           DetectorRole::kDisabled);
    assert(DetectorRoleForState(State::kAborting, true, true) ==
           DetectorRole::kDisabled);
}

}  // namespace

int main() {
    TestActivationOnlyRunsAtTheAssistantBoundary();
    TestInterruptRequiresItsOwnValidatedProfile();
    TestProvisioningAndCancellationNeverRunTheDetector();
    std::cout << "wake_detector_policy_test: passed\n";
    return 0;
}
