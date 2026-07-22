#include "network/provisioning_portal.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstring>

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
constexpr char kPortalHtml[] = R"HTML(<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Thiết lập Veetee</title><link rel="stylesheet" href="/portal.css"></head><body><main><header class="hero"><div class="brand-row"><span class="brand"><i></i><i></i></span><span><b>VEETEE</b><small>DEVICE SETUP</small></span><em><i></i> LOCAL</em></div><div class="hero-copy"><span>GET ONLINE</span><h1>Kết nối robot với mạng của bạn.</h1><p>Thiết lập trực tiếp trên thiết bị. Không cần domain và không gửi mật khẩu Wi-Fi ra Internet.</p></div><div class="hero-meta"><span><b>01</b> Wi-Fi</span><i></i><span><b>02</b> Manager</span><i></i><span><b>03</b> Sẵn sàng</span></div></header>
<form id="setup" novalidate><section class="section"><div class="section-head"><div><small>BƯỚC 01</small><h2>Chọn mạng Wi-Fi</h2></div><button type="button" id="refresh">Quét lại</button></div><div class="scan-status" id="scanStatus">Đang quét các mạng gần đây...</div><div class="network-list" id="networkList"></div>
<label>Tên mạng<input id="ssid" name="ssid" maxlength="32" required autocomplete="off" placeholder="Chọn ở trên hoặc nhập mạng ẩn"><span class="hint">Bạn có thể nhập thủ công nếu mạng không phát SSID.</span></label>
<label>Mật khẩu Wi-Fi<div class="password-wrap"><input id="password" name="password" type="password" maxlength="64" autocomplete="new-password" placeholder="Nhập mật khẩu"><button type="button" id="togglePassword">Hiện</button></div><span class="hint">Để trống nếu muốn dùng lại mật khẩu của mạng đã lưu.</span></label></section>
<section class="section"><div class="section-head"><div><small>BƯỚC 02</small><h2>Kết nối Veetee Manager</h2></div></div><label>Bootstrap URL<input id="bootstrapUrl" name="bootstrap_url" maxlength="256" inputmode="url" autocapitalize="none" spellcheck="false" placeholder="http://192.168.1.10:8001/veetee/ota/" required><span class="hint">Dùng địa chỉ LAN của máy đang chạy Manager API.</span></label></section>
<details><summary>Cấu hình nâng cao <b>+</b></summary><div class="row"><label>Ngôn ngữ<input id="locale" name="locale" maxlength="15" value="vi-VN" required></label><label>Wake profile ID<input id="wakeProfile" name="wake_profile" maxlength="64" placeholder="Gán sau"></label></div></details>
<button class="submit" type="submit"><span>Lưu và kết nối</span><b>→</b></button><div class="status" id="status" role="status" aria-live="polite"></div><footer><i></i><span>Kết nối cục bộ được bảo vệ trên thiết bị</span></footer></form></main><script src="/portal-ui.js"></script><script src="/portal.js"></script></body></html>)HTML";

constexpr char kPortalCss[] = R"CSS(:root{color-scheme:light;--canvas:#f3f3ed;--paper:#fbfbf7;--white:#fff;--ink:#13272c;--ink2:#284047;--muted:#687b7f;--line:#d9ded8;--navy:#102c33;--navy2:#1a424a;--orange:#f2643c;--orange2:#d94b27;--lime:#c8f36b;--blue:#dceeee;--danger:#b9382b;--success:#18745e}
*{box-sizing:border-box}html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;background:var(--canvas)}body{margin:0;min-width:320px;min-height:100vh;padding:max(16px,env(safe-area-inset-top)) max(14px,env(safe-area-inset-right)) max(24px,env(safe-area-inset-bottom)) max(14px,env(safe-area-inset-left));color:var(--ink);background:radial-gradient(circle at 88% 0,#dbe8df,transparent 30%),var(--canvas);font-family:"Be Vietnam Pro","Noto Sans","Segoe UI",sans-serif}button,input{font:inherit}button{-webkit-tap-highlight-color:transparent;cursor:pointer}main{width:min(100%,560px);overflow:hidden;margin:auto;border:1px solid var(--line);border-radius:26px;background:var(--paper);box-shadow:0 22px 70px rgba(16,44,51,.12)}
.hero{position:relative;overflow:hidden;padding:22px 23px 20px;color:white;background:var(--navy)}.hero:after{position:absolute;inset:0;content:"";pointer-events:none;opacity:.3;background:radial-gradient(circle at 90% 0,rgba(200,243,107,.4),transparent 28%),radial-gradient(circle at 1px 1px,rgba(255,255,255,.15) 1px,transparent 0);background-size:auto,22px 22px}.brand-row,.hero-copy,.hero-meta{position:relative;z-index:1}.brand-row{display:flex;align-items:center;gap:10px}.brand{position:relative;display:inline-flex;width:38px;height:38px;align-items:center;justify-content:center;border-radius:12px;background:var(--orange);transform:rotate(-3deg)}.brand i{width:5px;height:5px;margin:0 4px;border-radius:50%;background:white}.brand-row>span:nth-child(2){display:grid;gap:1px}.brand-row b{font-size:13px;letter-spacing:.08em}.brand-row small{color:#78969a;font-size:7px;letter-spacing:.16em}.brand-row em{display:flex;align-items:center;gap:7px;margin-left:auto;border:1px solid rgba(255,255,255,.13);border-radius:999px;padding:5px 9px;color:#a8bbbd;font-size:8px;font-style:normal;font-weight:700;letter-spacing:.08em}.brand-row em i,footer i{width:6px;height:6px;border-radius:50%;background:var(--lime);box-shadow:0 0 0 4px rgba(200,243,107,.1)}.hero-copy>span,.section-head small{color:var(--lime);font-size:8px;font-weight:700;letter-spacing:.16em}.hero-copy{margin-top:28px}.hero h1{max-width:470px;margin:6px 0 9px;font-size:clamp(29px,8vw,43px);line-height:1.08;letter-spacing:-.045em}.hero p{max-width:450px;margin:0;color:#a6bbbe;font-size:11px;line-height:1.65}.hero-meta{display:flex;align-items:center;gap:8px;margin-top:22px;color:#7d999d;font-size:8px}.hero-meta span{white-space:nowrap}.hero-meta b{color:white}.hero-meta>i{width:22px;height:1px;background:rgba(255,255,255,.14)}
form{padding:22px 23px 18px}.section+.section{margin-top:25px;border-top:1px solid var(--line);padding-top:22px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:11px}.section-head>div{display:grid;gap:3px}.section-head small{color:var(--orange2)}.section-head h2{margin:0;font-size:17px;letter-spacing:-.025em}.section-head button{flex:none;border:1px solid var(--line);border-radius:10px;padding:8px 11px;color:var(--ink2);background:white;font-size:10px;font-weight:700}.scan-status{margin:0 0 9px;color:var(--muted);font-size:10px}.network-list{display:grid;max-height:230px;gap:7px;overflow:auto;overscroll-behavior:contain;padding:1px}.network{display:grid;width:100%;grid-template-columns:37px minmax(0,1fr) auto;align-items:center;gap:10px;border:1px solid var(--line);border-radius:13px;padding:10px;background:white;color:var(--ink);text-align:left}.network[aria-pressed="true"]{border-color:var(--navy2);background:#f2f8f5;box-shadow:0 0 0 3px rgba(26,66,74,.1)}.signal{display:grid;width:35px;height:35px;place-items:center;border-radius:11px;color:var(--navy2);background:var(--blue);font-size:10px;font-weight:800}.network b,.network small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.network b{font-size:12px}.network small{margin-top:3px;color:var(--muted);font-size:8px}.lock{border-radius:999px;padding:4px 7px;color:var(--muted);background:#f0f2ee;font-size:7px;font-weight:700;text-transform:uppercase}
label{display:block;margin-top:14px;color:var(--ink2);font-size:10px;font-weight:700}input{width:100%;min-height:47px;margin-top:7px;border:1px solid var(--line);border-radius:12px;outline:0;padding:11px 13px;color:var(--ink);background:white;font-size:14px;font-weight:500;transition:.15s}input:hover{border-color:#abbab2}input:focus{border-color:var(--navy2);box-shadow:0 0 0 3px rgba(26,66,74,.12)}input::placeholder{color:#9aa6a4;font-weight:400}.hint{display:block;margin-top:5px;color:var(--muted);font-size:9px;font-weight:400;line-height:1.5}.password-wrap{position:relative}.password-wrap input{padding-right:64px}.password-wrap button{position:absolute;right:7px;bottom:7px;border:0;border-radius:8px;padding:8px;color:var(--orange2);background:#ffebe5;font-size:9px;font-weight:700}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}details{margin-top:20px;border-top:1px dashed #c7d0c9;padding-top:15px}summary{display:flex;justify-content:space-between;color:var(--ink2);font-size:10px;font-weight:700;cursor:pointer;list-style:none}.submit{display:flex;width:100%;min-height:51px;align-items:center;justify-content:space-between;margin-top:23px;border:0;border-radius:13px;padding:0 17px;color:white;background:var(--orange);box-shadow:0 10px 25px rgba(242,100,60,.2);font-size:13px;font-weight:700}.submit b{font-size:19px}.submit:disabled{cursor:wait;opacity:.58}.status{min-height:18px;margin-top:10px;color:var(--success);font-size:10px;line-height:1.5}.status.error{color:var(--danger)}footer{display:flex;align-items:center;justify-content:center;gap:8px;border-top:1px solid var(--line);margin-top:12px;padding-top:15px;color:var(--muted);font-size:8px}footer i{background:var(--success);box-shadow:0 0 0 4px rgba(24,116,94,.08)}
@media(max-width:440px){body{padding:0;background:var(--paper)}main{min-height:100vh;border:0;border-radius:0;box-shadow:none}.hero{padding:20px 18px}.hero-copy{margin-top:24px}.hero h1{font-size:34px}form{padding:20px 18px}.row{grid-template-columns:1fr}.network-list{max-height:212px}})CSS";

constexpr char kPortalUiScript[] = R"JS(const form=document.querySelector('#setup'),statusEl=document.querySelector('#status'),scanStatus=document.querySelector('#scanStatus'),networkList=document.querySelector('#networkList'),ssidInput=document.querySelector('#ssid'),passwordInput=document.querySelector('#password'),submit=form.querySelector('.submit');
let selected='',scanRetry=0;
function setStatus(message,error=false){statusEl.textContent=message;statusEl.classList.toggle('error',error)}
function quality(rssi){return rssi>=-55?'Rất tốt':rssi>=-67?'Tốt':rssi>=-75?'Ổn định':'Yếu'}
function renderNetworks(items){networkList.replaceChildren();if(!items.length){scanStatus.textContent='Chưa thấy mạng. Bạn vẫn có thể nhập mạng ẩn bên dưới.';return}scanStatus.textContent=`Đã tìm thấy ${items.length} mạng. Chạm để chọn.`;for(const item of items){const button=document.createElement('button');button.type='button';button.className='network';button.setAttribute('aria-pressed',String(item.ssid===selected));const signal=document.createElement('span');signal.className='signal';signal.textContent=item.rssi>=-60?'III':item.rssi>=-72?'II':'I';const copy=document.createElement('span');const name=document.createElement('b');name.textContent=item.ssid;const detail=document.createElement('small');detail.textContent=`${item.saved?'Đã lưu · ':''}${quality(item.rssi)} · ${item.rssi} dBm · Kênh ${item.channel}`;copy.append(name,detail);const lock=document.createElement('span');lock.className='lock';lock.textContent=item.saved?'Đã lưu':item.secure?'Bảo mật':'Mở';button.append(signal,copy,lock);button.addEventListener('click',()=>{selected=item.ssid;ssidInput.value=item.ssid;passwordInput.required=item.secure&&!item.saved;passwordInput.value='';renderNetworks(items);passwordInput.focus()});networkList.append(button)}})JS";

constexpr char kPortalScript[] = R"JS(async function scan(){scanStatus.textContent='Đang quét các mạng gần đây...';networkList.replaceChildren();document.querySelector('#refresh').disabled=true;try{const response=await fetch('/api/scan',{cache:'no-store'});if(!response.ok)throw new Error();const items=await response.json();if(!items.length&&scanRetry<3){scanRetry++;scanStatus.textContent='Đang hoàn tất quét Wi-Fi...';setTimeout(scan,1200);return}scanRetry=0;renderNetworks(items)}catch{scanStatus.textContent='Bộ quét đang bận. Chạm Quét lại để thử tiếp.'}finally{document.querySelector('#refresh').disabled=false}}
document.querySelector('#refresh').addEventListener('click',()=>{scanRetry=0;scan()});ssidInput.addEventListener('input',()=>{if(ssidInput.value!==selected){selected='';for(const item of networkList.children)item.setAttribute('aria-pressed','false')}});document.querySelector('#togglePassword').addEventListener('click',e=>{const visible=passwordInput.type==='text';passwordInput.type=visible?'password':'text';e.currentTarget.textContent=visible?'Hiện':'Ẩn'});
fetch('/api/config',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(config=>{if(!config)return;if(config.ssid){selected=config.ssid;ssidInput.value=config.ssid}if(config.bootstrap_url)document.querySelector('#bootstrapUrl').value=config.bootstrap_url;if(config.locale)document.querySelector('#locale').value=config.locale;if(config.wake_profile)document.querySelector('#wakeProfile').value=config.wake_profile}).catch(()=>{}).finally(scan);
form.addEventListener('submit',async e=>{e.preventDefault();const bootstrap=document.querySelector('#bootstrapUrl');const missing=!ssidInput.value.trim()?ssidInput:!bootstrap.value.trim()?bootstrap:passwordInput.required&&!passwordInput.value?passwordInput:null;if(missing){setStatus(missing===bootstrap?'Hãy nhập Bootstrap URL để robot tìm thấy Veetee Manager.':missing===ssidInput?'Hãy chọn hoặc nhập tên mạng Wi-Fi.':'Hãy nhập mật khẩu Wi-Fi.',true);missing.focus();missing.scrollIntoView({behavior:'smooth',block:'center'});return}setStatus('Đang lưu cấu hình và kết nối Wi-Fi...');submit.disabled=true;try{const response=await fetch('/api/provision',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams(new FormData(form))});const result=await response.json().catch(()=>({message:'Phản hồi không hợp lệ'}));setStatus(result.message||'Hoàn tất',!response.ok);if(response.ok){submit.querySelector('span').textContent='Đang kết nối...';setStatus('Đã lưu. Điện thoại có thể tự rời mạng Veetee khi robot vào Wi-Fi của bạn.')}else submit.disabled=false}catch{setStatus('Kết nối thiết lập bị gián đoạn. Hãy vào lại mạng Veetee nếu robot chưa kết nối.',true);submit.disabled=false}});)JS";

static_assert(sizeof(kPortalHtml) <= 4096);
static_assert(sizeof(kPortalCss) <= 8192);
static_assert(sizeof(kPortalUiScript) <= 4096);
static_assert(sizeof(kPortalScript) <= 4096);

esp_err_t SendStatic(httpd_req_t* request, const char* content_type,
                     const char* content) {
    const std::size_t content_length = std::strlen(content);
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
            request, content + offset, static_cast<ssize_t>(chunk_length));
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
    if (IsRunning()) {
        ap_address_ = ap_address;
        current_ = current;
        wifi_profiles_ = wifi_profiles;
        save_sink_ = sink;
        save_context_ = context;
        ESP_LOGI(kTag, "Captive portal already running; refreshed setup context");
        return ESP_OK;
    }
    Stop();
    ap_address_ = ap_address;
    current_ = current;
    wifi_profiles_ = wifi_profiles;
    save_sink_ = sink;
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

void ProvisioningPortal::ResetClientSessions() {
    client_network_ready_.store(false);
    if (scan_in_progress_.exchange(false)) {
        const esp_err_t error = esp_wifi_scan_stop();
        if (error != ESP_OK && error != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGD(kTag, "Stopping captive scan returned %s",
                     esp_err_to_name(error));
        }
    }
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
        return httpd_resp_sendstr(request, "{\"message\":\"Kích thước biểu mẫu không hợp lệ\"}");
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
                                  "{\"message\":\"Hãy kiểm tra SSID, ngôn ngữ và Bootstrap URL\"}");
    }

    const esp_err_t error = SaveFromInternalRam(candidate);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist provisioning: %s", esp_err_to_name(error));
        httpd_resp_set_status(request, "500 Internal Server Error");
        return httpd_resp_sendstr(request, "{\"message\":\"Không thể lưu cấu hình\"}");
    }
    current_ = candidate;
    settings::UpsertWifiProfile(&wifi_profiles_, candidate.ssid,
                                candidate.password);
    return httpd_resp_sendstr(
        request,
        "{\"message\":\"Đã lưu. Veetee đang kết nối tới mạng đã chọn.\"}");
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
