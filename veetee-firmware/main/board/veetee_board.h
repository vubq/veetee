#pragma once

#include "app/state_machine.h"
#include "audio/i2s_audio.h"
#include "audio/wake_detector.h"
#include "display/st7789_display.h"
#include "esp_err.h"
#include "input/button.h"

namespace veetee::board {

class VeeteeBoard {
public:
    using ButtonSink = input::Button::EventSink;
    using EncodedAudioSink = audio::I2sAudio::EncodedAudioSink;
    using PlaybackFinishedSink = audio::I2sAudio::PlaybackFinishedSink;
    using DetectorEventSink = audio::WakeDetector::EventSink;

    VeeteeBoard();

    esp_err_t Initialize(ButtonSink button_sink,
                         DetectorEventSink detector_event_sink,
                         EncodedAudioSink encoded_audio_sink,
                         PlaybackFinishedSink playback_finished_sink,
                         void* context);
    esp_err_t StartAudio();
    esp_err_t ShowActivationCode(const char* code);
    esp_err_t ShowStandby();
    void ApplyState(app::State state);
    void BeginPlayback();
    bool QueueOpusPlayback(const std::uint8_t* packet, std::size_t length);
    void EndPlayback();
    void AbortPlayback();
    bool SetSpeakerVolume(int volume_percent);
    [[nodiscard]] int speaker_volume() const;

private:
    display::St7789Display display_;
    audio::I2sAudio audio_;
    audio::WakeDetector wake_detector_;
    input::Button button_;
};

}  // namespace veetee::board
