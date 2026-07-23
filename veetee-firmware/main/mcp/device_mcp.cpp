#include "mcp/device_mcp.h"

#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "cJSON.h"

namespace veetee::mcp {
namespace {

constexpr char kProtocolVersion[] = "2024-11-05";
constexpr char kBoardName[] = "veetee-s3-n16r8";
constexpr std::size_t kMaximumInnerPayloadBytes = 8000;
constexpr std::size_t kMaximumToolListResultBytes = 7000;

enum class ToolHandler {
    kDeviceStatus,
    kGetVolume,
    kSetVolume,
    kSystemInfo,
    kDiagnosticsHealth,
    kDiagnosticsAudioStart,
    kDiagnosticsSelfTest,
};

struct ToolDefinition {
    const char* name;
    const char* description;
    const char* input_schema;
    const char* safety_class;
    bool user_only;
    ToolHandler handler;
};

constexpr char kEmptyInputSchema[] =
    R"({"type":"object","additionalProperties":false,"properties":{}})";
constexpr char kVolumeInputSchema[] =
    R"({"type":"object","additionalProperties":false,"required":["volume"],"properties":{"volume":{"type":"integer","minimum":0,"maximum":100}}})";
constexpr char kAudioDiagnosticInputSchema[] =
    R"({"type":"object","additionalProperties":false,"required":["duration_seconds"],"properties":{"duration_seconds":{"type":"integer","minimum":1,"maximum":30}}})";

constexpr std::array<ToolDefinition, 7> kTools = {{
    {
        "self.get_device_status",
        "Read the current device state, assistant gate, firmware version and speaker volume.",
        kEmptyInputSchema,
        "read_only",
        false,
        ToolHandler::kDeviceStatus,
    },
    {
        "self.audio_speaker.get_volume",
        "Read the current speaker output volume as a percentage from 0 to 100.",
        kEmptyInputSchema,
        "read_only",
        false,
        ToolHandler::kGetVolume,
    },
    {
        "self.audio_speaker.set_volume",
        "Set speaker output volume from 0 to 100 percent.",
        kVolumeInputSchema,
        "reversible",
        false,
        ToolHandler::kSetVolume,
    },
    {
        "self.get_system_info",
        "Read board and firmware identity for an explicit user diagnostic action.",
        kEmptyInputSchema,
        "read_only",
        true,
        ToolHandler::kSystemInfo,
    },
    {
        "self.diagnostics.get_health",
        "Read bounded device, memory, network, audio and resource health without changing device state.",
        kEmptyInputSchema,
        "read_only",
        true,
        ToolHandler::kDiagnosticsHealth,
    },
    {
        "self.diagnostics.audio.start",
        "Start a bounded metrics-only microphone diagnostic without storing raw audio.",
        kAudioDiagnosticInputSchema,
        "disruptive",
        true,
        ToolHandler::kDiagnosticsAudioStart,
    },
    {
        "self.diagnostics.run_self_test",
        "Run a non-destructive immediate device self-test without changing Wi-Fi or NVS.",
        kEmptyInputSchema,
        "disruptive",
        true,
        ToolHandler::kDiagnosticsSelfTest,
    },
}};

const cJSON* AsJson(const void* value) {
    return static_cast<const cJSON*>(value);
}

cJSON* AsMutableJson(void* value) {
    return static_cast<cJSON*>(value);
}

bool IsRequestId(const cJSON* value) {
    if (cJSON_IsString(value)) {
        return value->valuestring != nullptr && value->valuestring[0] != '\0' &&
               std::strlen(value->valuestring) <= 128;
    }
    return cJSON_IsNumber(value) && std::isfinite(value->valuedouble) &&
           std::floor(value->valuedouble) == value->valuedouble;
}

bool IsStringValue(const cJSON* value) {
    return cJSON_IsString(value) && value->valuestring != nullptr;
}

bool IsEmptyObject(const cJSON* value) {
    return cJSON_IsObject(value) && value->child == nullptr;
}

cJSON* CreateToolJson(const ToolDefinition& tool) {
    cJSON* value = cJSON_CreateObject();
    cJSON* schema = cJSON_Parse(tool.input_schema);
    if (value == nullptr || schema == nullptr) {
        cJSON_Delete(schema);
        cJSON_Delete(value);
        return nullptr;
    }
    if (!cJSON_AddStringToObject(value, "name", tool.name) ||
        !cJSON_AddStringToObject(value, "description", tool.description) ||
        !cJSON_AddStringToObject(value, "audience",
                                tool.user_only ? "user" : "regular") ||
        !cJSON_AddStringToObject(value, "safetyClass", tool.safety_class) ||
        !cJSON_AddBoolToObject(value, "requiresConfirmation", tool.user_only) ||
        !cJSON_AddItemToObject(value, "inputSchema", schema)) {
        // schema remains caller-owned when cJSON_AddItemToObject fails.
        cJSON_Delete(schema);
        cJSON_Delete(value);
        return nullptr;
    }
    return value;
}

cJSON* CreateTextResult(const char* text) {
    cJSON* result = cJSON_CreateObject();
    if (result == nullptr) return nullptr;
    cJSON* content = cJSON_AddArrayToObject(result, "content");
    cJSON* item = cJSON_CreateObject();
    if (content == nullptr || item == nullptr ||
        !cJSON_AddStringToObject(item, "type", "text") ||
        !cJSON_AddStringToObject(item, "text", text)) {
        cJSON_Delete(item);
        cJSON_Delete(result);
        return nullptr;
    }
    if (!cJSON_AddItemToArray(content, item)) {
        cJSON_Delete(item);
        cJSON_Delete(result);
        return nullptr;
    }
    if (!cJSON_AddBoolToObject(result, "isError", false)) {
        cJSON_Delete(result);
        return nullptr;
    }
    return result;
}

bool AddCounterFields(cJSON* value, const audio::AudioCounters& counters) {
    return cJSON_AddNumberToObject(value, "mic_frames", counters.mic_frames) &&
           cJSON_AddNumberToObject(value, "mic_samples",
                                   counters.mic_samples) &&
           cJSON_AddNumberToObject(value, "mic_read_errors",
                                   counters.mic_read_errors) &&
           cJSON_AddNumberToObject(value, "mic_read_timeouts",
                                   counters.mic_read_timeouts) &&
           cJSON_AddNumberToObject(value, "detector_frame_drops",
                                   counters.detector_frame_drops) &&
           cJSON_AddNumberToObject(value, "opus_encode_failures",
                                   counters.opus_encode_failures) &&
           cJSON_AddNumberToObject(value, "uplink_drops",
                                   counters.uplink_drops) &&
           cJSON_AddNumberToObject(value, "playback_queue_drops",
                                   counters.playback_queue_drops) &&
           cJSON_AddNumberToObject(value, "playback_queue_high_water",
                                   counters.playback_queue_high_water) &&
           cJSON_AddNumberToObject(value, "opus_decode_failures",
                                   counters.opus_decode_failures) &&
           cJSON_AddNumberToObject(value, "speaker_write_failures",
                                   counters.speaker_write_failures);
}

cJSON* CreateAudioDiagnosticJson(
    const audio::AudioDiagnosticSnapshot& diagnostic) {
    cJSON* value = cJSON_CreateObject();
    cJSON* counters =
        value == nullptr ? nullptr : cJSON_AddObjectToObject(value, "counters");
    if (value == nullptr || counters == nullptr ||
        !cJSON_AddStringToObject(value, "state",
                                audio::ToString(diagnostic.state)) ||
        !cJSON_AddNumberToObject(value, "session_id",
                                diagnostic.session_id) ||
        !cJSON_AddNumberToObject(value, "duration_seconds",
                                diagnostic.duration_seconds) ||
        !cJSON_AddNumberToObject(value, "started_ms",
                                diagnostic.started_ms) ||
        !cJSON_AddNumberToObject(value, "ends_ms", diagnostic.ends_ms) ||
        !cJSON_AddNumberToObject(value, "pcm_frames",
                                diagnostic.pcm_frames) ||
        !cJSON_AddNumberToObject(value, "sample_count",
                                diagnostic.sample_count) ||
        !cJSON_AddNumberToObject(value, "rms", diagnostic.rms) ||
        !cJSON_AddNumberToObject(value, "peak_absolute",
                                diagnostic.peak_absolute) ||
        !cJSON_AddNumberToObject(value, "dc_offset",
                                diagnostic.dc_offset) ||
        !cJSON_AddNumberToObject(value, "clipped_samples",
                                diagnostic.clipped_samples) ||
        !cJSON_AddNumberToObject(value, "clipping_percent",
                                diagnostic.clipping_percent) ||
        !cJSON_AddBoolToObject(value, "raw_audio_stored", false) ||
        !AddCounterFields(counters, diagnostic.counters)) {
        cJSON_Delete(value);
        return nullptr;
    }
    return value;
}

cJSON* CreateHealthJson(const DeviceDiagnostics& diagnostics) {
    cJSON* root = cJSON_CreateObject();
    cJSON* device =
        root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "device");
    cJSON* memory =
        root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "memory");
    cJSON* network =
        root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "network");
    cJSON* audio =
        root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "audio");
    cJSON* resources =
        root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "resources");
    cJSON* lifetime =
        audio == nullptr ? nullptr : cJSON_AddObjectToObject(audio, "lifetime");
    cJSON* diagnostic = CreateAudioDiagnosticJson(
        diagnostics.audio.diagnostic);
    if (root == nullptr || device == nullptr || memory == nullptr ||
        network == nullptr || audio == nullptr || resources == nullptr ||
        lifetime == nullptr || diagnostic == nullptr ||
        !cJSON_AddNumberToObject(root, "schema_version", 1) ||
        !cJSON_AddStringToObject(device, "board", kBoardName) ||
        !cJSON_AddStringToObject(device, "firmware_version",
                                diagnostics.device.firmware_version) ||
        !cJSON_AddStringToObject(device, "state",
                                diagnostics.device.state) ||
        !cJSON_AddBoolToObject(device, "assistant_gate_open",
                              diagnostics.device.assistant_gate_open) ||
        !cJSON_AddNumberToObject(device, "uptime_ms",
                                diagnostics.uptime_ms) ||
        !cJSON_AddStringToObject(device, "reset_reason",
                                diagnostics.reset_reason) ||
        !cJSON_AddNumberToObject(memory, "internal_free_bytes",
                                diagnostics.internal_free_bytes) ||
        !cJSON_AddNumberToObject(memory, "internal_min_free_bytes",
                                diagnostics.internal_min_free_bytes) ||
        !cJSON_AddNumberToObject(memory, "psram_free_bytes",
                                diagnostics.psram_free_bytes) ||
        !cJSON_AddNumberToObject(memory, "psram_min_free_bytes",
                                diagnostics.psram_min_free_bytes) ||
        !cJSON_AddBoolToObject(network, "connected",
                              diagnostics.network_connected) ||
        !cJSON_AddNumberToObject(network, "rssi",
                                diagnostics.network_rssi) ||
        !cJSON_AddStringToObject(network, "ipv4",
                                diagnostics.network_ipv4.data()) ||
        !cJSON_AddNumberToObject(network, "disconnect_count",
                                diagnostics.network_disconnect_count) ||
        !cJSON_AddNumberToObject(network, "reconnect_attempt_count",
                                diagnostics.network_reconnect_attempt_count) ||
        !cJSON_AddNumberToObject(network, "last_disconnect_reason",
                                diagnostics.network_last_disconnect_reason) ||
        !cJSON_AddBoolToObject(audio, "capture_task_running",
                              diagnostics.audio.capture_task_running) ||
        !cJSON_AddBoolToObject(audio, "playback_task_running",
                              diagnostics.audio.playback_task_running) ||
        !AddCounterFields(lifetime, diagnostics.audio.lifetime) ||
        !cJSON_AddBoolToObject(resources, "wake_resource_healthy",
                              diagnostics.wake_resource_healthy) ||
        !cJSON_AddBoolToObject(resources, "ui_pack_healthy",
                              diagnostics.ui_pack_healthy) ||
        !cJSON_AddNumberToObject(resources, "wake_dropped_frames",
                                diagnostics.wake_dropped_frames) ||
        !cJSON_AddItemToObject(audio, "diagnostic", diagnostic)) {
        cJSON_Delete(diagnostic);
        cJSON_Delete(root);
        return nullptr;
    }
    return root;
}

bool AddSelfTestCheck(cJSON* checks, const char* id, const char* status,
                      const char* detail, bool requires_listener = false) {
    cJSON* check = cJSON_CreateObject();
    if (check == nullptr ||
        !cJSON_AddStringToObject(check, "id", id) ||
        !cJSON_AddStringToObject(check, "status", status) ||
        !cJSON_AddStringToObject(check, "detail", detail) ||
        !cJSON_AddBoolToObject(check, "requires_listener",
                              requires_listener) ||
        !cJSON_AddItemToArray(checks, check)) {
        cJSON_Delete(check);
        return false;
    }
    return true;
}

cJSON* CreateSelfTestJson(const DeviceDiagnostics& diagnostics) {
    const bool mic_observed = diagnostics.audio.lifetime.mic_frames > 0;
    const bool passed = diagnostics.network_connected &&
                        diagnostics.audio.capture_task_running &&
                        diagnostics.audio.playback_task_running &&
                        mic_observed &&
                        diagnostics.internal_free_bytes > 0 &&
                        diagnostics.psram_free_bytes > 0 &&
                        diagnostics.wake_resource_healthy &&
                        diagnostics.ui_pack_healthy;
    cJSON* root = cJSON_CreateObject();
    cJSON* checks =
        root == nullptr ? nullptr : cJSON_AddArrayToObject(root, "checks");
    if (root == nullptr || checks == nullptr ||
        !cJSON_AddNumberToObject(root, "schema_version", 1) ||
        !cJSON_AddNumberToObject(root, "run_at_uptime_ms",
                                diagnostics.uptime_ms) ||
        !cJSON_AddStringToObject(root, "overall",
                                passed ? "pass" : "fail") ||
        !AddSelfTestCheck(checks, "application_state", "pass",
                          "Application state provider is available.") ||
        !AddSelfTestCheck(checks, "wifi_connected",
                          diagnostics.network_connected ? "pass" : "fail",
                          diagnostics.network_connected
                              ? "Station has an active IP connection."
                              : "Station is not connected.") ||
        !AddSelfTestCheck(checks, "capture_task",
                          diagnostics.audio.capture_task_running ? "pass"
                                                                 : "fail",
                          diagnostics.audio.capture_task_running
                              ? "Capture task is running."
                              : "Capture task is unavailable.") ||
        !AddSelfTestCheck(checks, "playback_task",
                          diagnostics.audio.playback_task_running ? "pass"
                                                                  : "fail",
                          diagnostics.audio.playback_task_running
                              ? "Playback task is running."
                              : "Playback task is unavailable.") ||
        !AddSelfTestCheck(checks, "mic_frames_observed",
                          mic_observed ? "pass" : "fail",
                          mic_observed
                              ? "PCM frames have been observed."
                              : "No PCM frame has been observed.") ||
        !AddSelfTestCheck(checks, "internal_heap",
                          diagnostics.internal_free_bytes > 0 ? "pass"
                                                              : "fail",
                          diagnostics.internal_free_bytes > 0
                              ? "Internal heap is available."
                              : "Internal heap is exhausted.") ||
        !AddSelfTestCheck(checks, "psram",
                          diagnostics.psram_free_bytes > 0 ? "pass" : "fail",
                          diagnostics.psram_free_bytes > 0
                              ? "PSRAM is available."
                              : "PSRAM is unavailable.") ||
        !AddSelfTestCheck(checks, "wake_resource",
                          diagnostics.wake_resource_healthy ? "pass" : "fail",
                          diagnostics.wake_resource_healthy
                              ? "Wake subsystem is healthy."
                              : "Wake subsystem is unhealthy.") ||
        !AddSelfTestCheck(checks, "display_ui",
                          diagnostics.ui_pack_healthy ? "pass" : "fail",
                          diagnostics.ui_pack_healthy
                              ? "Display/UI health check passed."
                              : "Display/UI health check failed.") ||
        !AddSelfTestCheck(checks, "physical_speaker_output", "not_run",
                          "Physical sound requires a nearby listener.", true)) {
        cJSON_Delete(root);
        return nullptr;
    }
    return root;
}

cJSON* CreateJsonTextResult(cJSON* value) {
    if (value == nullptr) return nullptr;
    char* encoded = cJSON_PrintUnformatted(value);
    cJSON_Delete(value);
    if (encoded == nullptr) return nullptr;
    cJSON* result = CreateTextResult(encoded);
    cJSON_free(encoded);
    return result;
}

const ToolDefinition* FindTool(const char* name) {
    for (const auto& tool : kTools) {
        if (std::strcmp(tool.name, name) == 0) return &tool;
    }
    return nullptr;
}

}  // namespace

bool DeviceMcp::Initialize(StatusProvider status_provider,
                           DiagnosticsProvider diagnostics_provider,
                           AudioDiagnosticStarter audio_diagnostic_starter,
                           VolumeSetter volume_setter,
                           ResponseSink response_sink, void* context) {
    if (status_provider == nullptr || diagnostics_provider == nullptr ||
        audio_diagnostic_starter == nullptr || volume_setter == nullptr ||
        response_sink == nullptr) {
        return false;
    }
    status_provider_ = status_provider;
    diagnostics_provider_ = diagnostics_provider;
    audio_diagnostic_starter_ = audio_diagnostic_starter;
    volume_setter_ = volume_setter;
    response_sink_ = response_sink;
    context_ = context;
    return true;
}

bool DeviceMcp::HandleEnvelope(const char* envelope, std::size_t length) {
    if (envelope == nullptr || length == 0 ||
        length > kMaximumInnerPayloadBytes + 192 || response_sink_ == nullptr) {
        return false;
    }
    cJSON* root = cJSON_ParseWithLength(envelope, length);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON* type = cJSON_GetObjectItemCaseSensitive(root, "type");
    const cJSON* session_id =
        cJSON_GetObjectItemCaseSensitive(root, "session_id");
    const cJSON* payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    if (!IsStringValue(type) || std::strcmp(type->valuestring, "mcp") != 0 ||
        !IsStringValue(session_id) || session_id->valuestring[0] == '\0' ||
        !cJSON_IsObject(payload)) {
        cJSON_Delete(root);
        return false;
    }

    const cJSON* version =
        cJSON_GetObjectItemCaseSensitive(payload, "jsonrpc");
    const cJSON* request_id = cJSON_GetObjectItemCaseSensitive(payload, "id");
    const cJSON* method = cJSON_GetObjectItemCaseSensitive(payload, "method");
    const cJSON* params = cJSON_GetObjectItemCaseSensitive(payload, "params");
    if (!IsStringValue(version) || std::strcmp(version->valuestring, "2.0") != 0 ||
        !IsRequestId(request_id) || !IsStringValue(method) ||
        (params != nullptr && !cJSON_IsObject(params))) {
        const bool replied = IsRequestId(request_id)
                                 ? ReplyError(request_id, -32600,
                                              "Invalid JSON-RPC request")
                                 : false;
        cJSON_Delete(root);
        return replied;
    }

    bool handled = false;
    if (std::strcmp(method->valuestring, "initialize") == 0) {
        handled = HandleInitialize(request_id, params);
    } else if (std::strcmp(method->valuestring, "tools/list") == 0) {
        handled = HandleToolsList(request_id, params);
    } else if (std::strcmp(method->valuestring, "tools/call") == 0) {
        handled = HandleToolsCall(request_id, params);
    } else {
        handled = ReplyError(request_id, -32601, "Method not found");
    }
    cJSON_Delete(root);
    return handled;
}

bool DeviceMcp::HandleInitialize(const void* request_id, const void* params) {
    if (params != nullptr && !cJSON_IsObject(AsJson(params))) {
        return ReplyError(request_id, -32602, "Invalid initialize params");
    }
    DeviceStatus status{};
    if (!status_provider_(&status, context_) || status.firmware_version == nullptr) {
        return ReplyError(request_id, -32000, "Device status unavailable");
    }

    cJSON* result = cJSON_CreateObject();
    if (result == nullptr) return false;
    cJSON* capabilities = cJSON_AddObjectToObject(result, "capabilities");
    if (capabilities == nullptr) {
        cJSON_Delete(result);
        return false;
    }
    cJSON* tools = cJSON_AddObjectToObject(capabilities, "tools");
    if (tools == nullptr) {
        cJSON_Delete(result);
        return false;
    }
    cJSON* server_info = cJSON_AddObjectToObject(result, "serverInfo");
    if (server_info == nullptr ||
        !cJSON_AddStringToObject(result, "protocolVersion", kProtocolVersion) ||
        !cJSON_AddStringToObject(server_info, "name", kBoardName) ||
        !cJSON_AddStringToObject(server_info, "version",
                                status.firmware_version)) {
        cJSON_Delete(result);
        return false;
    }
    return ReplyResult(request_id, result);
}

bool DeviceMcp::HandleToolsList(const void* request_id, const void* params_value) {
    const cJSON* params = AsJson(params_value);
    const char* cursor = "";
    bool with_user_tools = false;
    if (params != nullptr) {
        const cJSON* cursor_value =
            cJSON_GetObjectItemCaseSensitive(params, "cursor");
        const cJSON* user_value =
            cJSON_GetObjectItemCaseSensitive(params, "withUserTools");
        if ((cursor_value != nullptr && !IsStringValue(cursor_value)) ||
            (user_value != nullptr && !cJSON_IsBool(user_value))) {
            return ReplyError(request_id, -32602, "Invalid tools/list params");
        }
        if (cursor_value != nullptr) cursor = cursor_value->valuestring;
        if (user_value != nullptr) with_user_tools = cJSON_IsTrue(user_value);
    }

    std::size_t start = 0;
    if (cursor[0] != '\0') {
        start = kTools.size();
        for (std::size_t index = 0; index < kTools.size(); ++index) {
            if (std::strcmp(kTools[index].name, cursor) == 0) {
                start = index;
                break;
            }
        }
        if (start == kTools.size()) {
            return ReplyError(request_id, -32602, "Unknown tools/list cursor");
        }
    }

    cJSON* result = cJSON_CreateObject();
    if (result == nullptr) return false;
    cJSON* tools = cJSON_AddArrayToObject(result, "tools");
    if (tools == nullptr) {
        cJSON_Delete(result);
        return false;
    }

    const char* next_cursor = "";
    std::size_t estimated_bytes = 64;
    for (std::size_t index = start; index < kTools.size(); ++index) {
        const ToolDefinition& tool = kTools[index];
        if (tool.user_only && !with_user_tools) continue;
        cJSON* tool_json = CreateToolJson(tool);
        if (tool_json == nullptr) {
            cJSON_Delete(result);
            return false;
        }
        char* encoded = cJSON_PrintUnformatted(tool_json);
        if (encoded == nullptr) {
            cJSON_free(encoded);
            cJSON_Delete(tool_json);
            cJSON_Delete(result);
            return false;
        }
        const std::size_t tool_bytes = std::strlen(encoded);
        cJSON_free(encoded);
        if (estimated_bytes + tool_bytes + 64 > kMaximumToolListResultBytes) {
            next_cursor = tool.name;
            cJSON_Delete(tool_json);
            break;
        }
        estimated_bytes += tool_bytes + 1;
        if (!cJSON_AddItemToArray(tools, tool_json)) {
            cJSON_Delete(tool_json);
            cJSON_Delete(result);
            return false;
        }
    }
    if (!cJSON_AddStringToObject(result, "nextCursor", next_cursor)) {
        cJSON_Delete(result);
        return false;
    }
    return ReplyResult(request_id, result);
}

bool DeviceMcp::HandleToolsCall(const void* request_id, const void* params_value) {
    const cJSON* params = AsJson(params_value);
    const cJSON* name =
        cJSON_GetObjectItemCaseSensitive(params, "name");
    const cJSON* arguments =
        cJSON_GetObjectItemCaseSensitive(params, "arguments");
    if (!cJSON_IsObject(params) || !IsStringValue(name) ||
        !cJSON_IsObject(arguments)) {
        return ReplyError(request_id, -32602, "Invalid tools/call params");
    }
    const ToolDefinition* tool = FindTool(name->valuestring);
    if (tool == nullptr) {
        return ReplyError(request_id, -32601, "Unknown tool");
    }

    DeviceStatus status{};
    if (!status_provider_(&status, context_) || status.state == nullptr ||
        status.firmware_version == nullptr || status.volume_percent < 0 ||
        status.volume_percent > 100) {
        return ReplyError(request_id, -32000, "Device status unavailable");
    }

    if (tool->handler == ToolHandler::kSetVolume) {
        const cJSON* volume =
            cJSON_GetObjectItemCaseSensitive(arguments, "volume");
        if (arguments->child == nullptr || arguments->child->next != nullptr ||
            !cJSON_IsNumber(volume) || !std::isfinite(volume->valuedouble) ||
            std::floor(volume->valuedouble) != volume->valuedouble ||
            volume->valuedouble < 0 || volume->valuedouble > 100) {
            return ReplyError(request_id, -32602, "Invalid volume");
        }
        if (!volume_setter_(static_cast<int>(volume->valuedouble), context_)) {
            return ReplyError(request_id, -32000, "Unable to set volume");
        }
        return ReplyResult(request_id, CreateTextResult("true"));
    }
    if (tool->handler == ToolHandler::kDiagnosticsAudioStart) {
        const cJSON* duration =
            cJSON_GetObjectItemCaseSensitive(arguments, "duration_seconds");
        if (arguments->child == nullptr || arguments->child->next != nullptr ||
            !cJSON_IsNumber(duration) ||
            !std::isfinite(duration->valuedouble) ||
            std::floor(duration->valuedouble) != duration->valuedouble ||
            duration->valuedouble <
                audio::AudioDiagnostics::kMinimumDurationSeconds ||
            duration->valuedouble >
                audio::AudioDiagnostics::kMaximumDurationSeconds) {
            return ReplyError(request_id, -32602,
                              "Invalid audio diagnostic duration");
        }
        if (!audio_diagnostic_starter_(
                static_cast<std::uint32_t>(duration->valuedouble),
                context_)) {
            return ReplyError(request_id, -32001,
                              "Audio diagnostic is already running");
        }
        DeviceDiagnostics diagnostics{};
        if (!diagnostics_provider_(&diagnostics, context_)) {
            return ReplyError(request_id, -32000,
                              "Device diagnostics unavailable");
        }
        return ReplyResult(
            request_id,
            CreateJsonTextResult(CreateAudioDiagnosticJson(
                diagnostics.audio.diagnostic)));
    }
    if (!IsEmptyObject(arguments)) {
        return ReplyError(request_id, -32602, "Unexpected tool arguments");
    }

    if (tool->handler == ToolHandler::kGetVolume) {
        char volume[4] = {};
        std::snprintf(volume, sizeof(volume), "%d", status.volume_percent);
        return ReplyResult(request_id, CreateTextResult(volume));
    }
    if (tool->handler == ToolHandler::kDiagnosticsHealth ||
        tool->handler == ToolHandler::kDiagnosticsSelfTest) {
        DeviceDiagnostics diagnostics{};
        if (!diagnostics_provider_(&diagnostics, context_) ||
            diagnostics.device.state == nullptr ||
            diagnostics.device.firmware_version == nullptr ||
            diagnostics.reset_reason == nullptr) {
            return ReplyError(request_id, -32000,
                              "Device diagnostics unavailable");
        }
        return ReplyResult(
            request_id,
            CreateJsonTextResult(
                tool->handler == ToolHandler::kDiagnosticsHealth
                    ? CreateHealthJson(diagnostics)
                    : CreateSelfTestJson(diagnostics)));
    }

    cJSON* status_json = cJSON_CreateObject();
    if (status_json == nullptr ||
        !cJSON_AddStringToObject(status_json, "board", kBoardName) ||
        !cJSON_AddStringToObject(status_json, "firmware_version",
                                status.firmware_version)) {
        cJSON_Delete(status_json);
        return false;
    }
    if (tool->handler == ToolHandler::kDeviceStatus) {
        if (!cJSON_AddStringToObject(status_json, "state", status.state) ||
            !cJSON_AddBoolToObject(status_json, "assistant_gate_open",
                                  status.assistant_gate_open) ||
            !cJSON_AddNumberToObject(status_json, "volume_percent",
                                    status.volume_percent)) {
            cJSON_Delete(status_json);
            return false;
        }
    }
    char* encoded = cJSON_PrintUnformatted(status_json);
    cJSON_Delete(status_json);
    if (encoded == nullptr) return false;
    cJSON* result = CreateTextResult(encoded);
    cJSON_free(encoded);
    return ReplyResult(request_id, result);
}

bool DeviceMcp::ReplyResult(const void* request_id, void* result_value) {
    cJSON* result = AsMutableJson(result_value);
    if (result == nullptr) return false;
    cJSON* response = cJSON_CreateObject();
    if (response == nullptr ||
        !cJSON_AddStringToObject(response, "jsonrpc", "2.0")) {
        cJSON_Delete(result);
        cJSON_Delete(response);
        return false;
    }
    cJSON* id = cJSON_Duplicate(AsJson(request_id), true);
    if (id == nullptr || !cJSON_AddItemToObject(response, "id", id)) {
        cJSON_Delete(id);
        cJSON_Delete(result);
        cJSON_Delete(response);
        return false;
    }
    if (!cJSON_AddItemToObject(response, "result", result)) {
        cJSON_Delete(result);
        cJSON_Delete(response);
        return false;
    }
    return SendResponse(response);
}

bool DeviceMcp::ReplyError(const void* request_id, int code,
                           const char* message) {
    cJSON* response = cJSON_CreateObject();
    if (response == nullptr ||
        !cJSON_AddStringToObject(response, "jsonrpc", "2.0")) {
        cJSON_Delete(response);
        return false;
    }
    cJSON* id = cJSON_Duplicate(AsJson(request_id), true);
    if (id == nullptr || !cJSON_AddItemToObject(response, "id", id)) {
        cJSON_Delete(id);
        cJSON_Delete(response);
        return false;
    }
    cJSON* error = cJSON_AddObjectToObject(response, "error");
    if (error == nullptr || !cJSON_AddNumberToObject(error, "code", code) ||
        !cJSON_AddStringToObject(error, "message", message)) {
        cJSON_Delete(response);
        return false;
    }
    return SendResponse(response);
}

bool DeviceMcp::SendResponse(void* response_value) {
    cJSON* response = AsMutableJson(response_value);
    char* encoded = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);
    if (encoded == nullptr) return false;
    const std::size_t length = std::strlen(encoded);
    const bool sent = length <= kMaximumInnerPayloadBytes &&
                      response_sink_(encoded, length, context_);
    cJSON_free(encoded);
    return sent;
}

}  // namespace veetee::mcp
