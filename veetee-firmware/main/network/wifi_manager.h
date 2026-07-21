#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "network/provisioning_portal.h"
#include "network/wifi_candidate_order.h"
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
    static constexpr std::size_t kMaxStationScanResults = 32;
    static constexpr std::uint8_t kMaxCandidateReconnects = 5;

    static void EventHandler(void* context, esp_event_base_t event_base,
                             std::int32_t event_id, void* event_data);
    static void ConnectionTimeout(void* context);
    static void RetryScan(void* context);
    static void ProvisioningTransition(void* context);
    static esp_err_t SaveProvisioning(settings::DeviceSettings* settings,
                                      void* context);

    void Emit(WifiManagerEvent event) const;
    esp_err_t EnsureWifiStarted();
    esp_err_t ConfigureCaptivePortalDhcp();
    esp_err_t BeginScan();
    void BuildCandidateQueue();
    void ConnectNextCandidate();
    void ScheduleScanRetry();

    struct CandidateTarget {
        std::size_t profile_index = 0;
        bool visible = false;
    };

    settings::SettingsStore* store_ = nullptr;
    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    esp_netif_t* station_netif_ = nullptr;
    esp_netif_t* ap_netif_ = nullptr;
    esp_event_handler_instance_t wifi_handler_ = nullptr;
    esp_event_handler_instance_t ip_handler_ = nullptr;
    esp_timer_handle_t connect_timer_ = nullptr;
    esp_timer_handle_t retry_timer_ = nullptr;
    esp_timer_handle_t provisioning_timer_ = nullptr;
    ProvisioningPortal portal_;
    settings::WifiProfileRecord profiles_{};
    std::array<wifi_ap_record_t, kMaxStationScanResults> station_scan_records_{};
    std::array<VisibleWifiNetwork, kMaxStationScanResults> visible_networks_{};
    std::array<CandidateTarget, settings::kMaxWifiProfiles> candidates_{};
    std::size_t candidate_count_ = 0;
    std::size_t candidate_cursor_ = 0;
    std::uint8_t candidate_reconnect_count_ = 0;
    bool wifi_started_ = false;
    bool station_connecting_ = false;
    bool station_connected_ = false;
    bool candidate_in_flight_ = false;
    bool scan_pending_ = false;
    bool ignore_disconnect_until_scan_ = false;
    bool provisioning_active_ = false;
    char connecting_ssid_[33] = {};
    char ap_ssid_[24] = {};
};

}  // namespace veetee::network
