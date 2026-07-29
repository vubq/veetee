#pragma once

#include <cstddef>
#include <cstdint>

#include "app/state_machine.h"

namespace veetee::app {

inline constexpr std::size_t kApplicationQueueDepth = 16;
inline constexpr std::size_t kWakeApplicationQueueDepth = 2;
inline constexpr std::size_t kCriticalApplicationQueueDepth = 4;
inline constexpr std::uint32_t kApplicationQueuePollMs = 5;

enum class ApplicationQueueLane : std::uint8_t {
    kNone,
    kRegular,
    kWake,
    kCriticalControl,
};

constexpr ApplicationQueueLane ApplicationQueueForEvent(Event event) {
    switch (event) {
        case Event::kButtonShortPress:
        case Event::kButtonLongPress:
        case Event::kInterruptDetected:
            return ApplicationQueueLane::kCriticalControl;
        case Event::kActivationWakeDetected:
            return ApplicationQueueLane::kWake;
        default:
            return ApplicationQueueLane::kRegular;
    }
}

constexpr ApplicationQueueLane SelectApplicationQueue(bool critical_ready,
                                                       bool wake_ready,
                                                       bool regular_ready) {
    if (critical_ready) return ApplicationQueueLane::kCriticalControl;
    if (wake_ready) return ApplicationQueueLane::kWake;
    if (regular_ready) return ApplicationQueueLane::kRegular;
    return ApplicationQueueLane::kNone;
}

constexpr bool ShouldHandleMcpEnvelope(std::uint32_t enqueued_generation,
                                       std::uint32_t current_generation) {
    return enqueued_generation == current_generation;
}

constexpr bool ShouldServiceDeferredHealth(bool health_window_due, State state,
                                           bool assistant_gate_open) {
    return health_window_due && state == State::kIdle &&
           !assistant_gate_open;
}

}  // namespace veetee::app
