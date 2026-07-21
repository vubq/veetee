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
#include "network/endpoint_url.h"

namespace veetee::network {
namespace {

constexpr char kTag[] = "veetee_portal";
constexpr std::size_t kMaxPostBytes = 1024;
constexpr std::size_t kMaxScanResults = 16;
constexpr std::size_t kHttpServerStackBytes = 12 * 1024;
constexpr std::uint64_t kScanRetryIntervalUs = 1500000ULL;
constexpr std::array<const char*, 10> kCaptivePortalPaths = {
    "/hotspot-detect.html",       // Apple
    "/library/test/success.html", // Apple
    "/generate_204*",             // Android
    "/mobile/status.php",         // Android
    "/check_network_status.txt",  // Windows
    "/ncsi.txt",                  // Windows
    "/fwlink/",                   // Windows
    "/connectivity-check.html",   // Firefox
    "/success.txt",               // Other clients
    "/portal.html",               // Other clients
};

constexpr char kPortalHtml[] = R"HTML(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veetee setup</title><link rel="stylesheet" href="/portal.css"></head><body><main><div class="eyebrow">Local-first robot setup</div><h1>Meet Veetee.</h1><p class="lead">Choose a nearby Wi-Fi network, then point the robot at the Veetee manager on your LAN. No domain is required.</p>
<form id="setup"><section class="section"><div class="section-head"><h2>1. Choose a Wi-Fi network</h2><button type="button" id="refresh">Refresh</button></div><div class="scan-status" id="scanStatus">Scanning nearby networks...</div><div class="network-list" id="networkList"></div>
<label>Network name<input id="ssid" name="ssid" maxlength="32" required autocomplete="off" placeholder="Select above or enter a hidden network"><span class="hint">Hidden networks can be entered manually.</span></label>
<label>Wi-Fi password<div class="password-wrap"><input id="password" name="password" type="password" maxlength="64" autocomplete="new-password"><button type="button" id="togglePassword">Show</button></div><span class="hint">For a saved network, leave this empty to reuse its password.</span></label></section>
<section class="section"><div class="section-head"><h2>2. Connect to Veetee Manager</h2></div><label>Bootstrap URL<input id="bootstrapUrl" name="bootstrap_url" maxlength="256" inputmode="url" autocapitalize="none" spellcheck="false" placeholder="http://192.168.1.10:8001/veetee/ota/" required><span class="hint">Use the LAN IP of the computer running Manager API.</span></label></section>
<details><summary>Advanced settings</summary><div class="row"><label>Locale<input id="locale" name="locale" maxlength="15" value="vi-VN" required></label><label>Wake profile ID<input id="wakeProfile" name="wake_profile" maxlength="64" placeholder="assigned later"></label></div></details>
<button class="submit" type="submit">Save and connect</button><div class="status" id="status" role="status" aria-live="polite"></div></form></main>
<script src="/portal-ui.js"></script><script src="/portal.js"></script></body></html>)HTML";

constexpr char kPortalCss[] = R"CSS(:root{color-scheme:light;--ink:#132019;--muted:#66726b;--leaf:#1f6b45;--leaf-soft:#e5f0e8;--paper:#f4f0e5;--card:#fffdf8;--line:#cbc5b6;--sun:#f2b544;--danger:#a73e32}
*{box-sizing:border-box}html{background:var(--paper)}body{margin:0;min-height:100vh;font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:radial-gradient(circle at 94% 4%,#f8d98d 0 8%,transparent 30%),linear-gradient(145deg,#dcead7,var(--paper) 48%,#eadbc5);padding:max(12px,env(safe-area-inset-top)) max(12px,env(safe-area-inset-right)) max(20px,env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left))}
main{width:min(100%,540px);margin:0 auto;padding:clamp(22px,6vw,40px);background:rgba(255,255,255,.9);border:1px solid rgba(19,32,25,.17);border-radius:28px;box-shadow:0 24px 70px rgba(27,54,40,.14);backdrop-filter:blur(14px)}
.eyebrow{font-size:11px;font-weight:800;letter-spacing:.17em;text-transform:uppercase;color:var(--leaf)}h1{font-family:Georgia,serif;font-size:clamp(38px,11vw,58px);line-height:.94;margin:.28em 0 .22em}.lead{margin:0 0 22px;color:#39463f;font:15px/1.55 Georgia,serif;max-width:42ch}
.section{margin-top:22px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.section-head h2{margin:0;font:800 13px/1.2 ui-sans-serif,sans-serif;letter-spacing:.04em}.section-head button{flex:none;width:auto;margin:0;padding:8px 10px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--leaf);font-size:12px}
.scan-status{margin:8px 0;color:var(--muted);font-size:12px}.network-list{display:grid;gap:8px;max-height:238px;overflow:auto;overscroll-behavior:contain;padding:1px}.network{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:10px;align-items:center;width:100%;margin:0;padding:11px;border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--ink);text-align:left}.network[aria-pressed="true"]{border-color:var(--leaf);background:var(--leaf-soft);box-shadow:0 0 0 2px rgba(31,107,69,.12)}.signal{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#eef2ea;color:var(--leaf);font-weight:900}.network b,.network small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.network b{font-size:14px}.network small{margin-top:3px;color:var(--muted);font-size:11px}.lock{font-size:14px;color:var(--muted)}
label{display:block;margin-top:14px;font-size:12px;font-weight:800;line-height:1.35;letter-spacing:.02em}input,select{width:100%;margin-top:7px;padding:13px 14px;border:1px solid var(--line);border-radius:12px;outline:0;background:var(--card);font:16px ui-sans-serif,sans-serif;color:var(--ink)}input:focus,select:focus{border-color:var(--leaf);box-shadow:0 0 0 3px rgba(31,107,69,.12)}.password-wrap{position:relative}.password-wrap input{padding-right:72px}.password-wrap button{position:absolute;right:7px;bottom:7px;width:auto;margin:0;padding:7px 9px;border-radius:8px;background:#edf1e9;color:var(--leaf);font-size:11px}.hint{display:block;margin-top:6px;color:var(--muted);font-size:11px;font-weight:500;letter-spacing:0}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}details{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}summary{cursor:pointer;color:var(--leaf);font-size:12px;font-weight:800}button{border:0;cursor:pointer}.submit{margin-top:24px;width:100%;min-height:50px;padding:14px 18px;border-radius:14px;background:var(--leaf);color:white;font-size:15px;font-weight:800}.submit:disabled{cursor:wait;opacity:.65}.status{min-height:20px;margin-top:12px;color:var(--leaf);font-size:13px;line-height:1.45}.status.error{color:var(--danger)}
@media(max-width:420px){body{padding:0}main{min-height:100vh;border:0;border-radius:0;padding:22px 18px 30px;box-shadow:none}.row{grid-template-columns:1fr}.network-list{max-height:210px}})CSS";

constexpr char kPortalUiScript[] = R"JS(const form=document.querySelector('#setup'),statusEl=document.querySelector('#status'),scanStatus=document.querySelector('#scanStatus'),networkList=document.querySelector('#networkList'),ssidInput=document.querySelector('#ssid'),passwordInput=document.querySelector('#password'),submit=form.querySelector('.submit');
let selected='',scanRetry=0;
function setStatus(message,error=false){statusEl.textContent=message;statusEl.classList.toggle('error',error)}
function quality(rssi){return rssi>=-55?'Excellent':rssi>=-67?'Good':rssi>=-75?'Fair':'Weak'}
function renderNetworks(items){networkList.replaceChildren();if(!items.length){scanStatus.textContent='No visible networks found. Enter a hidden network below.';return}scanStatus.textContent=`${items.length} network${items.length===1?'':'s'} found. Tap one to select it.`;for(const item of items){const button=document.createElement('button');button.type='button';button.className='network';button.setAttribute('aria-pressed',String(item.ssid===selected));const signal=document.createElement('span');signal.className='signal';signal.textContent=item.rssi>=-60?'3':item.rssi>=-72?'2':'1';const copy=document.createElement('span');const name=document.createElement('b');name.textContent=item.ssid;const detail=document.createElement('small');detail.textContent=`${item.saved?'Saved - ':''}${quality(item.rssi)} - ${item.rssi} dBm - channel ${item.channel}`;copy.append(name,detail);const lock=document.createElement('span');lock.className='lock';lock.textContent=item.saved?'saved':item.secure?'lock':'open';button.append(signal,copy,lock);button.addEventListener('click',()=>{selected=item.ssid;ssidInput.value=item.ssid;passwordInput.required=item.secure&&!item.saved;passwordInput.value='';renderNetworks(items);passwordInput.focus()});networkList.append(button)}})JS";

constexpr char kPortalScript[] = R"JS(async function scan(){scanStatus.textContent='Scanning nearby networks...';networkList.replaceChildren();document.querySelector('#refresh').disabled=true;try{const response=await fetch('/api/scan',{cache:'no-store'});if(!response.ok)throw new Error();const items=await response.json();if(!items.length&&scanRetry<3){scanRetry++;scanStatus.textContent='Finding nearby networks...';setTimeout(scan,1200);return}scanRetry=0;renderNetworks(items)}catch{scanStatus.textContent='Scan is busy. Tap Refresh to try again.'}finally{document.querySelector('#refresh').disabled=false}}
document.querySelector('#refresh').addEventListener('click',()=>{scanRetry=0;scan()});ssidInput.addEventListener('input',()=>{if(ssidInput.value!==selected){selected='';for(const item of networkList.children)item.setAttribute('aria-pressed','false')}});document.querySelector('#togglePassword').addEventListener('click',e=>{const visible=passwordInput.type==='text';passwordInput.type=visible?'password':'text';e.currentTarget.textContent=visible?'Show':'Hide'});
fetch('/api/config',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(config=>{if(!config)return;if(config.ssid){selected=config.ssid;ssidInput.value=config.ssid}if(config.bootstrap_url)document.querySelector('#bootstrapUrl').value=config.bootstrap_url;if(config.locale)document.querySelector('#locale').value=config.locale;if(config.wake_profile)document.querySelector('#wakeProfile').value=config.wake_profile}).catch(()=>{}).finally(scan);
form.addEventListener('submit',async e=>{e.preventDefault();setStatus('Saving settings and starting Wi-Fi...');submit.disabled=true;try{const response=await fetch('/api/provision',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams(new FormData(form))});const result=await response.json().catch(()=>({message:'Invalid response'}));setStatus(result.message||'Done',!response.ok);if(response.ok){submit.textContent='Connecting...';setStatus('Saved. Your phone may leave the Veetee network while the robot joins your Wi-Fi.')}}catch{setStatus('The setup connection was interrupted. Rejoin the Veetee network if the robot does not connect.',true);submit.disabled=false}});)JS";

static_assert(sizeof(kPortalHtml) <= 4096);
static_assert(sizeof(kPortalCss) <= 4096);
static_assert(sizeof(kPortalUiScript) <= 4096);
static_assert(sizeof(kPortalScript) <= 4096);

esp_err_t SendStatic(httpd_req_t* request, const char* content_type,
                     const char* content) {
    ESP_LOGI(kTag, "HTTP GET %s bytes=%u", request->uri,
             static_cast<unsigned>(std::strlen(content)));
    httpd_resp_set_type(request, content_type);
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    return httpd_resp_send(request, content, HTTPD_RESP_USE_STRLEN);
}

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
                                    SaveSink sink, void* context) {
    Stop();
    ap_address_ = ap_address;
    current_ = current;
    wifi_profiles_ = wifi_profiles;
    save_sink_ = sink;
    save_context_ = context;

    scan_mutex_ = xSemaphoreCreateMutex();
    if (scan_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = esp_event_handler_instance_register(
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
    error = httpd_start(&http_server_, &config);
    if (error != ESP_OK) return error;

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
        (error = httpd_register_uri_handler(http_server_, &save)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &portal)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &style)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &ui_script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &script)) != ESP_OK ||
        (error = httpd_register_uri_handler(http_server_, &favicon)) != ESP_OK) {
        Stop();
        return error;
    }
    for (const char* path : kCaptivePortalPaths) {
        httpd_uri_t captive = {};
        captive.uri = path;
        captive.method = HTTP_GET;
        captive.handler = &ProvisioningPortal::CaptivePortalHandler;
        captive.user_ctx = this;
        error = httpd_register_uri_handler(http_server_, &captive);
        if (error != ESP_OK) {
            Stop();
            return error;
        }
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
    StartScan();
    ESP_LOGI(kTag, "Captive portal started at http://192.168.4.1");
    return ESP_OK;
}

void ProvisioningPortal::Stop() {
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

void ProvisioningPortal::ResetClientSessions() {
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
    return SendStatic(request, "text/html; charset=utf-8", kPortalHtml);
}

esp_err_t ProvisioningPortal::StyleHandler(httpd_req_t* request) {
    return SendStatic(request, "text/css; charset=utf-8", kPortalCss);
}

esp_err_t ProvisioningPortal::UiScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      kPortalUiScript);
}

esp_err_t ProvisioningPortal::ScriptHandler(httpd_req_t* request) {
    return SendStatic(request, "application/javascript; charset=utf-8",
                      kPortalScript);
}

esp_err_t ProvisioningPortal::FaviconHandler(httpd_req_t* request) {
    ESP_LOGI(kTag, "HTTP GET %s -> 204", request->uri);
    httpd_resp_set_status(request, "204 No Content");
    httpd_resp_set_hdr(request, "Cache-Control", "public, max-age=86400");
    httpd_resp_set_hdr(request, "Connection", "close");
    return httpd_resp_send(request, nullptr, 0);
}

esp_err_t ProvisioningPortal::CaptivePortalHandler(httpd_req_t* request) {
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
    return httpd_resp_send(request, nullptr, 0);
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
    if (count == 0) {
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

esp_err_t ProvisioningPortal::ConfigHandler(httpd_req_t* request) {
    const auto* portal = static_cast<const ProvisioningPortal*>(request->user_ctx);
    ESP_LOGI(kTag, "HTTP GET %s", request->uri);
    char ssid[129] = {};
    char bootstrap_url[1025] = {};
    char locale[65] = {};
    char wake_profile[257] = {};
    JsonEscapeString(portal->current_.ssid, ssid, sizeof(ssid));
    JsonEscapeString(portal->current_.bootstrap_url, bootstrap_url,
                     sizeof(bootstrap_url));
    JsonEscapeString(portal->current_.locale, locale, sizeof(locale));
    JsonEscapeString(portal->current_.wake_profile, wake_profile,
                     sizeof(wake_profile));
    char response[1600] = {};
    std::snprintf(response, sizeof(response),
                  "{\"ssid\":\"%s\",\"bootstrap_url\":\"%s\",\"locale\":\"%s\",\"wake_profile\":\"%s\"}",
                  ssid, bootstrap_url, locale, wake_profile);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
    return httpd_resp_sendstr(request, response);
}

esp_err_t ProvisioningPortal::SaveHandler(httpd_req_t* request) {
    return static_cast<ProvisioningPortal*>(request->user_ctx)->HandleSave(request);
}

esp_err_t ProvisioningPortal::HandleSave(httpd_req_t* request) {
    ESP_LOGI(kTag, "HTTP POST %s bytes=%d", request->uri,
             request->content_len);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Connection", "close");
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
        IsHttpEndpointUrl(candidate.bootstrap_url);
    if (!valid || save_sink_ == nullptr) {
        httpd_resp_set_status(request, "400 Bad Request");
        return httpd_resp_sendstr(request,
                                  "{\"message\":\"Check SSID, locale and bootstrap URL\"}");
    }

    const esp_err_t error = save_sink_(&candidate, save_context_);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist provisioning: %s", esp_err_to_name(error));
        httpd_resp_set_status(request, "500 Internal Server Error");
        return httpd_resp_sendstr(request, "{\"message\":\"Unable to save settings\"}");
    }
    current_ = candidate;
    settings::UpsertWifiProfile(&wifi_profiles_, candidate.ssid,
                                candidate.password);
    return httpd_resp_sendstr(
        request,
        "{\"message\":\"Saved. Veetee is connecting to the selected network.\"}");
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
    if (http_server_ == nullptr || scan_in_progress_.exchange(true)) return;
    wifi_scan_config_t scan_config = {};
    scan_config.show_hidden = true;
    const esp_err_t error = esp_wifi_scan_start(&scan_config, false);
    if (error == ESP_OK) return;
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
