#pragma once

#include <array>
#include <cstdint>

#include "app/state_machine.h"
#include "esp_err.h"

namespace veetee::display {

enum class UiComposition : std::uint8_t {
    kSignal,
    kMonolith,
    kQuiet,
};

struct UiStateStyle {
    std::uint16_t background = 0;
    std::uint16_t foreground = 0;
    std::uint16_t accent = 0;
};

struct UiTheme {
    bool external = false;
    UiComposition composition = UiComposition::kSignal;
    char theme_id[33] = "signal";
    std::array<UiStateStyle, 13> states{};
};

UiTheme BuiltInSignalTheme();
bool IsValidUiTheme(const UiTheme& theme);
esp_err_t LoadUiPackPartition(const char* partition_label, UiTheme* theme);

}  // namespace veetee::display
