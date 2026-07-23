#pragma once

#include <cstddef>

namespace veetee::transport {

// This task calls Wi-Fi/TLS code, so its stack stays in internal RAM.
inline constexpr std::size_t kWebSocketIoTaskStackBytes = 10 * 1024;
inline constexpr char kWebSocketIoTaskName[] = "veetee_ws_io";

constexpr bool CanAllocateWebSocketIoTask(std::size_t largest_internal_block) {
    return largest_internal_block >= kWebSocketIoTaskStackBytes;
}

}  // namespace veetee::transport
