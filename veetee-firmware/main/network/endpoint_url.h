#pragma once

#include <cstddef>

namespace veetee::network {

bool IsHttpEndpointUrl(const char* value);
bool IsWebSocketEndpointUrl(const char* value);
bool BuildHttpOriginEndpoint(const char* source_url, const char* endpoint_path,
                             char* output, std::size_t output_size);

}  // namespace veetee::network
