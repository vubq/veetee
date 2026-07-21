#include "network/wifi_manager.h"

#include <algorithm>
#include <array>
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
constexpr std::size_t kMaxStationScanResults = 32;
constexpr std::uint64_t kRetryScanDelayUs = 2500000ULL;

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

    esp_timer_create_args_t timeout_config = {};
    timeout_config.callback = &WifiManager::ConnectionTimeout;
    timeout_config.arg = this;
    timeout_config.dispatch_method = ESP_TIMER_TASK;
    timeout_config.name = "veetee_wifi_timeout";
    timeout_config.skip_unhandled_events = true;
    error = esp_timer_create(&timeout_config, &connect_timer_);
    if (error != ESP_OK) return error;

    esp_timer_create_args_t retry_config = timeout_config;
    retry_config.callback = &WifiManager::RetryScan;
    retry_config.name = "veetee_wifi_retry";
    return esp_timer_create(&retry_config, &retry_timer_);
}

esp_err_t WifiManager::StartStation() {
    if (settings_ == nullptr || store_ == nullptr || !settings_->HasProvisioning()) {
        return ESP_ERR_INVALID_STATE;
    }
    profiles_ = store_->WifiProfiles();
    if (!settings::IsValidWifiProfileRecord(profiles_) || profiles_.count == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    portal_.Stop();
    esp_timer_stop(connect_timer_);
    esp_timer_stop(retry_timer_);
    station_connected_ = false;
    station_connecting_ = true;
    candidate_in_flight_ = false;
    scan_pending_ = false;
    ignore_disconnect_until_scan_ = true;
    candidate_count_ = 0;
    candidate_cursor_ = 0;
    connecting_ssid_[0] = '\0';

    esp_err_t error = esp_wifi_set_mode(WIFI_MODE_STA);
    if (error == ESP_OK) error = EnsureWifiStarted();
    if (error == ESP_OK) {
        const esp_err_t disconnect_error = esp_wifi_disconnect();
        if (disconnect_error != ESP_OK &&
            disconnect_error != ESP_ERR_WIFI_NOT_CONNECT) {
            ESP_LOGD(kTag, "Pre-scan disconnect returned %s",
                     esp_err_to_name(disconnect_error));
        }
        error = BeginScan();
    }
    if (error == ESP_OK) {
        esp_timer_start_once(connect_timer_,
                             CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS * 1000000ULL);
        ESP_LOGI(kTag,
                 "Searching for %u saved Wi-Fi profile(s) with %d second fallback timeout",
                 static_cast<unsigned>(profiles_.count),
                 CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS);
    }
    return error;
}

esp_err_t WifiManager::StartProvisioning() {
    station_connecting_ = false;
    station_connected_ = false;
    candidate_in_flight_ = false;
    scan_pending_ = false;
    ignore_disconnect_until_scan_ = true;
    esp_timer_stop(connect_timer_);
    esp_timer_stop(retry_timer_);
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
        error = portal_.Start(kApAddress, *settings_, store_->WifiProfiles(),
                              &WifiManager::SaveProvisioning, this);
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
                               std::int32_t event_id, void* event_data) {
    auto* manager = static_cast<WifiManager*>(context);
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_SCAN_DONE) {
        if (!manager->station_connecting_ || !manager->scan_pending_) return;
        manager->scan_pending_ = false;
        manager->BuildCandidateQueue();
        manager->ignore_disconnect_until_scan_ = false;
        manager->ConnectNextCandidate();
    } else if (event_base == WIFI_EVENT &&
               event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const bool was_connected = manager->station_connected_;
        manager->station_connected_ = false;
        const auto* disconnected =
            static_cast<const wifi_event_sta_disconnected_t*>(event_data);
        ESP_LOGW(kTag, "Station disconnected from '%s' reason=%u",
                 manager->connecting_ssid_[0] == '\0'
                     ? "saved network"
                     : manager->connecting_ssid_,
                 disconnected == nullptr ? 0U
                                         : static_cast<unsigned>(disconnected->reason));
        if (was_connected) {
            manager->station_connecting_ = false;
            manager->candidate_in_flight_ = false;
            manager->scan_pending_ = false;
            esp_timer_stop(manager->connect_timer_);
            esp_timer_stop(manager->retry_timer_);
            manager->Emit(WifiManagerEvent::kDisconnected);
        } else if (manager->station_connecting_ &&
                   manager->candidate_in_flight_ &&
                   !manager->ignore_disconnect_until_scan_) {
            manager->candidate_in_flight_ = false;
            manager->ConnectNextCandidate();
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        manager->station_connecting_ = false;
        manager->station_connected_ = true;
        manager->candidate_in_flight_ = false;
        manager->scan_pending_ = false;
        esp_timer_stop(manager->connect_timer_);
        esp_timer_stop(manager->retry_timer_);
        const esp_err_t save_error = manager->store_->MarkWifiProfileSuccessful(
            manager->connecting_ssid_, manager->settings_);
        if (save_error != ESP_OK) {
            ESP_LOGW(kTag, "Unable to persist last-successful Wi-Fi profile: %s",
                     esp_err_to_name(save_error));
        }
        manager->Emit(WifiManagerEvent::kConnected);
    }
}

void WifiManager::ConnectionTimeout(void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (!manager->station_connected_) {
        manager->station_connecting_ = false;
        manager->candidate_in_flight_ = false;
        manager->scan_pending_ = false;
        manager->ignore_disconnect_until_scan_ = true;
        esp_timer_stop(manager->retry_timer_);
        esp_wifi_scan_stop();
        esp_wifi_disconnect();
        manager->Emit(WifiManagerEvent::kConnectionTimeout);
    }
}

void WifiManager::RetryScan(void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (!manager->station_connecting_ || manager->station_connected_) return;
    const esp_err_t error = manager->BeginScan();
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to retry Wi-Fi scan: %s", esp_err_to_name(error));
        manager->ScheduleScanRetry();
    }
}

esp_err_t WifiManager::SaveProvisioning(settings::DeviceSettings* settings,
                                        void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (settings == nullptr) return ESP_ERR_INVALID_ARG;
    const esp_err_t error = manager->store_->SaveProvisioning(settings);
    if (error == ESP_OK) {
        *manager->settings_ = *settings;
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

esp_err_t WifiManager::BeginScan() {
    if (!station_connecting_ || station_connected_) return ESP_ERR_INVALID_STATE;
    wifi_scan_config_t scan_config = {};
    scan_config.show_hidden = true;
    scan_pending_ = true;
    candidate_in_flight_ = false;
    const esp_err_t error = esp_wifi_scan_start(&scan_config, false);
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Scanning for saved Wi-Fi networks");
        return ESP_OK;
    }
    scan_pending_ = false;
    ESP_LOGW(kTag, "Wi-Fi scan unavailable (%s); trying saved MRU order",
             esp_err_to_name(error));
    const WifiCandidateOrder order =
        BuildWifiCandidateOrder(profiles_, nullptr, 0);
    candidate_count_ = order.count;
    candidate_cursor_ = 0;
    for (std::size_t index = 0; index < order.count; ++index) {
        candidates_[index] = CandidateTarget{
            .profile_index = order.profile_indices[index],
        };
    }
    ignore_disconnect_until_scan_ = false;
    ConnectNextCandidate();
    return candidate_count_ == 0 ? ESP_ERR_NOT_FOUND : ESP_OK;
}

void WifiManager::BuildCandidateQueue() {
    std::uint16_t count = kMaxStationScanResults;
    std::array<wifi_ap_record_t, kMaxStationScanResults> records{};
    if (esp_wifi_scan_get_ap_records(&count, records.data()) != ESP_OK) count = 0;

    std::array<VisibleWifiNetwork, kMaxStationScanResults> visible{};
    for (std::size_t index = 0; index < count; ++index) {
        std::snprintf(visible[index].ssid, sizeof(visible[index].ssid), "%s",
                      reinterpret_cast<const char*>(records[index].ssid));
        visible[index].rssi = records[index].rssi;
    }
    const WifiCandidateOrder order =
        BuildWifiCandidateOrder(profiles_, visible.data(), count);
    candidate_count_ = order.count;
    candidate_cursor_ = 0;
    for (std::size_t index = 0; index < order.count; ++index) {
        CandidateTarget target{
            .profile_index = order.profile_indices[index],
        };
        const char* ssid = profiles_.profiles[target.profile_index].ssid;
        const wifi_ap_record_t* strongest = nullptr;
        for (std::size_t record_index = 0; record_index < count; ++record_index) {
            if (std::strcmp(ssid, reinterpret_cast<const char*>(
                                      records[record_index].ssid)) == 0 &&
                (strongest == nullptr ||
                 records[record_index].rssi > strongest->rssi)) {
                strongest = &records[record_index];
            }
        }
        if (strongest != nullptr) {
            target.channel = strongest->primary;
            std::memcpy(target.bssid, strongest->bssid, sizeof(target.bssid));
            target.bssid_set = true;
        }
        candidates_[index] = target;
    }
    ESP_LOGI(kTag, "Prepared %u saved Wi-Fi candidate(s), including hidden profiles",
             static_cast<unsigned>(candidate_count_));
}

void WifiManager::ConnectNextCandidate() {
    while (station_connecting_ && !station_connected_ &&
           candidate_cursor_ < candidate_count_) {
        const CandidateTarget& target = candidates_[candidate_cursor_++];
        if (target.profile_index >= profiles_.count) continue;
        const settings::WifiProfile& profile =
            profiles_.profiles[target.profile_index];

        wifi_config_t config = {};
        std::snprintf(reinterpret_cast<char*>(config.sta.ssid),
                      sizeof(config.sta.ssid), "%s", profile.ssid);
        std::snprintf(reinterpret_cast<char*>(config.sta.password),
                      sizeof(config.sta.password), "%s", profile.password);
        config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
        config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
        config.sta.failure_retry_cnt = 1;
        config.sta.threshold.authmode = WIFI_AUTH_OPEN;
        config.sta.pmf_cfg.capable = true;
        config.sta.pmf_cfg.required = false;
        if (target.bssid_set) {
            config.sta.channel = target.channel;
            config.sta.bssid_set = true;
            std::memcpy(config.sta.bssid, target.bssid, sizeof(target.bssid));
        }

        std::snprintf(connecting_ssid_, sizeof(connecting_ssid_), "%s",
                      profile.ssid);
        std::snprintf(settings_->ssid, sizeof(settings_->ssid), "%s",
                      profile.ssid);
        std::snprintf(settings_->password, sizeof(settings_->password), "%s",
                      profile.password);
        esp_err_t error = esp_wifi_set_config(WIFI_IF_STA, &config);
        if (error == ESP_OK) error = esp_wifi_connect();
        if (error == ESP_OK || error == ESP_ERR_WIFI_CONN) {
            candidate_in_flight_ = true;
            ESP_LOGI(kTag, "Connecting to saved SSID '%s' candidate=%u/%u%s",
                     connecting_ssid_,
                     static_cast<unsigned>(candidate_cursor_),
                     static_cast<unsigned>(candidate_count_),
                     target.bssid_set ? " (visible AP)" : " (hidden/not visible)");
            return;
        }
        ESP_LOGW(kTag, "Unable to start connection to SSID '%s': %s",
                 connecting_ssid_, esp_err_to_name(error));
    }
    if (station_connecting_ && !station_connected_) ScheduleScanRetry();
}

void WifiManager::ScheduleScanRetry() {
    candidate_count_ = 0;
    candidate_cursor_ = 0;
    candidate_in_flight_ = false;
    ignore_disconnect_until_scan_ = true;
    esp_timer_stop(retry_timer_);
    const esp_err_t error = esp_timer_start_once(retry_timer_, kRetryScanDelayUs);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to schedule Wi-Fi rescan: %s",
                 esp_err_to_name(error));
    } else {
        ESP_LOGI(kTag, "No saved network connected; rescanning before AP fallback");
    }
}

}  // namespace veetee::network
