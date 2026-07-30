#pragma once

#include <cstddef>
#include <cstdint>

#include "transport/protocol_v1.h"

namespace veetee::transport {

enum class WebSocketCommandPriority : std::uint8_t {
    kRegular,
    kUrgent,
    kCriticalControl,
};

constexpr bool CanFallbackToRegularQueue(WebSocketCommandPriority priority) {
    return priority == WebSocketCommandPriority::kUrgent;
}

constexpr bool ShouldReplaceOldestUrgentCommand(
    WebSocketCommandPriority priority) {
    return priority == WebSocketCommandPriority::kCriticalControl;
}

// This task calls Wi-Fi/TLS code, so its stack stays in internal RAM.
inline constexpr std::size_t kWebSocketIoTaskStackBytes = 10 * 1024;
inline constexpr char kWebSocketIoTaskName[] = "veetee_ws_io";

// Hold a contiguous internal block while the component allocates its client
// state. Releasing it immediately before start guarantees that the I/O task has
// a suitable stack block even when the remaining internal heap is fragmented.
inline constexpr std::size_t kWebSocketIoReserveDesiredBytes = 16 * 1024;
inline constexpr std::size_t kWebSocketIoReserveMinimumBytes =
    kWebSocketIoTaskStackBytes;

constexpr bool CanAllocateWebSocketIoTask(std::size_t largest_internal_block) {
    return largest_internal_block >= kWebSocketIoTaskStackBytes;
}

constexpr std::size_t WebSocketIoReserveBytesForLargestBlock(
    std::size_t largest_internal_block) {
    if (largest_internal_block >= kWebSocketIoReserveDesiredBytes) {
        return kWebSocketIoReserveDesiredBytes;
    }
    return CanAllocateWebSocketIoTask(largest_internal_block)
               ? kWebSocketIoReserveMinimumBytes
               : 0;
}

inline constexpr std::uint8_t kWebSocketReconnectAttempts = 3;
inline constexpr std::uint32_t kWebSocketReconnectBaseDelayMs = 250;
inline constexpr std::uint32_t kWebSocketReconnectMaximumDelayMs = 2'000;

constexpr bool CanRetryWebSocket(bool session_open, bool protocol_failure,
                                 std::uint8_t completed_attempts) {
    return session_open && !protocol_failure &&
           completed_attempts < kWebSocketReconnectAttempts;
}

enum class WakeOpeningPhase : std::uint8_t {
    kIdle,
    kPending,
    kStarted,
};

// Owned by the WebSocket control task. Binding source and progress to the
// transport generation prevents an init/start retry from inheriting stale
// wake metadata or replaying a partially consumed pre-roll ring.
class WakeOpeningSnapshot {
public:
    constexpr void Begin(std::uint32_t generation, WakeSource source) {
        generation_ = generation;
        source_ = source;
        phase_ = WakeOpeningPhase::kPending;
    }

    [[nodiscard]] constexpr bool Matches(std::uint32_t generation,
                                         WakeSource source) const {
        return phase_ != WakeOpeningPhase::kIdle &&
               generation_ == generation && source_ == source;
    }

    [[nodiscard]] constexpr bool Matches(std::uint32_t generation) const {
        return phase_ != WakeOpeningPhase::kIdle &&
               generation_ == generation;
    }

    constexpr bool MarkStarted(std::uint32_t generation) {
        if (!Matches(generation)) return false;
        phase_ = WakeOpeningPhase::kStarted;
        return true;
    }

    [[nodiscard]] constexpr bool PreserveAudioForRetry(
        bool retryable, std::uint32_t generation) const {
        return retryable && Matches(generation) &&
               source_ == WakeSource::kWakeWord &&
               phase_ == WakeOpeningPhase::kPending;
    }

    [[nodiscard]] constexpr WakeSource source() const { return source_; }
    [[nodiscard]] constexpr std::uint32_t generation() const {
        return generation_;
    }
    [[nodiscard]] constexpr WakeOpeningPhase phase() const { return phase_; }

private:
    std::uint32_t generation_ = 0;
    WakeSource source_ = WakeSource::kButton;
    WakeOpeningPhase phase_ = WakeOpeningPhase::kIdle;
};

constexpr std::uint32_t WebSocketReconnectDelayMs(std::uint8_t attempt,
                                                  std::uint32_t entropy) {
    const std::uint8_t shift = attempt > 3 ? 3 : attempt;
    const std::uint32_t exponential =
        kWebSocketReconnectBaseDelayMs << shift;
    const std::uint32_t bounded =
        exponential > kWebSocketReconnectMaximumDelayMs
            ? kWebSocketReconnectMaximumDelayMs
            : exponential;
    const std::uint32_t jitter_window =
        kWebSocketReconnectBaseDelayMs / 2 + 1;
    const std::uint32_t jitter = entropy % jitter_window;
    const std::uint32_t delayed = bounded + jitter;
    return delayed > kWebSocketReconnectMaximumDelayMs
               ? kWebSocketReconnectMaximumDelayMs
               : delayed;
}

}  // namespace veetee::transport
