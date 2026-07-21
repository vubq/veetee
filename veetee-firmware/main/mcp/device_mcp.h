#pragma once

#include <cstddef>

namespace veetee::mcp {

struct DeviceStatus {
    const char* state;
    bool assistant_gate_open;
    const char* firmware_version;
    int volume_percent;
};

class DeviceMcp {
public:
    using StatusProvider = bool (*)(DeviceStatus* status, void* context);
    using VolumeSetter = bool (*)(int volume_percent, void* context);
    using ResponseSink = bool (*)(const char* payload, std::size_t length,
                                  void* context);

    bool Initialize(StatusProvider status_provider, VolumeSetter volume_setter,
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
    VolumeSetter volume_setter_ = nullptr;
    ResponseSink response_sink_ = nullptr;
    void* context_ = nullptr;
};

}  // namespace veetee::mcp
