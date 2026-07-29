#include "network/wifi_manager.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "lwip/ip_addr.h"
#include "sdkconfig.h"

namespace veetee::network {
namespace {

constexpr char kTag[] = "veetee_wifi";
constexpr std::uint32_t kApAddress = 0x0104A8C0U;  // 192.168.4.1 in network byte order.
constexpr std::uint32_t kDhcpReadyTimeoutMs = 1000;
constexpr std::uint32_t kDhcpReadyPollMs = 10;
constexpr std::uint64_t kRetryScanDelayUs = 2500000ULL;
constexpr std::uint64_t kProvisioningTransitionDelayUs = 750000ULL;
constexpr std::uint64_t kProvisioningObservedCleanupDelayUs = 3000000ULL;
constexpr std::uint64_t kProvisioningFallbackCleanupDelayUs = 15000000ULL;

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

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    wifi_config.nvs_enable = false;
    if ((error = esp_wifi_init(&wifi_config)) != ESP_OK ||
        (error = esp_event_handler_instance_register(
             WIFI_EVENT, ESP_EVENT_ANY_ID, &WifiManager::EventHandler, this,
             &wifi_handler_)) != ESP_OK ||
        (error = esp_event_handler_instance_register(
             IP_EVENT, ESP_EVENT_ANY_ID, &WifiManager::EventHandler, this,
             &ip_handler_)) != ESP_OK) {
        return error;
    }

    std::uint8_t mac[6] = {};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        std::snprintf(ap_ssid_, sizeof(ap_ssid_), "Veetee-%02X%02X", mac[4], mac[5]);
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
    error = esp_timer_create(&retry_config, &retry_timer_);
    if (error != ESP_OK) return error;

    esp_timer_create_args_t provisioning_config = timeout_config;
    provisioning_config.callback = &WifiManager::ProvisioningTransition;
    provisioning_config.name = "wifi_provision";
    error = esp_timer_create(&provisioning_config, &provisioning_timer_);
    if (error != ESP_OK) return error;

    esp_timer_create_args_t cleanup_config = timeout_config;
    cleanup_config.callback = &WifiManager::ProvisioningCleanup;
    cleanup_config.name = "wifi_ap_cleanup";
    return esp_timer_create(&cleanup_config, &provisioning_cleanup_timer_);
}

esp_err_t WifiManager::StartStation() {
    if (settings_ == nullptr || store_ == nullptr || !settings_->HasProvisioning()) {
        return ESP_ERR_INVALID_STATE;
    }
    const bool keep_portal = provisioning_active_ && portal_.IsRunning() &&
                             ap_netif_ != nullptr && wifi_started_;
    provisioning_handoff_ = keep_portal;
    provisioning_connection_attempt_id_ = 0;
    if (keep_portal) {
        const auto snapshot = provisioning_status_.Snapshot();
        provisioning_connection_attempt_id_ = snapshot.attempt_id;
        provisioning_status_.MarkConnecting(
            provisioning_connection_attempt_id_);
    }
    const auto fail_handoff = [this, keep_portal](esp_err_t error) {
        if (keep_portal && provisioning_connection_attempt_id_ != 0) {
            provisioning_status_.MarkFailed(
                provisioning_connection_attempt_id_,
                ProvisioningFailure::kConnectionTimeout);
        }
        return error;
    };
    profiles_ = store_->WifiProfiles();
    if (!settings::IsValidWifiProfileRecord(profiles_) || profiles_.count == 0) {
        return fail_handoff(ESP_ERR_INVALID_STATE);
    }

    if (keep_portal) {
        portal_.PauseScan();
        esp_wifi_scan_stop();
    } else {
        provisioning_active_ = false;
        provisioning_wifi_ready_ = false;
        portal_.Stop();
        if (wifi_started_) {
            esp_wifi_disconnect();
            const esp_err_t stop_error = esp_wifi_stop();
            if (stop_error != ESP_OK) return fail_handoff(stop_error);
            wifi_started_ = false;
        }
        if (ap_netif_ != nullptr) {
            esp_netif_destroy_default_wifi(ap_netif_);
            ap_netif_ = nullptr;
        }
    }
    if (station_netif_ == nullptr) {
        station_netif_ = esp_netif_create_default_wifi_sta();
        if (station_netif_ == nullptr) return fail_handoff(ESP_ERR_NO_MEM);
        const esp_err_t hostname_error =
            esp_netif_set_hostname(station_netif_, ap_ssid_);
        if (hostname_error != ESP_OK) return fail_handoff(hostname_error);
    }
    esp_timer_stop(connect_timer_);
    esp_timer_stop(retry_timer_);
    esp_timer_stop(provisioning_timer_);
    esp_timer_stop(provisioning_cleanup_timer_);
    station_connected_ = false;
    station_connecting_ = true;
    station_associated_ = false;
    candidate_in_flight_ = false;
    scan_pending_ = false;
    ignore_disconnect_until_scan_ = true;
    candidate_count_ = 0;
    candidate_cursor_ = 0;
    candidate_reconnect_count_ = 0;
    connecting_ssid_[0] = '\0';
    esp_err_t error = esp_wifi_set_mode(keep_portal ? WIFI_MODE_APSTA
                                                    : WIFI_MODE_STA);
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
                 "Searching for %u saved Wi-Fi profile(s) with %d second fallback timeout%s",
                 static_cast<unsigned>(profiles_.count),
                 CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS,
                 keep_portal ? " while setup portal remains available" : "");
    }
    return error == ESP_OK ? ESP_OK : fail_handoff(error);
}

esp_err_t WifiManager::StartProvisioning() {
    if (provisioning_status_.Snapshot().phase ==
        ProvisioningPhase::kConnected) {
        provisioning_status_.Reset();
    }
    if (portal_.IsRunning()) {
        provisioning_active_ = true;
        provisioning_handoff_ = false;
        provisioning_connection_attempt_id_ = 0;
        station_connecting_ = false;
        station_connected_ = false;
        station_associated_ = false;
        esp_timer_stop(provisioning_cleanup_timer_);
        const auto snapshot = provisioning_status_.Snapshot();
        if (snapshot.phase == ProvisioningPhase::kSaved ||
            snapshot.phase == ProvisioningPhase::kConnecting) {
            provisioning_status_.MarkFailed(
                snapshot.attempt_id, ProvisioningFailure::kConnectionTimeout);
        }
        portal_.NotifyClientNetworkReady();
        ESP_LOGI(kTag, "Provisioning portal is already active; keeping SoftAP stable");
        return ESP_OK;
    }
    provisioning_active_ = false;
    station_connecting_ = false;
    station_connected_ = false;
    station_associated_ = false;
    candidate_in_flight_ = false;
    scan_pending_ = false;
    ignore_disconnect_until_scan_ = true;
    esp_timer_stop(connect_timer_);
    esp_timer_stop(retry_timer_);
    esp_timer_stop(provisioning_timer_);
    esp_timer_stop(provisioning_cleanup_timer_);
    provisioning_handoff_ = false;
    provisioning_connection_attempt_id_ = 0;

    esp_err_t error = ESP_OK;
    if (!provisioning_wifi_ready_) {
        if (wifi_started_) {
            const esp_err_t disconnect_error = esp_wifi_disconnect();
            if (disconnect_error != ESP_OK &&
                disconnect_error != ESP_ERR_WIFI_NOT_CONNECT) {
                ESP_LOGD(kTag, "Provisioning disconnect returned %s",
                         esp_err_to_name(disconnect_error));
            }
            error = esp_wifi_stop();
            if (error == ESP_OK) wifi_started_ = false;
        }
        if (error == ESP_OK && station_netif_ != nullptr) {
            esp_netif_destroy_default_wifi(station_netif_);
            station_netif_ = nullptr;
        }
        if (error == ESP_OK && ap_netif_ != nullptr) {
            esp_netif_destroy_default_wifi(ap_netif_);
            ap_netif_ = nullptr;
        }
        if (error == ESP_OK) {
            ap_netif_ = esp_netif_create_default_wifi_ap();
            if (ap_netif_ == nullptr) error = ESP_ERR_NO_MEM;
        }

        wifi_config_t config = {};
        std::snprintf(reinterpret_cast<char*>(config.ap.ssid), sizeof(config.ap.ssid),
                      "%s", ap_ssid_);
        config.ap.ssid_len = std::strlen(ap_ssid_);
        config.ap.channel = 1;
        config.ap.max_connection = 4;
        config.ap.authmode = WIFI_AUTH_OPEN;

        if (error == ESP_OK) error = esp_wifi_set_mode(WIFI_MODE_APSTA);
        if (error == ESP_OK) error = esp_wifi_set_config(WIFI_IF_AP, &config);
        // Prepare DHCP before the SoftAP becomes visible, matching Xiaozhi's
        // proven order and avoiding a second stop/start after WIFI_EVENT_AP_START.
        if (error == ESP_OK) error = ConfigureCaptivePortalDhcp();
        if (error == ESP_OK) error = EnsureWifiStarted();
        if (error == ESP_OK) error = esp_wifi_set_ps(WIFI_PS_NONE);
        if (error == ESP_OK) {
            error = esp_wifi_set_band_mode(WIFI_BAND_MODE_2G_ONLY);
        }
        if (error == ESP_OK) error = WaitForCaptivePortalDhcp();
        if (error == ESP_OK) {
            provisioning_wifi_ready_ = true;
        }
    }
    if (error == ESP_OK) {
        error = portal_.Start(kApAddress, *settings_, store_->WifiProfiles(),
                              &WifiManager::SaveProvisioning,
                              &WifiManager::ReadProvisioningStatus,
                              &WifiManager::ObserveProvisioningSuccess,
                              &WifiManager::CanSaveProvisioning, this);
    }
    if (error == ESP_OK) {
        provisioning_active_ = true;
        ESP_LOGI(kTag, "Provisioning AP '%s' is open for physical setup", ap_ssid_);
    } else if (!provisioning_wifi_ready_) {
        portal_.Stop();
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
               event_id == WIFI_EVENT_AP_STACONNECTED &&
               manager->provisioning_active_) {
        const auto* connected =
            static_cast<const wifi_event_ap_staconnected_t*>(event_data);
        if (connected != nullptr) {
            ESP_LOGI(kTag, "Setup client " MACSTR " joined AID=%u",
                     MAC2STR(connected->mac),
                     static_cast<unsigned>(connected->aid));
        }
    } else if (event_base == WIFI_EVENT &&
               event_id == WIFI_EVENT_AP_STADISCONNECTED &&
               manager->provisioning_active_) {
        const auto* disconnected =
            static_cast<const wifi_event_ap_stadisconnected_t*>(event_data);
        if (disconnected != nullptr) {
            ESP_LOGI(kTag, "Setup client " MACSTR " left AID=%u reason=%u",
                     MAC2STR(disconnected->mac),
                     static_cast<unsigned>(disconnected->aid),
                     static_cast<unsigned>(disconnected->reason));
        }
        wifi_sta_list_t stations = {};
        if (esp_wifi_ap_get_sta_list(&stations) == ESP_OK && stations.num == 0) {
            manager->portal_.ResetClientSessions();
            ESP_LOGI(kTag,
                     "Last setup client left; captive HTTP sessions reset");
        }
    } else if (event_base == WIFI_EVENT &&
               event_id == WIFI_EVENT_STA_CONNECTED) {
        manager->station_associated_ = true;
    } else if (event_base == WIFI_EVENT &&
               event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const bool was_connected = manager->station_connected_;
        manager->station_connected_ = false;
        manager->station_associated_ = false;
        const auto* disconnected =
            static_cast<const wifi_event_sta_disconnected_t*>(event_data);
        if (was_connected ||
            (manager->station_connecting_ &&
             manager->candidate_in_flight_ &&
             !manager->ignore_disconnect_until_scan_)) {
            manager->disconnect_count_.fetch_add(1,
                                                  std::memory_order_relaxed);
            manager->last_disconnect_reason_.store(
                disconnected == nullptr ? 0U
                                        : static_cast<std::uint32_t>(
                                              disconnected->reason),
                std::memory_order_relaxed);
        }
        ESP_LOGW(kTag, "Station disconnected from '%s' reason=%u",
                 manager->connecting_ssid_[0] == '\0'
                     ? "saved network"
                     : manager->connecting_ssid_,
                 disconnected == nullptr ? 0U
                                         : static_cast<unsigned>(disconnected->reason));
        if (manager->provisioning_handoff_ && !was_connected &&
            manager->station_connecting_ && manager->candidate_in_flight_ &&
            !manager->ignore_disconnect_until_scan_ &&
            disconnected != nullptr) {
            const auto reason =
                static_cast<wifi_err_reason_t>(disconnected->reason);
            ProvisioningFailure failure = ProvisioningFailure::kNone;
            if (reason == WIFI_REASON_AUTH_FAIL ||
                reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
                reason == WIFI_REASON_HANDSHAKE_TIMEOUT) {
                failure = ProvisioningFailure::kAuthenticationFailed;
            } else if (reason == WIFI_REASON_NO_AP_FOUND ||
                       reason == WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY ||
                       reason == WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD ||
                       reason == WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD) {
                failure = ProvisioningFailure::kNetworkNotFound;
            }
            if (failure != ProvisioningFailure::kNone) {
                manager->provisioning_status_.RememberFailure(
                    manager->provisioning_connection_attempt_id_, failure);
            }
        }
        if (was_connected) {
            manager->station_connecting_ = false;
            manager->candidate_in_flight_ = false;
            manager->scan_pending_ = false;
            esp_timer_stop(manager->connect_timer_);
            esp_timer_stop(manager->retry_timer_);
            esp_timer_stop(manager->provisioning_cleanup_timer_);
            if (manager->provisioning_handoff_) {
                manager->provisioning_status_.MarkFailed(
                    manager->provisioning_connection_attempt_id_,
                    ProvisioningFailure::kUnknown);
            }
            manager->Emit(WifiManagerEvent::kDisconnected);
        } else if (manager->station_connecting_ &&
                   manager->candidate_in_flight_ &&
                   !manager->ignore_disconnect_until_scan_) {
            if (manager->candidate_reconnect_count_ <
                kMaxCandidateReconnects) {
                ++manager->candidate_reconnect_count_;
                manager->reconnect_attempt_count_.fetch_add(
                    1, std::memory_order_relaxed);
                const esp_err_t reconnect_error = esp_wifi_connect();
                if (reconnect_error == ESP_OK ||
                    reconnect_error == ESP_ERR_WIFI_CONN) {
                    ESP_LOGI(kTag,
                             "Retrying saved SSID '%s' attempt=%u/%u",
                             manager->connecting_ssid_,
                             static_cast<unsigned>(
                                 manager->candidate_reconnect_count_),
                             static_cast<unsigned>(kMaxCandidateReconnects));
                    return;
                }
                ESP_LOGW(kTag, "Unable to retry SSID '%s': %s",
                         manager->connecting_ssid_,
                         esp_err_to_name(reconnect_error));
            }
            manager->candidate_in_flight_ = false;
            manager->ConnectNextCandidate();
        }
    } else if (event_base == IP_EVENT &&
               event_id == IP_EVENT_ASSIGNED_IP_TO_CLIENT) {
        const auto* assigned =
            static_cast<const ip_event_assigned_ip_to_client_t*>(event_data);
        if (assigned != nullptr) {
            ESP_LOGI(kTag, "Setup client " MACSTR " received " IPSTR,
                     MAC2STR(assigned->mac), IP2STR(&assigned->ip));
            manager->portal_.NotifyClientNetworkReady();
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        manager->station_connecting_ = false;
        manager->station_connected_ = true;
        manager->station_associated_ = true;
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
        if (manager->provisioning_handoff_ &&
            manager->provisioning_status_.MarkConnected(
                manager->provisioning_connection_attempt_id_)) {
            esp_timer_stop(manager->provisioning_cleanup_timer_);
            const esp_err_t cleanup_error = esp_timer_start_once(
                manager->provisioning_cleanup_timer_,
                kProvisioningFallbackCleanupDelayUs);
            if (cleanup_error != ESP_OK) {
                ESP_LOGW(kTag, "Unable to schedule setup AP cleanup: %s",
                         esp_err_to_name(cleanup_error));
            }
        }
        manager->Emit(WifiManagerEvent::kConnected);
    }
}

void WifiManager::ConnectionTimeout(void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (!manager->station_connected_) {
        if (manager->provisioning_handoff_) {
            const auto snapshot = manager->provisioning_status_.Snapshot();
            const ProvisioningFailure remembered_failure =
                snapshot.attempt_id ==
                            manager->provisioning_connection_attempt_id_
                    ? snapshot.failure
                    : ProvisioningFailure::kNone;
            const ProvisioningFailure failure = ClassifyProvisioningTimeout(
                manager->station_associated_, remembered_failure);
            manager->provisioning_status_.MarkFailed(
                manager->provisioning_connection_attempt_id_, failure);
        }
        manager->station_connecting_ = false;
        manager->station_associated_ = false;
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

void WifiManager::ProvisioningTransition(void* context) {
    static_cast<WifiManager*>(context)->Emit(
        WifiManagerEvent::kProvisioningSaved);
}

void WifiManager::ProvisioningCleanup(void* context) {
    static_cast<WifiManager*>(context)->Emit(
        WifiManagerEvent::kProvisioningCleanup);
}

void WifiManager::RetryProvisioningCleanup() {
    if (!provisioning_handoff_ || !station_connected_) return;
    esp_timer_stop(provisioning_cleanup_timer_);
    const esp_err_t error = esp_timer_start_once(
        provisioning_cleanup_timer_, kProvisioningObservedCleanupDelayUs);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to retry setup AP cleanup: %s",
                 esp_err_to_name(error));
    }
}

esp_err_t WifiManager::FinishProvisioningHandoff() {
    if (!provisioning_handoff_ || !station_connected_) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t error = esp_wifi_set_mode(WIFI_MODE_STA);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to disable setup AP after DHCP success: %s",
                 esp_err_to_name(error));
        RetryProvisioningCleanup();
        return error;
    }
    portal_.Stop();
    provisioning_active_ = false;
    provisioning_wifi_ready_ = false;
    provisioning_handoff_ = false;
    provisioning_connection_attempt_id_ = 0;
    provisioning_status_.Reset();
    if (ap_netif_ != nullptr) {
        esp_netif_destroy_default_wifi(ap_netif_);
        ap_netif_ = nullptr;
    }
    ESP_LOGI(kTag, "Setup AP closed after station DHCP success");
    return ESP_OK;
}

ProvisioningStatusSnapshot WifiManager::ReadProvisioningStatus(void* context) {
    return static_cast<WifiManager*>(context)->provisioning_status_.Snapshot();
}

bool WifiManager::CanSaveProvisioning(void* context) {
    return context != nullptr &&
           static_cast<const WifiManager*>(context)
               ->provisioning_status_.CanBeginAttempt();
}

void WifiManager::ObserveProvisioningSuccess(std::uint32_t attempt_id,
                                             void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    const auto snapshot = manager->provisioning_status_.Snapshot();
    if (!manager->provisioning_handoff_ || snapshot.attempt_id != attempt_id ||
        snapshot.phase != ProvisioningPhase::kConnected) {
        return;
    }
    esp_timer_stop(manager->provisioning_cleanup_timer_);
    const esp_err_t error = esp_timer_start_once(
        manager->provisioning_cleanup_timer_,
        kProvisioningObservedCleanupDelayUs);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to shorten setup AP cleanup delay: %s",
                 esp_err_to_name(error));
    }
}

esp_err_t WifiManager::SaveProvisioning(settings::DeviceSettings* settings,
                                        void* context) {
    auto* manager = static_cast<WifiManager*>(context);
    if (settings == nullptr) return ESP_ERR_INVALID_ARG;
    const esp_err_t error = manager->store_->SaveProvisioning(settings);
    if (error == ESP_OK) {
        *manager->settings_ = *settings;
        const std::uint32_t attempt_id =
            manager->provisioning_status_.BeginAttempt();
        manager->provisioning_status_.MarkSaved(attempt_id);
        esp_timer_stop(manager->provisioning_timer_);
        const esp_err_t timer_error = esp_timer_start_once(
            manager->provisioning_timer_, kProvisioningTransitionDelayUs);
        if (timer_error != ESP_OK) {
            ESP_LOGW(kTag,
                     "Unable to delay provisioning transition: %s",
                     esp_err_to_name(timer_error));
            manager->Emit(WifiManagerEvent::kProvisioningSaved);
        }
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

esp_err_t WifiManager::ConfigureCaptivePortalDhcp() {
    if (ap_netif_ == nullptr) return ESP_ERR_INVALID_STATE;

    esp_netif_ip_info_t ip_info = {};
    IP4_ADDR(&ip_info.ip, 192, 168, 4, 1);
    IP4_ADDR(&ip_info.gw, 192, 168, 4, 1);
    IP4_ADDR(&ip_info.netmask, 255, 255, 255, 0);

    const esp_err_t stop_error = esp_netif_dhcps_stop(ap_netif_);
    if (stop_error != ESP_OK &&
        stop_error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        return stop_error;
    }

    esp_err_t error = esp_netif_set_ip_info(ap_netif_, &ip_info);
    std::uint8_t offer_dns = 1;
    if (error == ESP_OK) {
        error = esp_netif_dhcps_option(ap_netif_, ESP_NETIF_OP_SET,
                                   ESP_NETIF_DOMAIN_NAME_SERVER, &offer_dns,
                                   sizeof(offer_dns));
    }
    if (error == ESP_OK) {
        esp_netif_dns_info_t dns = {};
        dns.ip.type = ESP_IPADDR_TYPE_V4;
        dns.ip.u_addr.ip4.addr = ip_info.ip.addr;
        error = esp_netif_set_dns_info(ap_netif_, ESP_NETIF_DNS_MAIN, &dns);
    }
    const esp_err_t start_error = esp_netif_dhcps_start(ap_netif_);
    if (error == ESP_OK && start_error != ESP_OK &&
        start_error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) {
        error = start_error;
    }
    if (error == ESP_OK) {
        ESP_LOGI(kTag,
                 "DHCP advertises captive DNS at 192.168.4.1; probe URLs redirect to the portal");
    }
    return error;
}

esp_err_t WifiManager::WaitForCaptivePortalDhcp() {
    for (std::uint32_t elapsed = 0; elapsed < kDhcpReadyTimeoutMs;
         elapsed += kDhcpReadyPollMs) {
        esp_netif_dhcp_status_t status = ESP_NETIF_DHCP_INIT;
        const esp_err_t error = esp_netif_dhcps_get_status(ap_netif_, &status);
        if (error != ESP_OK) return error;
        if (status == ESP_NETIF_DHCP_STARTED) {
            ESP_LOGI(kTag, "DHCP server ready before captive services start");
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(kDhcpReadyPollMs));
    }
    ESP_LOGE(kTag, "DHCP server did not become ready within %u ms",
             static_cast<unsigned>(kDhcpReadyTimeoutMs));
    return ESP_ERR_TIMEOUT;
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
    candidate_reconnect_count_ = 0;
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
    if (esp_wifi_scan_get_ap_records(&count, station_scan_records_.data()) !=
        ESP_OK) {
        count = 0;
    }

    for (std::size_t index = 0; index < count; ++index) {
        std::snprintf(visible_networks_[index].ssid,
                      sizeof(visible_networks_[index].ssid), "%s",
                      reinterpret_cast<const char*>(
                          station_scan_records_[index].ssid));
        visible_networks_[index].rssi = station_scan_records_[index].rssi;
    }
    const WifiCandidateOrder order =
        BuildWifiCandidateOrder(profiles_, visible_networks_.data(), count);
    candidate_count_ = order.count;
    candidate_cursor_ = 0;
    candidate_reconnect_count_ = 0;
    for (std::size_t index = 0; index < order.count; ++index) {
        CandidateTarget target{
            .profile_index = order.profile_indices[index],
        };
        const char* ssid = profiles_.profiles[target.profile_index].ssid;
        const wifi_ap_record_t* strongest = nullptr;
        for (std::size_t record_index = 0; record_index < count; ++record_index) {
            if (std::strcmp(ssid, reinterpret_cast<const char*>(
                                      station_scan_records_[record_index].ssid)) == 0 &&
                (strongest == nullptr ||
                 station_scan_records_[record_index].rssi > strongest->rssi)) {
                strongest = &station_scan_records_[record_index];
            }
        }
        if (strongest != nullptr) {
            target.visible = true;
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
        config.sta.failure_retry_cnt = 3;
        config.sta.listen_interval = 10;

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
            candidate_reconnect_count_ = 0;
            ESP_LOGI(kTag, "Connecting to saved SSID '%s' candidate=%u/%u%s",
                     connecting_ssid_,
                     static_cast<unsigned>(candidate_cursor_),
                     static_cast<unsigned>(candidate_count_),
                     target.visible ? " (visible AP, roaming enabled)"
                                    : " (hidden/not visible)");
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
    candidate_reconnect_count_ = 0;
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

WifiHealth WifiManager::Health() const {
    WifiHealth health{
        .disconnect_count =
            disconnect_count_.load(std::memory_order_relaxed),
        .reconnect_attempt_count =
            reconnect_attempt_count_.load(std::memory_order_relaxed),
        .last_disconnect_reason =
            last_disconnect_reason_.load(std::memory_order_relaxed),
    };
    wifi_ap_record_t access_point{};
    if (esp_wifi_sta_get_ap_info(&access_point) == ESP_OK) {
        health.connected = true;
        health.rssi = access_point.rssi;
    }
    if (health.connected && station_netif_ != nullptr) {
        esp_netif_ip_info_t ip_info{};
        if (esp_netif_get_ip_info(station_netif_, &ip_info) == ESP_OK) {
            std::snprintf(health.ipv4, sizeof(health.ipv4), IPSTR,
                          IP2STR(&ip_info.ip));
        }
    }
    return health;
}

}  // namespace veetee::network
