#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::transport {

// This task calls Wi-Fi/TLS code, so its stack stays in internal RAM.
inline constexpr std::size_t kWebSocketIoTaskStackBytes = 10 * 1024;
inline constexpr char kWebSocketIoTaskName[] = "veetee_ws_io";

constexpr bool CanAllocateWebSocketIoTask(std::size_t largest_internal_block) {
    return largest_internal_block >= kWebSocketIoTaskStackBytes;
}

inline constexpr std::uint8_t kWebSocketReconnectAttempts = 3;
inline constexpr std::uint32_t kWebSocketReconnectBaseDelayMs = 250;
inline constexpr std::uint32_t kWebSocketReconnectMaximumDelayMs = 2'000;

constexpr bool CanRetryWebSocket(bool session_open, bool protocol_failure,
                                 std::uint8_t completed_attempts) {
    return session_open && !protocol_failure &&
           completed_attempts < kWebSocketReconnectAttempts;
}

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
