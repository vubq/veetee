#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "settings/settings_store.h"

namespace veetee::ota {

enum class BootstrapEvent : std::uint8_t {
    kActivationCodeAvailable,
    kActivationComplete,
    kResourceDesired,
};

struct BootstrapNotification {
    BootstrapEvent event;
    char activation_code[7] = {};
    char resource_version[33] = {};
    char resource_manifest_url[257] = {};
};

class BootstrapClient {
public:
    using EventSink = bool (*)(const BootstrapNotification& notification,
                               void* context);

    esp_err_t Initialize(settings::SettingsStore* store,
                         settings::DeviceSettings* settings,
                         EventSink sink, void* context);
    void Start();
    void Cancel();

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
    };

    struct ActivationPayload {
        char device_id[37] = {};
        char device_token[129] = {};
        char websocket_url[257] = {};
        std::uint32_t config_version = 0;
    };

    static void TaskEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);

    void TaskLoop();
    void Run(std::uint32_t generation);
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
    bool Emit(BootstrapEvent event, const char* activation_code,
              const BootstrapPayload* payload,
              std::uint32_t generation) const;
    bool EmitWithRetry(BootstrapEvent event, const char* activation_code,
                       const BootstrapPayload* payload,
                       std::uint32_t generation) const;
    bool Delay(std::uint32_t generation, std::uint32_t milliseconds) const;
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;

    settings::SettingsStore* store_ = nullptr;
    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    TaskHandle_t task_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<bool> active_{false};
    char hardware_id_[18] = {};
    std::array<char, 8193> response_{};
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
};

}  // namespace veetee::ota
