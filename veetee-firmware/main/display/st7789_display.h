#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace veetee::display {

class St7789Display {
public:
    esp_err_t Initialize();
    esp_err_t DrawColorBars();
    esp_err_t DrawActivationCode(const char* code);
    esp_err_t DrawStandby();

private:
    static bool OnColorTransferDone(esp_lcd_panel_io_handle_t panel_io,
                                    esp_lcd_panel_io_event_data_t* event_data,
                                    void* context);
    esp_err_t DrawBitmapSync(int x_start, int y_start, int x_end, int y_end,
                             const void* pixels);
    esp_err_t FillRectangle(int x, int y, int width, int height,
                            std::uint16_t color);

    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    SemaphoreHandle_t transfer_done_ = nullptr;
    std::uint16_t* line_buffer_ = nullptr;
    std::size_t line_buffer_pixels_ = 0;
    bool transfer_faulted_ = false;
};

}  // namespace veetee::display
