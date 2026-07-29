#include <cassert>
#include <iostream>

#include "transport/websocket_task_policy.h"

namespace {

void TestObservedFragmentedHeapCanCreateIoTask() {
    assert(veetee::transport::CanAllocateWebSocketIoTask(11'776));
}

void TestInsufficientContiguousHeapIsRejected() {
    assert(!veetee::transport::CanAllocateWebSocketIoTask(
        veetee::transport::kWebSocketIoTaskStackBytes - 1));
}

void TestReconnectPolicyIsBoundedAndJittered() {
    using namespace veetee::transport;
    assert(CanRetryWebSocket(true, false, 0));
    assert(CanRetryWebSocket(true, false, 2));
    assert(!CanRetryWebSocket(true, false, 3));
    assert(!CanRetryWebSocket(false, false, 0));
    assert(!CanRetryWebSocket(true, true, 0));
    assert(WebSocketReconnectDelayMs(0, 0) == 250);
    assert(WebSocketReconnectDelayMs(1, 125) == 625);
    assert(WebSocketReconnectDelayMs(7, 999) <= 2'000);
}

void TestWakeOpeningRetryKeepsOnlyAnUntouchedMatchingSnapshot() {
    using namespace veetee::transport;
    WakeOpeningSnapshot snapshot;
    assert(snapshot.phase() == WakeOpeningPhase::kIdle);

    // Models client_init/client_start failure: source is already bound to the
    // generation, but no detect/binary/start frame has been attempted yet.
    snapshot.Begin(7, WakeSource::kWakeWord);
    assert(snapshot.Matches(7, WakeSource::kWakeWord));
    assert(snapshot.PreserveAudioForRetry(true, 7));
    assert(!snapshot.PreserveAudioForRetry(true, 6));
    assert(!snapshot.PreserveAudioForRetry(false, 7));

    // Once any opening frame may have been sent, retry must never replay the
    // partially consumed ring.
    assert(snapshot.MarkStarted(7));
    assert(snapshot.phase() == WakeOpeningPhase::kStarted);
    assert(!snapshot.PreserveAudioForRetry(true, 7));
    assert(!snapshot.MarkStarted(8));

    // A fresh Open atomically replaces source/progress for the new generation.
    snapshot.Begin(8, WakeSource::kButton);
    assert(snapshot.Matches(8, WakeSource::kButton));
    assert(!snapshot.Matches(8, WakeSource::kWakeWord));
    assert(!snapshot.PreserveAudioForRetry(true, 8));
}

void TestCriticalControlCannotFallBackBehindMcpBacklog() {
    using namespace veetee::transport;
    assert(!CanFallbackToRegularQueue(
        WebSocketCommandPriority::kCriticalControl));
    assert(ShouldReplaceOldestUrgentCommand(
        WebSocketCommandPriority::kCriticalControl));
    assert(CanFallbackToRegularQueue(WebSocketCommandPriority::kUrgent));
    assert(!ShouldReplaceOldestUrgentCommand(
        WebSocketCommandPriority::kUrgent));
}

}  // namespace

int main() {
    TestObservedFragmentedHeapCanCreateIoTask();
    TestInsufficientContiguousHeapIsRejected();
    TestReconnectPolicyIsBoundedAndJittered();
    TestWakeOpeningRetryKeepsOnlyAnUntouchedMatchingSnapshot();
    TestCriticalControlCannotFallBackBehindMcpBacklog();
    std::cout << "websocket_task_policy_test: passed\n";
    return 0;
}
