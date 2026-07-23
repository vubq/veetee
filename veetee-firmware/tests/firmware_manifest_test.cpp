#include "ota/firmware_manifest.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>

namespace {
std::string Fixture() {
    std::ifstream input(std::string(VEETEE_REPO_ROOT) +
                        "/veetee-server/packages/contracts/fixtures/artifacts/firmware-manifest-v1.json");
    assert(input.good());
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
std::array<std::uint8_t, 32> Key() {
    constexpr char value[] =
        "3aba7750d5dc57b11711cf2abde514e900d0a03e9f8d4cbeb1b425e732ec131a";
    std::array<std::uint8_t, 32> key{};
    auto nibble = [](char c) -> std::uint8_t {
        return c <= '9' ? static_cast<std::uint8_t>(c - '0')
                        : static_cast<std::uint8_t>(c - 'a' + 10);
    };
    for (std::size_t i = 0; i < key.size(); ++i) {
        key[i] = static_cast<std::uint8_t>((nibble(value[i * 2]) << 4) |
                                           nibble(value[i * 2 + 1]));
    }
    return key;
}
veetee::ota::DeviceFirmwareCapability Capability() {
    return {
        .board = "veetee-s3-n16r8",
        .chip = "esp32s3",
        .flash_bytes = 16777216,
        .psram_bytes = 8388608,
        .slot_bytes = 0x3a0000,
    };
}
}  // namespace

int main() {
    const std::string document = Fixture();
    const veetee::ota::TrustedReleaseKey key = {
        .key_id = "firmware-test-key",
        .minimum_security_epoch = 1,
        .public_key = Key(),
    };
    veetee::ota::VerifiedFirmwareManifest manifest{};
    assert(veetee::ota::VerifyFirmwareManifest(document, Capability(), &key, 1,
                                                &manifest) ==
           veetee::ota::FirmwareManifestError::kOk);
    assert(std::string(manifest.version) == "0.4.0");
    assert(manifest.payload_bytes == 1532480);

    auto tooSmall = Capability();
    tooSmall.slot_bytes = 1024;
    assert(veetee::ota::VerifyFirmwareManifest(document, tooSmall, &key, 1,
                                                &manifest) ==
           veetee::ota::FirmwareManifestError::kCapacityExceeded);

    auto wrongBoard = Capability();
    wrongBoard.board = "other";
    assert(veetee::ota::VerifyFirmwareManifest(document, wrongBoard, &key, 1,
                                                &manifest) ==
           veetee::ota::FirmwareManifestError::kTargetMismatch);

    auto stale = key;
    stale.minimum_security_epoch = 2;
    assert(veetee::ota::VerifyFirmwareManifest(document, Capability(), &stale, 1,
                                                &manifest) ==
           veetee::ota::FirmwareManifestError::kSecurityDowngrade);

    std::string tampered = document;
    tampered.replace(tampered.find("1532480"), 7, "1532481");
    assert(veetee::ota::VerifyFirmwareManifest(tampered, Capability(), &key, 1,
                                                &manifest) ==
           veetee::ota::FirmwareManifestError::kInvalidSignature);
}
