#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "cJSON.h"
#include "transport/protocol_v1.h"

namespace {

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

void ExpectJsonEquals(const char* actual, const std::string& expected,
                      const char* description) {
    cJSON* actual_json = cJSON_Parse(actual);
    cJSON* expected_json = cJSON_Parse(expected.c_str());
    const bool equal = actual_json != nullptr && expected_json != nullptr &&
                       cJSON_Compare(actual_json, expected_json, true);
    cJSON_Delete(actual_json);
    cJSON_Delete(expected_json);
    Expect(equal, description);
}

void TestContractBuilders() {
    using namespace veetee::transport;

    ExpectJsonEquals(
        DeviceHelloJson(),
        ReadFixture("veetee-server/packages/contracts/fixtures/ws/device-hello-v1.json"),
        "device hello matches shared fixture");

    char buffer[384] = {};
    std::size_t length = 0;
    constexpr char kSession[] = "01J00000000000000000000000";
    Expect(BuildListenStart(kSession, WakeSource::kButton, buffer, sizeof(buffer),
                            &length),
           "button listen event builds");
    Expect(length > 0, "button listen event has bytes");
    ExpectJsonEquals(
        buffer,
        ReadFixture(
            "veetee-server/packages/contracts/fixtures/ws/listen-start-button-auto.json"),
        "button listen event matches shared fixture");

    Expect(BuildListenStart(kSession, WakeSource::kWakeWord, buffer,
                            sizeof(buffer), &length),
           "wake listen event builds");
    ExpectJsonEquals(
        buffer,
        ReadFixture(
            "veetee-server/packages/contracts/fixtures/ws/listen-start-wake-auto.json"),
        "wake listen event matches shared fixture");

    Expect(BuildAbort(kSession, "wake_word_detected", "button", buffer,
                      sizeof(buffer), &length),
           "abort event builds");
    const std::string expected_abort =
        R"({"session_id":"01J00000000000000000000000","type":"abort","reason":"wake_word_detected","source":"button"})";
    ExpectJsonEquals(buffer, expected_abort, "abort event has contract fields");
}

void TestServerHelloParser() {
    using namespace veetee::transport;
    const std::string hello = ReadFixture(
        "veetee-server/packages/contracts/fixtures/ws/server-hello-v1.json");
    ServerEvent event{};
    Expect(ParseServerEvent(hello.data(), hello.size(), &event),
           "server hello fixture parses");
    Expect(event.kind == ServerEventKind::kHello, "server hello kind");
    Expect(std::string(event.session_id) == "01J00000000000000000000000",
           "server session id copied");

    const std::string wrong_rate =
        R"({"type":"hello","transport":"websocket","session_id":"session-1","audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}})";
    Expect(!ParseServerEvent(wrong_rate.data(), wrong_rate.size(), &event),
           "wrong downlink sample rate rejected");

    const std::string missing_session =
        R"({"type":"hello","transport":"websocket","audio_params":{"format":"opus","sample_rate":24000,"channels":1,"frame_duration":60}})";
    Expect(!ParseServerEvent(missing_session.data(), missing_session.size(), &event),
           "missing session id rejected");
}

void TestFragmentAssembly() {
    using namespace veetee::transport;
    TextFrameAssembler assembler;
    const char* message = nullptr;
    std::size_t length = 0;
    const std::string payload =
        R"({"type":"hello","transport":"websocket","session_id":"session-1","audio_params":{"format":"opus","sample_rate":24000,"channels":1,"frame_duration":60}})";
    const std::size_t split = 47;
    Expect(assembler.Append(0x1, true, payload.size(), 0, payload.data(), split,
                            &message, &length) == AssembleResult::kIncomplete,
           "first TCP chunk is incomplete");
    Expect(assembler.Append(0x1, true, payload.size(), split,
                            payload.data() + split, payload.size() - split,
                            &message, &length) == AssembleResult::kComplete,
           "second TCP chunk completes text frame");
    Expect(std::string(message, length) == payload, "assembled text is exact");

    assembler.Reset();
    const std::string first = R"({"type":"st)";
    const std::string second = R"(t","session_id":"session-1"})";
    Expect(assembler.Append(0x1, false, first.size(), 0, first.data(), first.size(),
                            &message, &length) == AssembleResult::kIncomplete,
           "fragmented text waits for continuation");
    Expect(assembler.Append(0x0, true, second.size(), 0, second.data(), second.size(),
                            &message, &length) == AssembleResult::kComplete,
           "continuation completes fragmented text");
    Expect(std::string(message, length) == first + second,
           "fragmented text is exact");

    assembler.Reset();
    Expect(assembler.Append(0x1, true, 10, 4, "bad", 3, &message, &length) ==
               AssembleResult::kError,
           "gap at first chunk is rejected");

    std::string oversized(kMaximumControlFrameBytes + 1, 'x');
    Expect(assembler.Append(0x1, true, oversized.size(), 0, oversized.data(),
                            oversized.size(), &message, &length) ==
               AssembleResult::kError,
           "oversized control frame is rejected");
}

void TestRuntimeEventsAndBinaryAssembly() {
    using namespace veetee::transport;
    constexpr char kSession[] = "session-1";
    struct EventFixture {
        const char* json;
        ServerEventKind kind;
    };
    constexpr EventFixture events[] = {
        {R"({"session_id":"session-1","type":"listen","state":"start"})",
         ServerEventKind::kListenStart},
        {R"({"session_id":"session-1","type":"stt","text":"xin chao"})",
         ServerEventKind::kStt},
        {R"({"session_id":"session-1","type":"llm","emotion":"thinking"})",
         ServerEventKind::kLlm},
        {R"({"session_id":"session-1","type":"llm","emotion":"neutral","text":"xin"})",
         ServerEventKind::kOther},
        {R"({"session_id":"session-1","type":"tts","state":"start"})",
         ServerEventKind::kTtsStart},
        {R"({"session_id":"session-1","type":"tts","state":"stop"})",
         ServerEventKind::kTtsStop},
        {R"({"session_id":"session-1","type":"system","command":"assistant_sleep"})",
         ServerEventKind::kAssistantSleep},
        {R"({"session_id":"session-1","type":"mcp","payload":{"jsonrpc":"2.0","id":1,"result":{}}})",
         ServerEventKind::kMcp},
    };
    for (const auto& fixture : events) {
        ServerEvent event{};
        Expect(ParseServerEvent(fixture.json, std::strlen(fixture.json), &event),
               "runtime server event parses");
        Expect(event.kind == fixture.kind, "runtime server event kind");
        Expect(std::string(event.session_id) == kSession,
               "runtime server event session");
    }

    BinaryFrameAssembler assembler;
    const std::uint8_t* packet = nullptr;
    std::size_t packet_length = 0;
    const std::string opus = "bounded-opus-packet";
    Expect(assembler.Append(0x2, true, opus.size(), 0, opus.data(), 5,
                            &packet, &packet_length) ==
               AssembleResult::kIncomplete,
           "first binary TCP chunk is incomplete");
    Expect(assembler.Append(0x2, true, opus.size(), 5, opus.data() + 5,
                            opus.size() - 5, &packet, &packet_length) ==
               AssembleResult::kComplete,
           "second binary TCP chunk completes packet");
    Expect(packet_length == opus.size() &&
               std::memcmp(packet, opus.data(), opus.size()) == 0,
           "assembled binary packet is exact");

    std::string oversized(kMaximumOpusPacketBytes + 1, 'x');
    Expect(assembler.Append(0x2, true, oversized.size(), 0, oversized.data(),
                            oversized.size(), &packet, &packet_length) ==
               AssembleResult::kError,
           "oversized Opus packet is rejected");
}

}  // namespace

int main() {
    TestContractBuilders();
    TestServerHelloParser();
    TestFragmentAssembly();
    TestRuntimeEventsAndBinaryAssembly();
    std::cout << "protocol_v1_test: passed\n";
    return 0;
}
