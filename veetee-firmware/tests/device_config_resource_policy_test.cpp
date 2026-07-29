#include <cassert>
#include <cstring>
#include <iostream>

#include "config/device_config_resource_policy.h"

namespace {

veetee::config::DeviceConfig Config(const char* activation,
                                    const char* interrupt = nullptr) {
    veetee::config::DeviceConfig config{};
    config.version = 8;
    config.has_wake_profile = true;
    std::strcpy(config.required_resource_version.data(), "1.2.0");
    config.activation.enabled = true;
    std::strcpy(config.activation.model_id.data(), activation);
    if (interrupt != nullptr) {
        config.interrupt.enabled = true;
        std::strcpy(config.interrupt.model_id.data(), interrupt);
    }
    return config;
}

veetee::settings::ResourceDetectorInventory Inventory(
    const char* activation, const char* interrupt = nullptr) {
    veetee::settings::ResourceDetectorInventory inventory{};
    if (activation != nullptr) {
        std::strcpy(inventory.activation_model_id, activation);
    }
    if (interrupt != nullptr) {
        std::strcpy(inventory.interrupt_model_id, interrupt);
    }
    return inventory;
}

}  // namespace

int main() {
    using Decision = veetee::config::DeviceConfigResourceApplyDecision;
    using Error = veetee::config::DeviceConfigResourceLinkError;
    using veetee::config::DecideDeviceConfigResourceApply;
    using veetee::config::ValidateDeviceConfigResourceLink;

    const auto activation_only = Config("wn9s_veetee");
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0",
               Inventory("wn9s_veetee")) == Error::kOk);
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.3.0",
               Inventory("wn9s_veetee")) ==
           Error::kResourceVersionMismatch);
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0", Inventory(nullptr)) ==
           Error::kActivationDetectorMissing);
    assert(DecideDeviceConfigResourceApply(
               Error::kActivationDetectorMissing, false) ==
           Decision::kWaitForResource);
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0",
               Inventory("wn9s_other")) ==
           Error::kActivationDetectorMismatch);
    assert(DecideDeviceConfigResourceApply(
               Error::kActivationDetectorMismatch, true) ==
           Decision::kReject);
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0",
               Inventory("wn9s_other", "wn9s_veetee")) ==
           Error::kActivationDetectorWrongRole);

    // interrupt:null selects no interrupt role. A pack may omit the optional
    // role or carry one for another profile; both preserve exact selection.
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0",
               Inventory("wn9s_veetee", "wn9s_stop")) == Error::kOk);

    // Both callback orderings converge: config-first waits for the signed
    // manifest to hydrate legacy inventory, while resource-first applies as
    // soon as the same config arrives.
    const auto missing_link = ValidateDeviceConfigResourceLink(
        activation_only, "1.2.0", Inventory(nullptr));
    assert(DecideDeviceConfigResourceApply(missing_link, false) ==
           Decision::kWaitForResource);
    const auto hydrated_link = ValidateDeviceConfigResourceLink(
        activation_only, "1.2.0", Inventory("wn9s_veetee"));
    assert(DecideDeviceConfigResourceApply(hydrated_link, true) ==
           Decision::kApply);
    const auto resource_first_link = ValidateDeviceConfigResourceLink(
        activation_only, "1.2.0", Inventory("wn9s_veetee"));
    assert(DecideDeviceConfigResourceApply(resource_first_link, true) ==
           Decision::kApply);

    const auto with_interrupt = Config("wn9s_veetee", "wn9s_stop");
    assert(ValidateDeviceConfigResourceLink(
               with_interrupt, "1.2.0",
               Inventory("wn9s_veetee", "wn9s_stop")) == Error::kOk);
    assert(ValidateDeviceConfigResourceLink(
               with_interrupt, "1.2.0",
               Inventory("wn9s_veetee")) ==
           Error::kInterruptDetectorMissing);
    assert(ValidateDeviceConfigResourceLink(
               with_interrupt, "1.2.0",
               Inventory("wn9s_veetee", "wn9s_other")) ==
           Error::kInterruptDetectorMismatch);

    auto wrong_interrupt_role = Config("wn9s_veetee", "wn9s_activation");
    assert(ValidateDeviceConfigResourceLink(
               wrong_interrupt_role, "1.2.0",
               Inventory("wn9s_activation", "wn9s_veetee")) ==
           Error::kActivationDetectorWrongRole);
    wrong_interrupt_role = Config("wn9s_veetee", "wn9s_activation");
    assert(ValidateDeviceConfigResourceLink(
               wrong_interrupt_role, "1.2.0",
               Inventory("wn9s_activation", "wn9s_stop")) ==
           Error::kActivationDetectorMismatch);

    auto duplicate_config = Config("wn9s_same", "wn9s_same");
    assert(ValidateDeviceConfigResourceLink(
               duplicate_config, "1.2.0",
               Inventory("wn9s_same", "wn9s_stop")) ==
           Error::kDetectorRoleCollision);
    assert(ValidateDeviceConfigResourceLink(
               activation_only, "1.2.0",
               Inventory("wn9s_same", "wn9s_same")) ==
           Error::kDetectorRoleCollision);

    veetee::config::DeviceConfig button_only{};
    assert(ValidateDeviceConfigResourceLink(
               button_only, nullptr, Inventory(nullptr)) == Error::kOk);

    assert(std::strlen(veetee::config::DeviceConfigResourceLinkErrorName(
               Error::kActivationDetectorWrongRole)) < 33);
    std::cout << "device_config_resource_policy_test: passed\n";
    return 0;
}
