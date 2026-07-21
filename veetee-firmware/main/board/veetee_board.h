#pragma once

#include <array>

#include "app/state_machine.h"
#include "audio/i2s_audio.h"
#include "audio/wake_detector.h"
#include "display/st7789_display.h"
#include "esp_err.h"
#include "input/button.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

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
                         const char* active_resource_partition,
                         const char* fallback_resource_partition,
                         void* context);
    esp_err_t StartAudio(bool play_boot_chime);
    esp_err_t ReloadWakeResource(const char* partition_label);
    [[nodiscard]] bool WakeResourceHealthy() const;
    [[nodiscard]] const char* loaded_wake_partition() const {
        return loaded_wake_partition_[0] == '\0'
                   ? nullptr
                   : loaded_wake_partition_.data();
    }
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
    enum class DisplayCommandKind : std::uint8_t {
        kState,
        kActivationCode,
    };

    struct DisplayCommand {
        DisplayCommandKind kind = DisplayCommandKind::kState;
        app::State state = app::State::kStarting;
        char activation_code[7] = {};
    };

    static void DisplayTaskEntry(void* context);
    void RunDisplay();
    esp_err_t QueueDisplay(const DisplayCommand& command);

    display::St7789Display display_;
    audio::I2sAudio audio_;
    audio::WakeDetector wake_detector_;
    input::Button button_;
    app::State state_ = app::State::kStarting;
    std::array<char, 17> loaded_wake_partition_{};
    QueueHandle_t display_queue_ = nullptr;
    TaskHandle_t display_task_ = nullptr;
};

}  // namespace veetee::board
