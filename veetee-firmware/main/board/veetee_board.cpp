#include "board/veetee_board.h"

#include "board/board_config.h"

#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::board {
namespace {

constexpr char kTag[] = "veetee_board";

#if CONFIG_VEETEE_ESP_SR_BRINGUP
constexpr audio::DetectorProfile kBringupProfiles[] = {
    {
        .role = audio::DetectorRole::kActivation,
        .profile_id = "bringup-en-hi-esp-v1",
        .model_id = "wn9s_hiesp",
        .cooldown_ms = CONFIG_VEETEE_WAKE_COOLDOWN_MS,
        .detection_threshold = 0.0F,
    },
};
#endif

bool OnDetectorPcm(const std::int16_t* samples, std::size_t sample_count,
                   void* context) {
    return static_cast<audio::WakeDetector*>(context)->SubmitPcm(samples,
                                                                 sample_count);
}

bool SamePartition(const char* left, const char* right) {
    return left != nullptr && right != nullptr && std::strcmp(left, right) == 0;
}

}  // namespace

VeeteeBoard::VeeteeBoard()
    : button_(kAssistantButton, CONFIG_VEETEE_BUTTON_LONG_PRESS_MS,
              CONFIG_VEETEE_BUTTON_WIFI_RESET_MS) {}

esp_err_t VeeteeBoard::Initialize(ButtonSink button_sink,
                                  DetectorEventSink detector_event_sink,
                                  EncodedAudioSink encoded_audio_sink,
                                  PlaybackFinishedSink playback_finished_sink,
                                  const char* active_resource_partition,
                                  const char* fallback_resource_partition,
                                  void* context) {
    gpio_config_t led = {};
    led.pin_bit_mask = 1ULL << kStatusLed;
    led.mode = GPIO_MODE_OUTPUT;
    led.pull_up_en = GPIO_PULLUP_DISABLE;
    led.pull_down_en = GPIO_PULLDOWN_DISABLE;
    led.intr_type = GPIO_INTR_DISABLE;
    esp_err_t error = gpio_config(&led);
    if (error != ESP_OK) {
        return error;
    }
    gpio_set_level(kStatusLed, 0);

    if ((error = display_.Initialize()) != ESP_OK ||
        (error = display_.DrawColorBars()) != ESP_OK) {
        return error;
    }

    error = wake_detector_.Initialize(
#if CONFIG_VEETEE_ESP_SR_BRINGUP
             active_resource_partition, kBringupProfiles,
             sizeof(kBringupProfiles) / sizeof(kBringupProfiles[0]),
#else
             nullptr, nullptr, 0,
#endif
             detector_event_sink, context);
    if (error == ESP_OK && active_resource_partition != nullptr) {
        std::snprintf(loaded_wake_partition_.data(), loaded_wake_partition_.size(),
                      "%s", active_resource_partition);
    } else if (error != ESP_OK && fallback_resource_partition != nullptr &&
               !SamePartition(active_resource_partition,
                              fallback_resource_partition)) {
        ESP_LOGW(kTag, "Active wake resource %s failed: %s; trying %s",
                 active_resource_partition == nullptr ? "none"
                                                      : active_resource_partition,
                 esp_err_to_name(error), fallback_resource_partition);
        error = wake_detector_.Initialize(
#if CONFIG_VEETEE_ESP_SR_BRINGUP
            fallback_resource_partition, kBringupProfiles,
            sizeof(kBringupProfiles) / sizeof(kBringupProfiles[0]),
#else
            nullptr, nullptr, 0,
#endif
            detector_event_sink, context);
        if (error == ESP_OK) {
            std::snprintf(loaded_wake_partition_.data(),
                          loaded_wake_partition_.size(), "%s",
                          fallback_resource_partition);
        }
    }
    if (error != ESP_OK) {
        ESP_LOGE(kTag,
                 "No ESP-SR resource could be loaded: %s; continuing button-only",
                 esp_err_to_name(error));
        error = wake_detector_.Initialize(nullptr, nullptr, 0,
                                          detector_event_sink, context);
    }

    if (error != ESP_OK ||
        (error = audio_.Initialize(encoded_audio_sink, &OnDetectorPcm,
                                   playback_finished_sink,
                                   context, &wake_detector_)) != ESP_OK ||
        (error = button_.Start(button_sink, context)) != ESP_OK) {
        return error;
    }

    ESP_LOGI(kTag, "Board profile initialized: %s", kBoardName);
    return ESP_OK;
}

esp_err_t VeeteeBoard::StartAudio() {
    esp_err_t error = wake_detector_.Start();
    if (error != ESP_OK) return error;
    return audio_.Start();
}

esp_err_t VeeteeBoard::ReloadWakeResource(const char* partition_label) {
    esp_err_t error = wake_detector_.Reload(
#if CONFIG_VEETEE_ESP_SR_BRINGUP
        partition_label, kBringupProfiles,
        sizeof(kBringupProfiles) / sizeof(kBringupProfiles[0])
#else
        nullptr, nullptr, 0
#endif
    );
    if (error != ESP_OK) {
        loaded_wake_partition_[0] = '\0';
        return error;
    }
    if (partition_label == nullptr) {
        loaded_wake_partition_[0] = '\0';
    } else {
        std::snprintf(loaded_wake_partition_.data(), loaded_wake_partition_.size(),
                      "%s", partition_label);
    }
    ApplyState(state_);
    return ESP_OK;
}

bool VeeteeBoard::WakeResourceHealthy() const {
    return wake_detector_.healthy();
}

esp_err_t VeeteeBoard::ShowActivationCode(const char* code) {
    return display_.DrawActivationCode(code);
}

esp_err_t VeeteeBoard::ShowStandby() {
    return display_.DrawStandby();
}

void VeeteeBoard::ApplyState(app::State state) {
    state_ = state;
    const bool active = state == app::State::kConnecting ||
                        state == app::State::kListening ||
                        state == app::State::kEvaluating ||
                        state == app::State::kThinking ||
                        state == app::State::kSpeaking ||
                        state == app::State::kAborting ||
                        state == app::State::kClosing;
    gpio_set_level(kStatusLed, active ? 1 : 0);
    audio_.SetCaptureEnabled(state == app::State::kListening);
    const audio::DetectorRole detector_role = audio::DetectorRoleForState(
        state,
        wake_detector_.HasProfile(audio::DetectorRole::kActivation),
        wake_detector_.HasProfile(audio::DetectorRole::kInterrupt));
    if (!wake_detector_.SetRole(detector_role)) {
        ESP_LOGW(kTag, "Unable to apply detector role %s",
                 audio::ToString(detector_role));
    }
}

void VeeteeBoard::BeginPlayback() {
    audio_.BeginPlayback();
}

bool VeeteeBoard::QueueOpusPlayback(const std::uint8_t* packet,
                                    std::size_t length) {
    return audio_.QueueOpusPlayback(packet, length);
}

void VeeteeBoard::EndPlayback() {
    audio_.EndPlayback();
}

void VeeteeBoard::AbortPlayback() {
    audio_.AbortPlayback();
}

bool VeeteeBoard::SetSpeakerVolume(int volume_percent) {
    return audio_.SetVolumePercent(volume_percent);
}

int VeeteeBoard::speaker_volume() const {
    return audio_.volume_percent();
}

}  // namespace veetee::board
