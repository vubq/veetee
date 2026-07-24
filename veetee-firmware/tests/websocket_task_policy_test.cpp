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

}  // namespace

int main() {
    TestObservedFragmentedHeapCanCreateIoTask();
    TestInsufficientContiguousHeapIsRejected();
    TestReconnectPolicyIsBoundedAndJittered();
    std::cout << "websocket_task_policy_test: passed\n";
    return 0;
}
