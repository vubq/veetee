#pragma once

#include <cstdint>

#include "app/state_machine.h"

namespace veetee::display {

enum class VisualIcon : std::uint8_t {
    kBoot,
    kWifi,
    kLink,
    kKey,
    kFace,
    kListen,
    kThink,
    kSpeak,
    kStop,
    kClose,
    kError,
};

struct StateVisual {
    const char* label;
    const char* detail;
    std::uint16_t background;
    std::uint16_t foreground;
    std::uint16_t accent;
    VisualIcon icon;
};

const StateVisual& VisualForState(app::State state);

}  // namespace veetee::display
