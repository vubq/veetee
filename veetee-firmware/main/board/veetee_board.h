#pragma once

#include "app/state_machine.h"
#include "audio/i2s_audio.h"
#include "display/st7789_display.h"
#include "esp_err.h"
#include "input/button.h"

namespace veetee::board {

class VeeteeBoard {
public:
    using ButtonSink = input::Button::EventSink;

    VeeteeBoard();

    esp_err_t Initialize(ButtonSink sink, void* context);
    esp_err_t StartDiagnostics();
    esp_err_t ShowActivationCode(const char* code);
    esp_err_t ShowStandby();
    void ApplyState(app::State state);
    void AbortPlayback();

private:
    display::St7789Display display_;
    audio::I2sAudio audio_;
    input::Button button_;
};

}  // namespace veetee::board
