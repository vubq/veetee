#pragma once

#include <atomic>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "settings/settings_store.h"

namespace veetee::network {

class ProvisioningPortal {
public:
    using SaveSink = esp_err_t (*)(const settings::DeviceSettings& settings, void* context);

    esp_err_t Start(std::uint32_t ap_address, const settings::DeviceSettings& current,
                    SaveSink sink, void* context);
    void Stop();

private:
    static esp_err_t PortalHandler(httpd_req_t* request);
    static esp_err_t ScanHandler(httpd_req_t* request);
    static esp_err_t SaveHandler(httpd_req_t* request);
    static void DnsTaskEntry(void* context);

    esp_err_t HandleSave(httpd_req_t* request);
    void RunDnsServer();

    httpd_handle_t http_server_ = nullptr;
    TaskHandle_t dns_task_ = nullptr;
    SemaphoreHandle_t dns_stopped_ = nullptr;
    std::atomic<bool> dns_running_{false};
    std::atomic<int> dns_socket_{-1};
    std::uint32_t ap_address_ = 0;
    settings::DeviceSettings current_{};
    SaveSink save_sink_ = nullptr;
    void* save_context_ = nullptr;
};

}  // namespace veetee::network
