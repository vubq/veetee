#include "network/captive_portal_routes.h"

#include <array>
#include <string_view>

namespace veetee::network {
namespace {

constexpr std::array<std::string_view, 13> kExactProbePaths = {
    "/hotspot-detect.html",       // Apple
    "/hotspotdetect.html",        // Apple variants
    "/library/test/success.html", // Apple
    "/mobile/status.php",         // Android variants
    "/check_network_status.txt",  // Windows
    "/ncsi.txt",                  // Windows
    "/connecttest.txt",           // Windows 10/11
    "/redirect",                  // Windows captive redirect
    "/connectivity-check.html",   // Firefox
    "/canonical.html",            // Firefox
    "/success.txt",               // Other clients
    "/success.html",              // Other clients
    "/portal.html",               // Other clients
};

constexpr std::array<std::string_view, 3> kProbePrefixes = {
    "/generate_204", // Android
    "/gen_204",      // Chromium variants
    "/fwlink/",      // Windows
};

std::string_view RequestPath(const char* uri) {
    if (uri == nullptr) return {};
    std::string_view path(uri);
    const std::size_t query = path.find_first_of("?#");
    if (query != std::string_view::npos) path = path.substr(0, query);
    return path;
}

}  // namespace

bool IsCaptivePortalProbePath(const char* uri) {
    const std::string_view path = RequestPath(uri);
    for (const std::string_view probe : kExactProbePaths) {
        if (path == probe) return true;
    }
    for (const std::string_view prefix : kProbePrefixes) {
        if (path.size() >= prefix.size() && path.substr(0, prefix.size()) == prefix) {
            return true;
        }
    }
    return false;
}

bool CanStartCaptivePortalScan(bool client_has_ipv4, bool portal_running,
                               bool scan_in_progress) {
    return client_has_ipv4 && portal_running && !scan_in_progress;
}

}  // namespace veetee::network
