#include <cassert>
#include <iostream>

#include "transport/websocket_task_policy.h"

namespace {

void TestIoTaskPreflightUsesTheContiguousBlockBoundary() {
    using namespace veetee::transport;
    assert(!CanAllocateWebSocketIoTask(0));
    assert(!CanAllocateWebSocketIoTask(kWebSocketIoTaskStackBytes - 1));
    assert(CanAllocateWebSocketIoTask(kWebSocketIoTaskStackBytes));
    assert(CanAllocateWebSocketIoTask(kWebSocketIoTaskStackBytes + 1));
}

void TestObservedFragmentationRejectsOpenEvenWhenTotalHeapIsLarger() {
    using namespace veetee::transport;
    constexpr std::size_t kObservedInternalFreeBytes = 11'975;
    constexpr std::size_t kObservedLargestInternalBlockBytes = 3'584;
    static_assert(kObservedInternalFreeBytes > kWebSocketIoTaskStackBytes);
    assert(!CanAllocateWebSocketIoTask(kObservedLargestInternalBlockBytes));
}

void TestReservePrefersSixteenKiBAndFallsBackToTheIoTaskMinimum() {
    using namespace veetee::transport;
    static_assert(kWebSocketIoReserveMinimumBytes ==
                  kWebSocketIoTaskStackBytes);
    static_assert(kWebSocketIoReserveDesiredBytes == 16 * 1024);
    assert(WebSocketIoReserveBytesForLargestBlock(
               kWebSocketIoReserveMinimumBytes - 1) == 0);
    assert(WebSocketIoReserveBytesForLargestBlock(
               kWebSocketIoReserveMinimumBytes) ==
           kWebSocketIoReserveMinimumBytes);
    assert(WebSocketIoReserveBytesForLargestBlock(
               kWebSocketIoReserveDesiredBytes - 1) ==
           kWebSocketIoReserveMinimumBytes);
    assert(WebSocketIoReserveBytesForLargestBlock(
               kWebSocketIoReserveDesiredBytes) ==
           kWebSocketIoReserveDesiredBytes);
    assert(WebSocketIoReserveBytesForLargestBlock(
               kWebSocketIoReserveDesiredBytes + 4096) ==
           kWebSocketIoReserveDesiredBytes);
}

void TestRepeatedOpenClosePreflightDoesNotLatchAnAllocationFailure() {
    using namespace veetee::transport;
    constexpr std::size_t kLargestBlocksByCycle[] = {
        kWebSocketIoTaskStackBytes + 4096,
        kWebSocketIoTaskStackBytes,
        kWebSocketIoTaskStackBytes - 1,
        kWebSocketIoTaskStackBytes + 2048,
    };
    constexpr bool kExpectedAdmissionByCycle[] = {true, true, false, true};

    for (std::size_t cycle = 0;
         cycle < sizeof(kLargestBlocksByCycle) /
                     sizeof(kLargestBlocksByCycle[0]);
         ++cycle) {
        assert(CanAllocateWebSocketIoTask(kLargestBlocksByCycle[cycle]) ==
               kExpectedAdmissionByCycle[cycle]);
    }
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
    TestIoTaskPreflightUsesTheContiguousBlockBoundary();
    TestObservedFragmentationRejectsOpenEvenWhenTotalHeapIsLarger();
    TestReservePrefersSixteenKiBAndFallsBackToTheIoTaskMinimum();
    TestRepeatedOpenClosePreflightDoesNotLatchAnAllocationFailure();
    TestReconnectPolicyIsBoundedAndJittered();
    TestWakeOpeningRetryKeepsOnlyAnUntouchedMatchingSnapshot();
    TestCriticalControlCannotFallBackBehindMcpBacklog();
    std::cout << "websocket_task_policy_test: passed\n";
    return 0;
}
