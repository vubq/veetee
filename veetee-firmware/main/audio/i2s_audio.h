#pragma once

#include <array>
#include <atomic>
#include <cstdint>

#include "driver/i2s_std.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace veetee::audio {

class I2sAudio {
public:
    esp_err_t Initialize();
    esp_err_t StartDiagnostics();
    void RequestPlaybackStop();

private:
    static void MicTaskEntry(void* context);
    static void SpeakerTaskEntry(void* context);
    void RunMicDiagnostics();
    void PlayBootTone();

    static constexpr std::size_t kMicFrameSamples = 320;
    static constexpr std::size_t kToneFrameSamples = 240;

    i2s_chan_handle_t rx_handle_ = nullptr;
    i2s_chan_handle_t tx_handle_ = nullptr;
    TaskHandle_t mic_task_ = nullptr;
    TaskHandle_t speaker_task_ = nullptr;
    std::array<std::int32_t, kMicFrameSamples> mic_dma_buffer_{};
    std::array<std::int32_t, kToneFrameSamples> tone_dma_buffer_{};
    std::atomic<bool> stop_playback_{false};
};

}  // namespace veetee::audio
