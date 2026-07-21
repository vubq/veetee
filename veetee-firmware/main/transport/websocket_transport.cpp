#include "transport/websocket_transport.h"

#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstring>

#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "network/endpoint_url.h"

namespace veetee::transport {
namespace {

constexpr char kTag[] = "veetee_websocket";
constexpr UBaseType_t kCommandQueueDepth = 16;
constexpr TickType_t kCommandSendTimeout = pdMS_TO_TICKS(20);
constexpr TickType_t kSendTimeout = pdMS_TO_TICKS(1000);
constexpr TickType_t kHelloTimeout = pdMS_TO_TICKS(10000);
constexpr TickType_t kNotificationRetry = pdMS_TO_TICKS(50);

void CopyBounded(char* destination, std::size_t capacity, const char* source) {
    if (destination == nullptr || capacity == 0) return;
    std::snprintf(destination, capacity, "%s", source == nullptr ? "" : source);
}

}  // namespace

esp_err_t WebSocketTransport::Initialize(settings::DeviceSettings* settings,
                                         EventSink sink, void* context) {
    if (settings == nullptr || sink == nullptr || task_ != nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    settings_ = settings;
    sink_ = sink;
    sink_context_ = context;

    std::uint8_t mac[6] = {};
    const esp_err_t mac_error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (mac_error != ESP_OK) return mac_error;
    std::snprintf(hardware_id_, sizeof(hardware_id_),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);

    command_queue_ = xQueueCreate(kCommandQueueDepth, sizeof(Command));
    if (command_queue_ == nullptr) return ESP_ERR_NO_MEM;
    if (xTaskCreate(&WebSocketTransport::TaskEntry, "veetee_ws", 12288, this, 6,
                    &task_) != pdPASS) {
        vQueueDelete(command_queue_);
        command_queue_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t WebSocketTransport::Open(WakeSource source) {
    if (task_ == nullptr || settings_ == nullptr ||
        !settings_->HasDeviceIdentity() ||
        !network::IsWebSocketEndpointUrl(settings_->websocket_url)) {
        return ESP_ERR_INVALID_STATE;
    }
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    Command command{.type = CommandType::kOpen, .generation = generation,
                    .wake_source = source};
    if (QueueCommand(command, kCommandSendTimeout)) return ESP_OK;
    std::uint32_t expected = generation;
    requested_generation_.compare_exchange_strong(expected, previous_generation);
    return ESP_ERR_TIMEOUT;
}

esp_err_t WebSocketTransport::Abort(const char* reason, const char* source) {
    if (task_ == nullptr || reason == nullptr || source == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    Command command{.type = CommandType::kAbort,
                    .generation = requested_generation_.load()};
    CopyBounded(command.reason, sizeof(command.reason), reason);
    CopyBounded(command.source, sizeof(command.source), source);
    return QueueCommand(command, kCommandSendTimeout) ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t WebSocketTransport::StopListening(const char* reason) {
    if (task_ == nullptr || reason == nullptr) return ESP_ERR_INVALID_ARG;
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    Command command{.type = CommandType::kStopListening,
                    .generation = generation};
    CopyBounded(command.reason, sizeof(command.reason), reason);
    if (QueueCommand(command, kCommandSendTimeout)) return ESP_OK;
    std::uint32_t expected = generation;
    requested_generation_.compare_exchange_strong(expected, previous_generation);
    return ESP_ERR_TIMEOUT;
}

void WebSocketTransport::Close() {
    if (task_ == nullptr) return;
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    if (!QueueCommand(Command{.type = CommandType::kClose,
                              .generation = generation},
                      kCommandSendTimeout)) {
        std::uint32_t expected = generation;
        requested_generation_.compare_exchange_strong(expected,
                                                       previous_generation);
        ESP_LOGW(kTag, "Unable to queue WebSocket close command");
    }
}

void WebSocketTransport::TaskEntry(void* context) {
    static_cast<WebSocketTransport*>(context)->TaskLoop();
}

void WebSocketTransport::WebSocketEventHandler(
    void* handler_arg, esp_event_base_t, std::int32_t event_id,
    void* event_data) {
    auto* transport = static_cast<WebSocketTransport*>(handler_arg);
    if (transport == nullptr) return;
    const std::uint32_t generation = transport->client_generation_.load();

    Command command{};
    command.generation = generation;
    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            command.type = CommandType::kSocketConnected;
            transport->QueueCommand(command, kCommandSendTimeout);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
        case WEBSOCKET_EVENT_CLOSED:
        case WEBSOCKET_EVENT_ERROR:
            command.type = CommandType::kSocketLost;
            transport->QueueCommand(command, kCommandSendTimeout);
            break;
        case WEBSOCKET_EVENT_DATA:
            if (event_data != nullptr) {
                transport->HandleData(
                    *static_cast<esp_websocket_event_data_t*>(event_data), generation);
            }
            break;
        default:
            break;
    }
}

void WebSocketTransport::TaskLoop() {
    Command command{};
    while (true) {
        const TickType_t timeout = ReceiveTimeout();
        if (xQueueReceive(command_queue_, &command, timeout) == pdTRUE) {
            HandleCommand(command);
            continue;
        }
        if (awaiting_hello_) {
            HandleLoss(client_generation_.load(), "server_hello_timeout");
        }
    }
}

void WebSocketTransport::HandleCommand(const Command& command) {
    if (!IsCurrent(command.generation)) return;

    switch (command.type) {
        case CommandType::kOpen:
            StartClient(command.generation, command.wake_source);
            break;
        case CommandType::kClose:
            Teardown(true);
            break;
        case CommandType::kSocketConnected:
            HandleSocketConnected(command.generation);
            break;
        case CommandType::kSocketLost:
            if (client_ != nullptr) HandleLoss(command.generation, "socket_lost");
            break;
        case CommandType::kServerEvent:
            HandleServerEvent(command.generation, command.server_event);
            break;
        case CommandType::kProtocolError:
            HandleLoss(command.generation, "protocol_error");
            break;
        case CommandType::kAbort: {
            std::size_t length = 0;
            if (ready_ && BuildAbort(session_id_, command.reason, command.source,
                                     control_buffer_.data(), control_buffer_.size(),
                                     &length) &&
                !SendText(control_buffer_.data(), length)) {
                HandleLoss(command.generation, "abort_send_failed");
            }
            break;
        }
        case CommandType::kStopListening: {
            std::size_t length = 0;
            if (ready_ && BuildListenStop(session_id_, command.reason,
                                          control_buffer_.data(),
                                          control_buffer_.size(), &length)) {
                SendText(control_buffer_.data(), length);
            }
            Teardown(true);
            break;
        }
    }
}

void WebSocketTransport::StartClient(std::uint32_t generation,
                                     WakeSource source) {
    Teardown(false);
    if (!IsCurrent(generation) || settings_ == nullptr ||
        !settings_->HasDeviceIdentity() ||
        !network::IsWebSocketEndpointUrl(settings_->websocket_url)) {
        NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
        return;
    }

    CopyBounded(uri_.data(), uri_.size(), settings_->websocket_url);
    const int header_length = std::snprintf(
        headers_.data(), headers_.size(),
        "Authorization: Bearer %s\r\nProtocol-Version: 1\r\nDevice-Id: %s\r\nClient-Id: %s\r\n",
        settings_->device_token, hardware_id_, settings_->client_id);
    if (header_length <= 0 ||
        static_cast<std::size_t>(header_length) >= headers_.size()) {
        NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
        return;
    }

    esp_websocket_client_config_t config = {};
    config.uri = uri_.data();
    config.headers = headers_.data();
    config.disable_auto_reconnect = true;
    config.enable_close_reconnect = false;
    config.network_timeout_ms = 10000;
    config.ping_interval_sec = 15;
    config.pingpong_timeout_sec = 10;
    config.buffer_size = 4096;
    config.task_stack = 6144;
    config.task_prio = 5;
    config.user_agent = "veetee-firmware/0.1";
    config.crt_bundle_attach = esp_crt_bundle_attach;

    client_ = esp_websocket_client_init(&config);
    if (client_ == nullptr) {
        NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
        return;
    }
    client_generation_.store(generation);
    wake_source_ = source;
    text_assembler_.Reset();
    const esp_err_t register_error = esp_websocket_register_events(
        client_, WEBSOCKET_EVENT_ANY, &WebSocketTransport::WebSocketEventHandler,
        this);
    const esp_err_t start_error = register_error == ESP_OK
                                      ? esp_websocket_client_start(client_)
                                      : register_error;
    if (start_error != ESP_OK) {
        ESP_LOGW(kTag, "WebSocket start failed: %s",
                 esp_err_to_name(start_error));
        Teardown(false);
        NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
        return;
    }
    ESP_LOGI(kTag, "WebSocket connection started generation=%u",
             static_cast<unsigned>(generation));
}

void WebSocketTransport::HandleSocketConnected(std::uint32_t generation) {
    if (!IsCurrent(generation) || client_ == nullptr || ready_ || awaiting_hello_) {
        return;
    }
    const char* hello = DeviceHelloJson();
    if (!SendText(hello, std::strlen(hello))) {
        HandleLoss(generation, "device_hello_send_failed");
        return;
    }
    awaiting_hello_ = true;
    hello_deadline_ = xTaskGetTickCount() + kHelloTimeout;
}

void WebSocketTransport::HandleServerEvent(std::uint32_t generation,
                                           const ServerEvent& event) {
    if (!IsCurrent(generation) || client_ == nullptr) return;
    if (awaiting_hello_) {
        if (event.kind != ServerEventKind::kHello) {
            HandleLoss(generation, "server_hello_required");
            return;
        }
        CopyBounded(session_id_, sizeof(session_id_), event.session_id);
        std::size_t length = 0;
        if (!BuildListenStart(session_id_, wake_source_, control_buffer_.data(),
                              control_buffer_.size(), &length) ||
            !SendText(control_buffer_.data(), length)) {
            HandleLoss(generation, "listen_start_send_failed");
            return;
        }
        awaiting_hello_ = false;
        ready_ = true;
        ESP_LOGI(kTag, "WebSocket protocol ready generation=%u",
                 static_cast<unsigned>(generation));
        NotifyWithRetry(WebSocketTransportEvent::kReady, generation);
        return;
    }

    if (!ready_ || std::strcmp(event.session_id, session_id_) != 0 ||
        event.kind == ServerEventKind::kHello) {
        HandleLoss(generation, "invalid_session_event");
    }
}

void WebSocketTransport::HandleLoss(std::uint32_t generation,
                                    const char* reason) {
    if (!IsCurrent(generation)) return;
    ESP_LOGW(kTag, "WebSocket session lost: %s", reason);
    Teardown(false, 1002, reason);
    NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
}

void WebSocketTransport::HandleData(const esp_websocket_event_data_t& data,
                                    std::uint32_t generation) {
    if (!IsCurrent(generation) || data.op_code >= 0x8) return;
    if (data.op_code == 0x2) {
        // Binary Opus handling is added by the next audio transport slice.
        return;
    }
    if (data.data_len < 0 || data.payload_len < 0 || data.payload_offset < 0) {
        QueueCommand(Command{.type = CommandType::kProtocolError,
                             .generation = generation},
                     kCommandSendTimeout);
        return;
    }

    const char* message = nullptr;
    std::size_t message_length = 0;
    const AssembleResult result = text_assembler_.Append(
        data.op_code, data.fin, static_cast<std::size_t>(data.payload_len),
        static_cast<std::size_t>(data.payload_offset), data.data_ptr,
        static_cast<std::size_t>(data.data_len), &message, &message_length);
    if (result == AssembleResult::kIncomplete) return;
    if (result == AssembleResult::kError) {
        QueueCommand(Command{.type = CommandType::kProtocolError,
                             .generation = generation},
                     kCommandSendTimeout);
        return;
    }

    ServerEvent event{};
    if (!ParseServerEvent(message, message_length, &event)) {
        QueueCommand(Command{.type = CommandType::kProtocolError,
                             .generation = generation},
                     kCommandSendTimeout);
        return;
    }
    Command command{.type = CommandType::kServerEvent,
                    .generation = generation,
                    .server_event = event};
    QueueCommand(command, kCommandSendTimeout);
}

void WebSocketTransport::Teardown(bool clean, int close_code,
                                  const char* reason) {
    esp_websocket_client_handle_t client = client_;
    client_ = nullptr;
    awaiting_hello_ = false;
    ready_ = false;
    session_id_[0] = '\0';
    if (client != nullptr) {
        if (clean && esp_websocket_client_is_connected(client)) {
            const char* close_reason = reason == nullptr ? "" : reason;
            esp_websocket_client_close_with_code(
                client, close_code, close_reason,
                static_cast<int>(std::strlen(close_reason)), pdMS_TO_TICKS(250));
        } else {
            esp_websocket_client_stop(client);
        }
        esp_websocket_client_destroy(client);
    }
    text_assembler_.Reset();
    std::fill(headers_.begin(), headers_.end(), '\0');
    std::fill(control_buffer_.begin(), control_buffer_.end(), '\0');
}

bool WebSocketTransport::SendText(const char* text, std::size_t length) {
    if (client_ == nullptr || text == nullptr || length == 0 ||
        length > static_cast<std::size_t>(INT_MAX) ||
        !esp_websocket_client_is_connected(client_)) {
        return false;
    }
    const int sent = esp_websocket_client_send_text(
        client_, text, static_cast<int>(length), kSendTimeout);
    return sent == static_cast<int>(length);
}

bool WebSocketTransport::QueueCommand(const Command& command,
                                      TickType_t timeout) {
    return command_queue_ != nullptr &&
           xQueueSend(command_queue_, &command, timeout) == pdTRUE;
}

bool WebSocketTransport::NotifyWithRetry(WebSocketTransportEvent event,
                                         std::uint32_t generation) const {
    if (sink_ == nullptr) return false;
    const WebSocketTransportNotification notification{.event = event};
    while (IsCurrent(generation)) {
        if (sink_(notification, sink_context_)) return true;
        vTaskDelay(kNotificationRetry);
    }
    return false;
}

bool WebSocketTransport::IsCurrent(std::uint32_t generation) const {
    return requested_generation_.load() == generation;
}

TickType_t WebSocketTransport::ReceiveTimeout() const {
    if (!awaiting_hello_) return portMAX_DELAY;
    const TickType_t now = xTaskGetTickCount();
    const std::int32_t remaining =
        static_cast<std::int32_t>(hello_deadline_ - now);
    return remaining > 0 ? static_cast<TickType_t>(remaining) : 0;
}

}  // namespace veetee::transport
