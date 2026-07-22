#pragma once

namespace veetee::network {

bool IsCaptivePortalProbePath(const char* uri);
bool CanStartCaptivePortalScan(bool client_has_ipv4, bool portal_running,
                               bool scan_in_progress);

}  // namespace veetee::network
