#include "display/st7789_display.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "board/board_config.h"
#include "display/state_visual.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::display {
namespace {

constexpr char kTag[] = "veetee_display";
constexpr spi_host_device_t kLcdHost = SPI3_HOST;
constexpr int kDrawLines = 16;
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

struct ScreenCopy {
    const char* number;
    const char* kicker;
    const char* title;
    const char* hint;
};

constexpr std::array<ScreenCopy, 13> kScreenCopy = {{
    {"00", "SYSTEM / BOOT", "VEE TEE", "INITIALIZING HARDWARE"},
    {"01", "NETWORK / CONFIG", "WI-FI SETUP", "OPEN 192.168.4.1"},
    {"02", "NETWORK / LINK", "CONNECTING", "TRYING SAVED NETWORKS"},
    {"03", "DEVICE / PAIR", "PAIRING", "ENTER CODE IN MANAGER"},
    {"04", "DEVICE / RECOVERY", "PAIRING LOST", "HOLD BUTTON FOR RECOVERY"},
    {"05", "ASSISTANT / READY", "HEY VEETEE", "BUTTON OR WAKE WORD"},
    {"06", "SESSION / OPEN", "CONNECTING", "OPENING VOICE CHANNEL"},
    {"07", "AUDIO / INPUT", "LISTENING", "SPEAK NATURALLY"},
    {"08", "INPUT / ADMISSION", "EVALUATING", "SIGNAL AND INTENT CHECK"},
    {"09", "AI / EXECUTION", "THINKING", "MODEL AND MCP TOOLS"},
    {"10", "AUDIO / OUTPUT", "SPEAKING", "PRESS TO INTERRUPT"},
    {"11", "TURN / CANCEL", "STOPPING", "CLEARING CURRENT TURN"},
    {"12", "SESSION / CLOSE", "GOODBYE", "READY TO WAKE AGAIN"},
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
    framebuffer_pixels_ = static_cast<std::size_t>(CONFIG_VEETEE_LCD_WIDTH) *
                          CONFIG_VEETEE_LCD_HEIGHT;
    framebuffer_ = static_cast<std::uint16_t*>(heap_caps_malloc(
        framebuffer_pixels_ * sizeof(std::uint16_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (framebuffer_ == nullptr) {
        framebuffer_ = static_cast<std::uint16_t*>(heap_caps_malloc(
            framebuffer_pixels_ * sizeof(std::uint16_t), MALLOC_CAP_8BIT));
    }
    if (framebuffer_ == nullptr) return ESP_ERR_NO_MEM;

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
    return RenderState(state, nullptr);
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
    return RenderState(app::State::kActivating, code);
}

esp_err_t St7789Display::DrawStandby() {
    return DrawState(app::State::kIdle);
}

esp_err_t St7789Display::ReloadUiPack(const char* partition_label) {
    UiTheme candidate{};
    const esp_err_t error = LoadUiPackPartition(partition_label, &candidate);
    if (error != ESP_OK) return error;
    theme_ = candidate;
    std::snprintf(loaded_ui_partition_, sizeof(loaded_ui_partition_), "%s",
                  partition_label);
    last_render_ok_ = false;
    return ESP_OK;
}

void St7789Display::UseBuiltInSignal() {
    theme_ = BuiltInSignalTheme();
    loaded_ui_partition_[0] = '\0';
    last_render_ok_ = false;
}

bool St7789Display::UiPackHealthy() const {
    return last_render_ok_ && IsValidUiTheme(theme_);
}

esp_err_t St7789Display::RenderState(app::State state,
                                     const char* activation_code) {
    if (panel_ == nullptr || line_buffer_ == nullptr || framebuffer_ == nullptr ||
        transfer_faulted_) {
        return ESP_ERR_INVALID_STATE;
    }
    const std::size_t state_index = static_cast<std::size_t>(state);
    if (state_index >= theme_.states.size()) return ESP_ERR_INVALID_ARG;
    const UiStateStyle& style = theme_.states[state_index];
    switch (theme_.composition) {
        case UiComposition::kSignal:
            RenderSignal(state, style, activation_code);
            break;
        case UiComposition::kMonolith:
            RenderMonolith(state, style, activation_code);
            break;
        case UiComposition::kQuiet:
            RenderQuiet(state, style, activation_code);
            break;
    }
    const esp_err_t error = FlushFramebuffer();
    last_render_ok_ = error == ESP_OK;
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "State rendered state=%s theme=%s external=%d",
                 app::ToString(state), theme_.theme_id, theme_.external);
    }
    return error;
}

void St7789Display::RenderSignal(app::State state, const UiStateStyle& style,
                                 const char* activation_code) {
    const std::size_t index = static_cast<std::size_t>(state);
    const ScreenCopy& copy = kScreenCopy[index];
    CanvasFill(style.background);
    CanvasRectangle(16, 18, 30, 3, style.accent);
    CanvasText("VEE/TEE", 54, 13, 1, style.foreground);
    CanvasText(copy.number, 16, 48, 2, style.accent);
    CanvasText(copy.kicker, 58, 53, 1, style.foreground);
    CanvasTextCentered(copy.title, 82, 3, style.foreground);

    const int center_x = CONFIG_VEETEE_LCD_WIDTH / 2;
    const int center_y = 183;
    if (activation_code != nullptr) {
        CanvasCircle(center_x, center_y, 57, style.accent, false);
        CanvasCircle(center_x, center_y, 49, style.foreground, false);
        CanvasTextCentered(activation_code, center_y - 12, 4, style.accent);
    } else {
        CanvasCircle(center_x, center_y, 53, style.accent, false);
        CanvasCircle(center_x, center_y, 40, style.foreground, false);
        CanvasCircle(center_x, center_y, 17, style.accent, true);
        for (int segment = 0; segment < 7; ++segment) {
            const int x0 = center_x - 76 + segment * 24;
            const int amplitude = 5 + ((static_cast<int>(index) + segment) % 4) * 4;
            CanvasLine(x0, center_y, x0 + 12, center_y - amplitude, 2,
                       style.foreground);
            CanvasLine(x0 + 12, center_y - amplitude, x0 + 24, center_y, 2,
                       style.foreground);
        }
    }
    CanvasRectangle(16, 273, CONFIG_VEETEE_LCD_WIDTH - 32, 1, style.accent);
    CanvasTextCentered(copy.hint, 290, 1, style.foreground);
}

void St7789Display::RenderMonolith(app::State state,
                                   const UiStateStyle& style,
                                   const char* activation_code) {
    const std::size_t index = static_cast<std::size_t>(state);
    const ScreenCopy& copy = kScreenCopy[index];
    CanvasFill(style.background);
    CanvasRectangle(0, 0, 9, CONFIG_VEETEE_LCD_HEIGHT, style.accent);
    CanvasText("VEE/TEE", 24, 20, 2, style.foreground);
    CanvasText(copy.number, 24, 62, 4, style.accent);
    CanvasText(copy.kicker, 24, 104, 1, style.foreground);
    CanvasText(copy.title, 24, 126, 3, style.foreground);
    if (activation_code != nullptr) {
        CanvasText(activation_code, 24, 188, 4, style.accent);
    } else {
        for (int bar = 0; bar < 9; ++bar) {
            const int height = 10 + ((bar + static_cast<int>(index)) % 5) * 11;
            CanvasRectangle(24 + bar * 20, 234 - height, 11, height,
                            bar % 2 == 0 ? style.accent : style.foreground);
        }
    }
    CanvasText(copy.hint, 24, 286, 1, style.foreground);
}

void St7789Display::RenderQuiet(app::State state, const UiStateStyle& style,
                                const char* activation_code) {
    const std::size_t index = static_cast<std::size_t>(state);
    const ScreenCopy& copy = kScreenCopy[index];
    CanvasFill(style.background);
    CanvasText("VEE TEE", 18, 18, 1, style.foreground);
    CanvasText(copy.number, CONFIG_VEETEE_LCD_WIDTH - 42, 18, 1, style.accent);
    const int center_x = CONFIG_VEETEE_LCD_WIDTH / 2;
    const int center_y = 150;
    CanvasCircle(center_x, center_y, 58, style.accent, false);
    CanvasCircle(center_x, center_y, 40, style.foreground, false);
    if (activation_code != nullptr) {
        CanvasTextCentered(activation_code, center_y - 10, 3, style.accent);
    } else {
        CanvasCircle(center_x, center_y, 11, style.accent, true);
    }
    CanvasTextCentered(copy.title, 229, 2, style.foreground);
    CanvasTextCentered(copy.hint, 278, 1, style.foreground);
}

void St7789Display::CanvasFill(std::uint16_t color) {
    if (framebuffer_ != nullptr) {
        std::fill_n(framebuffer_, framebuffer_pixels_, color);
    }
}

void St7789Display::CanvasRectangle(int x, int y, int width, int height,
                                    std::uint16_t color) {
    if (framebuffer_ == nullptr || width <= 0 || height <= 0) return;
    const int left = std::max(0, x);
    const int top = std::max(0, y);
    const int right = std::min(CONFIG_VEETEE_LCD_WIDTH, x + width);
    const int bottom = std::min(CONFIG_VEETEE_LCD_HEIGHT, y + height);
    for (int row = top; row < bottom; ++row) {
        std::fill(framebuffer_ + row * CONFIG_VEETEE_LCD_WIDTH + left,
                  framebuffer_ + row * CONFIG_VEETEE_LCD_WIDTH + right, color);
    }
}

void St7789Display::CanvasCircle(int center_x, int center_y, int radius,
                                 std::uint16_t color, bool filled) {
    if (radius <= 0) return;
    const int outer_squared = radius * radius;
    const int inner_radius = std::max(0, radius - 2);
    const int inner_squared = inner_radius * inner_radius;
    for (int y = center_y - radius; y <= center_y + radius; ++y) {
        for (int x = center_x - radius; x <= center_x + radius; ++x) {
            const int dx = x - center_x;
            const int dy = y - center_y;
            const int distance = dx * dx + dy * dy;
            if (distance <= outer_squared && (filled || distance >= inner_squared)) {
                CanvasRectangle(x, y, 1, 1, color);
            }
        }
    }
}

void St7789Display::CanvasLine(int x0, int y0, int x1, int y1, int thickness,
                               std::uint16_t color) {
    const int dx = std::abs(x1 - x0);
    const int step_x = x0 < x1 ? 1 : -1;
    const int dy = -std::abs(y1 - y0);
    const int step_y = y0 < y1 ? 1 : -1;
    int error = dx + dy;
    while (true) {
        CanvasRectangle(x0 - thickness / 2, y0 - thickness / 2, thickness,
                        thickness, color);
        if (x0 == x1 && y0 == y1) break;
        const int doubled = 2 * error;
        if (doubled >= dy) {
            error += dy;
            x0 += step_x;
        }
        if (doubled <= dx) {
            error += dx;
            y0 += step_y;
        }
    }
}

void St7789Display::CanvasText(const char* text, int x, int y, int scale,
                               std::uint16_t color) {
    if (text == nullptr || scale <= 0) return;
    for (const char* cursor = text; *cursor != '\0'; ++cursor) {
        CanvasGlyph(*cursor, x, y, scale, color);
        x += 6 * scale;
    }
}

void St7789Display::CanvasTextCentered(const char* text, int y, int scale,
                                       std::uint16_t color) {
    if (text == nullptr || scale <= 0) return;
    const int length = static_cast<int>(std::strlen(text));
    while (scale > 1 && length * 6 * scale > CONFIG_VEETEE_LCD_WIDTH - 20) {
        --scale;
    }
    const int width = std::max(0, length * 6 * scale - scale);
    CanvasText(text, std::max(0, (CONFIG_VEETEE_LCD_WIDTH - width) / 2), y,
               scale, color);
}

void St7789Display::CanvasGlyph(char character, int x, int y, int scale,
                                std::uint16_t color) {
    if (character >= 'a' && character <= 'z') {
        character = static_cast<char>(character - 'a' + 'A');
    }
    if (character == ' ') return;
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
    } else if (character == '/') {
        glyph = {0x40, 0x30, 0x0C, 0x03, 0x00};
    } else {
        return;
    }
    for (int column = 0; column < 5; ++column) {
        for (int row = 0; row < 7; ++row) {
            if ((glyph[column] & (1U << row)) != 0) {
                CanvasRectangle(x + column * scale, y + row * scale, scale,
                                scale, color);
            }
        }
    }
}

esp_err_t St7789Display::FlushFramebuffer() {
    if (framebuffer_ == nullptr || line_buffer_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = ESP_OK;
    for (int y = 0; y < CONFIG_VEETEE_LCD_HEIGHT && error == ESP_OK;
         y += kDrawLines) {
        const int lines = std::min(kDrawLines, CONFIG_VEETEE_LCD_HEIGHT - y);
        const std::size_t pixels =
            static_cast<std::size_t>(lines) * CONFIG_VEETEE_LCD_WIDTH;
        for (std::size_t index = 0; index < pixels; ++index) {
            line_buffer_[index] = ToPanelEndian(
                framebuffer_[static_cast<std::size_t>(y) *
                                 CONFIG_VEETEE_LCD_WIDTH +
                             index]);
        }
        error = DrawBitmapSync(0, y, CONFIG_VEETEE_LCD_WIDTH, y + lines,
                               line_buffer_);
    }
    return error;
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
