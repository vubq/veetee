#pragma once

#include "esp_err.h"

namespace veetee::diagnostics {

// Benchmark-only interval sampler. Release builds compile a no-op surface and
// therefore do not pay the FreeRTOS trace/TCB overhead.
class RuntimeStatsSampler {
public:
    esp_err_t Initialize();
    void Sample(const char* state_label);

private:
    struct State;
    State* state_ = nullptr;
};

}  // namespace veetee::diagnostics
