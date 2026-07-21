#include "audio/i2s_audio.h"

#include <algorithm>
#include <cinttypes>
#include <cmath>
#include <limits>

#include "board/board_config.h"
#include "esp_log.h"
#include "sdkconfig.h"

namespace veetee::audio {
namespace {

constexpr char kTag[] = "veetee_audio";
constexpr double kPi = 3.14159265358979323846;

std::int16_t ToPcm16(std::int32_t sample) {
    const std::int32_t shifted = sample >> CONFIG_VEETEE_MIC_SAMPLE_SHIFT;
    return static_cast<std::int16_t>(std::clamp<std::int32_t>(
        shifted, std::numeric_limits<std::int16_t>::min(),
        std::numeric_limits<std::int16_t>::max()));
}

}  // namespace

esp_err_t I2sAudio::Initialize() {
    i2s_chan_config_t tx_channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    tx_channel.dma_desc_num = 6;
    tx_channel.dma_frame_num = kToneFrameSamples;
    tx_channel.auto_clear_after_cb = true;
    esp_err_t error = i2s_new_channel(&tx_channel, &tx_handle_, nullptr);
    if (error != ESP_OK) {
        return error;
    }

    i2s_std_config_t tx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(board::kSpeakerSampleRate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {},
    };
    tx_config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    tx_config.gpio_cfg.mclk = I2S_GPIO_UNUSED;
    tx_config.gpio_cfg.bclk = board::kSpeakerBclk;
    tx_config.gpio_cfg.ws = board::kSpeakerWs;
    tx_config.gpio_cfg.dout = board::kSpeakerData;
    tx_config.gpio_cfg.din = I2S_GPIO_UNUSED;
    error = i2s_channel_init_std_mode(tx_handle_, &tx_config);
    if (error != ESP_OK) {
        return error;
    }

    i2s_chan_config_t rx_channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    rx_channel.dma_desc_num = 6;
    rx_channel.dma_frame_num = kMicFrameSamples;
    error = i2s_new_channel(&rx_channel, nullptr, &rx_handle_);
    if (error != ESP_OK) {
        return error;
    }

    i2s_std_config_t rx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(board::kMicSampleRate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {},
    };
    rx_config.slot_cfg.slot_mask = board::kMicSlot;
    rx_config.gpio_cfg.mclk = I2S_GPIO_UNUSED;
    rx_config.gpio_cfg.bclk = board::kMicBclk;
    rx_config.gpio_cfg.ws = board::kMicWs;
    rx_config.gpio_cfg.dout = I2S_GPIO_UNUSED;
    rx_config.gpio_cfg.din = board::kMicData;
    error = i2s_channel_init_std_mode(rx_handle_, &rx_config);
    if (error != ESP_OK) {
        return error;
    }

    if ((error = i2s_channel_enable(tx_handle_)) != ESP_OK ||
        (error = i2s_channel_enable(rx_handle_)) != ESP_OK) {
        return error;
    }

    ESP_LOGI(kTag, "Simplex I2S ready: mic=%" PRIu32 " Hz slot=%s speaker=%" PRIu32 " Hz",
             board::kMicSampleRate,
             board::kMicSlot == I2S_STD_SLOT_LEFT ? "left" : "right",
             board::kSpeakerSampleRate);
    return ESP_OK;
}

esp_err_t I2sAudio::StartDiagnostics() {
#if CONFIG_VEETEE_MIC_DIAGNOSTICS
    if (xTaskCreate(&I2sAudio::MicTaskEntry, "veetee_mic", 4096, this, 5,
                    &mic_task_) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
#endif

#if CONFIG_VEETEE_BOOT_TONE
    stop_playback_.store(false);
    if (xTaskCreate(&I2sAudio::SpeakerTaskEntry, "veetee_tone", 3072, this, 4,
                    &speaker_task_) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
#endif
    return ESP_OK;
}

void I2sAudio::RequestPlaybackStop() {
    stop_playback_.store(true);
}

void I2sAudio::MicTaskEntry(void* context) {
    static_cast<I2sAudio*>(context)->RunMicDiagnostics();
}

void I2sAudio::SpeakerTaskEntry(void* context) {
    auto* audio = static_cast<I2sAudio*>(context);
    audio->PlayBootTone();
    audio->speaker_task_ = nullptr;
    vTaskDelete(nullptr);
}

void I2sAudio::RunMicDiagnostics() {
    std::int64_t sum = 0;
    std::int64_t sum_squares = 0;
    std::uint32_t sample_count = 0;
    std::uint32_t clipped = 0;
    std::int16_t minimum = std::numeric_limits<std::int16_t>::max();
    std::int16_t maximum = std::numeric_limits<std::int16_t>::min();
    TickType_t last_report = xTaskGetTickCount();

    while (true) {
        size_t bytes_read = 0;
        const esp_err_t error = i2s_channel_read(
            rx_handle_, mic_dma_buffer_.data(),
            mic_dma_buffer_.size() * sizeof(mic_dma_buffer_[0]), &bytes_read, 250);
        if (error != ESP_OK) {
            if (error != ESP_ERR_TIMEOUT) {
                ESP_LOGW(kTag, "Microphone read failed: %s", esp_err_to_name(error));
            }
            continue;
        }

        const std::size_t samples = bytes_read / sizeof(mic_dma_buffer_[0]);
        for (std::size_t index = 0; index < samples; ++index) {
            const std::int16_t pcm = ToPcm16(mic_dma_buffer_[index]);
            minimum = std::min(minimum, pcm);
            maximum = std::max(maximum, pcm);
            sum += pcm;
            sum_squares += static_cast<std::int64_t>(pcm) * pcm;
            clipped += std::abs(static_cast<int>(pcm)) >= 32760 ? 1U : 0U;
        }
        sample_count += static_cast<std::uint32_t>(samples);

        const TickType_t now = xTaskGetTickCount();
        if (now - last_report >= pdMS_TO_TICKS(1000) && sample_count > 0) {
            const double mean = static_cast<double>(sum) / sample_count;
            const double rms = std::sqrt(static_cast<double>(sum_squares) / sample_count);
            ESP_LOGI(kTag,
                     "Mic PCM16: samples=%" PRIu32 " min=%d max=%d mean=%.1f rms=%.1f clipped=%" PRIu32,
                     sample_count, minimum, maximum, mean, rms, clipped);
            sum = 0;
            sum_squares = 0;
            sample_count = 0;
            clipped = 0;
            minimum = std::numeric_limits<std::int16_t>::max();
            maximum = std::numeric_limits<std::int16_t>::min();
            last_report = now;
        }
    }
}

void I2sAudio::PlayBootTone() {
    constexpr double kFrequencyHz = 440.0;
    constexpr double kAmplitude = 3500.0;
    constexpr int kFrames = 40;

    for (std::size_t index = 0; index < tone_dma_buffer_.size(); ++index) {
        const double phase = 2.0 * kPi * kFrequencyHz * index / board::kSpeakerSampleRate;
        const std::int32_t pcm16 = static_cast<std::int32_t>(std::sin(phase) * kAmplitude);
        tone_dma_buffer_[index] = pcm16 * 65536;
    }

    for (int frame = 0; frame < kFrames && !stop_playback_.load(); ++frame) {
        size_t bytes_written = 0;
        const esp_err_t error = i2s_channel_write(
            tx_handle_, tone_dma_buffer_.data(),
            tone_dma_buffer_.size() * sizeof(tone_dma_buffer_[0]), &bytes_written, 100);
        if (error != ESP_OK) {
            ESP_LOGW(kTag, "Speaker write failed: %s", esp_err_to_name(error));
            break;
        }
    }

    tone_dma_buffer_.fill(0);
    size_t bytes_written = 0;
    i2s_channel_write(tx_handle_, tone_dma_buffer_.data(),
                      tone_dma_buffer_.size() * sizeof(tone_dma_buffer_[0]),
                      &bytes_written, 100);
    ESP_LOGI(kTag, "Boot tone complete");
}

}  // namespace veetee::audio
