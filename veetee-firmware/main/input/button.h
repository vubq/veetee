#pragma once

#include <cstdint>

#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_timer.h"

namespace veetee::input {

enum class ButtonEvent : std::uint8_t {
    kShortPress,
    kLongPress,
    kWifiConfigHold,
};

class Button {
public:
    using EventSink = void (*)(ButtonEvent event, void* context);

    Button(gpio_num_t pin, std::uint32_t long_press_ms, std::uint32_t wifi_hold_ms);
    ~Button();

    esp_err_t Start(EventSink sink, void* context);

private:
    static void TimerCallback(void* context);
    void Poll();
    void Emit(ButtonEvent event) const;

    gpio_num_t pin_;
    std::uint32_t long_press_ms_;
    std::uint32_t wifi_hold_ms_;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    esp_timer_handle_t timer_ = nullptr;
    bool sampled_pressed_ = false;
    bool stable_pressed_ = false;
    bool long_press_sent_ = false;
    bool wifi_hold_sent_ = false;
    std::uint8_t stable_samples_ = 0;
    std::int64_t pressed_at_us_ = 0;
};

}  // namespace veetee::input
