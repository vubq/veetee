#include <cassert>
#include <iostream>

#include "app/application_queue_policy.h"

namespace {

void TestConversationControlsUseUrgentLane() {
    using veetee::app::Event;
    using veetee::app::ApplicationQueueForEvent;
    using veetee::app::ApplicationQueueLane;
    assert(ApplicationQueueForEvent(Event::kButtonShortPress) ==
           ApplicationQueueLane::kCriticalControl);
    assert(ApplicationQueueForEvent(Event::kButtonLongPress) ==
           ApplicationQueueLane::kCriticalControl);
    assert(ApplicationQueueForEvent(Event::kInterruptDetected) ==
           ApplicationQueueLane::kCriticalControl);
    assert(ApplicationQueueForEvent(Event::kActivationWakeDetected) ==
           ApplicationQueueLane::kWake);
    assert(ApplicationQueueForEvent(Event::kTransportConnected) ==
           ApplicationQueueLane::kRegular);
}

void TestUrgentControlWinsWhileRegularBacklogIsFull() {
    using namespace veetee::app;
    static_assert(kApplicationQueueDepth == 16);
    static_assert(kWakeApplicationQueueDepth == 2);
    static_assert(kCriticalApplicationQueueDepth == 4);
    static_assert(SelectApplicationQueue(true, true, true) ==
                  ApplicationQueueLane::kCriticalControl);
    static_assert(SelectApplicationQueue(false, true, true) ==
                  ApplicationQueueLane::kWake);
    static_assert(SelectApplicationQueue(false, false, true) ==
                  ApplicationQueueLane::kRegular);
    static_assert(kApplicationQueuePollMs <= 5);
}

void TestAbortGenerationDropsQueuedMcpAndCannotBeStarvedByWakeBacklog() {
    using namespace veetee::app;
    static_assert(!ShouldHandleMcpEnvelope(7, 8));
    static_assert(ShouldHandleMcpEnvelope(8, 8));
    static_assert(SelectApplicationQueue(true, true, true) ==
                  ApplicationQueueLane::kCriticalControl);
}

void TestDeferredHealthWaitsForIdleConversationBoundary() {
    using namespace veetee::app;
    static_assert(!ShouldServiceDeferredHealth(false, State::kIdle, false));
    static_assert(ShouldServiceDeferredHealth(true, State::kIdle, false));
    static_assert(!ShouldServiceDeferredHealth(true, State::kIdle, true));
    static_assert(!ShouldServiceDeferredHealth(true, State::kSpeaking, true));
}

}  // namespace

int main() {
    TestConversationControlsUseUrgentLane();
    TestUrgentControlWinsWhileRegularBacklogIsFull();
    TestAbortGenerationDropsQueuedMcpAndCannotBeStarvedByWakeBacklog();
    TestDeferredHealthWaitsForIdleConversationBoundary();
    std::cout << "application_queue_policy_test: passed\n";
    return 0;
}
