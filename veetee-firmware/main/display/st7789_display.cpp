#include "display/st7789_display.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>

#include "board/board_config.h"
#include "display/state_visual.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::display {
namespace {

constexpr char kTag[] = "veetee_display";
constexpr spi_host_device_t kLcdHost = SPI3_HOST;
constexpr int kDrawLines = 16;
constexpr int kDigitWidth = 30;
constexpr int kDigitHeight = 66;
constexpr int kDigitThickness = 6;
constexpr int kDigitSpacing = 8;
constexpr std::uint16_t kActivationBackground = 0x0B44;
constexpr std::uint16_t kActivationDigit = 0xFEC0;
constexpr std::uint16_t kStandbyBackground = 0x10C3;
constexpr std::uint16_t kStandbyAccent = 0x4E69;
constexpr std::array<std::uint16_t, 8> kColorBars = {
    0xFFFF,  // white
    0xFFE0,  // yellow
    0x07FF,  // cyan
    0x07E0,  // green
    0xF81F,  // magenta
    0xF800,  // red
    0x001F,  // blue
    0x0000,  // black
};
constexpr std::array<std::uint8_t, 10> kSevenSegmentDigits = {
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F,
};
constexpr std::array<std::array<std::uint8_t, 5>, 36> kFont = {{
    {{0x3E, 0x51, 0x49, 0x45, 0x3E}}, {{0x00, 0x42, 0x7F, 0x40, 0x00}},
    {{0x42, 0x61, 0x51, 0x49, 0x46}}, {{0x21, 0x41, 0x45, 0x4B, 0x31}},
    {{0x18, 0x14, 0x12, 0x7F, 0x10}}, {{0x27, 0x45, 0x45, 0x45, 0x39}},
    {{0x3C, 0x4A, 0x49, 0x49, 0x30}}, {{0x01, 0x71, 0x09, 0x05, 0x03}},
    {{0x36, 0x49, 0x49, 0x49, 0x36}}, {{0x06, 0x49, 0x49, 0x29, 0x1E}},
    {{0x7E, 0x11, 0x11, 0x11, 0x7E}}, {{0x7F, 0x49, 0x49, 0x49, 0x36}},
    {{0x3E, 0x41, 0x41, 0x41, 0x22}}, {{0x7F, 0x41, 0x41, 0x22, 0x1C}},
    {{0x7F, 0x49, 0x49, 0x49, 0x41}}, {{0x7F, 0x09, 0x09, 0x09, 0x01}},
    {{0x3E, 0x41, 0x49, 0x49, 0x7A}}, {{0x7F, 0x08, 0x08, 0x08, 0x7F}},
    {{0x00, 0x41, 0x7F, 0x41, 0x00}}, {{0x20, 0x40, 0x41, 0x3F, 0x01}},
    {{0x7F, 0x08, 0x14, 0x22, 0x41}}, {{0x7F, 0x40, 0x40, 0x40, 0x40}},
    {{0x7F, 0x02, 0x0C, 0x02, 0x7F}}, {{0x7F, 0x04, 0x08, 0x10, 0x7F}},
    {{0x3E, 0x41, 0x41, 0x41, 0x3E}}, {{0x7F, 0x09, 0x09, 0x09, 0x06}},
    {{0x3E, 0x41, 0x51, 0x21, 0x5E}}, {{0x7F, 0x09, 0x19, 0x29, 0x46}},
    {{0x46, 0x49, 0x49, 0x49, 0x31}}, {{0x01, 0x01, 0x7F, 0x01, 0x01}},
    {{0x3F, 0x40, 0x40, 0x40, 0x3F}}, {{0x1F, 0x20, 0x40, 0x20, 0x1F}},
    {{0x3F, 0x40, 0x38, 0x40, 0x3F}}, {{0x63, 0x14, 0x08, 0x14, 0x63}},
    {{0x07, 0x08, 0x70, 0x08, 0x07}}, {{0x61, 0x51, 0x49, 0x45, 0x43}},
}};

std::uint16_t ToPanelEndian(std::uint16_t color) {
    return static_cast<std::uint16_t>((color << 8U) | (color >> 8U));
}

}  // namespace

esp_err_t St7789Display::Initialize() {
    transfer_done_ = xSemaphoreCreateBinary();
    if (transfer_done_ == nullptr) return ESP_ERR_NO_MEM;

    gpio_config_t backlight = {};
    backlight.pin_bit_mask = 1ULL << board::kDisplayBacklight;
    backlight.mode = GPIO_MODE_OUTPUT;
    backlight.pull_up_en = GPIO_PULLUP_DISABLE;
    backlight.pull_down_en = GPIO_PULLDOWN_DISABLE;
    backlight.intr_type = GPIO_INTR_DISABLE;
    esp_err_t error = gpio_config(&backlight);
    if (error != ESP_OK) {
        return error;
    }
    gpio_set_level(board::kDisplayBacklight,
                   board::kLcdBacklightInvert ? 1 : 0);

    spi_bus_config_t bus_config = {};
    bus_config.mosi_io_num = board::kDisplayMosi;
    bus_config.miso_io_num = GPIO_NUM_NC;
    bus_config.sclk_io_num = board::kDisplayClock;
    bus_config.quadwp_io_num = GPIO_NUM_NC;
    bus_config.quadhd_io_num = GPIO_NUM_NC;
    bus_config.max_transfer_sz = CONFIG_VEETEE_LCD_WIDTH * kDrawLines *
                                 static_cast<int>(sizeof(std::uint16_t));
    error = spi_bus_initialize(kLcdHost, &bus_config, SPI_DMA_CH_AUTO);
    if (error != ESP_OK) {
        return error;
    }
    line_buffer_pixels_ = static_cast<std::size_t>(CONFIG_VEETEE_LCD_WIDTH) *
                          kDrawLines;
    line_buffer_ = static_cast<std::uint16_t*>(spi_bus_dma_memory_alloc(
        kLcdHost, line_buffer_pixels_ * sizeof(std::uint16_t), 0));
    if (line_buffer_ == nullptr) return ESP_ERR_NO_MEM;

    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num = board::kDisplayChipSelect;
    io_config.dc_gpio_num = board::kDisplayDc;
    io_config.spi_mode = board::kLcdSpiMode;
    io_config.pclk_hz = CONFIG_VEETEE_LCD_SPI_CLOCK_HZ;
    io_config.trans_queue_depth = 4;
    io_config.lcd_cmd_bits = 8;
    io_config.lcd_param_bits = 8;
    error = esp_lcd_new_panel_io_spi(kLcdHost, &io_config, &panel_io_);
    if (error != ESP_OK) {
        return error;
    }
    esp_lcd_panel_io_callbacks_t callbacks = {};
    callbacks.on_color_trans_done = &St7789Display::OnColorTransferDone;
    error = esp_lcd_panel_io_register_event_callbacks(panel_io_, &callbacks, this);
    if (error != ESP_OK) return error;

    esp_lcd_panel_dev_config_t panel_config = {};
    panel_config.reset_gpio_num = board::kDisplayReset;
    panel_config.rgb_ele_order = board::kLcdBgrOrder
                                     ? LCD_RGB_ELEMENT_ORDER_BGR
                                     : LCD_RGB_ELEMENT_ORDER_RGB;
    panel_config.bits_per_pixel = 16;
    error = esp_lcd_new_panel_st7789(panel_io_, &panel_config, &panel_);
    if (error != ESP_OK) {
        return error;
    }

    if ((error = esp_lcd_panel_reset(panel_)) != ESP_OK ||
        (error = esp_lcd_panel_init(panel_)) != ESP_OK ||
        (error = esp_lcd_panel_invert_color(panel_, board::kLcdInvertColor)) != ESP_OK ||
        (error = esp_lcd_panel_swap_xy(panel_, board::kLcdSwapXy)) != ESP_OK ||
        (error = esp_lcd_panel_mirror(panel_, board::kLcdMirrorX,
                                      board::kLcdMirrorY)) != ESP_OK ||
        (error = esp_lcd_panel_set_gap(panel_, CONFIG_VEETEE_LCD_OFFSET_X,
                                      CONFIG_VEETEE_LCD_OFFSET_Y)) != ESP_OK ||
        (error = esp_lcd_panel_disp_on_off(panel_, true)) != ESP_OK) {
        return error;
    }

    gpio_set_level(board::kDisplayBacklight,
                   board::kLcdBacklightInvert ? 0 : 1);
    ESP_LOGI(kTag, "ST7789 initialized: %dx%d offset=%d,%d SPI=%d Hz mode=%d",
             CONFIG_VEETEE_LCD_WIDTH, CONFIG_VEETEE_LCD_HEIGHT,
             CONFIG_VEETEE_LCD_OFFSET_X, CONFIG_VEETEE_LCD_OFFSET_Y,
             CONFIG_VEETEE_LCD_SPI_CLOCK_HZ, board::kLcdSpiMode);
    return ESP_OK;
}

esp_err_t St7789Display::DrawColorBars() {
    if (panel_ == nullptr || line_buffer_ == nullptr || transfer_faulted_) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t error = ESP_OK;
    for (int y = 0; y < CONFIG_VEETEE_LCD_HEIGHT && error == ESP_OK; y += kDrawLines) {
        const int lines = std::min(kDrawLines, CONFIG_VEETEE_LCD_HEIGHT - y);
        for (int row = 0; row < lines; ++row) {
            for (int x = 0; x < CONFIG_VEETEE_LCD_WIDTH; ++x) {
                const std::size_t bar = std::min<std::size_t>(
                    kColorBars.size() - 1,
                    static_cast<std::size_t>(x) * kColorBars.size() /
                        CONFIG_VEETEE_LCD_WIDTH);
                line_buffer_[row * CONFIG_VEETEE_LCD_WIDTH + x] =
                    ToPanelEndian(kColorBars[bar]);
            }
        }
        error = DrawBitmapSync(0, y, CONFIG_VEETEE_LCD_WIDTH, y + lines,
                               line_buffer_);
    }
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Color bars rendered");
    }
    return error;
}

esp_err_t St7789Display::DrawState(app::State state) {
    if (panel_ == nullptr || line_buffer_ == nullptr || transfer_faulted_) {
        return ESP_ERR_INVALID_STATE;
    }
    const StateVisual& visual = VisualForState(state);
    esp_err_t error = FillRectangle(0, 0, CONFIG_VEETEE_LCD_WIDTH,
                                    CONFIG_VEETEE_LCD_HEIGHT,
                                    visual.background);
    const int margin = std::max(8, CONFIG_VEETEE_LCD_WIDTH / 20);
    if (error == ESP_OK) {
        error = FillRectangle(margin, margin, CONFIG_VEETEE_LCD_WIDTH - margin * 2,
                              6, visual.accent);
    }
    if (error == ESP_OK) {
        error = DrawTextCentered(visual.label, margin + 22, 3,
                                 visual.foreground);
    }
    if (error == ESP_OK) {
        error = DrawIcon(state, visual.foreground, visual.accent);
    }
    if (error == ESP_OK) {
        error = DrawTextCentered(visual.detail,
                                 CONFIG_VEETEE_LCD_HEIGHT - margin - 28, 2,
                                 visual.foreground);
    }
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "State screen rendered: %s", app::ToString(state));
    }
    return error;
}

esp_err_t St7789Display::DrawTextCentered(const char* text, int y,
                                          int preferred_scale,
                                          std::uint16_t color) {
    if (text == nullptr || preferred_scale <= 0) return ESP_ERR_INVALID_ARG;
    const int length = static_cast<int>(std::strlen(text));
    int scale = preferred_scale;
    while (scale > 1 && length * 6 * scale > CONFIG_VEETEE_LCD_WIDTH - 16) {
        --scale;
    }
    const int width = std::max(0, length * 6 * scale - scale);
    int x = std::max(0, (CONFIG_VEETEE_LCD_WIDTH - width) / 2);
    for (const char* cursor = text; *cursor != '\0'; ++cursor) {
        const esp_err_t error = DrawGlyph(*cursor, x, y, scale, color);
        if (error != ESP_OK) return error;
        x += 6 * scale;
    }
    return ESP_OK;
}

esp_err_t St7789Display::DrawGlyph(char character, int x, int y, int scale,
                                   std::uint16_t color) {
    if (character == ' ') return ESP_OK;
    std::array<std::uint8_t, 5> glyph{};
    if (character >= '0' && character <= '9') {
        glyph = kFont[character - '0'];
    } else if (character >= 'A' && character <= 'Z') {
        glyph = kFont[10 + character - 'A'];
    } else if (character == '-') {
        glyph = {0x08, 0x08, 0x08, 0x08, 0x08};
    } else if (character == '.') {
        glyph = {0x00, 0x60, 0x60, 0x00, 0x00};
    } else if (character == ':') {
        glyph = {0x00, 0x36, 0x36, 0x00, 0x00};
    } else {
        return ESP_OK;
    }
    for (int column = 0; column < 5; ++column) {
        for (int row = 0; row < 7; ++row) {
            if ((glyph[column] & (1U << row)) == 0) continue;
            const esp_err_t error = FillRectangle(x + column * scale,
                                                  y + row * scale, scale,
                                                  scale, color);
            if (error != ESP_OK) return error;
        }
    }
    return ESP_OK;
}

esp_err_t St7789Display::DrawIcon(app::State state,
                                  std::uint16_t foreground,
                                  std::uint16_t accent) {
    const StateVisual& visual = VisualForState(state);
    const int center_x = CONFIG_VEETEE_LCD_WIDTH / 2;
    const int center_y = CONFIG_VEETEE_LCD_HEIGHT / 2;
    const int left = center_x - 52;
    const int top = center_y - 42;
    esp_err_t error = FillRectangle(left, top, 104, 84, accent);
    if (error != ESP_OK) return error;

    switch (visual.icon) {
        case VisualIcon::kBoot:
        case VisualIcon::kFace:
            if ((error = FillRectangle(center_x - 28, center_y - 16, 12, 14,
                                       foreground)) != ESP_OK ||
                (error = FillRectangle(center_x + 16, center_y - 16, 12, 14,
                                       foreground)) != ESP_OK ||
                (error = FillRectangle(center_x - 22, center_y + 16, 44, 7,
                                       foreground)) != ESP_OK) {
                return error;
            }
            break;
        case VisualIcon::kWifi:
            for (int index = 0; index < 3 && error == ESP_OK; ++index) {
                const int width = 72 - index * 20;
                error = FillRectangle(center_x - width / 2,
                                      center_y - 24 + index * 18, width, 7,
                                      foreground);
            }
            if (error == ESP_OK) {
                error = FillRectangle(center_x - 5, center_y + 28, 10, 10,
                                      foreground);
            }
            break;
        case VisualIcon::kLink:
            error = FillRectangle(center_x - 38, center_y - 6, 76, 12,
                                  foreground);
            if (error == ESP_OK) {
                error = FillRectangle(center_x - 6, center_y - 28, 12, 56,
                                      foreground);
            }
            break;
        case VisualIcon::kKey:
            error = FillRectangle(center_x - 34, center_y - 18, 36, 36,
                                  foreground);
            if (error == ESP_OK) {
                error = FillRectangle(center_x, center_y - 6, 38, 12,
                                      foreground);
            }
            break;
        case VisualIcon::kListen:
        case VisualIcon::kSpeak:
            for (int index = 0; index < 5 && error == ESP_OK; ++index) {
                const int height = 18 + (index % 3) * 18;
                error = FillRectangle(center_x - 42 + index * 18,
                                      center_y - height / 2, 9, height,
                                      foreground);
            }
            break;
        case VisualIcon::kThink:
            for (int index = 0; index < 3 && error == ESP_OK; ++index) {
                error = FillRectangle(center_x - 34 + index * 26,
                                      center_y - 7, 14, 14, foreground);
            }
            break;
        case VisualIcon::kStop:
            error = FillRectangle(center_x - 24, center_y - 24, 48, 48,
                                  foreground);
            break;
        case VisualIcon::kClose:
            error = FillRectangle(center_x - 30, center_y - 8, 18, 8,
                                  foreground);
            if (error == ESP_OK) {
                error = FillRectangle(center_x + 12, center_y - 8, 18, 8,
                                      foreground);
            }
            if (error == ESP_OK) {
                error = FillRectangle(center_x - 18, center_y + 20, 36, 6,
                                      foreground);
            }
            break;
        case VisualIcon::kError:
            for (int offset = -24; offset <= 24 && error == ESP_OK; offset += 6) {
                error = FillRectangle(center_x + offset, center_y + offset, 8, 8,
                                      foreground);
                if (error == ESP_OK) {
                    error = FillRectangle(center_x + offset, center_y - offset,
                                          8, 8, foreground);
                }
            }
            break;
    }
    return error;
}

esp_err_t St7789Display::DrawActivationCode(const char* code) {
    if (panel_ == nullptr || code == nullptr || std::strlen(code) != 6) {
        return ESP_ERR_INVALID_ARG;
    }
    for (const char* digit = code; *digit != '\0'; ++digit) {
        if (*digit < '0' || *digit > '9') return ESP_ERR_INVALID_ARG;
    }

    esp_err_t error = FillRectangle(0, 0, CONFIG_VEETEE_LCD_WIDTH,
                                    CONFIG_VEETEE_LCD_HEIGHT,
                                    kActivationBackground);
    const int total_width = 6 * kDigitWidth + 5 * kDigitSpacing;
    const int origin_x = std::max(0, (CONFIG_VEETEE_LCD_WIDTH - total_width) / 2);
    const int origin_y = std::max(0, (CONFIG_VEETEE_LCD_HEIGHT - kDigitHeight) / 2);
    const int middle_y = (kDigitHeight - kDigitThickness) / 2;
    const int vertical_height = (kDigitHeight - 3 * kDigitThickness) / 2;

    for (int index = 0; index < 6 && error == ESP_OK; ++index) {
        const int x = origin_x + index * (kDigitWidth + kDigitSpacing);
        const int y = origin_y;
        const std::uint8_t segments = kSevenSegmentDigits[code[index] - '0'];
        if ((segments & 0x01U) != 0) {
            error = FillRectangle(x + kDigitThickness, y,
                                  kDigitWidth - 2 * kDigitThickness,
                                  kDigitThickness, kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x02U) != 0) {
            error = FillRectangle(x + kDigitWidth - kDigitThickness,
                                  y + kDigitThickness, kDigitThickness,
                                  vertical_height, kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x04U) != 0) {
            error = FillRectangle(x + kDigitWidth - kDigitThickness,
                                  y + middle_y + kDigitThickness,
                                  kDigitThickness, vertical_height,
                                  kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x08U) != 0) {
            error = FillRectangle(x + kDigitThickness,
                                  y + kDigitHeight - kDigitThickness,
                                  kDigitWidth - 2 * kDigitThickness,
                                  kDigitThickness, kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x10U) != 0) {
            error = FillRectangle(x, y + middle_y + kDigitThickness,
                                  kDigitThickness, vertical_height,
                                  kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x20U) != 0) {
            error = FillRectangle(x, y + kDigitThickness, kDigitThickness,
                                  vertical_height, kActivationDigit);
        }
        if (error == ESP_OK && (segments & 0x40U) != 0) {
            error = FillRectangle(x + kDigitThickness, y + middle_y,
                                  kDigitWidth - 2 * kDigitThickness,
                                  kDigitThickness, kActivationDigit);
        }
    }
    if (error == ESP_OK) ESP_LOGI(kTag, "Activation code rendered");
    return error;
}

esp_err_t St7789Display::DrawStandby() {
    esp_err_t error = FillRectangle(0, 0, CONFIG_VEETEE_LCD_WIDTH,
                                    CONFIG_VEETEE_LCD_HEIGHT,
                                    kStandbyBackground);
    if (error != ESP_OK) return error;
    constexpr int kAccentWidth = 72;
    constexpr int kAccentHeight = 6;
    return FillRectangle((CONFIG_VEETEE_LCD_WIDTH - kAccentWidth) / 2,
                         (CONFIG_VEETEE_LCD_HEIGHT - kAccentHeight) / 2,
                         kAccentWidth, kAccentHeight, kStandbyAccent);
}

bool St7789Display::OnColorTransferDone(esp_lcd_panel_io_handle_t,
                                       esp_lcd_panel_io_event_data_t*,
                                       void* context) {
    auto* display = static_cast<St7789Display*>(context);
    BaseType_t task_woken = pdFALSE;
    xSemaphoreGiveFromISR(display->transfer_done_, &task_woken);
    return task_woken == pdTRUE;
}

esp_err_t St7789Display::DrawBitmapSync(int x_start, int y_start, int x_end,
                                       int y_end, const void* pixels) {
    if (transfer_faulted_ || transfer_done_ == nullptr || panel_ == nullptr ||
        pixels == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    while (xSemaphoreTake(transfer_done_, 0) == pdTRUE) {
    }
    const esp_err_t error = esp_lcd_panel_draw_bitmap(panel_, x_start, y_start,
                                                       x_end, y_end, pixels);
    if (error != ESP_OK) return error;
    if (xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(1000)) == pdTRUE) {
        return ESP_OK;
    }
    // DMA may still own the persistent buffer, so never free or reuse it after
    // a transfer timeout.
    transfer_faulted_ = true;
    return ESP_ERR_TIMEOUT;
}

esp_err_t St7789Display::FillRectangle(int x, int y, int width, int height,
                                      std::uint16_t color) {
    if (width <= 0 || height <= 0 || x < 0 || y < 0 ||
        x + width > CONFIG_VEETEE_LCD_WIDTH ||
        y + height > CONFIG_VEETEE_LCD_HEIGHT) {
        return ESP_ERR_INVALID_ARG;
    }
    const int stripe_height = std::min(kDrawLines, height);
    const std::size_t pixel_count = static_cast<std::size_t>(width) * stripe_height;
    if (line_buffer_ == nullptr || transfer_faulted_ ||
        pixel_count > line_buffer_pixels_) {
        return ESP_ERR_INVALID_STATE;
    }
    std::fill_n(line_buffer_, pixel_count, ToPanelEndian(color));

    esp_err_t error = ESP_OK;
    for (int offset = 0; offset < height && error == ESP_OK; offset += stripe_height) {
        const int lines = std::min(stripe_height, height - offset);
        error = DrawBitmapSync(x, y + offset, x + width, y + offset + lines,
                               line_buffer_);
    }
    return error;
}

}  // namespace veetee::display
