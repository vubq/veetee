#include "board/veetee_board.h"

#include "board/board_config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::board {
namespace {

constexpr char kTag[] = "veetee_board";

}  // namespace

VeeteeBoard::VeeteeBoard()
    : button_(kAssistantButton, CONFIG_VEETEE_BUTTON_LONG_PRESS_MS,
              CONFIG_VEETEE_BUTTON_WIFI_RESET_MS) {}

esp_err_t VeeteeBoard::Initialize(ButtonSink button_sink,
                                  EncodedAudioSink encoded_audio_sink,
                                  PlaybackFinishedSink playback_finished_sink,
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
        (error = display_.DrawColorBars()) != ESP_OK ||
        (error = audio_.Initialize(encoded_audio_sink, playback_finished_sink,
                                   context)) != ESP_OK ||
        (error = button_.Start(button_sink, context)) != ESP_OK) {
        return error;
    }

    ESP_LOGI(kTag, "Board profile initialized: %s", kBoardName);
    return ESP_OK;
}

esp_err_t VeeteeBoard::StartAudio() {
    return audio_.Start();
}

esp_err_t VeeteeBoard::ShowActivationCode(const char* code) {
    return display_.DrawActivationCode(code);
}

esp_err_t VeeteeBoard::ShowStandby() {
    return display_.DrawStandby();
}

void VeeteeBoard::ApplyState(app::State state) {
    const bool active = state == app::State::kConnecting ||
                        state == app::State::kListening ||
                        state == app::State::kEvaluating ||
                        state == app::State::kThinking ||
                        state == app::State::kSpeaking ||
                        state == app::State::kAborting ||
                        state == app::State::kClosing;
    gpio_set_level(kStatusLed, active ? 1 : 0);
    audio_.SetCaptureEnabled(state == app::State::kListening);
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

}  // namespace veetee::board
