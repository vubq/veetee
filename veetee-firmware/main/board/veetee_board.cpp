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
                                  const char* active_ui_partition,
                                  const char* fallback_ui_partition,
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

    if ((error = display_.Initialize()) != ESP_OK) {
        return error;
    }
    display_mutex_ = xSemaphoreCreateMutex();
    if (display_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    if (active_ui_partition != nullptr) {
        error = display_.ReloadUiPack(active_ui_partition);
        if (error != ESP_OK && fallback_ui_partition != nullptr &&
            !SamePartition(active_ui_partition, fallback_ui_partition)) {
            ESP_LOGW(kTag, "Active UI Pack %s failed: %s; trying %s",
                     active_ui_partition, esp_err_to_name(error),
                     fallback_ui_partition);
            error = display_.ReloadUiPack(fallback_ui_partition);
        }
        if (error != ESP_OK) {
            ESP_LOGW(kTag, "No UI Pack loaded: %s; using built-in Signal",
                     esp_err_to_name(error));
            display_.UseBuiltInSignal();
        }
    }
    if ((error = display_.DrawState(app::State::kStarting)) != ESP_OK) {
        return error;
    }
    display_queue_ = xQueueCreate(1, sizeof(DisplayCommand));
    if (display_queue_ == nullptr ||
        xTaskCreate(&VeeteeBoard::DisplayTaskEntry, "veetee_display", 6144,
                    this, 3, &display_task_) != pdPASS) {
        return ESP_ERR_NO_MEM;
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

esp_err_t VeeteeBoard::StartAudio(bool play_boot_chime) {
    esp_err_t error = wake_detector_.Start();
    if (error != ESP_OK) return error;
    return audio_.Start(play_boot_chime);
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

esp_err_t VeeteeBoard::ReloadUiPack(const char* partition_label) {
    if (display_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(display_mutex_, portMAX_DELAY);
    esp_err_t error = display_.ReloadUiPack(partition_label);
    if (error == ESP_OK) error = display_.DrawState(state_);
    xSemaphoreGive(display_mutex_);
    return error;
}

void VeeteeBoard::UseBuiltInSignal() {
    if (display_mutex_ == nullptr) return;
    xSemaphoreTake(display_mutex_, portMAX_DELAY);
    display_.UseBuiltInSignal();
    const esp_err_t error = display_.DrawState(state_);
    xSemaphoreGive(display_mutex_);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Built-in Signal render failed: %s",
                 esp_err_to_name(error));
    }
}

bool VeeteeBoard::UiPackHealthy() const {
    return display_.UiPackHealthy();
}

esp_err_t VeeteeBoard::ShowActivationCode(const char* code) {
    if (code == nullptr || std::strlen(code) != 6) return ESP_ERR_INVALID_ARG;
    DisplayCommand command{.kind = DisplayCommandKind::kActivationCode,
                           .state = app::State::kActivating};
    std::snprintf(command.activation_code, sizeof(command.activation_code), "%s",
                  code);
    return QueueDisplay(command);
}

esp_err_t VeeteeBoard::ShowStandby() {
    return QueueDisplay(DisplayCommand{.kind = DisplayCommandKind::kState,
                                       .state = app::State::kIdle});
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
    const esp_err_t display_error = QueueDisplay(
        DisplayCommand{.kind = DisplayCommandKind::kState, .state = state});
    if (display_error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to queue state screen %s: %s",
                 app::ToString(state), esp_err_to_name(display_error));
    }
}

void VeeteeBoard::DisplayTaskEntry(void* context) {
    static_cast<VeeteeBoard*>(context)->RunDisplay();
}

void VeeteeBoard::RunDisplay() {
    DisplayCommand command{};
    while (xQueueReceive(display_queue_, &command, portMAX_DELAY) == pdTRUE) {
        xSemaphoreTake(display_mutex_, portMAX_DELAY);
        const esp_err_t error =
            command.kind == DisplayCommandKind::kActivationCode
                ? display_.DrawActivationCode(command.activation_code)
                : display_.DrawState(command.state);
        xSemaphoreGive(display_mutex_);
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Display command failed: %s", esp_err_to_name(error));
        }
    }
}

esp_err_t VeeteeBoard::QueueDisplay(const DisplayCommand& command) {
    if (display_queue_ == nullptr) return ESP_ERR_INVALID_STATE;
    return xQueueOverwrite(display_queue_, &command) == pdTRUE ? ESP_OK
                                                               : ESP_FAIL;
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
