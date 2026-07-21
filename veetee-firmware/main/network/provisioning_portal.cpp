#include "network/provisioning_portal.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_wifi.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

namespace veetee::network {
namespace {

constexpr char kTag[] = "veetee_portal";
constexpr std::size_t kMaxPostBytes = 1024;
constexpr std::size_t kMaxScanResults = 16;

constexpr char kPortalHtml[] = R"HTML(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veetee setup</title><style>
:root{color-scheme:light;--ink:#132019;--leaf:#1f6b45;--paper:#f4f0e5;--line:#c9c2af;--sun:#f2b544}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:Georgia,serif;color:var(--ink);background:radial-gradient(circle at 80% 10%,#f8d98d 0 12%,transparent 33%),linear-gradient(145deg,#dcead7,var(--paper) 48%,#eadbc5)}
main{width:min(92vw,680px);margin:7vh auto;padding:clamp(24px,5vw,52px);background:rgba(255,255,255,.82);border:1px solid rgba(19,32,25,.2);box-shadow:12px 16px 0 rgba(31,107,69,.15)}
.eyebrow{font:700 12px/1.2 sans-serif;letter-spacing:.18em;text-transform:uppercase;color:var(--leaf)}h1{font-size:clamp(42px,9vw,76px);line-height:.88;margin:.25em 0}.lead{font-size:18px;line-height:1.55;max-width:48ch}
label{display:block;margin-top:18px;font:700 13px/1.4 sans-serif;letter-spacing:.04em}input,select{width:100%;margin-top:7px;padding:13px 14px;border:1px solid var(--line);background:#fffdf8;font:16px sans-serif;color:var(--ink)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}button{margin-top:24px;width:100%;border:0;padding:15px 18px;background:var(--leaf);color:white;font:700 15px sans-serif;cursor:pointer}button:hover{background:#174f34}.status{min-height:24px;margin-top:14px;font:14px sans-serif;color:var(--leaf)}
@media(max-width:560px){main{margin:0;min-height:100vh;box-shadow:none}.row{grid-template-columns:1fr}}
</style></head><body><main><div class="eyebrow">Local-first robot setup</div><h1>Meet Veetee.</h1><p class="lead">Connect the robot to Wi-Fi and point it at the Veetee manager running on your LAN. No domain is required.</p>
<form id="setup"><label>Wi-Fi network<input name="ssid" list="networks" maxlength="32" required autocomplete="off"><datalist id="networks"></datalist></label>
<label>Wi-Fi password<input name="password" type="password" maxlength="64" autocomplete="new-password"></label>
<label>Bootstrap URL<input name="bootstrap_url" maxlength="256" placeholder="http://192.168.1.10:8001/veetee/ota/" required></label>
<div class="row"><label>Locale<input name="locale" maxlength="15" value="vi-VN" required></label><label>Wake profile ID<input name="wake_profile" maxlength="64" placeholder="assigned later"></label></div>
<button type="submit">Save and connect</button><div class="status" id="status" role="status"></div></form></main>
<script>
const form=document.querySelector('#setup'),statusEl=document.querySelector('#status'),list=document.querySelector('#networks');
fetch('/api/scan').then(r=>r.json()).then(items=>{for(const item of items){const option=document.createElement('option');option.value=item.ssid;option.label=`${item.rssi} dBm${item.secure?' - secured':''}`;list.append(option)}}).catch(()=>{});
form.addEventListener('submit',async e=>{e.preventDefault();statusEl.textContent='Saving settings...';const response=await fetch('/api/provision',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams(new FormData(form))});const result=await response.json().catch(()=>({message:'Invalid response'}));statusEl.textContent=result.message||'Done';if(response.ok){form.querySelector('button').disabled=true}});
</script></body></html>)HTML";

int HexValue(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    value = static_cast<char>(std::tolower(static_cast<unsigned char>(value)));
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    return -1;
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

void JsonEscapeSsid(const std::uint8_t* source, char* destination, std::size_t capacity) {
    std::size_t written = 0;
    for (std::size_t index = 0; source[index] != 0 && written + 1 < capacity; ++index) {
        const unsigned char value = source[index];
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
                                    SaveSink sink, void* context) {
    Stop();
    ap_address_ = ap_address;
    current_ = current;
    save_sink_ = sink;
    save_context_ = context;

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.uri_match_fn = httpd_uri_match_wildcard;
    config.max_uri_handlers = 8;
    esp_err_t error = httpd_start(&http_server_, &config);
    if (error != ESP_OK) return error;

    httpd_uri_t scan = {};
    scan.uri = "/api/scan";
    scan.method = HTTP_GET;
    scan.handler = &ProvisioningPortal::ScanHandler;
    scan.user_ctx = this;
    httpd_uri_t save = {};
    save.uri = "/api/provision";
    save.method = HTTP_POST;
    save.handler = &ProvisioningPortal::SaveHandler;
    save.user_ctx = this;
    httpd_uri_t portal = {};
    portal.uri = "/*";
    portal.method = HTTP_GET;
    portal.handler = &ProvisioningPortal::PortalHandler;
    portal.user_ctx = this;
    if ((error = httpd_register_uri_handler(http_server_, &scan)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &save)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &portal)) != ESP_OK) {
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
    ESP_LOGI(kTag, "Captive portal started at http://192.168.4.1");
    return ESP_OK;
}

void ProvisioningPortal::Stop() {
    if (http_server_ != nullptr) {
        httpd_stop(http_server_);
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

esp_err_t ProvisioningPortal::PortalHandler(httpd_req_t* request) {
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_send(request, kPortalHtml, HTTPD_RESP_USE_STRLEN);
}

esp_err_t ProvisioningPortal::ScanHandler(httpd_req_t* request) {
    wifi_scan_config_t scan_config = {};
    scan_config.show_hidden = true;
    esp_err_t error = esp_wifi_scan_start(&scan_config, true);
    if (error != ESP_OK) {
        httpd_resp_set_status(request, "503 Service Unavailable");
        return httpd_resp_sendstr(request, "[]");
    }

    std::uint16_t count = kMaxScanResults;
    std::array<wifi_ap_record_t, kMaxScanResults> records{};
    error = esp_wifi_scan_get_ap_records(&count, records.data());
    if (error != ESP_OK) count = 0;
    httpd_resp_set_type(request, "application/json");
    httpd_resp_sendstr_chunk(request, "[");
    for (std::uint16_t index = 0; index < count; ++index) {
        char ssid[129] = {};
        char item[224] = {};
        JsonEscapeSsid(records[index].ssid, ssid, sizeof(ssid));
        std::snprintf(item, sizeof(item), "%s{\"ssid\":\"%s\",\"rssi\":%d,\"secure\":%s}",
                      index == 0 ? "" : ",", ssid, records[index].rssi,
                      records[index].authmode == WIFI_AUTH_OPEN ? "false" : "true");
        httpd_resp_sendstr_chunk(request, item);
    }
    httpd_resp_sendstr_chunk(request, "]");
    return httpd_resp_sendstr_chunk(request, nullptr);
}

esp_err_t ProvisioningPortal::SaveHandler(httpd_req_t* request) {
    return static_cast<ProvisioningPortal*>(request->user_ctx)->HandleSave(request);
}

esp_err_t ProvisioningPortal::HandleSave(httpd_req_t* request) {
    if (request->content_len <= 0 || request->content_len > kMaxPostBytes) {
        httpd_resp_set_status(request, "413 Payload Too Large");
        return httpd_resp_sendstr(request, "{\"message\":\"Invalid form size\"}");
    }

    std::array<char, kMaxPostBytes + 1> body{};
    int received = 0;
    while (received < request->content_len) {
        const int result = httpd_req_recv(request, body.data() + received,
                                          request->content_len - received);
        if (result <= 0) {
            httpd_resp_set_status(request, "408 Request Timeout");
            return httpd_resp_sendstr(request, "{\"message\":\"Request timed out\"}");
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
        FormValue(body.data(), "wake_profile", candidate.wake_profile,
                  sizeof(candidate.wake_profile), false) &&
        (std::strncmp(candidate.bootstrap_url, "http://", 7) == 0 ||
         std::strncmp(candidate.bootstrap_url, "https://", 8) == 0);
    httpd_resp_set_type(request, "application/json");
    if (!valid || save_sink_ == nullptr) {
        httpd_resp_set_status(request, "400 Bad Request");
        return httpd_resp_sendstr(request,
                                  "{\"message\":\"Check SSID, locale and bootstrap URL\"}");
    }

    const esp_err_t error = save_sink_(candidate, save_context_);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist provisioning: %s", esp_err_to_name(error));
        httpd_resp_set_status(request, "500 Internal Server Error");
        return httpd_resp_sendstr(request, "{\"message\":\"Unable to save settings\"}");
    }
    current_ = candidate;
    return httpd_resp_sendstr(
        request,
        "{\"message\":\"Saved. Veetee is connecting to the selected network.\"}");
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
