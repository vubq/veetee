#pragma once

#include <array>
#include <cstdint>

#include "app/state_machine.h"
#include "audio/i2s_audio.h"
#include "audio/wake_detector.h"
#include "config/device_config.h"
#include "display/st7789_display.h"
#include "esp_err.h"
#include "input/button.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace veetee::board {

class VeeteeBoard : public audio::WakeAudioSource {
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
                         const char* active_ui_partition,
                         const char* fallback_ui_partition,
                         const config::DeviceConfig* applied_config,
                         void* context);
    esp_err_t StartAudio(bool play_boot_chime);
    esp_err_t ReloadWakeResource(const char* partition_label);
    esp_err_t ReloadWakeRuntime(const char* partition_label,
                                const config::DeviceConfig& config);
    bool RevokeWakeAudioConsent();
    bool EnableWakeAudioConsentAfterCommit(
        std::uint32_t expected_config_version);
    [[nodiscard]] bool WakeAudioConsentMatches(
        std::uint32_t expected_config_version,
        bool expected_send_wake_audio) const;
    [[nodiscard]] bool WakeRuntimeConfigVersionMatches(
        std::uint32_t expected_config_version) const;
    esp_err_t ApplyDeviceConfig(const config::DeviceConfig& config,
                                const char* active_resource_partition);
    [[nodiscard]] bool WakeResourceHealthy() const;
    [[nodiscard]] const char* loaded_wake_partition() const {
        return loaded_wake_partition_[0] == '\0'
                   ? nullptr
                   : loaded_wake_partition_.data();
    }
    esp_err_t ReloadUiPack(const char* partition_label);
    void UseBuiltInSignal();
    [[nodiscard]] bool UiPackHealthy() const;
    [[nodiscard]] const char* loaded_ui_partition() const {
        return display_.loaded_ui_partition()[0] == '\0'
                   ? nullptr
                   : display_.loaded_ui_partition();
    }
    esp_err_t ShowActivationCode(const char* code);
    esp_err_t ShowStandby();
    void ApplyState(app::State state);
    void BeginPlayback();
    bool QueueOpusPlayback(const std::uint8_t* packet, std::size_t length);
    void EndPlayback();
    void AbortPlayback();
    bool PlayRecoverySignal();
    bool SetSpeakerVolume(int volume_percent);
    [[nodiscard]] int speaker_volume() const;
    bool SetDisplayBrightness(int brightness_percent);
    [[nodiscard]] int display_brightness() const;
    bool StartAudioDiagnostic(std::uint32_t duration_seconds,
                              std::uint64_t now_ms);
    audio::AudioRuntimeHealth AudioHealth(std::uint64_t now_ms);
    [[nodiscard]] bool wake_task_expected() const {
        return wake_detector_.task_expected();
    }
    [[nodiscard]] bool wake_task_running() const {
        return wake_detector_.task_running();
    }
    [[nodiscard]] std::uint32_t wake_stack_free_bytes() const {
        return wake_detector_.stack_free_bytes();
    }
    [[nodiscard]] std::uint32_t wake_dropped_frames() const {
        return wake_detector_.dropped_frames();
    }
    [[nodiscard]] bool wake_profile_expected() const {
        return device_config_.version == 0 ||
               device_config_.has_wake_profile;
    }
    bool PopWakeAudioPacket(std::uint8_t* destination, std::size_t capacity,
                            std::size_t* length) override;
    void DiscardWakeAudio() override;

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
    std::size_t BuildDetectorProfiles(
        const config::DeviceConfig& config,
        std::array<audio::DetectorProfile, 2>* profiles) const;
    esp_err_t InitializeWakeDetector(
        const char* partition_label,
        const config::DeviceConfig& config,
        DetectorEventSink detector_event_sink, void* context);

    display::St7789Display display_;
    audio::I2sAudio audio_;
    audio::WakeDetector wake_detector_;
    config::DeviceConfig device_config_{};
    input::Button button_;
    app::State state_ = app::State::kStarting;
    std::array<char, 17> loaded_wake_partition_{};
    QueueHandle_t display_queue_ = nullptr;
    SemaphoreHandle_t display_mutex_ = nullptr;
    TaskHandle_t display_task_ = nullptr;
};

}  // namespace veetee::board
