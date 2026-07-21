#include "network/wifi_manager.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "sdkconfig.h"

namespace veetee::network {
namespace {

constexpr char kTag[] = "veetee_wifi";
constexpr std::uint32_t kApAddress = 0x0104A8C0U;  // 192.168.4.1 in network byte order.

}  // namespace

esp_err_t WifiManager::Initialize(settings::SettingsStore* store,
                                  settings::DeviceSettings* settings,
                                  EventSink sink, void* context) {
    if (store == nullptr || settings == nullptr || sink == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    store_ = store;
    settings_ = settings;
    sink_ = sink;
    sink_context_ = context;

    esp_err_t error = esp_netif_init();
    if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) return error;
    error = esp_event_loop_create_default();
    if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) return error;
    station_netif_ = esp_netif_create_default_wifi_sta();
    ap_netif_ = esp_netif_create_default_wifi_ap();
    if (station_netif_ == nullptr || ap_netif_ == nullptr) return ESP_ERR_NO_MEM;

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    if ((error = esp_wifi_init(&wifi_config)) != ESP_OK ||
        (error = esp_wifi_set_storage(WIFI_STORAGE_RAM)) != ESP_OK ||
        (error = esp_event_handler_instance_register(
             WIFI_EVENT, ESP_EVENT_ANY_ID, &WifiManager::EventHandler, this,
             &wifi_handler_)) != ESP_OK ||
        (error = esp_event_handler_instance_register(
             IP_EVENT, IP_EVENT_STA_GOT_IP, &WifiManager::EventHandler, this,
             &ip_handler_)) != ESP_OK) {
        return error;
    }

    std::uint8_t mac[6] = {};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        std::snprintf(ap_ssid_, sizeof(ap_ssid_), "Veetee-%02X%02X", mac[4], mac[5]);
        esp_netif_set_hostname(station_netif_, ap_ssid_);
    } else {
        std::snprintf(ap_ssid_, sizeof(ap_ssid_), "Veetee-Setup");
    }

    esp_timer_create_args_t timer_config = {};
    timer_config.callback = &WifiManager::ConnectionTimeout;
    timer_config.arg = this;
    timer_config.dispatch_method = ESP_TIMER_TASK;
    timer_config.name = "veetee_wifi_timeout";
    timer_config.skip_unhandled_events = true;
    return esp_timer_create(&timer_config, &connect_timer_);
}

esp_err_t WifiManager::StartStation() {
    if (settings_ == nullptr || !settings_->HasProvisioning()) return ESP_ERR_INVALID_STATE;
    portal_.Stop();
    esp_timer_stop(connect_timer_);
    station_connected_ = false;
    station_connecting_ = true;

    wifi_config_t config = {};
    const std::size_t ssid_length = std::min<std::size_t>(
        std::strlen(settings_->ssid), sizeof(config.sta.ssid));
    const std::size_t password_length = std::min<std::size_t>(
        std::strlen(settings_->password), sizeof(config.sta.password));
    std::memcpy(config.sta.ssid, settings_->ssid, ssid_length);
    std::memcpy(config.sta.password, settings_->password, password_length);
    config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    config.sta.threshold.authmode = WIFI_AUTH_OPEN;
    config.sta.pmf_cfg.capable = true;
    config.sta.pmf_cfg.required = false;

    esp_err_t error = esp_wifi_set_mode(WIFI_MODE_STA);
    if (error == ESP_OK) error = esp_wifi_set_config(WIFI_IF_STA, &config);
    if (error == ESP_OK) error = EnsureWifiStarted();
    if (error == ESP_OK) {
        error = esp_wifi_connect();
        if (error == ESP_ERR_WIFI_CONN) error = ESP_OK;
    }
    if (error == ESP_OK) {
        esp_timer_start_once(connect_timer_,
                             CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS * 1000000ULL);
        ESP_LOGI(kTag, "Connecting to SSID '%s' with %d second fallback timeout",
                 settings_->ssid, CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS);
    }
    return error;
}

esp_err_t WifiManager::StartProvisioning() {
    station_connecting_ = false;
    station_connected_ = false;
    esp_timer_stop(connect_timer_);
    portal_.Stop();
    if (wifi_started_) esp_wifi_disconnect();

    wifi_config_t config = {};
    std::snprintf(reinterpret_cast<char*>(config.ap.ssid), sizeof(config.ap.ssid),
                  "%s", ap_ssid_);
    config.ap.ssid_len = std::strlen(ap_ssid_);
    config.ap.channel = 1;
    config.ap.max_connection = 4;
    config.ap.authmode = WIFI_AUTH_OPEN;

    esp_err_t error = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (error == ESP_OK) error = esp_wifi_set_config(WIFI_IF_AP, &config);
    if (error == ESP_OK) error = EnsureWifiStarted();
    if (error == ESP_OK) {
        error = portal_.Start(kApAddress, *settings_, &WifiManager::SaveProvisioning,
                              this);
    }
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Provisioning AP '%s' is open for physical setup", ap_ssid_);
    }
    return error;
}

esp_err_t WifiManager::ResetProvisioning() {
    if (store_ == nullptr || settings_ == nullptr) return ESP_ERR_INVALID_STATE;
    const esp_err_t error = store_->ClearWifiCredentials(settings_);
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Stored Wi-Fi credentials and bootstrap URL cleared");
    }
    return error;
}

void WifiManager::EventHandler(void* context, esp_event_base_t event_base,
                               std::int32_t event_id, void*) {
    auto* manager = static_cast<WifiManager*>(context);
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const bool was_connected = manager->station_connected_;
        manager->station_connected_ = false;
        if (was_connected) {
            manager->Emit(WifiManagerEvent::kDisconnected);
        }
        if (manager->station_connecting_ || was_connected) {
            manager->station_connecting_ = true;
            esp_wifi_connect();
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        manager->station_connecting_ = false;
        manager->station_connected_ = true;
        esp_timer_stop(manager->connect_timer_);
        manager->Emit(WifiManagerEvent::kConnected);
    }
}

void WifiManager::ConnectionTimeout(void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (!manager->station_connected_) {
        manager->station_connecting_ = false;
        manager->Emit(WifiManagerEvent::kConnectionTimeout);
    }
}

esp_err_t WifiManager::SaveProvisioning(const settings::DeviceSettings& settings,
                                        void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    const esp_err_t error = manager->store_->SaveProvisioning(settings);
    if (error == ESP_OK) {
        *manager->settings_ = settings;
        manager->Emit(WifiManagerEvent::kProvisioningSaved);
    }
    return error;
}

void WifiManager::Emit(WifiManagerEvent event) const {
    if (sink_ != nullptr) sink_(event, sink_context_);
}

esp_err_t WifiManager::EnsureWifiStarted() {
    if (wifi_started_) return ESP_OK;
    const esp_err_t error = esp_wifi_start();
    if (error == ESP_OK) wifi_started_ = true;
    return error;
}

}  // namespace veetee::network
