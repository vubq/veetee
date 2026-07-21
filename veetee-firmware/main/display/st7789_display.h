#pragma once

#include <cstdint>

#include "esp_err.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"

namespace veetee::display {

class St7789Display {
public:
    esp_err_t Initialize();
    esp_err_t DrawColorBars();

private:
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
};

}  // namespace veetee::display
