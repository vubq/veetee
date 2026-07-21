#include "telemetry/reported_state_outbox.h"

namespace veetee::telemetry {

bool ReportedStateOutbox::Push(
    const settings::ReportedResourceState& state) {
    if (!settings::IsValidReportedResourceState(state)) return false;
    if (!settings::IsTerminalReportedResourcePhase(state.phase)) {
        latest_ = state;
        has_latest_ = true;
        return true;
    }
    if (terminal_count_ == terminals_.size()) return false;
    const std::size_t tail =
        (terminal_head_ + terminal_count_) % terminals_.size();
    terminals_[tail] = state;
    ++terminal_count_;
    return true;
}

bool ReportedStateOutbox::Pop(settings::ReportedResourceState* state,
                              bool* terminal) {
    if (state == nullptr || terminal == nullptr) return false;
    if (terminal_count_ != 0) {
        *state = terminals_[terminal_head_];
        terminal_head_ = (terminal_head_ + 1) % terminals_.size();
        --terminal_count_;
        *terminal = true;
        return true;
    }
    if (!has_latest_) return false;
    *state = latest_;
    has_latest_ = false;
    *terminal = false;
    return true;
}

}  // namespace veetee::telemetry
