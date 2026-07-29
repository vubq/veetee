#include <cassert>
#include <cstddef>
#include <iostream>

#include "display/ui_state_policy.h"

int main() {
    using veetee::app::State;
    using veetee::display::UiPackV1StyleIndex;
    using veetee::display::kUiPackV1StateCount;

    for (std::size_t index = 0; index < kUiPackV1StateCount; ++index) {
        assert(UiPackV1StyleIndex(static_cast<State>(index)) == index);
    }
    assert(UiPackV1StyleIndex(State::kUpgrading) ==
           static_cast<std::size_t>(State::kActivating));
    std::cout << "ui_state_policy_test: passed\n";
    return 0;
}
