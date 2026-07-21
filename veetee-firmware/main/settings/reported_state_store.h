#pragma once

#include "esp_err.h"
#include "nvs.h"
#include "settings/reported_state_record.h"

namespace veetee::settings {

class ReportedStateStore {
public:
    ~ReportedStateStore();

    esp_err_t Initialize();
    esp_err_t Save(const ReportedStateRecord& record);

    [[nodiscard]] const ReportedStateRecord& record() const { return record_; }

private:
    nvs_handle_t handle_ = 0;
    ReportedStateRecord record_{};
};

}  // namespace veetee::settings
