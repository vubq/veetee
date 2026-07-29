#include <array>
#include <cassert>
#include <iostream>

#include "ota/firmware_boot_health_policy.h"

namespace {

using veetee::ota::EvaluateFirmwareBootHealth;
using veetee::ota::FirmwareBootHealthDecision;
using veetee::ota::FirmwareBootHealthDeadlineExpired;
using veetee::ota::FirmwareBootHealthSnapshot;
using veetee::ota::FirmwareHealthPollFailureRequiresRollback;

FirmwareBootHealthSnapshot HealthySnapshot() {
    FirmwareBootHealthSnapshot snapshot{};
    snapshot.pending_verify = true;
    snapshot.identity_valid = true;
    snapshot.authenticated_bootstrap_complete = true;
    snapshot.app_idle = true;
    snapshot.capture_task_running = true;
    snapshot.playback_task_running = true;
    snapshot.wake_resource_healthy = true;
    snapshot.ui_pack_healthy = true;
    snapshot.wake_task_required = true;
    snapshot.wake_task_running = true;
    return snapshot;
}

void TestHealthyPendingImageIsConfirmed() {
    assert(EvaluateFirmwareBootHealth(HealthySnapshot()) ==
           FirmwareBootHealthDecision::kConfirm);
}

void TestEachRequiredGateWaitsThenRollsBack() {
    using Gate = bool FirmwareBootHealthSnapshot::*;
    constexpr std::array<Gate, 8> gates = {
        &FirmwareBootHealthSnapshot::identity_valid,
        &FirmwareBootHealthSnapshot::authenticated_bootstrap_complete,
        &FirmwareBootHealthSnapshot::app_idle,
        &FirmwareBootHealthSnapshot::capture_task_running,
        &FirmwareBootHealthSnapshot::playback_task_running,
        &FirmwareBootHealthSnapshot::wake_resource_healthy,
        &FirmwareBootHealthSnapshot::ui_pack_healthy,
        &FirmwareBootHealthSnapshot::wake_task_running,
    };
    for (const Gate gate : gates) {
        auto snapshot = HealthySnapshot();
        snapshot.*gate = false;
        assert(EvaluateFirmwareBootHealth(snapshot) ==
               FirmwareBootHealthDecision::kWait);
        snapshot.deadline_expired = true;
        assert(EvaluateFirmwareBootHealth(snapshot) ==
               FirmwareBootHealthDecision::kRollback);
    }
}

void TestWakeTaskCanBeOptional() {
    auto snapshot = HealthySnapshot();
    snapshot.wake_task_required = false;
    snapshot.wake_task_running = false;
    assert(EvaluateFirmwareBootHealth(snapshot) ==
           FirmwareBootHealthDecision::kConfirm);
}

void TestNonPendingImageIsNeverMutatedByThePolicy() {
    auto snapshot = HealthySnapshot();
    snapshot.pending_verify = false;
    assert(EvaluateFirmwareBootHealth(snapshot) ==
           FirmwareBootHealthDecision::kWait);
    snapshot.deadline_expired = true;
    assert(EvaluateFirmwareBootHealth(snapshot) ==
           FirmwareBootHealthDecision::kWait);
}

void TestHealthyImageAtDeadlineStillConfirms() {
    auto snapshot = HealthySnapshot();
    snapshot.deadline_expired = true;
    assert(EvaluateFirmwareBootHealth(snapshot) ==
           FirmwareBootHealthDecision::kConfirm);
}

void TestOverallDeadlineBoundsBootBeforeWifi() {
    assert(!FirmwareBootHealthDeadlineExpired(89999999, 90000000, 0));
    assert(FirmwareBootHealthDeadlineExpired(90000000, 90000000, 0));
}

void TestPostWifiDeadlineStartsOnlyAfterItIsArmed() {
    assert(!FirmwareBootHealthDeadlineExpired(30000000, 90000000, 0));
    assert(!FirmwareBootHealthDeadlineExpired(59999999, 90000000, 60000000));
    assert(FirmwareBootHealthDeadlineExpired(60000000, 90000000, 60000000));
}

void TestEitherDeadlineExpiresTheHealthWindow() {
    assert(FirmwareBootHealthDeadlineExpired(70000000, 90000000, 60000000));
    assert(FirmwareBootHealthDeadlineExpired(90000000, 90000000, 120000000));
    assert(!FirmwareBootHealthDeadlineExpired(-1, 1, 1));
}

void TestRepeatedHealthTimerFailureRequestsRollback() {
    assert(!FirmwareHealthPollFailureRequiresRollback(true, 3, 3));
    assert(!FirmwareHealthPollFailureRequiresRollback(false, 2, 3));
    assert(FirmwareHealthPollFailureRequiresRollback(false, 3, 3));
    assert(FirmwareHealthPollFailureRequiresRollback(false, 4, 3));
    assert(!FirmwareHealthPollFailureRequiresRollback(false, 255, 0));
}

}  // namespace

int main() {
    TestHealthyPendingImageIsConfirmed();
    TestEachRequiredGateWaitsThenRollsBack();
    TestWakeTaskCanBeOptional();
    TestNonPendingImageIsNeverMutatedByThePolicy();
    TestHealthyImageAtDeadlineStillConfirms();
    TestOverallDeadlineBoundsBootBeforeWifi();
    TestPostWifiDeadlineStartsOnlyAfterItIsArmed();
    TestEitherDeadlineExpiresTheHealthWindow();
    TestRepeatedHealthTimerFailureRequestsRollback();
    std::cout << "firmware_boot_health_policy_test: passed\n";
    return 0;
}
