#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "cJSON.h"
#include "mcp/device_mcp.h"

namespace {

struct Harness {
    veetee::mcp::DeviceMcp mcp;
    std::string response;
    int volume = 70;
    std::uint32_t diagnostic_duration = 0;
    std::uint32_t capture_stack_free_bytes = 4'096;
};

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

std::string ReadFixture(const char* relative_path) {
    const std::filesystem::path path =
        std::filesystem::path(VEETEE_REPO_ROOT) / relative_path;
    std::ifstream stream(path);
    Expect(stream.good(), "fixture is readable");
    std::ostringstream content;
    content << stream.rdbuf();
    return content.str();
}

std::string FixturePayload(const char* relative_path) {
    const std::string fixture = ReadFixture(relative_path);
    cJSON* envelope = cJSON_Parse(fixture.c_str());
    cJSON* payload = cJSON_GetObjectItemCaseSensitive(envelope, "payload");
    char* encoded = cJSON_PrintUnformatted(payload);
    Expect(envelope != nullptr && payload != nullptr && encoded != nullptr,
           "fixture payload parses");
    std::string result(encoded);
    cJSON_free(encoded);
    cJSON_Delete(envelope);
    return result;
}

void ExpectJsonEquals(const std::string& actual, const std::string& expected,
                      const char* description) {
    cJSON* actual_json = cJSON_Parse(actual.c_str());
    cJSON* expected_json = cJSON_Parse(expected.c_str());
    const bool equal = actual_json != nullptr && expected_json != nullptr &&
                       cJSON_Compare(actual_json, expected_json, true);
    cJSON_Delete(actual_json);
    cJSON_Delete(expected_json);
    Expect(equal, description);
}

bool ReadStatus(veetee::mcp::DeviceStatus* status, void* context) {
    auto* harness = static_cast<Harness*>(context);
    status->state = "listening";
    status->assistant_gate_open = true;
    status->firmware_version = "0.1.0";
    status->volume_percent = harness->volume;
    return true;
}

bool SetVolume(int volume, void* context) {
    static_cast<Harness*>(context)->volume = volume;
    return true;
}

bool ReadDiagnostics(veetee::mcp::DeviceDiagnostics* diagnostics,
                     void* context) {
    if (diagnostics == nullptr) return false;
    *diagnostics = veetee::mcp::DeviceDiagnostics{};
    ReadStatus(&diagnostics->device, context);
    diagnostics->uptime_ms = 42'000;
    diagnostics->reset_reason = "software";
    diagnostics->internal_free_bytes = 64'000;
    diagnostics->internal_min_free_bytes = 48'000;
    diagnostics->psram_free_bytes = 4'000'000;
    diagnostics->psram_min_free_bytes = 3'500'000;
    diagnostics->network_connected = true;
    diagnostics->network_rssi = -52;
    std::snprintf(diagnostics->network_ipv4.data(),
                  diagnostics->network_ipv4.size(), "%s", "192.168.1.20");
    diagnostics->network_disconnect_count = 2;
    diagnostics->network_reconnect_attempt_count = 3;
    diagnostics->network_last_disconnect_reason = 201;
    diagnostics->audio.capture_task_running = true;
    diagnostics->audio.playback_task_running = true;
    const auto* harness = static_cast<Harness*>(context);
    diagnostics->audio.capture_stack_free_bytes =
        harness->capture_stack_free_bytes;
    diagnostics->audio.playback_stack_free_bytes = 5'120;
    diagnostics->audio.lifetime.mic_frames = 100;
    diagnostics->audio.lifetime.mic_samples = 32'000;
    diagnostics->capture_task = {
        .expected = true,
        .running = true,
        .stack_free_bytes = harness->capture_stack_free_bytes,
    };
    diagnostics->playback_task = {
        .expected = true,
        .running = true,
        .stack_free_bytes = 5'120,
    };
    diagnostics->wake_task = {
        .expected = true,
        .running = true,
        .stack_free_bytes = 3'072,
    };
    diagnostics->websocket_control_task = {
        .expected = true,
        .running = true,
        .stack_free_bytes = 6'144,
    };
    if (harness->diagnostic_duration > 0) {
        diagnostics->audio.diagnostic.state =
            veetee::audio::AudioDiagnosticState::kRunning;
        diagnostics->audio.diagnostic.session_id = 9;
        diagnostics->audio.diagnostic.duration_seconds =
            harness->diagnostic_duration;
        diagnostics->audio.diagnostic.started_ms = 42'000;
        diagnostics->audio.diagnostic.ends_ms =
            42'000 + harness->diagnostic_duration * 1000;
    }
    diagnostics->wake_resource_healthy = true;
    diagnostics->ui_pack_healthy = true;
    diagnostics->wake_dropped_frames = 4;
    return true;
}

bool StartDiagnostic(std::uint32_t duration_seconds, void* context) {
    auto* harness = static_cast<Harness*>(context);
    if (harness->diagnostic_duration > 0) return false;
    harness->diagnostic_duration = duration_seconds;
    return true;
}

bool CaptureResponse(const char* payload, std::size_t length, void* context) {
    static_cast<Harness*>(context)->response.assign(payload, length);
    return true;
}

void InitializeHarness(Harness* harness) {
    Expect(harness->mcp.Initialize(&ReadStatus, &ReadDiagnostics,
                                   &StartDiagnostic, &SetVolume,
                                   &CaptureResponse, harness),
           "MCP harness initializes");
}

std::string Envelope(const std::string& payload) {
    return "{\"session_id\":\"01J00000000000000000000000\",\"type\":\"mcp\",\"payload\":" +
           payload + "}";
}

void TestInitializeAndRegularCatalogFixtures() {
    Harness harness;
    InitializeHarness(&harness);
    const std::string initialize = ReadFixture(
        "veetee-server/packages/contracts/fixtures/mcp/initialize.json");
    Expect(harness.mcp.HandleEnvelope(initialize.data(), initialize.size()),
           "initialize request handles");
    ExpectJsonEquals(
        harness.response,
        FixturePayload(
            "veetee-server/packages/contracts/fixtures/mcp/initialize-result.json"),
        "initialize response matches shared fixture");

    const std::string list = ReadFixture(
        "veetee-server/packages/contracts/fixtures/mcp/tools-list.json");
    Expect(harness.mcp.HandleEnvelope(list.data(), list.size()),
           "tools/list request handles");
    ExpectJsonEquals(
        harness.response,
        FixturePayload(
            "veetee-server/packages/contracts/fixtures/mcp/tools-list-result.json"),
        "regular tool catalog matches shared fixture");
}

void TestVolumeCallAndArgumentSafety() {
    Harness harness;
    InitializeHarness(&harness);
    const std::string call = ReadFixture(
        "veetee-server/packages/contracts/fixtures/mcp/tools-call-volume.json");
    Expect(harness.mcp.HandleEnvelope(call.data(), call.size()),
           "volume call handles");
    Expect(harness.volume == 55, "volume setter receives exact value");
    ExpectJsonEquals(
        harness.response,
        FixturePayload(
            "veetee-server/packages/contracts/fixtures/mcp/tools-call-result.json"),
        "volume response matches shared fixture");

    const std::string invalid = Envelope(
        R"({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"self.audio_speaker.set_volume","arguments":{"volume":101}}})");
    Expect(harness.mcp.HandleEnvelope(invalid.data(), invalid.size()),
           "invalid volume returns JSON-RPC error");
    ExpectJsonEquals(
        harness.response,
        FixturePayload(
            "veetee-server/packages/contracts/fixtures/mcp/tools-call-error.json"),
        "invalid volume response matches shared fixture");
    Expect(harness.volume == 55, "invalid volume does not mutate device");

    const std::string extra = Envelope(
        R"({"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"self.audio_speaker.set_volume","arguments":{"volume":20,"extra":true}}})");
    Expect(harness.mcp.HandleEnvelope(extra.data(), extra.size()),
           "additional volume property returns error");
    cJSON* response = cJSON_Parse(harness.response.c_str());
    Expect(cJSON_IsObject(cJSON_GetObjectItemCaseSensitive(response, "error")),
           "additional property is rejected before mutation");
    cJSON_Delete(response);
    Expect(harness.volume == 55, "additional property does not mutate device");
}

void TestStatusPaginationAndUserOnlySplit() {
    Harness harness;
    InitializeHarness(&harness);
    const std::string status = Envelope(
        R"({"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"self.get_device_status","arguments":{}}})");
    Expect(harness.mcp.HandleEnvelope(status.data(), status.size()),
           "device status call handles");
    cJSON* response = cJSON_Parse(harness.response.c_str());
    cJSON* result = cJSON_GetObjectItemCaseSensitive(response, "result");
    cJSON* content = cJSON_GetObjectItemCaseSensitive(result, "content");
    cJSON* text = cJSON_GetObjectItemCaseSensitive(
        cJSON_GetArrayItem(content, 0), "text");
    cJSON* status_json = cJSON_Parse(text->valuestring);
    Expect(cJSON_IsString(cJSON_GetObjectItemCaseSensitive(status_json, "state")),
           "status result includes state");
    Expect(cJSON_GetObjectItemCaseSensitive(status_json, "volume_percent")
                   ->valueint == 70,
           "status result includes volume");
    cJSON_Delete(status_json);
    cJSON_Delete(response);

    const std::string cursor = Envelope(
        R"({"jsonrpc":"2.0","id":7,"method":"tools/list","params":{"cursor":"self.audio_speaker.get_volume","withUserTools":false}})");
    Expect(harness.mcp.HandleEnvelope(cursor.data(), cursor.size()),
           "catalog cursor handles");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "tools");
    Expect(cJSON_GetArraySize(content) == 2,
           "cursor resumes at the requested regular tool");
    cJSON_Delete(response);

    const std::string user_tools = ReadFixture(
        "veetee-server/packages/contracts/fixtures/mcp/tools-list-user.json");
    Expect(harness.mcp.HandleEnvelope(user_tools.data(), user_tools.size()),
           "user-only catalog handles");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "tools");
    Expect(cJSON_GetArraySize(content) == 7,
           "user-only catalog is hidden unless explicitly requested");
    ExpectJsonEquals(
        harness.response,
        FixturePayload(
            "veetee-server/packages/contracts/fixtures/mcp/tools-list-user-result.json"),
        "user-only diagnostic catalog matches shared fixture");
    cJSON_Delete(response);
}

void TestStructuredDiagnosticsAndBounds() {
    Harness harness;
    InitializeHarness(&harness);

    const std::string health_call = Envelope(
        R"({"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"self.diagnostics.get_health","arguments":{}}})");
    Expect(harness.mcp.HandleEnvelope(health_call.data(), health_call.size()),
           "health diagnostic handles");
    cJSON* response = cJSON_Parse(harness.response.c_str());
    cJSON* result = cJSON_GetObjectItemCaseSensitive(response, "result");
    cJSON* content = cJSON_GetObjectItemCaseSensitive(result, "content");
    cJSON* text = cJSON_GetObjectItemCaseSensitive(
        cJSON_GetArrayItem(content, 0), "text");
    cJSON* health = cJSON_Parse(text->valuestring);
    Expect(cJSON_GetObjectItemCaseSensitive(health, "schema_version")
                   ->valueint == 1,
           "health uses a versioned schema");
    Expect(cJSON_IsFalse(cJSON_GetObjectItemCaseSensitive(
               cJSON_GetObjectItemCaseSensitive(
                   cJSON_GetObjectItemCaseSensitive(health, "audio"),
                   "diagnostic"),
               "raw_audio_stored")),
           "health explicitly reports no raw audio storage");
    cJSON* tasks = cJSON_GetObjectItemCaseSensitive(health, "tasks");
    Expect(cJSON_GetObjectItemCaseSensitive(
               tasks, "minimum_stack_free_bytes")->valueint == 2'048,
           "health publishes one bounded task stack threshold");
    cJSON* capture =
        cJSON_GetObjectItemCaseSensitive(tasks, "capture");
    Expect(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(capture, "expected")) &&
               cJSON_IsTrue(
                   cJSON_GetObjectItemCaseSensitive(capture, "running")) &&
               cJSON_GetObjectItemCaseSensitive(
                   capture, "stack_free_bytes")->valueint == 4'096,
           "health reports capture task stack headroom in bytes");
    cJSON_Delete(health);
    cJSON_Delete(response);

    const std::string invalid = Envelope(
        R"({"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"self.diagnostics.audio.start","arguments":{"duration_seconds":31}}})");
    Expect(harness.mcp.HandleEnvelope(invalid.data(), invalid.size()),
           "out-of-range diagnostic returns an error");
    response = cJSON_Parse(harness.response.c_str());
    Expect(cJSON_GetObjectItemCaseSensitive(
               cJSON_GetObjectItemCaseSensitive(response, "error"), "code")
                   ->valueint == -32602,
           "duration bounds are enforced before callback");
    cJSON_Delete(response);
    Expect(harness.diagnostic_duration == 0,
           "invalid duration does not start a session");

    const std::string start = Envelope(
        R"({"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"self.diagnostics.audio.start","arguments":{"duration_seconds":5}}})");
    Expect(harness.mcp.HandleEnvelope(start.data(), start.size()),
           "bounded audio diagnostic starts");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "content");
    text = cJSON_GetObjectItemCaseSensitive(cJSON_GetArrayItem(content, 0),
                                            "text");
    cJSON* diagnostic = cJSON_Parse(text->valuestring);
    Expect(cJSON_GetObjectItemCaseSensitive(diagnostic, "duration_seconds")
                   ->valueint == 5,
           "start response reports requested duration");
    Expect(std::strcmp(cJSON_GetObjectItemCaseSensitive(diagnostic, "state")
                           ->valuestring,
                       "running") == 0,
           "start response reports running session");
    cJSON_Delete(diagnostic);
    cJSON_Delete(response);

    const std::string busy = Envelope(
        R"({"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"self.diagnostics.audio.start","arguments":{"duration_seconds":3}}})");
    Expect(harness.mcp.HandleEnvelope(busy.data(), busy.size()),
           "concurrent audio diagnostic returns an error");
    response = cJSON_Parse(harness.response.c_str());
    Expect(cJSON_GetObjectItemCaseSensitive(
               cJSON_GetObjectItemCaseSensitive(response, "error"), "code")
                   ->valueint == -32001,
           "busy session uses a stable device error");
    cJSON_Delete(response);

    const std::string self_test = Envelope(
        R"({"jsonrpc":"2.0","id":14,"method":"tools/call","params":{"name":"self.diagnostics.run_self_test","arguments":{}}})");
    Expect(harness.mcp.HandleEnvelope(self_test.data(), self_test.size()),
           "self-test handles");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "content");
    text = cJSON_GetObjectItemCaseSensitive(cJSON_GetArrayItem(content, 0),
                                            "text");
    cJSON* self_test_result = cJSON_Parse(text->valuestring);
    Expect(std::strcmp(
               cJSON_GetObjectItemCaseSensitive(self_test_result, "overall")
                   ->valuestring,
               "pass") == 0,
           "software checks pass in healthy harness");
    cJSON* checks =
        cJSON_GetObjectItemCaseSensitive(self_test_result, "checks");
    Expect(cJSON_GetArraySize(checks) == 11,
           "self-test returns the bounded check catalog");
    cJSON* stack_headroom = cJSON_GetArrayItem(checks, 4);
    Expect(std::strcmp(
               cJSON_GetObjectItemCaseSensitive(stack_headroom, "id")
                   ->valuestring,
               "task_stack_headroom") == 0 &&
               std::strcmp(
                   cJSON_GetObjectItemCaseSensitive(stack_headroom, "status")
                       ->valuestring,
                   "pass") == 0,
           "self-test evaluates the common task headroom threshold");
    cJSON* speaker = cJSON_GetArrayItem(checks, 10);
    Expect(std::strcmp(
               cJSON_GetObjectItemCaseSensitive(speaker, "status")
                   ->valuestring,
               "not_run") == 0 &&
               cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(
                   speaker, "requires_listener")),
           "physical speaker check is not falsely reported as passed");
    cJSON_Delete(self_test_result);
    cJSON_Delete(response);

    harness.capture_stack_free_bytes = 1'024;
    Expect(harness.mcp.HandleEnvelope(self_test.data(), self_test.size()),
           "low stack headroom self-test handles");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "content");
    text = cJSON_GetObjectItemCaseSensitive(cJSON_GetArrayItem(content, 0),
                                            "text");
    self_test_result = cJSON_Parse(text->valuestring);
    Expect(std::strcmp(
               cJSON_GetObjectItemCaseSensitive(self_test_result, "overall")
                   ->valuestring,
               "fail") == 0,
           "low stack headroom fails the self-test before overflow");
    checks = cJSON_GetObjectItemCaseSensitive(self_test_result, "checks");
    stack_headroom = cJSON_GetArrayItem(checks, 4);
    Expect(std::strcmp(
               cJSON_GetObjectItemCaseSensitive(stack_headroom, "status")
                   ->valuestring,
               "fail") == 0,
           "low stack headroom identifies the bounded stack check");
    cJSON_Delete(self_test_result);
    cJSON_Delete(response);
}

void TestMalformedEnvelopeAndUnknownTool() {
    Harness harness;
    InitializeHarness(&harness);
    const std::string malformed =
        R"({"session_id":"session-1","type":"mcp","payload":[]})";
    Expect(!harness.mcp.HandleEnvelope(malformed.data(), malformed.size()),
           "malformed outer envelope is rejected");

    const std::string unknown = Envelope(
        R"({"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"self.unknown","arguments":{}}})");
    Expect(harness.mcp.HandleEnvelope(unknown.data(), unknown.size()),
           "unknown tool returns JSON-RPC error");
    cJSON* response = cJSON_Parse(harness.response.c_str());
    cJSON* error = cJSON_GetObjectItemCaseSensitive(response, "error");
    Expect(cJSON_GetObjectItemCaseSensitive(error, "code")->valueint == -32601,
           "unknown tool uses method-not-found code");
    cJSON_Delete(response);
}

}  // namespace

int main() {
    TestInitializeAndRegularCatalogFixtures();
    TestVolumeCallAndArgumentSafety();
    TestStatusPaginationAndUserOnlySplit();
    TestStructuredDiagnosticsAndBounds();
    TestMalformedEnvelopeAndUnknownTool();
    std::cout << "device_mcp_test: passed\n";
    return 0;
}
