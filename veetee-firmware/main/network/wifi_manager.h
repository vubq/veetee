#pragma once

#include <cstdint>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "network/provisioning_portal.h"
#include "settings/settings_store.h"

namespace veetee::network {

enum class WifiManagerEvent : std::uint8_t {
    kConnected,
    kConnectionTimeout,
    kDisconnected,
    kProvisioningSaved,
};

class WifiManager {
public:
    using EventSink = void (*)(WifiManagerEvent event, void* context);

    esp_err_t Initialize(settings::SettingsStore* store,
                         settings::DeviceSettings* settings,
                         EventSink sink, void* context);
    esp_err_t StartStation();
    esp_err_t StartProvisioning();
    esp_err_t ResetProvisioning();

private:
    static void EventHandler(void* context, esp_event_base_t event_base,
                             std::int32_t event_id, void* event_data);
    static void ConnectionTimeout(void* context);
    static esp_err_t SaveProvisioning(const settings::DeviceSettings& settings,
                                      void* context);

    void Emit(WifiManagerEvent event) const;
    esp_err_t EnsureWifiStarted();

    settings::SettingsStore* store_ = nullptr;
    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    esp_netif_t* station_netif_ = nullptr;
    esp_netif_t* ap_netif_ = nullptr;
    esp_event_handler_instance_t wifi_handler_ = nullptr;
    esp_event_handler_instance_t ip_handler_ = nullptr;
    esp_timer_handle_t connect_timer_ = nullptr;
    ProvisioningPortal portal_;
    bool wifi_started_ = false;
    bool station_connecting_ = false;
    bool station_connected_ = false;
    char ap_ssid_[24] = {};
};

}  // namespace veetee::network
