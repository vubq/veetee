#pragma once

#include <array>
#include <cstddef>

#include "settings/reported_state_record.h"

namespace veetee::telemetry {

class ReportedStateOutbox {
public:
    static constexpr std::size_t kTerminalCapacity = 16;

    bool Push(const settings::ReportedResourceState& state);
    bool Pop(settings::ReportedResourceState* state, bool* terminal);

    [[nodiscard]] bool HasTerminal() const { return terminal_count_ != 0; }
    [[nodiscard]] bool HasLatest() const { return has_latest_; }

private:
    std::array<settings::ReportedResourceState, kTerminalCapacity> terminals_{};
    settings::ReportedResourceState latest_{};
    std::size_t terminal_head_ = 0;
    std::size_t terminal_count_ = 0;
    bool has_latest_ = false;
};

}  // namespace veetee::telemetry
