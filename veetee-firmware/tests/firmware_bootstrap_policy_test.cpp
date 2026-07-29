#include <cassert>
#include <iostream>

#include "ota/firmware_bootstrap_policy.h"

int main() {
    using veetee::ota::FirmwareBootstrapRequiresUpdate;
    assert(!FirmwareBootstrapRequiresUpdate(false, "0.4.0", "0.3.0"));
    assert(!FirmwareBootstrapRequiresUpdate(true, "", "0.3.0"));
    assert(!FirmwareBootstrapRequiresUpdate(true, "0.3.0", "0.3.0"));
    assert(FirmwareBootstrapRequiresUpdate(true, "0.4.0", "0.3.0"));
    assert(!FirmwareBootstrapRequiresUpdate(true, "0.4.0", "0.3.0", true));
    std::cout << "firmware_bootstrap_policy_test: passed\n";
    return 0;
}
