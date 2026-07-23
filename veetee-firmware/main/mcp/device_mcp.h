#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "audio/audio_diagnostics.h"

namespace veetee::mcp {

struct DeviceStatus {
    const char* state;
    bool assistant_gate_open;
    const char* firmware_version;
    int volume_percent;
};

struct DeviceDiagnostics {
    DeviceStatus device{};
    std::uint64_t uptime_ms = 0;
    const char* reset_reason = nullptr;
    std::uint32_t internal_free_bytes = 0;
    std::uint32_t internal_min_free_bytes = 0;
    std::uint32_t psram_free_bytes = 0;
    std::uint32_t psram_min_free_bytes = 0;
    bool network_connected = false;
    std::int32_t network_rssi = 0;
    std::array<char, 16> network_ipv4{};
    std::uint64_t network_disconnect_count = 0;
    std::uint64_t network_reconnect_attempt_count = 0;
    std::uint32_t network_last_disconnect_reason = 0;
    audio::AudioRuntimeHealth audio{};
    bool wake_resource_healthy = false;
    bool ui_pack_healthy = false;
    std::uint32_t wake_dropped_frames = 0;
};

class DeviceMcp {
public:
    using StatusProvider = bool (*)(DeviceStatus* status, void* context);
    using DiagnosticsProvider = bool (*)(DeviceDiagnostics* diagnostics,
                                         void* context);
    using AudioDiagnosticStarter = bool (*)(std::uint32_t duration_seconds,
                                            void* context);
    using VolumeSetter = bool (*)(int volume_percent, void* context);
    using ResponseSink = bool (*)(const char* payload, std::size_t length,
                                  void* context);

    bool Initialize(StatusProvider status_provider,
                    DiagnosticsProvider diagnostics_provider,
                    AudioDiagnosticStarter audio_diagnostic_starter,
                    VolumeSetter volume_setter,
                    ResponseSink response_sink, void* context);
    bool HandleEnvelope(const char* envelope, std::size_t length);

private:
    bool HandleInitialize(const void* request_id, const void* params);
    bool HandleToolsList(const void* request_id, const void* params);
    bool HandleToolsCall(const void* request_id, const void* params);
    bool ReplyResult(const void* request_id, void* result);
    bool ReplyError(const void* request_id, int code, const char* message);
    bool SendResponse(void* response);

    StatusProvider status_provider_ = nullptr;
    DiagnosticsProvider diagnostics_provider_ = nullptr;
    AudioDiagnosticStarter audio_diagnostic_starter_ = nullptr;
    VolumeSetter volume_setter_ = nullptr;
    ResponseSink response_sink_ = nullptr;
    void* context_ = nullptr;
};

}  // namespace veetee::mcp
