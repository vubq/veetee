#include "display/state_visual.h"

#include <array>

namespace veetee::display {
namespace {

constexpr std::array<StateVisual, 13> kVisuals = {{
    {"VEETEE", "STARTING", 0xF6D6, 0x1163, 0xF3AA, VisualIcon::kBoot},
    {"SETUP WIFI", "OPEN 192.168.4.1", 0xFF36, 0x31A6, 0xE54C, VisualIcon::kWifi},
    {"WIFI", "CONNECTING", 0xE73F, 0x0945, 0x255D, VisualIcon::kLink},
    {"PAIRING", "CONNECT MANAGER", 0xFEF4, 0x4228, 0xFBE0, VisualIcon::kKey},
    {"PAIRING LOST", "HOLD BUTTON 5S", 0xF30C, 0xFFFF, 0xFBE0, VisualIcon::kError},
    {"HEY VEETEE", "READY", 0xDF59, 0x0AA4, 0x6E4E, VisualIcon::kFace},
    {"ASSISTANT", "CONNECTING", 0xD7FF, 0x09A7, 0x2E7F, VisualIcon::kLink},
    {"LISTENING", "SPEAK NATURALLY", 0xDFF4, 0x0B65, 0x57C8, VisualIcon::kListen},
    {"UNDERSTANDING", "CHECKING INPUT", 0xFF38, 0x39A5, 0xFD00, VisualIcon::kThink},
    {"THINKING", "AI AND TOOLS", 0xFF38, 0x39A5, 0xFD00, VisualIcon::kThink},
    {"SPEAKING", "PRESS TO INTERRUPT", 0xF6FB, 0x49A5, 0xF36D, VisualIcon::kSpeak},
    {"STOPPING", "CANCELLING TURN", 0xFBAE, 0xFFFF, 0xF800, VisualIcon::kStop},
    {"GOODBYE", "SLEEPING SOON", 0xDDFB, 0x21E8, 0x7517, VisualIcon::kClose},
}};

static_assert(kVisuals.size() == 13);

}  // namespace

const StateVisual& VisualForState(app::State state) {
    const auto index = static_cast<std::size_t>(state);
    return index < kVisuals.size() ? kVisuals[index] : kVisuals[0];
}

}  // namespace veetee::display
