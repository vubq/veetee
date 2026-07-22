#pragma once

#include <cstddef>
#include <cstdint>

#include "app/state_machine.h"
#include "display/ui_pack.h"
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
    esp_err_t DrawState(app::State state);
    esp_err_t DrawActivationCode(const char* code);
    esp_err_t DrawStandby();
    esp_err_t ReloadUiPack(const char* partition_label);
    void UseBuiltInSignal();
    [[nodiscard]] bool UiPackHealthy() const;
    [[nodiscard]] const char* loaded_ui_partition() const {
        return loaded_ui_partition_;
    }

private:
    static bool OnColorTransferDone(esp_lcd_panel_io_handle_t panel_io,
                                    esp_lcd_panel_io_event_data_t* event_data,
                                    void* context);
    esp_err_t DrawBitmapSync(int x_start, int y_start, int x_end, int y_end,
                             const void* pixels);
    esp_err_t FillRectangle(int x, int y, int width, int height,
                            std::uint16_t color);
    esp_err_t DrawTextCentered(const char* text, int y, int preferred_scale,
                               std::uint16_t color);
    esp_err_t DrawGlyph(char character, int x, int y, int scale,
                        std::uint16_t color);
    esp_err_t DrawIcon(app::State state, std::uint16_t foreground,
                       std::uint16_t accent);
    esp_err_t FlushFramebuffer();
    esp_err_t RenderState(app::State state, const char* activation_code);
    void RenderSignal(app::State state, const UiStateStyle& style,
                      const char* activation_code);
    void RenderMonolith(app::State state, const UiStateStyle& style,
                        const char* activation_code);
    void RenderQuiet(app::State state, const UiStateStyle& style,
                     const char* activation_code);
    void CanvasFill(std::uint16_t color);
    void CanvasRectangle(int x, int y, int width, int height,
                         std::uint16_t color);
    void CanvasCircle(int center_x, int center_y, int radius,
                      std::uint16_t color, bool filled);
    void CanvasLine(int x0, int y0, int x1, int y1, int thickness,
                    std::uint16_t color);
    void CanvasText(const char* text, int x, int y, int scale,
                    std::uint16_t color);
    void CanvasTextCentered(const char* text, int y, int scale,
                            std::uint16_t color);
    void CanvasGlyph(char character, int x, int y, int scale,
                     std::uint16_t color);

    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    SemaphoreHandle_t transfer_done_ = nullptr;
    std::uint16_t* line_buffer_ = nullptr;
    std::uint16_t* framebuffer_ = nullptr;
    std::size_t line_buffer_pixels_ = 0;
    std::size_t framebuffer_pixels_ = 0;
    UiTheme theme_ = BuiltInSignalTheme();
    char loaded_ui_partition_[16] = {};
    bool last_render_ok_ = false;
    bool transfer_faulted_ = false;
};

}  // namespace veetee::display
