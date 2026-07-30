#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "maintenance/maintenance_executor.h"
#include "settings/settings_store.h"

namespace veetee::ota {

enum class BootstrapEvent : std::uint8_t {
    kActivationCodeAvailable,
    kActivationComplete,
    kDeviceIdentityRejected,
    kConfigDesired,
    kResourceDesired,
    kUiPackDesired,
    kFirmwareDesired,
};

struct BootstrapNotification {
    BootstrapEvent event;
    char activation_code[7] = {};
    std::uint32_t config_version = 0;
    char config_etag[65] = {};
    char config_url[257] = {};
    char resource_version[33] = {};
    char resource_manifest_url[257] = {};
    char ui_version[33] = {};
  char ui_manifest_url[257] = {};
  char firmware_version[33] = {};
  char firmware_manifest_url[257] = {};
};

class BootstrapClient {
public:
    using EventSink = bool (*)(const BootstrapNotification& notification,
                               void* context);

    esp_err_t Initialize(settings::SettingsStore* store,
                         settings::DeviceSettings* settings,
                         maintenance::MaintenanceExecutor* executor,
                         EventSink sink, void* context);
    void Start();
    void Cancel();
    void SetFirmwareUpdatesDeferred(bool deferred) {
        firmware_updates_deferred_.store(deferred);
    }

private:
    struct BootstrapPayload {
        bool has_activation = false;
        char activation_code[7] = {};
        char activation_challenge[129] = {};
        char websocket_url[257] = {};
        std::uint32_t config_version = 0;
        bool has_config = false;
        char config_etag[65] = {};
        char config_url[257] = {};
        bool has_resources = false;
        char resource_version[33] = {};
        char resource_manifest_url[257] = {};
        bool has_ui = false;
        char ui_version[33] = {};
        char ui_manifest_url[257] = {};
        bool has_firmware = false;
        char firmware_version[33] = {};
        char firmware_manifest_url[257] = {};
    };

    struct ActivationPayload {
        char device_id[37] = {};
        char device_token[129] = {};
        char websocket_url[257] = {};
        std::uint32_t config_version = 0;
    };

    struct Attempt {
        std::uint32_t generation = 0;
        std::uint32_t retry_ms = 0;
        std::uint32_t activation_elapsed_ms = 0;
        bool refresh_activation_ticket = false;
        bool announce_pending_activation = false;
    };

    struct PendingNotification {
        std::uint32_t generation = 0;
        BootstrapNotification notification{};
    };

    static void MaintenanceEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);

    void ProcessPending();
    void ProcessAttempt(Attempt attempt);
    esp_err_t RequestBootstrap(const settings::DeviceSettings& snapshot,
                               bool authenticated,
                               BootstrapPayload* payload,
                               std::uint32_t generation);
    esp_err_t RequestActivation(const settings::DeviceSettings& snapshot,
                                ActivationPayload* payload,
                                std::uint32_t generation);
    esp_err_t PerformPost(const settings::DeviceSettings& snapshot,
                          const char* url, const char* body,
                          const char* bearer_token, int* status_code);
    esp_err_t ParseBootstrap(BootstrapPayload* payload) const;
    esp_err_t ParseActivation(ActivationPayload* payload) const;
    bool QueueNotification(BootstrapEvent event, const char* activation_code,
                           const BootstrapPayload* payload,
                           std::uint32_t generation);
    bool QueueBootstrapComplete(const BootstrapPayload& payload,
                                std::uint32_t generation);
    bool DeliverPendingNotification();
    bool Reschedule(const Attempt& attempt, std::uint32_t delay_ms);
    void Retry(Attempt attempt, esp_err_t error);
    void RequestIfPending();
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;

    settings::SettingsStore* store_ = nullptr;
    settings::DeviceSettings* settings_ = nullptr;
    maintenance::MaintenanceExecutor* executor_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t attempt_queue_ = nullptr;
    QueueHandle_t notification_queue_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<bool> active_{false};
    std::atomic<bool> firmware_updates_deferred_{false};
    char hardware_id_[18] = {};
    char* response_ = nullptr;
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
};

}  // namespace veetee::ota
