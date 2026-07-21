#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace veetee::security {

// Veetee manifests use the integer-only subset of RFC 8785. Object keys must
// be ASCII, while string values may contain valid UTF-8.
bool CanonicalizeJson(std::string_view document, std::string* canonical);
bool CanonicalizeManifestForSignature(std::string_view document,
                                      std::string* canonical);

bool VerifyEd25519Base64(const std::uint8_t public_key[32],
                         std::string_view message,
                         std::string_view signature_base64);

}  // namespace veetee::security
