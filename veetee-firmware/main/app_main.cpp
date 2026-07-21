#include <cinttypes>
#include <cstdlib>

#include "app/state_machine.h"
#include "board/board_config.h"
#include "board/veetee_board.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "input/button.h"

namespace {

constexpr char kTag[] = "veetee_app";
constexpr UBaseType_t kEventQueueDepth = 16;

QueueHandle_t g_event_queue = nullptr;
veetee::app::StateMachine g_state_machine;
veetee::board::VeeteeBoard g_board;

bool PostEvent(veetee::app::Event event) {
    if (g_event_queue == nullptr || xQueueSend(g_event_queue, &event, 0) != pdTRUE) {
        ESP_LOGW(kTag, "Dropping event %s: application queue full",
                 veetee::app::ToString(event));
        return false;
    }
    return true;
}

void OnButtonEvent(veetee::input::ButtonEvent event, void*) {
    switch (event) {
        case veetee::input::ButtonEvent::kShortPress:
            PostEvent(veetee::app::Event::kButtonShortPress);
            break;
        case veetee::input::ButtonEvent::kLongPress:
            PostEvent(veetee::app::Event::kButtonLongPress);
            break;
        case veetee::input::ButtonEvent::kWifiConfigHold:
            PostEvent(veetee::app::Event::kEnterWifiConfig);
            break;
    }
}

void RunApplication(void*) {
    veetee::app::Event event;
    while (xQueueReceive(g_event_queue, &event, portMAX_DELAY) == pdTRUE) {
        const veetee::app::TransitionResult result = g_state_machine.Handle(event);
        if (!result.accepted) {
            ESP_LOGD(kTag, "Ignored event %s in %s", veetee::app::ToString(event),
                     veetee::app::ToString(result.from));
            continue;
        }

        ESP_LOGI(kTag, "State %s -> %s event=%s gate=%s generation=%" PRIu32,
                 veetee::app::ToString(result.from), veetee::app::ToString(result.to),
                 veetee::app::ToString(event),
                 result.assistant_gate_open ? "open" : "closed",
                 result.cancellation_generation);
        g_board.ApplyState(result.to);

        if (result.to == veetee::app::State::kConnecting) {
            // The transport phase will replace this local bring-up completion event.
            PostEvent(veetee::app::Event::kTransportConnected);
        } else if (result.to == veetee::app::State::kAborting) {
            g_board.AbortPlayback();
            PostEvent(veetee::app::Event::kAbortComplete);
        }
    }
}

void LogPlatformInfo() {
    const esp_app_desc_t* app = esp_app_get_description();
    const std::size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const std::size_t psram_size = esp_psram_is_initialized() ? esp_psram_get_size() : 0;
    const std::size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    ESP_LOGI(kTag, "Veetee firmware %s board=%s reset_reason=%d",
             app->version, veetee::board::kBoardName,
             static_cast<int>(esp_reset_reason()));
    ESP_LOGI(kTag, "Heap internal_free=%u PSRAM size=%u free=%u",
             static_cast<unsigned>(internal_free), static_cast<unsigned>(psram_size),
             static_cast<unsigned>(psram_free));
}

}  // namespace

extern "C" void app_main() {
    LogPlatformInfo();

    g_event_queue = xQueueCreate(kEventQueueDepth, sizeof(veetee::app::Event));
    if (g_event_queue == nullptr) {
        ESP_LOGE(kTag, "Unable to allocate application event queue");
        abort();
    }

    ESP_ERROR_CHECK(g_board.Initialize(&OnButtonEvent, nullptr));
    ESP_ERROR_CHECK(g_board.StartDiagnostics());

    if (xTaskCreate(&RunApplication, "veetee_app", 6144, nullptr, 6, nullptr) != pdPASS) {
        ESP_LOGE(kTag, "Unable to create application task");
        abort();
    }
    PostEvent(veetee::app::Event::kBootCompleted);
}
