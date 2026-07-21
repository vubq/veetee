#include <cinttypes>
#include <cstdio>
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
#include "network/wifi_manager.h"
#include "ota/bootstrap_client.h"
#include "settings/settings_store.h"

namespace {

constexpr char kTag[] = "veetee_app";
constexpr UBaseType_t kEventQueueDepth = 16;

struct AppMessage {
    veetee::app::Event event;
    char activation_code[7] = {};
};

QueueHandle_t g_event_queue = nullptr;
veetee::app::StateMachine g_state_machine;
veetee::board::VeeteeBoard g_board;
veetee::settings::SettingsStore g_settings_store;
veetee::settings::DeviceSettings g_settings;
veetee::network::WifiManager g_wifi;
veetee::ota::BootstrapClient g_bootstrap;

bool PostMessage(const AppMessage& message) {
    if (g_event_queue == nullptr ||
        xQueueSend(g_event_queue, &message, 0) != pdTRUE) {
        ESP_LOGW(kTag, "Dropping event %s: application queue full",
                 veetee::app::ToString(message.event));
        return false;
    }
    return true;
}

bool PostEvent(veetee::app::Event event) {
    return PostMessage(AppMessage{.event = event});
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

void OnWifiEvent(veetee::network::WifiManagerEvent event, void*) {
    switch (event) {
        case veetee::network::WifiManagerEvent::kConnected:
            PostEvent(veetee::app::Event::kWifiConnected);
            break;
        case veetee::network::WifiManagerEvent::kConnectionTimeout:
            PostEvent(veetee::app::Event::kWifiConnectionTimeout);
            break;
        case veetee::network::WifiManagerEvent::kDisconnected:
            PostEvent(veetee::app::Event::kWifiDisconnected);
            break;
        case veetee::network::WifiManagerEvent::kProvisioningSaved:
            PostEvent(veetee::app::Event::kProvisioningSaved);
            break;
    }
}

bool OnBootstrapEvent(const veetee::ota::BootstrapNotification& notification,
                      void*) {
    AppMessage message{};
    switch (notification.event) {
        case veetee::ota::BootstrapEvent::kActivationCodeAvailable:
            message.event = veetee::app::Event::kActivationCodeAvailable;
            std::snprintf(message.activation_code, sizeof(message.activation_code),
                          "%s", notification.activation_code);
            break;
        case veetee::ota::BootstrapEvent::kActivationComplete:
            message.event = veetee::app::Event::kActivationComplete;
            break;
    }
    return PostMessage(message);
}

void RunApplication(void*) {
    AppMessage message{};
    while (xQueueReceive(g_event_queue, &message, portMAX_DELAY) == pdTRUE) {
        const veetee::app::Event event = message.event;
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

        if (result.to == veetee::app::State::kWifiConfiguring) {
            g_bootstrap.Cancel();
            if (event == veetee::app::Event::kEnterWifiConfig) {
                const esp_err_t reset_error = g_wifi.ResetProvisioning();
                if (reset_error != ESP_OK) {
                    ESP_LOGE(kTag, "Unable to clear stored provisioning: %s",
                             esp_err_to_name(reset_error));
                }
            }
            const esp_err_t error = g_wifi.StartProvisioning();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to start provisioning: %s; retrying",
                         esp_err_to_name(error));
                vTaskDelay(pdMS_TO_TICKS(1000));
                PostEvent(veetee::app::Event::kRetryWifiProvisioning);
            }
        } else if (result.to == veetee::app::State::kNetworkConnecting) {
            g_bootstrap.Cancel();
            const esp_err_t error = g_wifi.StartStation();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to start station: %s; opening setup portal",
                         esp_err_to_name(error));
                PostEvent(veetee::app::Event::kWifiConnectionTimeout);
            }
        } else if (result.to == veetee::app::State::kActivating) {
            if (event == veetee::app::Event::kActivationCodeAvailable) {
                const esp_err_t error = g_board.ShowActivationCode(
                    message.activation_code);
                if (error != ESP_OK) {
                    ESP_LOGE(kTag, "Unable to render activation code: %s",
                             esp_err_to_name(error));
                }
            } else {
                g_bootstrap.Start();
            }
        } else if (result.to == veetee::app::State::kIdle &&
                   event == veetee::app::Event::kActivationComplete) {
            g_bootstrap.Cancel();
            const esp_err_t error = g_board.ShowStandby();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to render standby screen: %s",
                         esp_err_to_name(error));
            }
        } else if (result.to == veetee::app::State::kConnecting) {
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

    g_event_queue = xQueueCreate(kEventQueueDepth, sizeof(AppMessage));
    if (g_event_queue == nullptr) {
        ESP_LOGE(kTag, "Unable to allocate application event queue");
        abort();
    }

    ESP_ERROR_CHECK(g_settings_store.Initialize(&g_settings));
    ESP_ERROR_CHECK(g_wifi.Initialize(&g_settings_store, &g_settings, &OnWifiEvent, nullptr));
    ESP_ERROR_CHECK(g_bootstrap.Initialize(&g_settings_store, &g_settings,
                                           &OnBootstrapEvent, nullptr));
    ESP_ERROR_CHECK(g_board.Initialize(&OnButtonEvent, nullptr));
    ESP_ERROR_CHECK(g_board.StartDiagnostics());

    if (xTaskCreate(&RunApplication, "veetee_app", 6144, nullptr, 6, nullptr) != pdPASS) {
        ESP_LOGE(kTag, "Unable to create application task");
        abort();
    }
    PostEvent(g_settings.HasProvisioning()
                  ? veetee::app::Event::kBootWithCredentials
                  : veetee::app::Event::kBootNeedsProvisioning);
}
