#include <cassert>
#include <cstdio>
#include <cstring>

#include "telemetry/reported_state_outbox.h"

namespace {

veetee::settings::ReportedResourceState MakeState(
    veetee::settings::ReportedResourcePhase phase, const char* desired,
    const char* error = "") {
    veetee::settings::ReportedResourceState state{};
    state.phase = phase;
    state.artifact_kind = veetee::settings::ReportedArtifactKind::kWakeResource;
    state.active_slot = 0;
    state.target_slot = 1;
    state.expected_bytes = 4096;
    state.downloaded_bytes = 1024;
    state.security_epoch = 1;
    std::snprintf(state.current_version, sizeof(state.current_version), "%s",
                  "factory-bringup");
    std::snprintf(state.desired_version, sizeof(state.desired_version), "%s",
                  desired);
    std::snprintf(state.error_code, sizeof(state.error_code), "%s", error);
    return state;
}

void TestLatestCoalescingAndTerminalPriority() {
    veetee::telemetry::ReportedStateOutbox outbox;
    assert(outbox.Push(MakeState(
        veetee::settings::ReportedResourcePhase::kChecking, "1.0.0")));
    assert(outbox.Push(MakeState(
        veetee::settings::ReportedResourcePhase::kDownloading, "1.0.1")));
    assert(outbox.Push(MakeState(
        veetee::settings::ReportedResourcePhase::kFailed, "1.0.0",
        "transport_failed")));

    veetee::settings::ReportedResourceState state{};
    bool terminal = false;
    assert(outbox.Pop(&state, &terminal));
    assert(terminal);
    assert(state.phase == veetee::settings::ReportedResourcePhase::kFailed);
    assert(outbox.Pop(&state, &terminal));
    assert(!terminal);
    assert(state.phase ==
           veetee::settings::ReportedResourcePhase::kDownloading);
    assert(std::strcmp(state.desired_version, "1.0.1") == 0);
    assert(!outbox.Pop(&state, &terminal));
}

void TestTerminalFifoAndBound() {
    veetee::telemetry::ReportedStateOutbox outbox;
    for (std::size_t index = 0;
         index < veetee::telemetry::ReportedStateOutbox::kTerminalCapacity;
         ++index) {
        assert(outbox.Push(MakeState(
            veetee::settings::ReportedResourcePhase::kActive, "1.0.0")));
    }
    assert(!outbox.Push(MakeState(
        veetee::settings::ReportedResourcePhase::kRolledBack, "1.0.1",
        "health_failed")));
}

}  // namespace

int main() {
    TestLatestCoalescingAndTerminalPriority();
    TestTerminalFifoAndBound();
    return 0;
}
