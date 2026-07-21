#pragma once

#include <cstdint>

#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "sdkconfig.h"

namespace veetee::board {

inline constexpr char kBoardName[] = "veetee-s3-n16r8";

inline constexpr gpio_num_t kMicWs = GPIO_NUM_4;
inline constexpr gpio_num_t kMicBclk = GPIO_NUM_5;
inline constexpr gpio_num_t kMicData = GPIO_NUM_6;

inline constexpr gpio_num_t kSpeakerData = GPIO_NUM_7;
inline constexpr gpio_num_t kSpeakerBclk = GPIO_NUM_15;
inline constexpr gpio_num_t kSpeakerWs = GPIO_NUM_16;

inline constexpr gpio_num_t kDisplayMosi = GPIO_NUM_47;
inline constexpr gpio_num_t kDisplayClock = GPIO_NUM_21;
inline constexpr gpio_num_t kDisplayDc = GPIO_NUM_40;
inline constexpr gpio_num_t kDisplayReset = GPIO_NUM_45;
inline constexpr gpio_num_t kDisplayChipSelect = GPIO_NUM_41;
inline constexpr gpio_num_t kDisplayBacklight = GPIO_NUM_42;

inline constexpr gpio_num_t kAssistantButton = GPIO_NUM_0;
inline constexpr gpio_num_t kStatusLed = GPIO_NUM_48;

inline constexpr std::uint32_t kMicSampleRate = 16000;
inline constexpr std::uint32_t kSpeakerSampleRate = 24000;
inline constexpr int kLcdSpiMode = CONFIG_VEETEE_LCD_SPI_MODE;

#ifdef CONFIG_VEETEE_LCD_MIRROR_X
inline constexpr bool kLcdMirrorX = true;
#else
inline constexpr bool kLcdMirrorX = false;
#endif

#ifdef CONFIG_VEETEE_LCD_MIRROR_Y
inline constexpr bool kLcdMirrorY = true;
#else
inline constexpr bool kLcdMirrorY = false;
#endif

#ifdef CONFIG_VEETEE_LCD_SWAP_XY
inline constexpr bool kLcdSwapXy = true;
#else
inline constexpr bool kLcdSwapXy = false;
#endif

#ifdef CONFIG_VEETEE_LCD_INVERT_COLOR
inline constexpr bool kLcdInvertColor = true;
#else
inline constexpr bool kLcdInvertColor = false;
#endif

#ifdef CONFIG_VEETEE_LCD_BGR_ORDER
inline constexpr bool kLcdBgrOrder = true;
#else
inline constexpr bool kLcdBgrOrder = false;
#endif

#ifdef CONFIG_VEETEE_LCD_BACKLIGHT_INVERT
inline constexpr bool kLcdBacklightInvert = true;
#else
inline constexpr bool kLcdBacklightInvert = false;
#endif

#if CONFIG_VEETEE_MIC_SLOT_RIGHT
inline constexpr i2s_std_slot_mask_t kMicSlot = I2S_STD_SLOT_RIGHT;
#else
inline constexpr i2s_std_slot_mask_t kMicSlot = I2S_STD_SLOT_LEFT;
#endif

}  // namespace veetee::board
