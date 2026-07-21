#pragma once

#include <cstdint>

#include "esp_err.h"
#include "nvs.h"
#include "settings/resource_record.h"

namespace veetee::settings {

class ResourceStateStore {
public:
    ~ResourceStateStore();

    esp_err_t Initialize(std::uint32_t minimum_security_epoch);
    esp_err_t Save(const ResourceRecord& record);

    [[nodiscard]] const ResourceRecord& record() const { return record_; }

private:
    nvs_handle_t handle_ = 0;
    ResourceRecord record_{};
};

}  // namespace veetee::settings
