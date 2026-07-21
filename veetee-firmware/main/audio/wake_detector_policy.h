#pragma once

#include <cstdint>

#include "app/state_machine.h"

namespace veetee::audio {

enum class DetectorRole : std::uint8_t {
    kDisabled,
    kActivation,
    kInterrupt,
};

DetectorRole DetectorRoleForState(app::State state,
                                  bool activation_available,
                                  bool interrupt_available);

const char* ToString(DetectorRole role);

}  // namespace veetee::audio
