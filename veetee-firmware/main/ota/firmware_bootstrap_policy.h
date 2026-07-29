#pragma once

#include <string_view>

namespace veetee::ota {

constexpr bool FirmwareBootstrapRequiresUpdate(
    bool has_signed_target, std::string_view desired_version,
    std::string_view running_version, bool defer_while_pending_verify = false) {
    return !defer_while_pending_verify && has_signed_target &&
           !desired_version.empty() &&
           desired_version != running_version;
}

}  // namespace veetee::ota
