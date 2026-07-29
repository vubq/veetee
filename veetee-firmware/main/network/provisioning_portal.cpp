#include "network/provisioning_portal.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "network/captive_portal_routes.h"
#include "network/endpoint_url.h"

namespace veetee::network {
namespace {

constexpr char kTag[] = "veetee_portal";
constexpr std::size_t kMaxPostBytes = 1024;
constexpr std::size_t kMaxScanResults = 16;
constexpr std::size_t kHttpServerStackBytes = 12 * 1024;
constexpr std::size_t kStaticResponseChunkBytes = 1024;
constexpr std::uint64_t kScanRetryIntervalUs = 1500000ULL;
extern const std::uint8_t kPortalHtmlStart[]
    asm("_binary_index_html_start");
extern const std::uint8_t kPortalHtmlEnd[]
    asm("_binary_index_html_end");
extern const std::uint8_t kPortalCssStart[]
    asm("_binary_portal_css_start");
extern const std::uint8_t kPortalCssEnd[]
    asm("_binary_portal_css_end");
extern const std::uint8_t kPortalEnglishScriptStart[]
    asm("_binary_portal_en_js_start");
extern const std::uint8_t kPortalEnglishScriptEnd[]
    asm("_binary_portal_en_js_end");
extern const std::uint8_t kPortalI18nScriptStart[]
    asm("_binary_portal_i18n_js_start");
extern const std::uint8_t kPortalI18nScriptEnd[]
    asm("_binary_portal_i18n_js_end");
extern const std::uint8_t kPortalUiScriptStart[]
    asm("_binary_portal_ui_js_start");
extern const std::uint8_t kPortalUiScriptEnd[]
    asm("_binary_portal_ui_js_end");
extern const std::uint8_t kPortalScriptStart[]
    asm("_binary_portal_js_start");
extern const std::uint8_t kPortalScriptEnd[]
    asm("_binary_portal_js_end");

struct EmbeddedText {
    const std::uint8_t* data;
    std::size_t length;
};

EmbeddedText TextResource(const std::uint8_t* start,
                          const std::uint8_t* end) {
    std::size_t length = static_cast<std::size_t>(end - start);
    if (length > 0 && start[length - 1] == 0) --length;
    return {.data = start, .length = length};
}

esp_err_t SendStatic(httpd_req_t* request, const char* content_type,
                     EmbeddedText content) {
    const std::size_t content_length = content.length;
    ESP_LOGI(kTag, "HTTP GET %s bytes=%u", request->uri,
             static_cast<unsigned>(content_length));
    httpd_resp_set_type(request, content_type);
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    for (std::size_t offset = 0; offset < content_length;
         offset += kStaticResponseChunkBytes) {
        const std::size_t chunk_length =
            std::min(kStaticResponseChunkBytes, content_length - offset);
        const esp_err_t error = httpd_resp_send_chunk(
            request, reinterpret_cast<const char*>(content.data + offset),
            static_cast<ssize_t>(chunk_length));
        if (error != ESP_OK) {
            ESP_LOGW(kTag, "HTTP send %s failed at %u/%u: %s", request->uri,
                     static_cast<unsigned>(offset),
                     static_cast<unsigned>(content_length),
                     esp_err_to_name(error));
            return error;
        }
    }
    const esp_err_t error = httpd_resp_send_chunk(request, nullptr, 0);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "HTTP send %s failed while finishing: %s", request->uri,
                 esp_err_to_name(error));
    }
    return error;
}

int HexValue(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    value = static_cast<char>(std::tolower(static_cast<unsigned char>(value)));
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    return -1;
}

bool AttemptIdFromRequest(httpd_req_t* request,
                          std::uint32_t* attempt_id) {
    if (request == nullptr || attempt_id == nullptr) return false;
    const int query_length = httpd_req_get_url_query_len(request);
    if (query_length <= 0 || query_length >= 64) return false;
    std::array<char, 64> query{};
    std::array<char, 16> value{};
    if (httpd_req_get_url_query_str(request, query.data(), query.size()) !=
            ESP_OK ||
        httpd_query_key_value(query.data(), "attempt_id", value.data(),
                              value.size()) != ESP_OK) {
        return false;
    }
    char* end = nullptr;
    const unsigned long parsed = std::strtoul(value.data(), &end, 10);
    if (end == value.data() || *end != '\0' || parsed == 0 ||
        parsed > std::numeric_limits<std::uint32_t>::max()) {
        return false;
    }
    *attempt_id = static_cast<std::uint32_t>(parsed);
    return true;
}

bool UrlDecode(const char* source, char* destination, std::size_t capacity) {
    if (source == nullptr || destination == nullptr || capacity == 0) return false;
    std::size_t written = 0;
    for (std::size_t index = 0; source[index] != '\0'; ++index) {
        if (written + 1 >= capacity) return false;
        if (source[index] == '+') {
            destination[written++] = ' ';
        } else if (source[index] == '%' && source[index + 1] != '\0' &&
                   source[index + 2] != '\0') {
            const int high = HexValue(source[index + 1]);
            const int low = HexValue(source[index + 2]);
            if (high < 0 || low < 0) return false;
            destination[written++] = static_cast<char>((high << 4) | low);
            index += 2;
        } else {
            destination[written++] = source[index];
        }
    }
    destination[written] = '\0';
    return true;
}

bool FormValue(const char* body, const char* key, char* destination, std::size_t capacity,
               bool required) {
    std::array<char, 513> encoded{};
    const esp_err_t error = httpd_query_key_value(body, key, encoded.data(), encoded.size());
    if (error != ESP_OK) {
        destination[0] = '\0';
        return !required;
    }
    return UrlDecode(encoded.data(), destination, capacity) &&
           (!required || destination[0] != '\0');
}

void JsonEscapeString(const char* source, char* destination, std::size_t capacity) {
    std::size_t written = 0;
    for (std::size_t index = 0; source[index] != 0 && written + 1 < capacity; ++index) {
        const unsigned char value = static_cast<unsigned char>(source[index]);
        if ((value == '"' || value == '\\') && written + 2 < capacity) {
            destination[written++] = '\\';
            destination[written++] = static_cast<char>(value);
        } else if (value >= 0x20) {
            destination[written++] = static_cast<char>(value);
        }
    }
    destination[written] = '\0';
}

}  // namespace

esp_err_t ProvisioningPortal::Start(std::uint32_t ap_address,
                                    const settings::DeviceSettings& current,
                                    const settings::WifiProfileRecord& wifi_profiles,
                                    SaveSink save_sink, StatusSink status_sink,
                                    SuccessObservedSink observed_sink,
                                    SaveAllowedSink save_allowed_sink,
                                    void* context) {
    if (IsRunning()) {
        ap_address_ = ap_address;
        current_ = current;
        wifi_profiles_ = wifi_profiles;
        save_sink_ = save_sink;
        status_sink_ = status_sink;
        observed_sink_ = observed_sink;
        save_allowed_sink_ = save_allowed_sink;
        save_context_ = context;
        ESP_LOGI(kTag, "Captive portal already running; refreshed setup context");
        return ESP_OK;
    }
    Stop();
    ap_address_ = ap_address;
    current_ = current;
    wifi_profiles_ = wifi_profiles;
    save_sink_ = save_sink;
    status_sink_ = status_sink;
    observed_sink_ = observed_sink;
    save_allowed_sink_ = save_allowed_sink;
    save_context_ = context;
    client_network_ready_.store(false);

    esp_err_t error = EnsureSaveTask();
    if (error != ESP_OK) {
        Stop();
        return error;
    }

    scan_mutex_ = xSemaphoreCreateMutex();
    if (scan_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    error = esp_event_handler_instance_register(
        WIFI_EVENT, WIFI_EVENT_SCAN_DONE, &ProvisioningPortal::ScanEventHandler,
        this, &scan_handler_);
    if (error != ESP_OK) {
        Stop();
        return error;
    }
    const esp_timer_create_args_t scan_timer_config = {
        .callback = &ProvisioningPortal::ScanTimer,
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "veetee_ap_scan",
        .skip_unhandled_events = false,
    };
    error = esp_timer_create(&scan_timer_config, &scan_timer_);
    if (error != ESP_OK) {
        Stop();
        return error;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 20;
    config.uri_match_fn = httpd_uri_match_wildcard;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 15;
    config.send_wait_timeout = 15;
    // ESP-IDF 6's HTTP send path plus the bounded scan/form handlers exceed the
    // 4 KiB default on ESP32-S3, especially inside iOS captive webviews.
    config.stack_size = kHttpServerStackBytes;
    // The N16R8 target has ample PSRAM while audio and WakeNet intentionally
    // reserve internal RAM. Keeping the portal stack external avoids an
    // ESP-IDF 6.0.2 failure path that leaves port 80 bound if task creation
    // runs out of contiguous internal memory.
    config.task_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    error = httpd_start(&http_server_, &config);
    if (error != ESP_OK) {
        Stop();
        return error;
    }

    httpd_uri_t scan = {};
    scan.uri = "/api/scan";
    scan.method = HTTP_GET;
    scan.handler = &ProvisioningPortal::ScanHandler;
    scan.user_ctx = this;
    httpd_uri_t config_uri = {};
    config_uri.uri = "/api/config";
    config_uri.method = HTTP_GET;
    config_uri.handler = &ProvisioningPortal::ConfigHandler;
    config_uri.user_ctx = this;
    httpd_uri_t status = {};
    status.uri = "/api/status";
    status.method = HTTP_GET;
    status.handler = &ProvisioningPortal::StatusHandler;
    status.user_ctx = this;
    httpd_uri_t save = {};
    save.uri = "/api/provision";
    save.method = HTTP_POST;
    save.handler = &ProvisioningPortal::SaveHandler;
    save.user_ctx = this;
    httpd_uri_t portal = {};
    portal.uri = "/";
    portal.method = HTTP_GET;
    portal.handler = &ProvisioningPortal::PortalHandler;
    portal.user_ctx = this;
    httpd_uri_t style = {};
    style.uri = "/portal.css";
    style.method = HTTP_GET;
    style.handler = &ProvisioningPortal::StyleHandler;
    style.user_ctx = this;
    httpd_uri_t english_script = {};
    english_script.uri = "/portal-en.js";
    english_script.method = HTTP_GET;
    english_script.handler = &ProvisioningPortal::EnglishScriptHandler;
    english_script.user_ctx = this;
    httpd_uri_t i18n_script = {};
    i18n_script.uri = "/portal-i18n.js";
    i18n_script.method = HTTP_GET;
    i18n_script.handler = &ProvisioningPortal::I18nScriptHandler;
    i18n_script.user_ctx = this;
    httpd_uri_t ui_script = {};
    ui_script.uri = "/portal-ui.js";
    ui_script.method = HTTP_GET;
    ui_script.handler = &ProvisioningPortal::UiScriptHandler;
    ui_script.user_ctx = this;
    httpd_uri_t script = {};
    script.uri = "/portal.js";
    script.method = HTTP_GET;
    script.handler = &ProvisioningPortal::ScriptHandler;
    script.user_ctx = this;
    httpd_uri_t favicon = {};
    favicon.uri = "/favicon.ico";
    favicon.method = HTTP_GET;
    favicon.handler = &ProvisioningPortal::FaviconHandler;
    favicon.user_ctx = this;
    if ((error = httpd_register_uri_handler(http_server_, &scan)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &config_uri)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &status)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &save)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &portal)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &style)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &english_script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &i18n_script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &ui_script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &favicon)) != ESP_OK) {
        Stop();
        return error;
    }
    httpd_uri_t captive = {};
    captive.uri = "/*";
    captive.method = HTTP_GET;
    captive.handler = &ProvisioningPortal::CaptivePortalHandler;
    captive.user_ctx = this;
    error = httpd_register_uri_handler(http_server_, &captive);
    if (error != ESP_OK) {
        Stop();
        return error;
    }
    dns_running_.store(true);
    dns_stopped_ = xSemaphoreCreateBinary();
    if (dns_stopped_ == nullptr) {
        Stop();
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(&ProvisioningPortal::DnsTaskEntry, "veetee_dns", 3072, this, 3,
                    &dns_task_) != pdPASS) {
        dns_running_.store(false);
        vSemaphoreDelete(dns_stopped_);
        dns_stopped_ = nullptr;
        Stop();
        return ESP_ERR_NO_MEM;
    }
    running_ = true;
    ESP_LOGI(kTag,
             "Captive portal started at http://192.168.4.1; Wi-Fi scan waits for a DHCP client");
    return ESP_OK;
}

void ProvisioningPortal::Stop() {
    running_ = false;
    client_network_ready_.store(false);
    if (scan_timer_ != nullptr) {
        esp_timer_stop(scan_timer_);
        esp_timer_delete(scan_timer_);
        scan_timer_ = nullptr;
    }
    if (scan_handler_ != nullptr) {
        esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_SCAN_DONE,
                                              scan_handler_);
        scan_handler_ = nullptr;
    }
    scan_in_progress_.store(false);
    if (scan_mutex_ != nullptr) {
        vSemaphoreDelete(scan_mutex_);
        scan_mutex_ = nullptr;
    }
    if (http_server_ != nullptr) {
        const esp_err_t error = httpd_stop(http_server_);
        if (error != ESP_OK) {
            ESP_LOGW(kTag, "Unable to stop captive HTTP server cleanly: %s",
                     esp_err_to_name(error));
        }
        http_server_ = nullptr;
    }
    dns_running_.store(false);
    const int dns_socket = dns_socket_.load();
    if (dns_socket >= 0) {
        shutdown(dns_socket, SHUT_RDWR);
    }
    if (dns_stopped_ != nullptr) {
        if (xSemaphoreTake(dns_stopped_, pdMS_TO_TICKS(1500)) != pdTRUE) {
            ESP_LOGW(kTag, "Captive DNS task did not stop before deadline");
            if (dns_task_ != nullptr) {
                vTaskDelete(dns_task_);
                dns_task_ = nullptr;
            }
            const int stale_socket = dns_socket_.exchange(-1);
            if (stale_socket >= 0) close(stale_socket);
        }
        vSemaphoreDelete(dns_stopped_);
        dns_stopped_ = nullptr;
    }
}

bool ProvisioningPortal::IsRunning() const {
    return running_ && http_server_ != nullptr;
}

void ProvisioningPortal::NotifyClientNetworkReady() {
    client_network_ready_.store(true);
}

void ProvisioningPortal::PauseScan() {
    client_network_ready_.store(false);
    if (scan_in_progress_.exchange(false)) {
        const esp_err_t error = esp_wifi_scan_stop();
        if (error != ESP_OK && error != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGD(kTag, "Stopping captive scan returned %s",
                     esp_err_to_name(error));
        }
    }
}

void ProvisioningPortal::ResetClientSessions() {
    PauseScan();
    if (http_server_ == nullptr) return;
    std::array<int, 8> client_sockets{};
    std::size_t count = client_sockets.size();
    if (httpd_get_client_list(http_server_, &count, client_sockets.data()) !=
        ESP_OK) {
        return;
    }
    for (std::size_t index = 0; index < count; ++index) {
        httpd_sess_trigger_close(http_server_, client_sockets[index]);
    }
    if (count > 0) {
        ESP_LOGI(kTag, "Closed %u stale captive HTTP session(s)",
                 static_cast<unsigned>(count));
    }
}

esp_err_t ProvisioningPortal::PortalHandler(httpd_req_t* request) {
    return SendStatic(request, "text/html; charset=utf-8",
                      TextResource(kPortalHtmlStart, kPortalHtmlEnd));
}

esp_err_t ProvisioningPortal::StyleHandler(httpd_req_t* request) {
    return SendStatic(request, "text/css; charset=utf-8",
                      TextResource(kPortalCssStart, kPortalCssEnd));
}

esp_err_t ProvisioningPortal::EnglishScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      TextResource(kPortalEnglishScriptStart,
                                   kPortalEnglishScriptEnd));
}

esp_err_t ProvisioningPortal::I18nScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      TextResource(kPortalI18nScriptStart,
                                   kPortalI18nScriptEnd));
}

esp_err_t ProvisioningPortal::UiScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      TextResource(kPortalUiScriptStart, kPortalUiScriptEnd));
}

esp_err_t ProvisioningPortal::ScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      TextResource(kPortalScriptStart, kPortalScriptEnd));
}

esp_err_t ProvisioningPortal::FaviconHandler(httpd_req_t* request) {
    ESP_LOGI(kTag, "HTTP GET %s -> 204", request->uri);
    httpd_resp_set_status(request, "204 No Content");
    httpd_resp_set_hdr(request, "Cache-Control", "public, max-age=86400");
    httpd_resp_set_hdr(request, "Connection", "close");
    return httpd_resp_send(request, nullptr, 0);
}

esp_err_t ProvisioningPortal::CaptivePortalHandler(httpd_req_t* request) {
    if (!IsCaptivePortalProbePath(request->uri)) {
        ESP_LOGI(kTag, "HTTP GET %s -> 404", request->uri);
        httpd_resp_set_type(request, "text/plain; charset=utf-8");
        httpd_resp_set_status(request, "404 Not Found");
        httpd_resp_set_hdr(request, "Cache-Control", "no-store");
        httpd_resp_set_hdr(request, "Connection", "close");
        return httpd_resp_sendstr(request, "Not found");
    }
    char location[96] = {};
    std::snprintf(location, sizeof(location),
                  "http://192.168.4.1/?_=%llu",
                  static_cast<unsigned long long>(esp_timer_get_time()));
    ESP_LOGI(kTag, "Captive probe %s -> %s", request->uri, location);
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_set_status(request, "302 Found");
    httpd_resp_set_hdr(request, "Location", location);
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    // Apple captive webviews require response content to treat the network as
    // a portal instead of a temporarily broken Internet connection.
    return httpd_resp_sendstr(request, "Mở trang thiết lập Veetee...");
}

esp_err_t ProvisioningPortal::ScanHandler(httpd_req_t* request) {
    const auto* portal = static_cast<const ProvisioningPortal*>(request->user_ctx);
    std::uint16_t count = 0;
    std::array<wifi_ap_record_t, kMaxScanResults> records{};
    if (portal->scan_mutex_ != nullptr &&
        xSemaphoreTake(portal->scan_mutex_, pdMS_TO_TICKS(100)) == pdTRUE) {
        count = portal->scan_count_;
        std::copy_n(portal->scan_records_.begin(), count, records.begin());
        xSemaphoreGive(portal->scan_mutex_);
    }
    const ProvisioningStatusSnapshot status =
        portal->status_sink_ == nullptr
            ? ProvisioningStatusSnapshot{}
            : portal->status_sink_(portal->save_context_);
    if (count == 0 && status.phase != ProvisioningPhase::kConnecting) {
        const_cast<ProvisioningPortal*>(portal)->StartScan();
    }
    ESP_LOGI(kTag, "HTTP GET %s cached_networks=%u", request->uri,
             static_cast<unsigned>(count));
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    httpd_resp_sendstr_chunk(request, "[");
    std::uint16_t emitted = 0;
    for (std::uint16_t index = 0; index < count; ++index) {
        if (records[index].ssid[0] == 0) continue;
        bool duplicate = false;
        for (std::uint16_t previous = 0; previous < index; ++previous) {
            if (std::strcmp(reinterpret_cast<const char*>(records[index].ssid),
                            reinterpret_cast<const char*>(records[previous].ssid)) == 0) {
                duplicate = true;
                break;
            }
        }
        if (duplicate) continue;
        char ssid[129] = {};
        char item[256] = {};
        JsonEscapeString(reinterpret_cast<const char*>(records[index].ssid), ssid,
                         sizeof(ssid));
        std::snprintf(item, sizeof(item),
                      "%s{\"ssid\":\"%s\",\"rssi\":%d,\"channel\":%u,\"secure\":%s,\"saved\":%s}",
                      emitted == 0 ? "" : ",", ssid, records[index].rssi,
                      records[index].primary,
                      records[index].authmode == WIFI_AUTH_OPEN ? "false" : "true",
                      settings::FindWifiProfile(
                          portal->wifi_profiles_,
                          reinterpret_cast<const char*>(records[index].ssid)) == nullptr
                          ? "false"
                          : "true");
        httpd_resp_sendstr_chunk(request, item);
        ++emitted;
    }
    httpd_resp_sendstr_chunk(request, "]");
    return httpd_resp_sendstr_chunk(request, nullptr);
}

esp_err_t ProvisioningPortal::StatusHandler(httpd_req_t* request) {
    auto* portal = static_cast<ProvisioningPortal*>(request->user_ctx);
    const ProvisioningStatusSnapshot status =
        portal->status_sink_ == nullptr
            ? ProvisioningStatusSnapshot{}
            : portal->status_sink_(portal->save_context_);
    char response[kProvisioningStatusJsonBytes] = {};
    if (!SerializeProvisioningStatus(status, response, sizeof(response))) {
        httpd_resp_set_status(request, "500 Internal Server Error");
        return httpd_resp_sendstr(request,
                                  "{\"code\":\"status_serialize_failed\"}");
    }
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    const esp_err_t error = httpd_resp_sendstr(request, response);
    std::uint32_t requested_attempt = 0;
    if (error == ESP_OK && status.phase == ProvisioningPhase::kConnected &&
        AttemptIdFromRequest(request, &requested_attempt) &&
        requested_attempt == status.attempt_id &&
        portal->observed_sink_ != nullptr) {
        portal->observed_sink_(status.attempt_id, portal->save_context_);
    }
    return error;
}

esp_err_t ProvisioningPortal::ConfigHandler(httpd_req_t* request) {
    const auto* portal = static_cast<const ProvisioningPortal*>(request->user_ctx);
    ESP_LOGI(kTag, "HTTP GET %s", request->uri);
    char ssid[129] = {};
    char bootstrap_url[1025] = {};
    char locale[65] = {};
    char time_zone[129] = {};
    char wake_profile[257] = {};
    JsonEscapeString(portal->current_.ssid, ssid, sizeof(ssid));
    JsonEscapeString(portal->current_.bootstrap_url, bootstrap_url,
                     sizeof(bootstrap_url));
    JsonEscapeString(portal->current_.locale, locale, sizeof(locale));
    JsonEscapeString(portal->current_.time_zone, time_zone, sizeof(time_zone));
    JsonEscapeString(portal->current_.wake_profile, wake_profile,
                     sizeof(wake_profile));
    char response[1600] = {};
    std::snprintf(response, sizeof(response),
                  "{\"ssid\":\"%s\",\"bootstrap_url\":\"%s\",\"locale\":\"%s\",\"time_zone\":\"%s\",\"wake_profile\":\"%s\"}",
                  ssid, bootstrap_url, locale, time_zone, wake_profile);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    return httpd_resp_sendstr(request, response);
}

esp_err_t ProvisioningPortal::SaveHandler(httpd_req_t* request) {
    return static_cast<ProvisioningPortal*>(request->user_ctx)->HandleSave(request);
}

void ProvisioningPortal::SaveTaskEntry(void* context) {
    auto* portal = static_cast<ProvisioningPortal*>(context);
    for (;;) {
        xSemaphoreTake(portal->save_request_, portMAX_DELAY);
        portal->save_result_ =
            portal->save_sink_ == nullptr
                ? ESP_ERR_INVALID_STATE
                : portal->save_sink_(&portal->pending_save_,
                                     portal->save_context_);
        xSemaphoreGive(portal->save_complete_);
    }
}

esp_err_t ProvisioningPortal::EnsureSaveTask() {
    if (save_task_ != nullptr) return ESP_OK;

    save_request_ = xSemaphoreCreateBinaryStatic(&save_request_storage_);
    save_complete_ = xSemaphoreCreateBinaryStatic(&save_complete_storage_);
    if (save_request_ == nullptr || save_complete_ == nullptr) {
        save_request_ = nullptr;
        save_complete_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    save_task_ = xTaskCreateStatic(
        &ProvisioningPortal::SaveTaskEntry, "veetee_wifi_save",
        save_task_stack_.size(), this, 5, save_task_stack_.data(),
        &save_task_control_);
    if (save_task_ == nullptr) {
        save_request_ = nullptr;
        save_complete_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t ProvisioningPortal::SaveFromInternalRam(
    const settings::DeviceSettings& candidate) {
    if (save_task_ == nullptr || save_request_ == nullptr ||
        save_complete_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    pending_save_ = candidate;
    save_result_ = ESP_FAIL;
    xSemaphoreGive(save_request_);
    xSemaphoreTake(save_complete_, portMAX_DELAY);
    return save_result_;
}

esp_err_t ProvisioningPortal::HandleSave(httpd_req_t* request) {
    ESP_LOGI(kTag, "HTTP POST %s bytes=%d", request->uri,
             request->content_len);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    if (request->content_len <= 0 || request->content_len > kMaxPostBytes) {
        httpd_resp_set_status(request, "413 Payload Too Large");
        return httpd_resp_sendstr(
            request,
            "{\"code\":\"form_too_large\",\"message\":\"Kích thước biểu mẫu không hợp lệ\"}");
    }
    if (save_allowed_sink_ != nullptr &&
        !save_allowed_sink_(save_context_)) {
        httpd_resp_set_status(request, "409 Conflict");
        return httpd_resp_sendstr(
            request,
            "{\"code\":\"setup_busy\",\"message\":\"Veetee đang thử cấu hình Wi-Fi trước đó. Hãy chờ kết quả rồi thử lại.\"}");
    }

    std::array<char, kMaxPostBytes + 1> body{};
    int received = 0;
    while (received < request->content_len) {
        const int result = httpd_req_recv(request, body.data() + received,
                                          request->content_len - received);
        if (result <= 0) {
            httpd_resp_set_status(request, "408 Request Timeout");
            return httpd_resp_sendstr(
                request,
                "{\"code\":\"request_timeout\",\"message\":\"Request timed out\"}");
        }
        received += result;
    }
    body[received] = '\0';

    settings::DeviceSettings candidate = current_;
    const bool valid =
        FormValue(body.data(), "ssid", candidate.ssid, sizeof(candidate.ssid), true) &&
        FormValue(body.data(), "password", candidate.password, sizeof(candidate.password), false) &&
        FormValue(body.data(), "bootstrap_url", candidate.bootstrap_url,
                  sizeof(candidate.bootstrap_url), true) &&
        FormValue(body.data(), "locale", candidate.locale, sizeof(candidate.locale), true) &&
        FormValue(body.data(), "time_zone", candidate.time_zone,
                  sizeof(candidate.time_zone), true) &&
        FormValue(body.data(), "wake_profile", candidate.wake_profile,
                  sizeof(candidate.wake_profile), false) &&
        IsHttpEndpointUrl(candidate.bootstrap_url);
    if (!valid || save_sink_ == nullptr) {
        httpd_resp_set_status(request, "400 Bad Request");
        return httpd_resp_sendstr(
            request,
            "{\"code\":\"invalid_form\",\"message\":\"Hãy kiểm tra SSID, ngôn ngữ và Bootstrap URL\"}");
    }

    const esp_err_t error = SaveFromInternalRam(candidate);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist provisioning: %s", esp_err_to_name(error));
        httpd_resp_set_status(request, "500 Internal Server Error");
        return httpd_resp_sendstr(
            request,
            "{\"code\":\"save_failed\",\"message\":\"Không thể lưu cấu hình\"}");
    }
    current_ = candidate;
    settings::UpsertWifiProfile(&wifi_profiles_, candidate.ssid,
                                candidate.password);
    const ProvisioningStatusSnapshot status =
        status_sink_ == nullptr ? ProvisioningStatusSnapshot{}
                                : status_sink_(save_context_);
    char response[192] = {};
    std::snprintf(response, sizeof(response),
                  "{\"message\":\"Đã lưu. Veetee đang kết nối tới mạng đã chọn.\",\"attempt_id\":%u}",
                  static_cast<unsigned>(status.attempt_id));
    return httpd_resp_sendstr(request, response);
}

void ProvisioningPortal::ScanEventHandler(void* context,
                                          esp_event_base_t event_base,
                                          std::int32_t event_id, void*) {
    auto* portal = static_cast<ProvisioningPortal*>(context);
    if (event_base != WIFI_EVENT || event_id != WIFI_EVENT_SCAN_DONE) return;
    if (portal->scan_mutex_ != nullptr &&
        xSemaphoreTake(portal->scan_mutex_, pdMS_TO_TICKS(250)) == pdTRUE) {
        std::uint16_t count = kMaxScanResults;
        if (esp_wifi_scan_get_ap_records(&count,
                                         portal->scan_records_.data()) == ESP_OK) {
            portal->scan_count_ = count;
            ESP_LOGI(kTag, "Cached %u nearby Wi-Fi network(s)",
                     static_cast<unsigned>(count));
        }
        xSemaphoreGive(portal->scan_mutex_);
    }
    portal->scan_in_progress_.store(false);
    if (portal->scan_timer_ != nullptr) {
        esp_timer_stop(portal->scan_timer_);
    }
}

void ProvisioningPortal::ScanTimer(void* context) {
    static_cast<ProvisioningPortal*>(context)->StartScan();
}

void ProvisioningPortal::StartScan() {
    if (!CanStartCaptivePortalScan(client_network_ready_.load(),
                                   http_server_ != nullptr,
                                   scan_in_progress_.load())) {
        return;
    }
    bool expected = false;
    if (!scan_in_progress_.compare_exchange_strong(expected, true)) return;
    if (!client_network_ready_.load()) {
        scan_in_progress_.store(false);
        return;
    }
    wifi_scan_config_t scan_config = {};
    scan_config.show_hidden = true;
    const esp_err_t error = esp_wifi_scan_start(&scan_config, false);
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Started Wi-Fi scan after captive client received IPv4");
        return;
    }
    scan_in_progress_.store(false);
    ESP_LOGW(kTag, "Unable to start background Wi-Fi scan: %s",
             esp_err_to_name(error));
    if (scan_timer_ != nullptr) {
        esp_timer_stop(scan_timer_);
        esp_timer_start_once(scan_timer_, kScanRetryIntervalUs);
    }
}

void ProvisioningPortal::DnsTaskEntry(void* context) {
    auto* portal = static_cast<ProvisioningPortal*>(context);
    portal->RunDnsServer();
    portal->dns_task_ = nullptr;
    xSemaphoreGive(portal->dns_stopped_);
    vTaskDelete(nullptr);
}

void ProvisioningPortal::RunDnsServer() {
    const int dns_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    dns_socket_.store(dns_socket);
    if (dns_socket < 0) {
        ESP_LOGE(kTag, "Unable to create captive DNS socket");
        return;
    }
    timeval timeout = {.tv_sec = 0, .tv_usec = 250000};
    setsockopt(dns_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(53);
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(dns_socket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        ESP_LOGE(kTag, "Unable to bind captive DNS socket");
        close(dns_socket);
        dns_socket_.store(-1);
        return;
    }

    std::array<std::uint8_t, 512> packet{};
    while (dns_running_.load()) {
        sockaddr_in client = {};
        socklen_t client_length = sizeof(client);
        const int length = recvfrom(dns_socket, packet.data(), packet.size() - 16, 0,
                                    reinterpret_cast<sockaddr*>(&client), &client_length);
        if (length < 12) continue;

        const std::uint16_t question_count =
            static_cast<std::uint16_t>((packet[4] << 8) | packet[5]);
        if (question_count == 0) continue;

        int question_end = 12;
        while (question_end < length && packet[question_end] != 0) {
            const int label_length = packet[question_end];
            if (label_length > 63 || question_end + label_length + 1 >= length) {
                question_end = length;
                break;
            }
            question_end += label_length + 1;
        }
        question_end += 5;
        if (question_end > length || question_end + 16 > static_cast<int>(packet.size())) continue;

        packet[2] = 0x81;
        packet[3] = 0x80;
        packet[6] = 0;
        packet[7] = 1;
        packet[8] = packet[9] = packet[10] = packet[11] = 0;
        int output = question_end;
        packet[output++] = 0xC0;
        packet[output++] = 0x0C;
        packet[output++] = 0;
        packet[output++] = 1;
        packet[output++] = 0;
        packet[output++] = 1;
        packet[output++] = 0;
        packet[output++] = 0;
        packet[output++] = 0;
        packet[output++] = 30;
        packet[output++] = 0;
        packet[output++] = 4;
        std::memcpy(packet.data() + output, &ap_address_, 4);
        output += 4;
        sendto(dns_socket, packet.data(), output, 0,
               reinterpret_cast<sockaddr*>(&client), client_length);
    }
    close(dns_socket);
    dns_socket_.store(-1);
}

}  // namespace veetee::network
