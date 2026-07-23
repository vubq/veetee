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

}  // namespace

int main() {
    TestObservedFragmentedHeapCanCreateIoTask();
    TestInsufficientContiguousHeapIsRejected();
    std::cout << "websocket_task_policy_test: passed\n";
    return 0;
}
