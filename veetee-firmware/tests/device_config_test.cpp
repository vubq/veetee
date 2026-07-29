#include <array>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "config/device_config.h"
#include "config/device_config_resource_policy.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

std::string ReadFixture(const char* name = "device-config-v1.json") {
    const std::filesystem::path path =
        std::filesystem::path(VEETEE_REPO_ROOT) /
        "veetee-server/packages/contracts/fixtures/config" / name;
    std::ifstream stream(path);
    Expect(stream.good(), "device-config fixture is readable");
    std::ostringstream output;
    output << stream.rdbuf();
    return output.str();
}

std::string ReadArtifactFixture(const char* name) {
    const std::filesystem::path path =
        std::filesystem::path(VEETEE_REPO_ROOT) /
        "veetee-server/packages/contracts/fixtures/artifacts" / name;
    std::ifstream stream(path);
    Expect(stream.good(), "artifact fixture is readable");
    std::ostringstream output;
    output << stream.rdbuf();
    return output.str();
}

veetee::ota::DeviceResourceCapability ResourceCapability() {
    static constexpr veetee::ota::SupportedResourceRuntime kRuntimes[] = {
        {.kind = "model_pack", .runtime = "esp-sr", .runtime_abi = 1},
    };
    return {
        .manifest_kind = "resource_bundle",
        .content_type = "application/vnd.veetee.esp-sr-model-pack",
        .board = "veetee-s3-n16r8",
        .chip = "esp32s3",
        .firmware_version = "0.2.0",
        .resource_abi = 1,
        .ui_abi = 0,
        .flash_bytes = 16777216,
        .psram_bytes = 8388608,
        .resource_slot_bytes = 4194304,
        .supported_runtimes = kRuntimes,
        .supported_runtime_count = std::size(kRuntimes),
    };
}

std::array<std::uint8_t, 32> PublicKey() {
    constexpr char kHex[] =
        "238eb0ac501669721bbab10734bcd770a4165ab1d6c49cfb30d1e27b9088d7b7";
    std::array<std::uint8_t, 32> key{};
    auto nibble = [](char value) {
        return value <= '9' ? value - '0' : value - 'a' + 10;
    };
    for (std::size_t index = 0; index < key.size(); ++index) {
        key[index] = static_cast<std::uint8_t>(
            (nibble(kHex[index * 2]) << 4) | nibble(kHex[index * 2 + 1]));
    }
    return key;
}

std::string Replace(std::string value, const std::string& before,
                    const std::string& after) {
    const std::size_t offset = value.find(before);
    Expect(offset != std::string::npos, "mutation source exists");
    value.replace(offset, before.size(), after);
    return value;
}

void ExpectFailureWithoutMutation(
    const std::string& document,
    veetee::config::DeviceConfigError expected,
    const veetee::ota::TrustedReleaseKey& key,
    const char* description) {
    veetee::config::DeviceConfig output{};
    output.version = 777;
    const auto error = veetee::config::VerifyDeviceConfig(
        document, "d74f1594-765c-4bd5-b07b-6443273777ed", 8, &key, 1,
        &output);
    Expect(error == expected, description);
    Expect(output.version == 777, "failed verify does not mutate output");
}

}  // namespace

int main() {
    using veetee::config::DeviceConfig;
    using veetee::config::DeviceConfigError;
    using veetee::config::VerifyDeviceConfig;
    const std::string fixture = ReadFixture();
    const veetee::ota::TrustedReleaseKey key{
        .key_id = "veetee-dev-release-2026-01",
        .minimum_security_epoch = 1,
        .public_key = PublicKey(),
    };

    DeviceConfig parsed{};
    Expect(VerifyDeviceConfig(
               fixture, "d74f1594-765c-4bd5-b07b-6443273777ed", 8,
               &key, 1, &parsed) == DeviceConfigError::kOk,
           "signed fixture verifies");
    Expect(parsed.version == 8 && parsed.has_wake_profile,
           "snapshot identity and wake profile parsed");
    Expect(std::string(parsed.activation.model_id.data()) == "wn9s_hiesp",
           "exact ESP-SR model id parsed");
    Expect(parsed.activation.threshold_ppm == 640000 &&
               parsed.activation.cooldown_ms == 1200,
           "integer detector bounds parsed");
    Expect(!parsed.interrupt.enabled && !parsed.send_wake_audio,
           "nullable interrupt and privacy default parsed");

    veetee::ota::VerifiedResourceManifest linked_manifest{};
    const std::string linked_manifest_document =
        ReadArtifactFixture("resource-config-link-v1.json");
    Expect(veetee::ota::VerifyResourceManifest(
               linked_manifest_document, ResourceCapability(), &key, 1,
               &linked_manifest) == veetee::ota::ResourceManifestError::kOk,
           "paired signed resource fixture verifies");
    veetee::settings::ResourceDetectorInventory linked_inventory{};
    std::strcpy(linked_inventory.activation_model_id,
                linked_manifest.activation_model_id);
    std::strcpy(linked_inventory.interrupt_model_id,
                linked_manifest.interrupt_model_id);
    Expect(veetee::config::ValidateDeviceConfigResourceLink(
               parsed, linked_manifest.version, linked_inventory) ==
               veetee::config::DeviceConfigResourceLinkError::kOk,
           "signed config and signed manifest link exact detector role");

    const std::string wake_audio_fixture =
        ReadFixture("device-config-wake-audio-v1.json");
    DeviceConfig wake_audio{};
    Expect(VerifyDeviceConfig(
               wake_audio_fixture,
               "d74f1594-765c-4bd5-b07b-6443273777ed", 9, &key, 1,
               &wake_audio) == DeviceConfigError::kOk,
           "signed privacy opt-in fixture verifies");
    Expect(wake_audio.send_wake_audio,
           "signed wake-audio opt-in reaches firmware config");

    DeviceConfig untouched{};
    untouched.version = 55;
    Expect(VerifyDeviceConfig(
               fixture, "other-device", 8, &key, 1, &untouched) ==
               DeviceConfigError::kDeviceMismatch,
           "wrong device rejected");
    Expect(untouched.version == 55, "wrong device leaves output unchanged");
    Expect(VerifyDeviceConfig(
               fixture, "d74f1594-765c-4bd5-b07b-6443273777ed", 9,
               &key, 1, &untouched) == DeviceConfigError::kVersionMismatch,
           "wrong desired version rejected");

    auto strict_key = key;
    strict_key.minimum_security_epoch = 2;
    ExpectFailureWithoutMutation(fixture, DeviceConfigError::kSecurityDowngrade,
                                 strict_key, "security downgrade rejected");
    auto other_key = key;
    other_key.key_id = "other-release-key";
    ExpectFailureWithoutMutation(fixture, DeviceConfigError::kUntrustedKey,
                                 other_key, "unknown key rejected");

    ExpectFailureWithoutMutation(
        Replace(fixture, "640000", "399999"),
        DeviceConfigError::kInvalidSchema, key,
        "threshold below firmware bound rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "640000", "999901"),
        DeviceConfigError::kInvalidSchema, key,
        "threshold above firmware bound rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "1200", "249"), DeviceConfigError::kInvalidSchema,
        key, "cooldown below firmware bound rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "1200", "10001"), DeviceConfigError::kInvalidSchema,
        key, "cooldown above firmware bound rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "\"send_wake_audio\": false",
                "\"send_wake_audio\": true"),
        DeviceConfigError::kInvalidSignature, key,
        "unsigned wake-audio opt-in tampering is rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "\"version\": 8", "\"version\": 8.0"),
        DeviceConfigError::kInvalidJson, key, "float syntax rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "{", "{\"version\":8,"),
        DeviceConfigError::kInvalidSchema, key,
        "duplicate key is rejected by canonical verification");
    ExpectFailureWithoutMutation(
        Replace(fixture, "{", "{\"unknown\":1,"),
        DeviceConfigError::kInvalidSchema, key,
        "unknown root fields are rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "\"version\": 8", "\"version\": 2147483648"),
        DeviceConfigError::kInvalidSchema, key,
        "device config version overflow is rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "    \"interrupt\": null,\n", ""),
        DeviceConfigError::kInvalidSchema, key,
        "interrupt key is required even when disabled");
    ExpectFailureWithoutMutation(
        Replace(
            fixture, "\"interrupt\": null",
            "\"interrupt\":{\"model_id\":\"wn9s_hiesp\",\"threshold_ppm\":640000,\"cooldown_ms\":1200,\"enabled_while_speaking\":true}"),
        DeviceConfigError::kUnsupportedFeature, key,
        "one WakeNet model cannot impersonate both detector roles");
    ExpectFailureWithoutMutation(fixture + " trailing",
                                 DeviceConfigError::kInvalidJson, key,
                                 "trailing content rejected");
    ExpectFailureWithoutMutation(std::string(8193, 'x'),
                                 DeviceConfigError::kInvalidJson, key,
                                 "oversized document rejected before parsing");
    std::string embedded_nul = fixture;
    embedded_nul.insert(embedded_nul.begin() + 2, '\0');
    ExpectFailureWithoutMutation(embedded_nul, DeviceConfigError::kInvalidJson,
                                 key, "embedded NUL rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "wn9s_hiesp", "wn9s_changed"),
        DeviceConfigError::kInvalidSignature, key,
        "signed field tampering rejected");
    ExpectFailureWithoutMutation(
        Replace(fixture, "wn9s_hiesp", "wakenet:hi_esp"),
        DeviceConfigError::kInvalidSchema, key,
        "logical detector aliases cannot reach the ESP-SR runtime");

    std::cout << "device_config_test: passed\n";
    return 0;
}
