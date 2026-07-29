#include "board/veetee_board.h"

#include "board/board_config.h"

#include <cstdio>
#include <cstring>

#include "config/device_config_health_policy.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::board {
namespace {

constexpr char kTag[] = "veetee_board";
constexpr TickType_t kDisplayAnimationPeriod = pdMS_TO_TICKS(500);

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
                                  const config::DeviceConfig* applied_config,
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
            ESP_LOGW(kTag, "No UI Pack loaded: %s; using built-in Mobile (signal)",
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

    device_config_ = applied_config == nullptr ? config::DeviceConfig{}
                                                : *applied_config;
    error = InitializeWakeDetector(active_resource_partition, device_config_,
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
        error = InitializeWakeDetector(fallback_resource_partition,
                                       device_config_, detector_event_sink,
                                       context);
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
                                   context, &wake_detector_)) != ESP_OK) {
        return error;
    }
    if (!audio_.ConfigureWakeAudioPreRoll(device_config_.send_wake_audio)) {
        ESP_LOGE(kTag,
                 "Unable to allocate signed wake pre-roll cache; button wake remains available");
    }
    if ((error = button_.Start(button_sink, context)) != ESP_OK) return error;

    ESP_LOGI(kTag, "Board profile initialized: %s", kBoardName);
    return ESP_OK;
}

esp_err_t VeeteeBoard::StartAudio(bool play_boot_chime) {
    esp_err_t error = wake_detector_.Start();
    if (error != ESP_OK) return error;
    return audio_.Start(play_boot_chime);
}

esp_err_t VeeteeBoard::ReloadWakeResource(const char* partition_label) {
    return ReloadWakeRuntime(partition_label, device_config_);
}

esp_err_t VeeteeBoard::ReloadWakeRuntime(
    const char* partition_label, const config::DeviceConfig& config) {
    const bool previous_send_wake_audio = device_config_.send_wake_audio;
    // Apply privacy revocation before detector/resource reload. Configure(false)
    // stops recording, invalidates capture generations, clears cached Opus and
    // releases the PSRAM ring even when the later detector reload fails.
    if (!audio_.ConfigureWakeAudioPreRoll(config.send_wake_audio)) {
        return ESP_ERR_NO_MEM;
    }
    if (!config.send_wake_audio) {
        device_config_.send_wake_audio = false;
    }
    std::array<audio::DetectorProfile, 2> profiles{};
    const std::size_t profile_count = BuildDetectorProfiles(config, &profiles);
    esp_err_t error = wake_detector_.Reload(
        profile_count == 0 ? nullptr : partition_label,
        profile_count == 0 ? nullptr : profiles.data(), profile_count);
    if (error != ESP_OK) {
        // Enabling is transactional: a failed reload cannot retain an unused
        // allocation. Disabling is fail-closed and is never rolled back to an
        // earlier opt-in merely because the detector/resource failed.
        const bool fallback_send_wake_audio =
            config.send_wake_audio ? previous_send_wake_audio : false;
        if (!audio_.ConfigureWakeAudioPreRoll(fallback_send_wake_audio)) {
            ESP_LOGE(kTag, "Unable to restore wake pre-roll after reload failure");
        }
        loaded_wake_partition_[0] = '\0';
        return error;
    }
    if (partition_label == nullptr) {
        loaded_wake_partition_[0] = '\0';
    } else {
        std::snprintf(loaded_wake_partition_.data(), loaded_wake_partition_.size(),
                      "%s", partition_label);
    }
    device_config_ = config;
    ApplyState(state_);
    return ESP_OK;
}

bool VeeteeBoard::RevokeWakeAudioConsent() {
    device_config_.send_wake_audio = false;
    return audio_.ConfigureWakeAudioPreRoll(false);
}

bool VeeteeBoard::EnableWakeAudioConsentAfterCommit(
    std::uint32_t expected_config_version) {
    if (device_config_.version != expected_config_version ||
        !device_config_.has_wake_profile) {
        return false;
    }
    if (device_config_.send_wake_audio &&
        audio_.wake_audio_pre_roll_configured()) {
        return true;
    }
    if (!audio_.ConfigureWakeAudioPreRoll(true)) return false;
    device_config_.send_wake_audio = true;
    ApplyState(state_);
    return true;
}

bool VeeteeBoard::WakeAudioConsentMatches(
    std::uint32_t expected_config_version,
    bool expected_send_wake_audio) const {
    return device_config_.version == expected_config_version &&
           config::WakeAudioRuntimeMatchesRequest(
               expected_send_wake_audio, device_config_.send_wake_audio,
               audio_.wake_audio_pre_roll_configured());
}

bool VeeteeBoard::WakeRuntimeConfigVersionMatches(
    std::uint32_t expected_config_version) const {
    return device_config_.version == expected_config_version;
}

esp_err_t VeeteeBoard::ApplyDeviceConfig(
    const config::DeviceConfig& config,
    const char* active_resource_partition) {
    const char* partition = loaded_wake_partition();
    if (config.has_wake_profile && partition == nullptr) {
        partition = active_resource_partition;
    }
    if (config.has_wake_profile && partition == nullptr) {
        if (!config.send_wake_audio) {
            // Privacy disable is independent from model availability.
            audio_.ConfigureWakeAudioPreRoll(false);
            device_config_.send_wake_audio = false;
        }
        return ESP_ERR_INVALID_STATE;
    }
    return ReloadWakeRuntime(partition, config);
}

bool VeeteeBoard::WakeResourceHealthy() const {
    return wake_detector_.healthy() &&
           (!device_config_.send_wake_audio ||
            audio_.wake_audio_pre_roll_configured());
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
        ESP_LOGE(kTag, "Built-in Mobile (signal) render failed: %s",
                 esp_err_to_name(error));
    }
}

bool VeeteeBoard::UiPackHealthy() const {
    if (display_mutex_ == nullptr) return false;
    xSemaphoreTake(display_mutex_, portMAX_DELAY);
    const bool healthy = display_.UiPackHealthy();
    xSemaphoreGive(display_mutex_);
    return healthy;
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
        wake_detector_.HasProfile(audio::DetectorRole::kInterrupt),
        device_config_.version == 0 ||
            device_config_.interrupt.enabled_while_speaking);
    if (!wake_detector_.SetRole(detector_role)) {
        ESP_LOGW(kTag, "Unable to apply detector role %s",
                 audio::ToString(detector_role));
    }
    if (!audio_.SetWakeAudioPreRollRecording(
            device_config_.send_wake_audio &&
            detector_role == audio::DetectorRole::kActivation)) {
        ESP_LOGW(kTag, "Unable to apply wake pre-roll recording state");
    }
    const esp_err_t display_error = QueueDisplay(
        DisplayCommand{.kind = DisplayCommandKind::kState, .state = state});
    if (display_error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to queue state screen %s: %s",
                 app::ToString(state), esp_err_to_name(display_error));
    }
}

std::size_t VeeteeBoard::BuildDetectorProfiles(
    const config::DeviceConfig& config,
    std::array<audio::DetectorProfile, 2>* profiles) const {
    if (profiles == nullptr) return 0;
    *profiles = {};
    if (config.version == 0) {
#if CONFIG_VEETEE_ESP_SR_BRINGUP
        (*profiles)[0] = kBringupProfiles[0];
        return 1;
#else
        return 0;
#endif
    }
    if (!config.has_wake_profile || !config.activation.enabled) return 0;

    (*profiles)[0] = audio::DetectorProfile{
        .role = audio::DetectorRole::kActivation,
        .profile_id = config.wake_profile_id.data(),
        .model_id = config.activation.model_id.data(),
        .cooldown_ms = config.activation.cooldown_ms,
        .detection_threshold =
            static_cast<float>(config.activation.threshold_ppm) / 1000000.0F,
    };
    if (!config.interrupt.enabled) return 1;
    (*profiles)[1] = audio::DetectorProfile{
        .role = audio::DetectorRole::kInterrupt,
        .profile_id = config.wake_profile_id.data(),
        .model_id = config.interrupt.model_id.data(),
        .cooldown_ms = config.interrupt.cooldown_ms,
        .detection_threshold =
            static_cast<float>(config.interrupt.threshold_ppm) / 1000000.0F,
    };
    return 2;
}

esp_err_t VeeteeBoard::InitializeWakeDetector(
    const char* partition_label, const config::DeviceConfig& config,
    DetectorEventSink detector_event_sink, void* context) {
    std::array<audio::DetectorProfile, 2> profiles{};
    const std::size_t profile_count = BuildDetectorProfiles(config, &profiles);
    return wake_detector_.Initialize(
        profile_count == 0 ? nullptr : partition_label,
        profile_count == 0 ? nullptr : profiles.data(), profile_count,
        detector_event_sink, context);
}

void VeeteeBoard::DisplayTaskEntry(void* context) {
    static_cast<VeeteeBoard*>(context)->RunDisplay();
}

void VeeteeBoard::RunDisplay() {
    DisplayCommand command{};
    while (true) {
        if (xQueueReceive(display_queue_, &command, kDisplayAnimationPeriod) ==
            pdTRUE) {
            xSemaphoreTake(display_mutex_, portMAX_DELAY);
            const esp_err_t error =
                command.kind == DisplayCommandKind::kActivationCode
                    ? display_.DrawActivationCode(command.activation_code)
                    : display_.DrawState(command.state);
            xSemaphoreGive(display_mutex_);
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Display command failed: %s",
                         esp_err_to_name(error));
            }
            continue;
        }

        xSemaphoreTake(display_mutex_, portMAX_DELAY);
        const esp_err_t error = display_.DrawAnimationFrame();
        xSemaphoreGive(display_mutex_);
        if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(kTag, "Display animation frame failed: %s",
                     esp_err_to_name(error));
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

bool VeeteeBoard::PlayRecoverySignal() {
    return audio_.PlayRecoverySignal();
}

bool VeeteeBoard::SetSpeakerVolume(int volume_percent) {
    return audio_.SetVolumePercent(volume_percent);
}

int VeeteeBoard::speaker_volume() const {
    return audio_.volume_percent();
}

bool VeeteeBoard::StartAudioDiagnostic(std::uint32_t duration_seconds,
                                       std::uint64_t now_ms) {
    return audio_.StartDiagnostic(duration_seconds, now_ms);
}

audio::AudioRuntimeHealth VeeteeBoard::AudioHealth(std::uint64_t now_ms) {
    return audio_.Health(now_ms);
}

bool VeeteeBoard::PopWakeAudioPacket(std::uint8_t* destination,
                                     std::size_t capacity,
                                     std::size_t* length) {
    return audio_.PopWakeAudioPacket(destination, capacity, length);
}

void VeeteeBoard::DiscardWakeAudio() {
    audio_.DiscardWakeAudio();
}

}  // namespace veetee::board
