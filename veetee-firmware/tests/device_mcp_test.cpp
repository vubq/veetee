#include <cstdlib>
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

bool CaptureResponse(const char* payload, std::size_t length, void* context) {
    static_cast<Harness*>(context)->response.assign(payload, length);
    return true;
}

void InitializeHarness(Harness* harness) {
    Expect(harness->mcp.Initialize(&ReadStatus, &SetVolume, &CaptureResponse,
                                   harness),
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

    const std::string user_tools = Envelope(
        R"({"jsonrpc":"2.0","id":8,"method":"tools/list","params":{"cursor":"","withUserTools":true}})");
    Expect(harness.mcp.HandleEnvelope(user_tools.data(), user_tools.size()),
           "user-only catalog handles");
    response = cJSON_Parse(harness.response.c_str());
    result = cJSON_GetObjectItemCaseSensitive(response, "result");
    content = cJSON_GetObjectItemCaseSensitive(result, "tools");
    Expect(cJSON_GetArraySize(content) == 4,
           "user-only catalog is hidden unless explicitly requested");
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
    TestMalformedEnvelopeAndUnknownTool();
    std::cout << "device_mcp_test: passed\n";
    return 0;
}
