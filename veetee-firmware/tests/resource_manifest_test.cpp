#include "ota/resource_manifest.h"
#include "security/signed_json.h"

#include <array>
#include <cassert>
#include <cstring>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>

#include "cJSON.h"

namespace {

std::string ReadFixture(const char* relative_path) {
    std::ifstream input(std::string(VEETEE_REPO_ROOT) + "/veetee-server/packages/contracts/fixtures/" +
                        relative_path);
    assert(input.good());
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

std::array<std::uint8_t, 32> HexKey(const char* value) {
    std::array<std::uint8_t, 32> result{};
    for (std::size_t index = 0; index < result.size(); ++index) {
        const auto nibble = [](char character) -> std::uint8_t {
            if (character >= '0' && character <= '9') return character - '0';
            if (character >= 'a' && character <= 'f') return character - 'a' + 10;
            assert(false);
            return 0;
        };
        result[index] = static_cast<std::uint8_t>(
            (nibble(value[index * 2]) << 4U) | nibble(value[index * 2 + 1]));
    }
    return result;
}

veetee::ota::DeviceResourceCapability Capability() {
    static constexpr veetee::ota::SupportedResourceRuntime kSupportedRuntimes[] = {
        {.kind = "model_pack", .runtime = "esp-sr", .runtime_abi = 1},
    };
    return {
        .board = "veetee-s3-n16r8",
        .chip = "esp32s3",
        .firmware_version = "0.2.0",
        .resource_abi = 1,
        .flash_bytes = 16777216,
        .psram_bytes = 8388608,
        .resource_slot_bytes = 4194304,
        .supported_runtimes = kSupportedRuntimes,
        .supported_runtime_count = std::size(kSupportedRuntimes),
    };
}

void TestRfc8785Vector() {
    const std::string vector_json =
        ReadFixture("artifacts/signed-resource-manifest-vector-v1.json");
    cJSON* vector = cJSON_ParseWithLength(vector_json.data(), vector_json.size());
    assert(vector != nullptr);
    const cJSON* document = cJSON_GetObjectItemCaseSensitive(vector, "document");
    const cJSON* expected =
        cJSON_GetObjectItemCaseSensitive(vector, "canonical_payload");
    const cJSON* signature =
        cJSON_GetObjectItemCaseSensitive(vector, "signature_base64");
    assert(cJSON_IsObject(document));
    assert(cJSON_IsString(expected));
    assert(cJSON_IsString(signature));
    char* encoded_document = cJSON_PrintUnformatted(document);
    assert(encoded_document != nullptr);

    std::string canonical;
    assert(veetee::security::CanonicalizeJson(encoded_document, &canonical));
    assert(canonical == expected->valuestring);
    const auto public_key =
        HexKey("4e398684acf8dbe2a1b3e88e44ac2e94603e73fccce92890a4708db8917d7546");
    assert(veetee::security::VerifyEd25519Base64(
        public_key.data(), canonical, signature->valuestring));
    assert(!veetee::security::CanonicalizeJson(
        std::string(encoded_document) + " trailing", &canonical));
    assert(!veetee::security::CanonicalizeJson(
        R"({"value":"\u0000suffix"})", &canonical));
    std::string embedded_null = R"({"value":1})";
    embedded_null.push_back('\0');
    embedded_null.append("hidden");
    assert(!veetee::security::CanonicalizeJson(embedded_null, &canonical));

    cJSON_free(encoded_document);
    cJSON_Delete(vector);
}

void TestResourceManifest() {
    const std::string document = ReadFixture("artifacts/resource-manifest-v1.json");
    const veetee::ota::TrustedReleaseKey key = {
        .key_id = "veetee-dev-release-2026-01",
        .minimum_security_epoch = 1,
        .public_key = HexKey(
            "5068dfa6e35f65702d5ae2ee0eead751b212daa6e0e9553571ac9b63bf5c906f"),
    };
    veetee::ota::VerifiedResourceManifest manifest{};
    auto capability = Capability();

    assert(veetee::ota::VerifyResourceManifest(document, capability, &key, 1,
                                                &manifest) ==
           veetee::ota::ResourceManifestError::kOk);
    assert(std::string(manifest.bundle_id) == "01JRESOURCE0000000000000000");
    assert(std::string(manifest.version) == "1.4.0");
    assert(manifest.payload_bytes == 1835008);
    assert(manifest.security_epoch == 1);
    assert(!manifest.requires_reboot);

    capability.board = "another-board";
    assert(veetee::ota::VerifyResourceManifest(document, capability, &key, 1,
                                                &manifest) ==
           veetee::ota::ResourceManifestError::kTargetMismatch);

    capability = Capability();
    capability.resource_slot_bytes = 1024;
    assert(veetee::ota::VerifyResourceManifest(document, capability, &key, 1,
                                                &manifest) ==
           veetee::ota::ResourceManifestError::kCapacityExceeded);

    capability = Capability();
    capability.firmware_version = "0.3.0";
    assert(veetee::ota::VerifyResourceManifest(document, capability, &key, 1,
                                                &manifest) ==
           veetee::ota::ResourceManifestError::kFirmwareIncompatible);

    auto downgraded_key = key;
    downgraded_key.minimum_security_epoch = 2;
    capability = Capability();
    assert(veetee::ota::VerifyResourceManifest(document, capability,
                                                &downgraded_key, 1, &manifest) ==
           veetee::ota::ResourceManifestError::kSecurityDowngrade);

    std::string tampered = document;
    const std::size_t marker = tampered.find("1835008");
    assert(marker != std::string::npos);
    tampered.replace(marker, 7, "1835009");
    assert(veetee::ota::VerifyResourceManifest(tampered, Capability(), &key, 1,
                                                &manifest) ==
           veetee::ota::ResourceManifestError::kInvalidSignature);

    const auto unknown_key = veetee::ota::TrustedReleaseKey{
        .key_id = "different-key",
        .minimum_security_epoch = 1,
        .public_key = key.public_key,
    };
    assert(veetee::ota::VerifyResourceManifest(document, Capability(),
                                                &unknown_key, 1, &manifest) ==
           veetee::ota::ResourceManifestError::kUntrustedKey);

    std::string unexpected_property = document;
    unexpected_property.insert(unexpected_property.find('{') + 1,
                               R"("unexpected":true,)");
    assert(veetee::ota::VerifyResourceManifest(
               unexpected_property, Capability(), &key, 1, &manifest) ==
           veetee::ota::ResourceManifestError::kInvalidSchema);

    std::string unsupported_runtime = document;
    const std::size_t runtime = unsupported_runtime.find(R"("runtime": "esp-sr")");
    assert(runtime != std::string::npos);
    unsupported_runtime.replace(runtime, std::strlen(R"("runtime": "esp-sr")"),
                                R"("runtime": "unknown")");
    assert(veetee::ota::VerifyResourceManifest(
               unsupported_runtime, Capability(), &key, 1, &manifest) ==
           veetee::ota::ResourceManifestError::kUnsupportedRuntime);

    std::string unsafe_name = document;
    const std::size_t name =
        unsafe_name.find(R"("name": "speech/esp-sr-vi-home")");
    assert(name != std::string::npos);
    unsafe_name.replace(name,
                        std::strlen(R"("name": "speech/esp-sr-vi-home")"),
                        R"("name": "speech/../resource.bin")");
    assert(veetee::ota::VerifyResourceManifest(
               unsafe_name, Capability(), &key, 1, &manifest) ==
           veetee::ota::ResourceManifestError::kInvalidSchema);
}

}  // namespace

int main() {
    TestRfc8785Vector();
    TestResourceManifest();
    return 0;
}
