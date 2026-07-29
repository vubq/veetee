#pragma once

#include <cstddef>

#include "app/state_machine.h"

namespace veetee::display {

constexpr std::size_t kUiPackV1StateCount = 13;

// UI Pack V1 has immutable styles for the original 13 states. Executable-only
// states added later must select a safe existing style without changing the V1
// signed bundle schema.
constexpr std::size_t UiPackV1StyleIndex(app::State state) {
    return state == app::State::kUpgrading
               ? static_cast<std::size_t>(app::State::kActivating)
               : static_cast<std::size_t>(state);
}

static_assert(static_cast<std::size_t>(app::State::kClosing) + 1U ==
              kUiPackV1StateCount);
static_assert(UiPackV1StyleIndex(app::State::kUpgrading) ==
              static_cast<std::size_t>(app::State::kActivating));

}  // namespace veetee::display
