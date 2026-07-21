#include "network/endpoint_url.h"

#include <cctype>
#include <cstddef>
#include <cstring>

namespace veetee::network {
namespace {

bool HasPrefix(const char* value, const char* prefix) {
    return std::strncmp(value, prefix, std::strlen(prefix)) == 0;
}

bool IsValidPort(const char* begin, const char* end) {
    if (begin == end) return false;
    unsigned port = 0;
    for (const char* cursor = begin; cursor != end; ++cursor) {
        if (!std::isdigit(static_cast<unsigned char>(*cursor))) return false;
        port = port * 10U + static_cast<unsigned>(*cursor - '0');
        if (port > 65535U) return false;
    }
    return port != 0;
}

bool IsValidHostCharacter(char value) {
    const unsigned char character = static_cast<unsigned char>(value);
    return std::isalnum(character) != 0 || value == '.' || value == '-' ||
           value == '_';
}

bool IsValidEndpointUrl(const char* value, const char* first_scheme,
                        const char* second_scheme) {
    if (value == nullptr || value[0] == '\0') return false;

    const char* authority = nullptr;
    if (HasPrefix(value, first_scheme)) {
        authority = value + std::strlen(first_scheme);
    } else if (HasPrefix(value, second_scheme)) {
        authority = value + std::strlen(second_scheme);
    } else {
        return false;
    }

    const char* authority_end = authority;
    while (*authority_end != '\0' && *authority_end != '/') {
        if (*authority_end == '?' || *authority_end == '#' ||
            std::iscntrl(static_cast<unsigned char>(*authority_end)) != 0 ||
            std::isspace(static_cast<unsigned char>(*authority_end)) != 0) {
            return false;
        }
        ++authority_end;
    }
    if (authority == authority_end ||
        std::memchr(authority, '@', static_cast<std::size_t>(authority_end - authority)) !=
            nullptr) {
        return false;
    }

    const char* port = nullptr;
    if (*authority == '[') {
        const char* closing = static_cast<const char*>(
            std::memchr(authority + 1, ']',
                        static_cast<std::size_t>(authority_end - authority - 1)));
        if (closing == nullptr || closing == authority + 1) return false;
        for (const char* cursor = authority + 1; cursor != closing; ++cursor) {
            const unsigned char character = static_cast<unsigned char>(*cursor);
            if (std::isxdigit(character) == 0 && *cursor != ':' && *cursor != '.') {
                return false;
            }
        }
        if (closing + 1 != authority_end) {
            if (closing[1] != ':') return false;
            port = closing + 2;
        }
    } else {
        const char* colon = static_cast<const char*>(
            std::memchr(authority, ':',
                        static_cast<std::size_t>(authority_end - authority)));
        const char* host_end = colon == nullptr ? authority_end : colon;
        if (host_end == authority) return false;
        for (const char* cursor = authority; cursor != host_end; ++cursor) {
            if (!IsValidHostCharacter(*cursor)) return false;
        }
        if (colon != nullptr) {
            if (std::memchr(colon + 1, ':',
                            static_cast<std::size_t>(authority_end - colon - 1)) != nullptr) {
                return false;
            }
            port = colon + 1;
        }
    }
    if (port != nullptr && !IsValidPort(port, authority_end)) return false;

    for (const char* cursor = authority_end; *cursor != '\0'; ++cursor) {
        const unsigned char character = static_cast<unsigned char>(*cursor);
        if (*cursor == '?' || *cursor == '#' || *cursor == '\\' ||
            std::iscntrl(character) != 0 || std::isspace(character) != 0) {
            return false;
        }
    }
    return true;
}

}  // namespace

bool IsHttpEndpointUrl(const char* value) {
    return IsValidEndpointUrl(value, "http://", "https://");
}

bool IsWebSocketEndpointUrl(const char* value) {
    return IsValidEndpointUrl(value, "ws://", "wss://");
}

}  // namespace veetee::network
