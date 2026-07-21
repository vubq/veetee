#include "display/st7789_display.h"

#include <algorithm>
#include <array>
#include <cstdlib>

#include "board/board_config.h"
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

std::uint16_t ToPanelEndian(std::uint16_t color) {
    return static_cast<std::uint16_t>((color << 8U) | (color >> 8U));
}

}  // namespace

esp_err_t St7789Display::Initialize() {
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

    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num = board::kDisplayChipSelect;
    io_config.dc_gpio_num = board::kDisplayDc;
    io_config.spi_mode = 0;
    io_config.pclk_hz = CONFIG_VEETEE_LCD_SPI_CLOCK_HZ;
    io_config.trans_queue_depth = 4;
    io_config.lcd_cmd_bits = 8;
    io_config.lcd_param_bits = 8;
    error = esp_lcd_new_panel_io_spi(kLcdHost, &io_config, &panel_io_);
    if (error != ESP_OK) {
        return error;
    }

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
    ESP_LOGI(kTag, "ST7789 initialized: %dx%d offset=%d,%d SPI=%d Hz",
             CONFIG_VEETEE_LCD_WIDTH, CONFIG_VEETEE_LCD_HEIGHT,
             CONFIG_VEETEE_LCD_OFFSET_X, CONFIG_VEETEE_LCD_OFFSET_Y,
             CONFIG_VEETEE_LCD_SPI_CLOCK_HZ);
    return ESP_OK;
}

esp_err_t St7789Display::DrawColorBars() {
    if (panel_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    const std::size_t pixel_count =
        static_cast<std::size_t>(CONFIG_VEETEE_LCD_WIDTH) * kDrawLines;
    auto* line_buffer = static_cast<std::uint16_t*>(
        spi_bus_dma_memory_alloc(kLcdHost, pixel_count * sizeof(std::uint16_t), 0));
    if (line_buffer == nullptr) {
        return ESP_ERR_NO_MEM;
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
                line_buffer[row * CONFIG_VEETEE_LCD_WIDTH + x] =
                    ToPanelEndian(kColorBars[bar]);
            }
        }
        error = esp_lcd_panel_draw_bitmap(panel_, 0, y, CONFIG_VEETEE_LCD_WIDTH,
                                          y + lines, line_buffer);
    }

    free(line_buffer);
    if (error == ESP_OK) {
        ESP_LOGI(kTag, "Color bars rendered");
    }
    return error;
}

}  // namespace veetee::display
