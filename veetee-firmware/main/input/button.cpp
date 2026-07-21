#include "input/button.h"

#include "esp_log.h"

namespace veetee::input {
namespace {

constexpr std::uint64_t kPollPeriodUs = 10000;
constexpr std::uint8_t kDebounceSamples = 3;
constexpr char kTag[] = "veetee_button";

}  // namespace

Button::Button(gpio_num_t pin, std::uint32_t long_press_ms, std::uint32_t wifi_hold_ms)
    : pin_(pin), long_press_ms_(long_press_ms), wifi_hold_ms_(wifi_hold_ms) {}

Button::~Button() {
    if (timer_ != nullptr) {
        esp_timer_stop(timer_);
        esp_timer_delete(timer_);
    }
}

esp_err_t Button::Start(EventSink sink, void* context) {
    sink_ = sink;
    sink_context_ = context;

    gpio_config_t config = {};
    config.pin_bit_mask = 1ULL << pin_;
    config.mode = GPIO_MODE_INPUT;
    config.pull_up_en = GPIO_PULLUP_ENABLE;
    config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    config.intr_type = GPIO_INTR_DISABLE;
    esp_err_t error = gpio_config(&config);
    if (error != ESP_OK) {
        return error;
    }

    esp_timer_create_args_t timer_config = {};
    timer_config.callback = &Button::TimerCallback;
    timer_config.arg = this;
    timer_config.dispatch_method = ESP_TIMER_TASK;
    timer_config.name = "veetee_button";
    timer_config.skip_unhandled_events = true;
    error = esp_timer_create(&timer_config, &timer_);
    if (error != ESP_OK) {
        return error;
    }

    ESP_LOGI(kTag, "Button polling started on GPIO %d", static_cast<int>(pin_));
    return esp_timer_start_periodic(timer_, kPollPeriodUs);
}

void Button::TimerCallback(void* context) {
    static_cast<Button*>(context)->Poll();
}

void Button::Poll() {
    const bool pressed = gpio_get_level(pin_) == 0;
    if (pressed == sampled_pressed_) {
        if (stable_samples_ < kDebounceSamples) {
            ++stable_samples_;
        }
    } else {
        sampled_pressed_ = pressed;
        stable_samples_ = 1;
    }

    if (stable_samples_ >= kDebounceSamples && sampled_pressed_ != stable_pressed_) {
        stable_pressed_ = sampled_pressed_;
        if (stable_pressed_) {
            pressed_at_us_ = esp_timer_get_time();
            long_press_sent_ = false;
            wifi_hold_sent_ = false;
        } else {
            if (!long_press_sent_ && !wifi_hold_sent_) {
                Emit(ButtonEvent::kShortPress);
            }
        }
    }

    if (stable_pressed_ && !wifi_hold_sent_) {
        const std::uint32_t held_ms = static_cast<std::uint32_t>(
            (esp_timer_get_time() - pressed_at_us_) / 1000);
        if (!long_press_sent_ && held_ms >= long_press_ms_) {
            long_press_sent_ = true;
            Emit(ButtonEvent::kLongPress);
        }
        if (held_ms >= wifi_hold_ms_) {
            wifi_hold_sent_ = true;
            Emit(ButtonEvent::kWifiConfigHold);
        }
    }
}

void Button::Emit(ButtonEvent event) const {
    if (sink_ != nullptr) {
        sink_(event, sink_context_);
    }
}

}  // namespace veetee::input
