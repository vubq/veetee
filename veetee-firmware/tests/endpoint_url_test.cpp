#include <cstdlib>
#include <iostream>

#include "network/endpoint_url.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::network::IsHttpEndpointUrl;
    using veetee::network::IsWebSocketEndpointUrl;

    Expect(IsHttpEndpointUrl("http://192.168.1.20:8001/veetee/ota/"),
           "LAN bootstrap URL");
    Expect(IsHttpEndpointUrl("https://manager.veetee.local/veetee/ota"),
           "TLS hostname bootstrap URL");
    Expect(IsHttpEndpointUrl("http://[::1]:8001/veetee/ota/"),
           "bracketed IPv6 bootstrap URL");
    Expect(IsWebSocketEndpointUrl("ws://192.168.1.20:8000/veetee/v1/"),
           "LAN WebSocket URL");
    Expect(IsWebSocketEndpointUrl("wss://voice.veetee.local/veetee/v1/"),
           "TLS WebSocket URL");

    Expect(!IsHttpEndpointUrl("ftp://192.168.1.20/veetee/ota/"),
           "unsupported scheme rejected");
    Expect(!IsHttpEndpointUrl("http://user:secret@192.168.1.20/veetee/ota/"),
           "userinfo rejected");
    Expect(!IsHttpEndpointUrl("http:///veetee/ota/"), "missing host rejected");
    Expect(!IsHttpEndpointUrl("http://192.168.1.20:0/veetee/ota/"),
           "zero port rejected");
    Expect(!IsHttpEndpointUrl("http://192.168.1.20:70000/veetee/ota/"),
           "oversized port rejected");
    Expect(!IsHttpEndpointUrl("http://192.168.1.20/veetee/ota/?next=other"),
           "query rejected");
    Expect(!IsWebSocketEndpointUrl("ws://2001:db8::1/veetee/v1/"),
           "unbracketed IPv6 rejected");

    std::cout << "endpoint URL tests passed\n";
    return 0;
}
