#pragma once

#include <array>
#include <atomic>
#include <cstdint>

#include "esp_err.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "esp_wifi_types_generic.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "settings/settings_store.h"

namespace veetee::network {

class ProvisioningPortal {
public:
    using SaveSink = esp_err_t (*)(settings::DeviceSettings* settings, void* context);

    esp_err_t Start(std::uint32_t ap_address, const settings::DeviceSettings& current,
                    const settings::WifiProfileRecord& wifi_profiles,
                    SaveSink sink, void* context);
    void Stop();
    void ResetClientSessions();
    bool IsRunning() const;

private:
    static esp_err_t PortalHandler(httpd_req_t* request);
    static esp_err_t StyleHandler(httpd_req_t* request);
    static esp_err_t UiScriptHandler(httpd_req_t* request);
    static esp_err_t ScriptHandler(httpd_req_t* request);
    static esp_err_t FaviconHandler(httpd_req_t* request);
    static esp_err_t ScanHandler(httpd_req_t* request);
    static esp_err_t ConfigHandler(httpd_req_t* request);
    static esp_err_t SaveHandler(httpd_req_t* request);
    static esp_err_t CaptivePortalHandler(httpd_req_t* request);
    static void DnsTaskEntry(void* context);
    static void ScanEventHandler(void* context, esp_event_base_t event_base,
                                 std::int32_t event_id, void* event_data);
    static void ScanTimer(void* context);

    esp_err_t HandleSave(httpd_req_t* request);
    void StartScan();
    void RunDnsServer();

    httpd_handle_t http_server_ = nullptr;
    TaskHandle_t dns_task_ = nullptr;
    SemaphoreHandle_t dns_stopped_ = nullptr;
    SemaphoreHandle_t scan_mutex_ = nullptr;
    esp_event_handler_instance_t scan_handler_ = nullptr;
    esp_timer_handle_t scan_timer_ = nullptr;
    std::atomic<bool> dns_running_{false};
    std::atomic<int> dns_socket_{-1};
    std::atomic<bool> scan_in_progress_{false};
    std::array<wifi_ap_record_t, 16> scan_records_{};
    std::uint16_t scan_count_ = 0;
    std::uint32_t ap_address_ = 0;
    settings::DeviceSettings current_{};
    settings::WifiProfileRecord wifi_profiles_{};
    SaveSink save_sink_ = nullptr;
    void* save_context_ = nullptr;
    bool running_ = false;
};

}  // namespace veetee::network
