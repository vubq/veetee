#include "transport/websocket_transport.h"

#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "freertos/idf_additions.h"
#include "network/endpoint_url.h"
#include "transport/websocket_task_policy.h"

namespace veetee::transport {
namespace {

constexpr char kTag[] = "veetee_websocket";
constexpr UBaseType_t kCommandQueueDepth = 16;
constexpr UBaseType_t kUrgentCommandQueueDepth = 4;
constexpr UBaseType_t kOutboundAudioQueueDepth = 6;
constexpr TickType_t kCommandSendTimeout = pdMS_TO_TICKS(20);
constexpr TickType_t kSendTimeout = pdMS_TO_TICKS(1000);
constexpr TickType_t kHelloTimeout = pdMS_TO_TICKS(10000);
constexpr TickType_t kNotificationRetry = pdMS_TO_TICKS(50);
constexpr TickType_t kTaskPollInterval = pdMS_TO_TICKS(5);

void CopyBounded(char* destination, std::size_t capacity, const char* source) {
    if (destination == nullptr || capacity == 0) return;
    std::snprintf(destination, capacity, "%s", source == nullptr ? "" : source);
}

}  // namespace

bool WebSocketTransport::AcquireIoTaskReserve() {
    if (io_task_reserve_ != nullptr) return true;

    constexpr std::uint32_t kInternalCaps =
        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    std::size_t largest = heap_caps_get_largest_free_block(kInternalCaps);
    std::size_t reserve_bytes =
        WebSocketIoReserveBytesForLargestBlock(largest);
    if (reserve_bytes == 0) {
        ESP_LOGW(
            kTag,
            "Unable to reserve WebSocket I/O stack block internal_free=%u largest=%u minimum=%u",
            static_cast<unsigned>(heap_caps_get_free_size(kInternalCaps)),
            static_cast<unsigned>(largest),
            static_cast<unsigned>(kWebSocketIoReserveMinimumBytes));
        return false;
    }

    void* reserve = heap_caps_malloc(reserve_bytes, kInternalCaps);
    if (reserve == nullptr &&
        reserve_bytes > kWebSocketIoReserveMinimumBytes) {
        largest = heap_caps_get_largest_free_block(kInternalCaps);
        if (CanAllocateWebSocketIoTask(largest)) {
            reserve_bytes = kWebSocketIoReserveMinimumBytes;
            reserve = heap_caps_malloc(reserve_bytes, kInternalCaps);
        }
    }
    if (reserve == nullptr) {
        ESP_LOGW(
            kTag,
            "WebSocket I/O stack reserve allocation failed internal_free=%u largest=%u requested=%u",
            static_cast<unsigned>(heap_caps_get_free_size(kInternalCaps)),
            static_cast<unsigned>(
                heap_caps_get_largest_free_block(kInternalCaps)),
            static_cast<unsigned>(reserve_bytes));
        return false;
    }

    io_task_reserve_ = reserve;
    io_task_reserve_bytes_ = reserve_bytes;
    ESP_LOGD(kTag, "Reserved WebSocket I/O internal block bytes=%u",
             static_cast<unsigned>(io_task_reserve_bytes_));
    return true;
}

void WebSocketTransport::ReleaseIoTaskReserve() {
    if (io_task_reserve_ == nullptr) return;
    heap_caps_free(io_task_reserve_);
    io_task_reserve_ = nullptr;
    io_task_reserve_bytes_ = 0;
}

esp_err_t WebSocketTransport::Initialize(settings::SettingsStore* settings_store,
                                         EventSink event_sink,
                                         AudioSink audio_sink, McpSink mcp_sink,
                                         audio::WakeAudioSource* wake_audio_source,
                                         void* context) {
    if (settings_store == nullptr || event_sink == nullptr ||
        audio_sink == nullptr ||
        mcp_sink == nullptr || wake_audio_source == nullptr || task_ != nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    settings_store_ = settings_store;
    event_sink_ = event_sink;
    audio_sink_ = audio_sink;
    mcp_sink_ = mcp_sink;
    wake_audio_source_ = wake_audio_source;
    sink_context_ = context;

    std::uint8_t mac[6] = {};
    const esp_err_t mac_error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (mac_error != ESP_OK) return mac_error;
    std::snprintf(hardware_id_, sizeof(hardware_id_),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);

    if (!AcquireIoTaskReserve()) return ESP_ERR_NO_MEM;
    ESP_LOGI(kTag, "WebSocket I/O internal reserve ready bytes=%u",
             static_cast<unsigned>(io_task_reserve_bytes_));

    const UBaseType_t external_memory_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    command_queue_ = xQueueCreateWithCaps(kCommandQueueDepth, sizeof(Command),
                                          external_memory_caps);
    if (command_queue_ == nullptr) {
        ReleaseIoTaskReserve();
        return ESP_ERR_NO_MEM;
    }
    urgent_command_queue_ = xQueueCreateWithCaps(
        kUrgentCommandQueueDepth, sizeof(Command), external_memory_caps);
    if (urgent_command_queue_ == nullptr) {
        vQueueDeleteWithCaps(command_queue_);
        command_queue_ = nullptr;
        ReleaseIoTaskReserve();
        return ESP_ERR_NO_MEM;
    }
    outbound_audio_queue_ = xQueueCreateWithCaps(
        kOutboundAudioQueueDepth, sizeof(OutboundAudioFrame),
        external_memory_caps);
    if (outbound_audio_queue_ == nullptr) {
        vQueueDeleteWithCaps(command_queue_);
        vQueueDeleteWithCaps(urgent_command_queue_);
        command_queue_ = nullptr;
        urgent_command_queue_ = nullptr;
        ReleaseIoTaskReserve();
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreateWithCaps(&WebSocketTransport::TaskEntry, "veetee_ws", 12288,
                            this, 6, &task_, external_memory_caps) != pdPASS) {
        vQueueDeleteWithCaps(command_queue_);
        vQueueDeleteWithCaps(urgent_command_queue_);
        vQueueDeleteWithCaps(outbound_audio_queue_);
        command_queue_ = nullptr;
        urgent_command_queue_ = nullptr;
        outbound_audio_queue_ = nullptr;
        ReleaseIoTaskReserve();
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t WebSocketTransport::Open(WakeSource source) {
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    if (task_ == nullptr || !settings_snapshot.HasDeviceIdentity() ||
        !network::IsWebSocketEndpointUrl(settings_snapshot.websocket_url)) {
        return ESP_ERR_INVALID_STATE;
    }
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    ready_for_audio_.store(false);
    xQueueReset(outbound_audio_queue_);
    if (source == WakeSource::kButton) wake_audio_source_->DiscardWakeAudio();
    Command command{.type = CommandType::kOpen, .generation = generation,
                    .wake_source = source};
    if (QueuePriorityCommand(command, kCommandSendTimeout)) return ESP_OK;
    wake_audio_source_->DiscardWakeAudio();
    std::uint32_t expected = generation;
    requested_generation_.compare_exchange_strong(expected, previous_generation);
    return ESP_ERR_TIMEOUT;
}

esp_err_t WebSocketTransport::Abort(const char* reason, const char* source) {
    if (task_ == nullptr || reason == nullptr || source == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    ready_for_audio_.store(false);
    xQueueReset(outbound_audio_queue_);
    wake_audio_source_->DiscardWakeAudio();
    Command command{.type = CommandType::kAbort,
                    .generation = requested_generation_.load()};
    CopyBounded(command.reason, sizeof(command.reason), reason);
    CopyBounded(command.source, sizeof(command.source), source);
    if (QueuePriorityCommand(command, kCommandSendTimeout)) {
        return ESP_OK;
    }
    Close(WebSocketCloseMode::kAbortive);
    return ESP_ERR_TIMEOUT;
}

esp_err_t WebSocketTransport::StopListening(const char* reason) {
    if (task_ == nullptr || reason == nullptr) return ESP_ERR_INVALID_ARG;
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    ready_for_audio_.store(false);
    xQueueReset(outbound_audio_queue_);
    wake_audio_source_->DiscardWakeAudio();
    Command command{.type = CommandType::kStopListening,
                    .generation = generation};
    CopyBounded(command.reason, sizeof(command.reason), reason);
    if (QueuePriorityCommand(command, kCommandSendTimeout)) {
        return ESP_OK;
    }
    Close(WebSocketCloseMode::kAbortive);
    return ESP_ERR_TIMEOUT;
}

bool WebSocketTransport::SendAudio(const std::uint8_t* packet,
                                   std::size_t length) {
    if (packet == nullptr || length == 0 || length > kMaximumOpusPacketBytes ||
        outbound_audio_queue_ == nullptr || !ready_for_audio_.load()) {
        return false;
    }
    OutboundAudioFrame frame{
        .generation = requested_generation_.load(),
        .length = static_cast<std::uint16_t>(length),
    };
    std::memcpy(frame.packet.data(), packet, length);
    if (xQueueSend(outbound_audio_queue_, &frame, 0) == pdTRUE) {
        const std::uint32_t depth = uxQueueMessagesWaiting(outbound_audio_queue_);
        std::uint32_t high_water = outbound_audio_queue_high_water_.load();
        while (depth > high_water &&
               !outbound_audio_queue_high_water_.compare_exchange_weak(
                   high_water, depth)) {
        }
        return true;
    }

    OutboundAudioFrame discarded{};
    if (xQueueReceive(outbound_audio_queue_, &discarded, 0) == pdTRUE) {
        outbound_audio_queue_drops_.fetch_add(1);
    }
    const bool queued = xQueueSend(outbound_audio_queue_, &frame, 0) == pdTRUE;
    if (!queued) outbound_audio_queue_drops_.fetch_add(1);
    return queued;
}

bool WebSocketTransport::SendMcpPayload(const char* payload,
                                        std::size_t length) {
    if (payload == nullptr || length == 0 || length > 8000 ||
        command_queue_ == nullptr) {
        return false;
    }
    char* copy = static_cast<char*>(std::malloc(length + 1));
    if (copy == nullptr) return false;
    std::memcpy(copy, payload, length);
    copy[length] = '\0';
    Command command{.type = CommandType::kMcpPayload,
                    .generation = requested_generation_.load(),
                    .control_payload = copy,
                    .control_length = static_cast<std::uint16_t>(length)};
    if (QueueCommand(command, kCommandSendTimeout)) return true;
    std::free(copy);
    return false;
}

void WebSocketTransport::Close(WebSocketCloseMode mode) {
    if (task_ == nullptr) return;
    reconnect_enabled_.store(false);
    if (mode == WebSocketCloseMode::kAbortive) {
        abortive_close_requested_.store(true);
    }
    const WebSocketCloseMode effective_mode =
        abortive_close_requested_.load() ? WebSocketCloseMode::kAbortive : mode;
    const std::uint32_t previous_generation = requested_generation_.fetch_add(1);
    const std::uint32_t generation = previous_generation + 1;
    ready_for_audio_.store(false);
    xQueueReset(outbound_audio_queue_);
    wake_audio_source_->DiscardWakeAudio();
    const Command command{.type = CommandType::kClose,
                          .generation = generation,
                          .close_mode = effective_mode};
    if (!QueuePriorityCommand(command, kCommandSendTimeout)) {
        abortive_close_requested_.store(true);
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
    if (transport == nullptr || event_data == nullptr) return;
    auto* data = static_cast<esp_websocket_event_data_t*>(event_data);
    if (data->client != transport->callback_client_.load()) return;
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
            transport->QueuePriorityCommand(command, kCommandSendTimeout);
            break;
        case WEBSOCKET_EVENT_DATA:
            transport->HandleData(*data, generation);
            break;
        default:
            break;
    }
}

void WebSocketTransport::TaskLoop() {
    Command command{};
    OutboundAudioFrame audio{};
    while (true) {
        if (abortive_close_requested_.load()) {
            reconnect_pending_ = false;
            Teardown(false);
            continue;
        }
        if (xQueueReceive(urgent_command_queue_, &command, 0) == pdTRUE) {
            HandleCommand(command);
            continue;
        }
        const TickType_t timeout =
            std::min(ReceiveTimeout(), kTaskPollInterval);
        if (xQueueReceive(command_queue_, &command, timeout) == pdTRUE) {
            HandleCommand(command);
            continue;
        }
        if (awaiting_hello_ && ReceiveTimeout() == 0) {
            HandleLoss(client_generation_.load(), "server_hello_timeout");
            continue;
        }
        if (reconnect_pending_ &&
            IsCurrent(reconnect_generation_) &&
            static_cast<std::int32_t>(
                xTaskGetTickCount() - reconnect_deadline_) >= 0) {
            reconnect_pending_ = false;
            ++reconnect_attempt_;
            reconnect_attempt_count_.fetch_add(1);
            StartClient(reconnect_generation_, wake_opening_.source(), false);
            continue;
        }
        if (ready_ &&
            xQueueReceive(outbound_audio_queue_, &audio, 0) == pdTRUE &&
            IsCurrent(audio.generation) &&
            !SendBinary(audio.packet.data(), audio.length)) {
            HandleLoss(audio.generation, "audio_send_failed");
        }
    }
}

void WebSocketTransport::HandleCommand(const Command& command) {
    if (!IsCurrent(command.generation)) {
        ReleaseCommandPayload(command);
        return;
    }

    switch (command.type) {
        case CommandType::kOpen:
            StartClient(command.generation, command.wake_source, true);
            break;
        case CommandType::kClose:
            Teardown(command.close_mode == WebSocketCloseMode::kGraceful);
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
        case CommandType::kOpusPacket:
            if (!ready_ || !playback_open_) break;
            if (audio_sink_ == nullptr) {
                HandleLoss(command.generation, "playback_sink_unavailable",
                           true);
            } else if (!audio_sink_(command.packet.data(), command.packet_length,
                                    sink_context_)) {
                // A local abort closes the board gate before this task handles
                // already queued prebuffer frames. They are stale, not fatal.
                ESP_LOGD(kTag, "Dropped stale or backpressured downlink frame");
            }
            break;
        case CommandType::kProtocolError:
            HandleLoss(command.generation, "protocol_error", true);
            break;
        case CommandType::kMcpEnvelope:
            HandleMcpEnvelope(command.generation, command.server_event,
                              command.control_payload,
                              command.control_length);
            break;
        case CommandType::kMcpPayload:
            if (!ready_ || !SendMcpPayloadNow(command.control_payload,
                                              command.control_length)) {
                HandleLoss(command.generation, "mcp_send_failed");
            }
            break;
        case CommandType::kAbort: {
            playback_open_ = false;
            std::size_t length = 0;
            if (ready_ && BuildAbort(session_id_, command.reason, command.source,
                                     control_buffer_.data(), control_buffer_.size(),
                                     &length) &&
                !SendText(control_buffer_.data(), length)) {
                HandleLoss(command.generation, "abort_send_failed");
            } else if (ready_) {
                ready_for_audio_.store(true);
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
    ReleaseCommandPayload(command);
}

void WebSocketTransport::StartClient(std::uint32_t generation,
                                     WakeSource source,
                                     bool reset_reconnect_policy) {
    if (reset_reconnect_policy) {
        wake_opening_.Begin(generation, source);
    } else if (!wake_opening_.Matches(generation, source)) {
        HandleLoss(generation, "wake_opening_snapshot_mismatch", true);
        return;
    }
    Teardown(false);
    if (reset_reconnect_policy) {
        reconnect_enabled_.store(true);
        reconnect_pending_ = false;
        reconnect_attempt_ = 0;
    }
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    if (!IsCurrent(generation) || !settings_snapshot.HasDeviceIdentity() ||
        !network::IsWebSocketEndpointUrl(settings_snapshot.websocket_url)) {
        HandleLoss(generation, "invalid_transport_configuration", true);
        return;
    }

    CopyBounded(uri_.data(), uri_.size(), settings_snapshot.websocket_url);
    const int header_length = std::snprintf(
        headers_.data(), headers_.size(),
        "Authorization: Bearer %s\r\nProtocol-Version: 1\r\nDevice-Id: %s\r\nClient-Id: %s\r\n",
        settings_snapshot.device_token, hardware_id_,
        settings_snapshot.client_id);
    if (header_length <= 0 ||
        static_cast<std::size_t>(header_length) >= headers_.size()) {
        HandleLoss(generation, "transport_headers_invalid", true);
        return;
    }
    if (!AcquireIoTaskReserve()) {
        HandleLoss(generation, "io_task_reserve_unavailable");
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
    config.task_name = kWebSocketIoTaskName;
    config.task_stack = static_cast<int>(kWebSocketIoTaskStackBytes);
    config.task_prio = 5;
    config.user_agent = "veetee-firmware/0.1";
    config.crt_bundle_attach = esp_crt_bundle_attach;

    client_ = esp_websocket_client_init(&config);
    if (client_ == nullptr) {
        HandleLoss(generation, "client_init_failed");
        return;
    }
    callback_client_.store(client_);
    client_generation_.store(generation);
    text_assembler_.Reset();
    binary_assembler_.Reset();
    xQueueReset(outbound_audio_queue_);
    const esp_err_t register_error = esp_websocket_register_events(
        client_, WEBSOCKET_EVENT_ANY, &WebSocketTransport::WebSocketEventHandler,
        this);
    esp_err_t start_error = register_error;
    if (register_error == ESP_OK) {
        ReleaseIoTaskReserve();
        const std::size_t largest_internal_block =
            heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL |
                                             MALLOC_CAP_8BIT);
        if (!CanAllocateWebSocketIoTask(largest_internal_block)) {
            ESP_LOGW(
                kTag,
                "WebSocket I/O preflight rejected internal_free=%u largest=%u required=%u",
                static_cast<unsigned>(heap_caps_get_free_size(
                    MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
                static_cast<unsigned>(largest_internal_block),
                static_cast<unsigned>(kWebSocketIoTaskStackBytes));
            start_error = ESP_ERR_NO_MEM;
        } else {
            ESP_LOGI(
                kTag,
                "WebSocket I/O preflight passed internal_free=%u largest=%u required=%u",
                static_cast<unsigned>(heap_caps_get_free_size(
                    MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
                static_cast<unsigned>(largest_internal_block),
                static_cast<unsigned>(kWebSocketIoTaskStackBytes));
            start_error = esp_websocket_client_start(client_);
        }
    }
    if (start_error != ESP_OK) {
        ESP_LOGW(
            kTag,
            "WebSocket start failed: %s internal_free=%u largest=%u psram_free=%u",
            esp_err_to_name(start_error),
            static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
            static_cast<unsigned>(
                heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)),
            static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
        Teardown(false);
        HandleLoss(generation, "client_start_failed");
        return;
    }
    const TaskHandle_t io_task = xTaskGetHandle(kWebSocketIoTaskName);
    if (io_task != nullptr) {
        ESP_LOGI(kTag, "WebSocket I/O stack free at start=%u bytes",
                 static_cast<unsigned>(uxTaskGetStackHighWaterMark(io_task)));
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
            HandleLoss(generation, "server_hello_required", true);
            return;
        }
        CopyBounded(session_id_, sizeof(session_id_), event.session_id);
        if (!SendOpeningSequence(generation)) {
            wake_audio_source_->DiscardWakeAudio();
            HandleLoss(generation, "listen_opening_sequence_failed");
            return;
        }
        awaiting_hello_ = false;
        ready_ = true;
        reconnect_pending_ = false;
        reconnect_attempt_ = 0;
        ready_for_audio_.store(true);
        ESP_LOGI(kTag, "WebSocket protocol ready generation=%u",
                 static_cast<unsigned>(generation));
        NotifyWithRetry(WebSocketTransportEvent::kReady, generation);
        return;
    }

    if (!ready_ || std::strcmp(event.session_id, session_id_) != 0 ||
        event.kind == ServerEventKind::kHello) {
        HandleLoss(generation, "invalid_session_event", true);
        return;
    }

    WebSocketTransportNotification notification{};
    bool handled = true;
    switch (event.kind) {
        case ServerEventKind::kListenStart:
            ready_for_audio_.store(true);
            notification.event = WebSocketTransportEvent::kListenStarted;
            break;
        case ServerEventKind::kStt:
            notification.event = WebSocketTransportEvent::kSttFinal;
            break;
        case ServerEventKind::kLlm:
            notification.event = WebSocketTransportEvent::kLlmStarted;
            break;
        case ServerEventKind::kTurnError:
            ready_for_audio_.store(true);
            notification.event = WebSocketTransportEvent::kTurnFailed;
            break;
        case ServerEventKind::kTtsStart:
            ready_for_audio_.store(false);
            xQueueReset(outbound_audio_queue_);
            playback_open_ = true;
            notification.event = WebSocketTransportEvent::kTtsStarted;
            break;
        case ServerEventKind::kTtsStop:
            ready_for_audio_.store(true);
            playback_open_ = false;
            notification.event = WebSocketTransportEvent::kTtsStopped;
            break;
        case ServerEventKind::kAssistantSleep:
            ready_for_audio_.store(false);
            notification.event = WebSocketTransportEvent::kAssistantSleep;
            break;
        case ServerEventKind::kConfigChanged:
            notification.event = WebSocketTransportEvent::kConfigChanged;
            notification.config_version = event.config_version;
            break;
        case ServerEventKind::kMcp:
        case ServerEventKind::kOther:
            handled = false;
            break;
        case ServerEventKind::kHello:
            break;
    }
    if (handled && !NotifyOnce(notification)) {
        HandleLoss(generation, "application_event_rejected", true);
    }
}

bool WebSocketTransport::SendOpeningSequence(std::uint32_t generation) {
    if (!IsCurrent(generation) || !wake_opening_.Matches(generation) ||
        session_id_[0] == '\0') {
        return false;
    }
    std::size_t length = 0;
    std::size_t wake_packets = 0;
    const WakeSource source = wake_opening_.source();
    if (source == WakeSource::kWakeWord) {
        if (!wake_opening_.MarkStarted(generation)) return false;
        if (!BuildListenDetect(session_id_, control_buffer_.data(),
                               control_buffer_.size(), &length) ||
            !SendText(control_buffer_.data(), length)) {
            return false;
        }
        std::size_t packet_length = 0;
        while (wake_audio_source_->PopWakeAudioPacket(
            wake_audio_buffer_.data(), wake_audio_buffer_.size(),
            &packet_length)) {
            if (!IsCurrent(generation) || packet_length == 0 ||
                !SendBinary(wake_audio_buffer_.data(), packet_length)) {
                return false;
            }
            ++wake_packets;
        }
        ESP_LOGI(kTag,
                 "Wake opening sent detect then %u cached Opus packets",
                 static_cast<unsigned>(wake_packets));
    } else {
        wake_audio_source_->DiscardWakeAudio();
    }
    return BuildListenStart(session_id_, source, control_buffer_.data(),
                            control_buffer_.size(), &length) &&
           SendText(control_buffer_.data(), length);
}

void WebSocketTransport::HandleMcpEnvelope(std::uint32_t generation,
                                           const ServerEvent& event,
                                           const char* envelope,
                                           std::size_t length) {
    if (!IsCurrent(generation) || client_ == nullptr) return;
    if (awaiting_hello_ || !ready_ || event.kind != ServerEventKind::kMcp ||
        std::strcmp(event.session_id, session_id_) != 0 || envelope == nullptr ||
        length == 0 || mcp_sink_ == nullptr) {
        HandleLoss(generation, "invalid_mcp_event", true);
        return;
    }
    if (!mcp_sink_(envelope, length, sink_context_)) {
        HandleLoss(generation, "mcp_event_rejected", true);
    }
}

void WebSocketTransport::HandleLoss(std::uint32_t generation,
                                    const char* reason,
                                    bool protocol_failure) {
    if (!IsCurrent(generation)) return;
    ESP_LOGW(kTag, "WebSocket session lost: %s", reason);
    Teardown(false, 1002, reason);
    const bool retry = CanRetryWebSocket(
        reconnect_enabled_.load(), protocol_failure, reconnect_attempt_);
    if (!wake_opening_.PreserveAudioForRetry(retry, generation)) {
        wake_audio_source_->DiscardWakeAudio();
    }
    if (retry) {
        reconnect_generation_ = generation;
        reconnect_deadline_ =
            xTaskGetTickCount() +
            pdMS_TO_TICKS(WebSocketReconnectDelayMs(
                reconnect_attempt_, esp_random()));
        reconnect_pending_ = true;
        if (NotifyWithRetry(WebSocketTransportEvent::kReconnecting,
                            generation)) {
            return;
        }
        reconnect_pending_ = false;
    }
    if (reconnect_enabled_.load() && !protocol_failure &&
        reconnect_attempt_ >= kWebSocketReconnectAttempts) {
        reconnect_exhausted_count_.fetch_add(1);
    }
    reconnect_enabled_.store(false);
    NotifyWithRetry(WebSocketTransportEvent::kLost, generation);
}

void WebSocketTransport::HandleData(const esp_websocket_event_data_t& data,
                                    std::uint32_t generation) {
    if (!IsCurrent(generation) || data.op_code >= 0x8) return;
    if (data.data_len < 0 || data.payload_len < 0 || data.payload_offset < 0) {
        QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                     .generation = generation},
                             kCommandSendTimeout);
        return;
    }

    if (data.op_code == 0x2 ||
        (data.op_code == 0x0 && binary_assembler_.active())) {
        const std::uint8_t* packet = nullptr;
        std::size_t packet_length = 0;
        const AssembleResult result = binary_assembler_.Append(
            data.op_code, data.fin, static_cast<std::size_t>(data.payload_len),
            static_cast<std::size_t>(data.payload_offset), data.data_ptr,
            static_cast<std::size_t>(data.data_len), &packet, &packet_length);
        if (result == AssembleResult::kIncomplete) return;
        if (result == AssembleResult::kError) {
            QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                         .generation = generation},
                                 kCommandSendTimeout);
            return;
        }
        Command command{.type = CommandType::kOpusPacket,
                        .generation = generation,
                        .packet_length = static_cast<std::uint16_t>(packet_length)};
        std::memcpy(command.packet.data(), packet, packet_length);
        if (!QueueCommand(command, kCommandSendTimeout)) {
            QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                         .generation = generation},
                                 kCommandSendTimeout);
        }
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
        QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                     .generation = generation},
                             kCommandSendTimeout);
        return;
    }

    ServerEvent event{};
    if (!ParseServerEvent(message, message_length, &event)) {
        QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                     .generation = generation},
                             kCommandSendTimeout);
        return;
    }
    if (event.kind == ServerEventKind::kMcp) {
        char* copy = static_cast<char*>(std::malloc(message_length + 1));
        if (copy == nullptr) {
            QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                         .generation = generation},
                                 kCommandSendTimeout);
            return;
        }
        std::memcpy(copy, message, message_length);
        copy[message_length] = '\0';
        Command command{.type = CommandType::kMcpEnvelope,
                        .generation = generation,
                        .server_event = event,
                        .control_payload = copy,
                        .control_length =
                            static_cast<std::uint16_t>(message_length)};
        if (!QueueCommand(command, kCommandSendTimeout)) {
            std::free(copy);
            QueuePriorityCommand(Command{.type = CommandType::kProtocolError,
                                         .generation = generation},
                                 kCommandSendTimeout);
        }
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
    callback_client_.store(nullptr);
    awaiting_hello_ = false;
    ready_ = false;
    playback_open_ = false;
    ready_for_audio_.store(false);
    session_id_[0] = '\0';
    if (client != nullptr) {
        const TaskHandle_t io_task = xTaskGetHandle(kWebSocketIoTaskName);
        if (io_task != nullptr) {
            ESP_LOGI(kTag, "WebSocket I/O stack minimum free=%u bytes",
                     static_cast<unsigned>(
                         uxTaskGetStackHighWaterMark(io_task)));
        }
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
    if (!AcquireIoTaskReserve()) {
        ESP_LOGW(kTag,
                 "WebSocket I/O reserve unavailable after client teardown");
    }
    text_assembler_.Reset();
    binary_assembler_.Reset();
    if (outbound_audio_queue_ != nullptr) xQueueReset(outbound_audio_queue_);
    abortive_close_requested_.store(false);
    std::fill(headers_.begin(), headers_.end(), '\0');
    std::fill(control_buffer_.begin(), control_buffer_.end(), '\0');
}

bool WebSocketTransport::SendText(const char* text, std::size_t length) {
    if (client_ == nullptr || text == nullptr || length == 0 ||
        length > kMaximumControlFrameBytes ||
        length > static_cast<std::size_t>(INT_MAX) ||
        !esp_websocket_client_is_connected(client_)) {
        return false;
    }
    const int sent = esp_websocket_client_send_text(
        client_, text, static_cast<int>(length), kSendTimeout);
    return sent == static_cast<int>(length);
}

bool WebSocketTransport::SendMcpPayloadNow(const char* payload,
                                           std::size_t length) {
    if (payload == nullptr || length == 0 || length > 8000 ||
        session_id_[0] == '\0') {
        return false;
    }
    char prefix[128] = {};
    const int prefix_length = std::snprintf(
        prefix, sizeof(prefix),
        R"({"session_id":"%s","type":"mcp","payload":)", session_id_);
    if (prefix_length <= 0 ||
        static_cast<std::size_t>(prefix_length) >= sizeof(prefix)) {
        return false;
    }
    const std::size_t prefix_bytes = static_cast<std::size_t>(prefix_length);
    const std::size_t total = prefix_bytes + length + 1;
    if (total > kMaximumControlFrameBytes) return false;
    char* envelope = static_cast<char*>(std::malloc(total + 1));
    if (envelope == nullptr) return false;
    std::memcpy(envelope, prefix, prefix_bytes);
    std::memcpy(envelope + prefix_bytes, payload, length);
    envelope[total - 1] = '}';
    envelope[total] = '\0';
    const bool sent = SendText(envelope, total);
    std::free(envelope);
    return sent;
}

bool WebSocketTransport::SendBinary(const std::uint8_t* data,
                                    std::size_t length) {
    if (client_ == nullptr || data == nullptr || length == 0 ||
        length > kMaximumOpusPacketBytes ||
        length > static_cast<std::size_t>(INT_MAX) ||
        !esp_websocket_client_is_connected(client_)) {
        return false;
    }
    const int sent = esp_websocket_client_send_bin(
        client_, reinterpret_cast<const char*>(data), static_cast<int>(length),
        kSendTimeout);
    return sent == static_cast<int>(length);
}

bool WebSocketTransport::QueueCommand(const Command& command,
                                      TickType_t timeout) {
    return command_queue_ != nullptr &&
           xQueueSend(command_queue_, &command, timeout) == pdTRUE;
}

bool WebSocketTransport::QueueUrgentCommand(const Command& command,
                                            TickType_t timeout) {
    return urgent_command_queue_ != nullptr &&
           xQueueSend(urgent_command_queue_, &command, timeout) == pdTRUE;
}

bool WebSocketTransport::QueueCriticalCommand(const Command& command,
                                              TickType_t timeout) {
    return urgent_command_queue_ != nullptr &&
           xQueueSendToFront(urgent_command_queue_, &command, timeout) == pdTRUE;
}

bool WebSocketTransport::QueuePriorityCommand(const Command& command,
                                              TickType_t timeout) {
    const WebSocketCommandPriority priority = CommandPriority(command);
    if (ShouldReplaceOldestUrgentCommand(priority)) {
        if (QueueCriticalCommand(command, timeout)) return true;
        Command displaced{};
        if (urgent_command_queue_ != nullptr &&
            xQueueReceive(urgent_command_queue_, &displaced, 0) == pdTRUE) {
            ReleaseCommandPayload(displaced);
            return QueueCriticalCommand(command, 0);
        }
        return false;
    }
    if (QueueUrgentCommand(command, timeout)) return true;
    return CanFallbackToRegularQueue(priority) && QueueCommand(command, timeout);
}

WebSocketCommandPriority WebSocketTransport::CommandPriority(
    const Command& command) {
    switch (command.type) {
        case CommandType::kOpen:
        case CommandType::kClose:
        case CommandType::kAbort:
        case CommandType::kStopListening:
            return WebSocketCommandPriority::kCriticalControl;
        case CommandType::kSocketLost:
        case CommandType::kProtocolError:
            return WebSocketCommandPriority::kUrgent;
        default:
            return WebSocketCommandPriority::kRegular;
    }
}

void WebSocketTransport::ReleaseCommandPayload(const Command& command) {
    if (command.type == CommandType::kMcpEnvelope ||
        command.type == CommandType::kMcpPayload) {
        std::free(command.control_payload);
    }
}

bool WebSocketTransport::NotifyWithRetry(WebSocketTransportEvent event,
                                         std::uint32_t generation) const {
    if (event_sink_ == nullptr) return false;
    const WebSocketTransportNotification notification{.event = event};
    while (IsCurrent(generation)) {
        if (event_sink_(notification, sink_context_)) return true;
        vTaskDelay(kNotificationRetry);
    }
    return false;
}

bool WebSocketTransport::NotifyOnce(
    const WebSocketTransportNotification& notification) const {
    if (event_sink_ == nullptr) return false;
    return event_sink_(notification, sink_context_);
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
