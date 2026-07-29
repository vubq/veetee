#pragma once

#include <cstddef>

namespace veetee::network {

bool IsHttpEndpointUrl(const char* value);
bool IsWebSocketEndpointUrl(const char* value);
bool BuildHttpOriginEndpoint(const char* source_url, const char* endpoint_path,
                             char* output, std::size_t output_size);
bool IsCanonicalArtifactManifestUrl(const char* bootstrap_url,
                                    const char* candidate_url);
bool IsCanonicalArtifactContentUrl(const char* bootstrap_url,
                                   const char* candidate_url);

}  // namespace veetee::network
