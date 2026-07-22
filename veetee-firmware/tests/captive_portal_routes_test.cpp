#include <cassert>

#include "network/captive_portal_routes.h"

int main() {
    using veetee::network::CanStartCaptivePortalScan;
    using veetee::network::IsCaptivePortalProbePath;

    assert(IsCaptivePortalProbePath("/hotspot-detect.html"));
    assert(IsCaptivePortalProbePath("/generate_204"));
    assert(IsCaptivePortalProbePath("/generate_204?x=1"));
    assert(IsCaptivePortalProbePath("/gen_204"));
    assert(IsCaptivePortalProbePath("/connecttest.txt"));
    assert(IsCaptivePortalProbePath("/canonical.html"));
    assert(IsCaptivePortalProbePath("/fwlink/"));

    assert(!IsCaptivePortalProbePath(nullptr));
    assert(!IsCaptivePortalProbePath("/"));
    assert(!IsCaptivePortalProbePath("/favicon.ico"));
    assert(!IsCaptivePortalProbePath("/portal.css.map"));
    assert(!IsCaptivePortalProbePath("/api/unknown"));
    assert(!IsCaptivePortalProbePath("/generate_205"));

    assert(!CanStartCaptivePortalScan(false, true, false));
    assert(!CanStartCaptivePortalScan(true, false, false));
    assert(!CanStartCaptivePortalScan(true, true, true));
    assert(CanStartCaptivePortalScan(true, true, false));
    return 0;
}
