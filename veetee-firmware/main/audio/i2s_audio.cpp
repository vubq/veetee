#include "audio/i2s_audio.h"

#include <algorithm>
#include <cinttypes>
#include <cmath>
#include <cstring>
#include <limits>

#include "board/board_config.h"
#include "decoder/impl/esp_opus_dec.h"
#include "encoder/impl/esp_opus_enc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/idf_additions.h"
#include "sdkconfig.h"

namespace veetee::audio {
namespace {

constexpr char kTag[] = "veetee_audio";
constexpr double kPi = 3.14159265358979323846;
constexpr UBaseType_t kPlaybackQueueDepth = 12;
constexpr TickType_t kPlaybackControlTimeout = pdMS_TO_TICKS(20);

std::int16_t ToPcm16(std::int32_t sample) {
    const std::int32_t shifted = sample >> CONFIG_VEETEE_MIC_SAMPLE_SHIFT;
    return static_cast<std::int16_t>(std::clamp<std::int32_t>(
        shifted, std::numeric_limits<std::int16_t>::min(),
        std::numeric_limits<std::int16_t>::max()));
}

std::int32_t ToSpeakerSample(std::int16_t sample, int volume_percent) {
    const std::int64_t scaled = static_cast<std::int64_t>(sample) *
                                volume_percent * 65536 / 100;
    return static_cast<std::int32_t>(scaled);
}

}  // namespace

esp_err_t I2sAudio::Initialize(EncodedAudioSink encoded_sink,
                               PcmFrameSink pcm_frame_sink,
                               PlaybackFinishedSink playback_finished_sink,
                               void* sink_context,
                               void* pcm_frame_context) {
    if (encoded_sink == nullptr || pcm_frame_sink == nullptr ||
        playback_finished_sink == nullptr || encoder_ != nullptr ||
        decoder_ != nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    encoded_sink_ = encoded_sink;
    pcm_frame_sink_ = pcm_frame_sink;
    playback_finished_sink_ = playback_finished_sink;
    sink_context_ = sink_context;
    pcm_frame_context_ = pcm_frame_context;
    volume_percent_.store(CONFIG_VEETEE_DEFAULT_VOLUME_PERCENT);

    i2s_chan_config_t tx_channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    tx_channel.dma_desc_num = 6;
    tx_channel.dma_frame_num = kSpeakerDmaSamples;
    tx_channel.auto_clear_after_cb = true;
    esp_err_t error = i2s_new_channel(&tx_channel, &tx_handle_, nullptr);
    if (error != ESP_OK) return error;

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
    if ((error = i2s_channel_init_std_mode(tx_handle_, &tx_config)) != ESP_OK) {
        return error;
    }

    i2s_chan_config_t rx_channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    rx_channel.dma_desc_num = 6;
    rx_channel.dma_frame_num = kMicReadSamples;
    if ((error = i2s_new_channel(&rx_channel, nullptr, &rx_handle_)) != ESP_OK) {
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
    if ((error = i2s_channel_init_std_mode(rx_handle_, &rx_config)) != ESP_OK) {
        return error;
    }
    if ((error = i2s_channel_enable(tx_handle_)) != ESP_OK ||
        (error = i2s_channel_enable(rx_handle_)) != ESP_OK) {
        return error;
    }

    esp_opus_enc_config_t encoder_config = ESP_OPUS_ENC_CONFIG_DEFAULT();
    encoder_config.sample_rate = board::kMicSampleRate;
    encoder_config.channel = ESP_AUDIO_MONO;
    encoder_config.bits_per_sample = ESP_AUDIO_BIT16;
    encoder_config.bitrate = 24000;
    encoder_config.frame_duration = ESP_OPUS_ENC_FRAME_DURATION_60_MS;
    encoder_config.application_mode = ESP_OPUS_ENC_APPLICATION_VOIP;
    encoder_config.complexity = 5;
    encoder_config.enable_fec = false;
    encoder_config.enable_dtx = false;
    encoder_config.enable_vbr = true;
    if (esp_opus_enc_open(&encoder_config, sizeof(encoder_config), &encoder_) !=
        ESP_AUDIO_ERR_OK) {
        return ESP_FAIL;
    }

    int encoder_input_size = 0;
    int encoder_output_size = 0;
    if (esp_opus_enc_get_frame_size(encoder_, &encoder_input_size,
                                    &encoder_output_size) != ESP_AUDIO_ERR_OK ||
        encoder_input_size != static_cast<int>(capture_pcm_.size() * sizeof(std::int16_t)) ||
        encoder_output_size <= 0 ||
        encoder_output_size > static_cast<int>(encoded_buffer_.size())) {
        return ESP_ERR_INVALID_SIZE;
    }

    esp_opus_dec_cfg_t decoder_config = ESP_OPUS_DEC_CONFIG_DEFAULT();
    decoder_config.sample_rate = board::kSpeakerSampleRate;
    decoder_config.channel = ESP_AUDIO_MONO;
    decoder_config.frame_duration = ESP_OPUS_DEC_FRAME_DURATION_60_MS;
    decoder_config.self_delimited = false;
    if (esp_opus_dec_open(&decoder_config, sizeof(decoder_config), &decoder_) !=
        ESP_AUDIO_ERR_OK) {
        return ESP_FAIL;
    }

    playback_queue_ = xQueueCreate(kPlaybackQueueDepth, sizeof(PlaybackItem));
    if (playback_queue_ == nullptr) return ESP_ERR_NO_MEM;

    ESP_LOGI(kTag,
             "Realtime I2S ready: uplink=%" PRIu32 " Hz/60 ms downlink=%" PRIu32
             " Hz/60 ms",
             board::kMicSampleRate, board::kSpeakerSampleRate);
    return ESP_OK;
}

esp_err_t I2sAudio::Start(bool play_boot_chime) {
    if (encoder_ == nullptr || decoder_ == nullptr || playback_queue_ == nullptr ||
        capture_task_ != nullptr || playback_task_ != nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    play_boot_chime_ = play_boot_chime;
    // Opus codec calls need an internal task stack on this ESP32-S3 profile.
    const UBaseType_t audio_stack_caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    if (xTaskCreateWithCaps(&I2sAudio::CaptureTaskEntry, "veetee_capture",
                            12 * 1024, this, 6, &capture_task_,
                            audio_stack_caps) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreateWithCaps(&I2sAudio::PlaybackTaskEntry, "veetee_playback",
                            12 * 1024, this, 6, &playback_task_,
                            audio_stack_caps) != pdPASS) {
        vTaskDeleteWithCaps(capture_task_);
        capture_task_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void I2sAudio::SetCaptureEnabled(bool enabled) {
    const bool previous = capture_enabled_.exchange(enabled);
    if (previous != enabled) capture_generation_.fetch_add(1);
}

void I2sAudio::BeginPlayback() {
    const std::uint32_t generation = playback_generation_.fetch_add(1) + 1;
    playback_accepting_.store(true);
    xQueueReset(playback_queue_);
    if (!QueuePlaybackControl(PlaybackItemKind::kBegin, generation)) {
        playback_accepting_.store(false);
        ESP_LOGW(kTag, "Unable to start playback generation=%" PRIu32, generation);
    }
}

bool I2sAudio::QueueOpusPlayback(const std::uint8_t* packet,
                                 std::size_t length) {
    if (packet == nullptr || length == 0 || length > 1500 ||
        !playback_accepting_.load() || playback_queue_ == nullptr) {
        return false;
    }
    PlaybackItem item{.kind = PlaybackItemKind::kPacket,
                      .generation = playback_generation_.load(),
                      .length = static_cast<std::uint16_t>(length)};
    std::memcpy(item.data.data(), packet, length);
    if (xQueueSend(playback_queue_, &item, 0) != pdTRUE) {
        RecordAudioCounter(AudioCounter::kPlaybackQueueDrop);
        return false;
    }
    ObservePlaybackQueueDepth();
    return true;
}

void I2sAudio::EndPlayback() {
    if (!playback_accepting_.exchange(false)) return;
    const std::uint32_t generation = playback_generation_.load();
    if (!QueuePlaybackControl(PlaybackItemKind::kEnd, generation)) {
        AbortPlayback();
    }
}

void I2sAudio::AbortPlayback() {
    playback_accepting_.store(false);
    const std::uint32_t generation = playback_generation_.fetch_add(1) + 1;
    if (playback_queue_ == nullptr) return;
    xQueueReset(playback_queue_);
    if (!QueuePlaybackControl(PlaybackItemKind::kAbort, generation)) {
        ESP_LOGW(kTag, "Unable to queue playback abort");
    }
}

bool I2sAudio::SetVolumePercent(int volume_percent) {
    if (volume_percent < 0 || volume_percent > 100) return false;
    volume_percent_.store(volume_percent);
    ESP_LOGI(kTag, "Speaker software volume=%d%%", volume_percent);
    return true;
}

bool I2sAudio::StartDiagnostic(std::uint32_t duration_seconds,
                               std::uint64_t now_ms) {
    taskENTER_CRITICAL(&diagnostics_mux_);
    const bool started = diagnostics_.Start(duration_seconds, now_ms);
    taskEXIT_CRITICAL(&diagnostics_mux_);
    return started;
}

AudioRuntimeHealth I2sAudio::Health(std::uint64_t now_ms) {
    AudioRuntimeHealth health{
        .capture_task_running = capture_task_ != nullptr,
        .playback_task_running = playback_task_ != nullptr,
        .capture_stack_free_bytes =
            capture_task_ == nullptr
                ? 0
                : static_cast<std::uint32_t>(
                      uxTaskGetStackHighWaterMark(capture_task_)),
        .playback_stack_free_bytes =
            playback_task_ == nullptr
                ? 0
                : static_cast<std::uint32_t>(
                      uxTaskGetStackHighWaterMark(playback_task_)),
    };
    taskENTER_CRITICAL(&diagnostics_mux_);
    health.lifetime = diagnostics_.LifetimeCounters();
    health.diagnostic = diagnostics_.Snapshot(now_ms);
    taskEXIT_CRITICAL(&diagnostics_mux_);
    return health;
}

void I2sAudio::CaptureTaskEntry(void* context) {
    static_cast<I2sAudio*>(context)->RunCapture();
}

void I2sAudio::PlaybackTaskEntry(void* context) {
    static_cast<I2sAudio*>(context)->RunPlayback();
}

void I2sAudio::RunCapture() {
    std::size_t captured_samples = 0;
    std::uint32_t local_generation = capture_generation_.load();
#if CONFIG_VEETEE_MIC_DIAGNOSTICS
    std::int64_t sum_squares = 0;
    std::uint32_t diagnostic_samples = 0;
    TickType_t last_report = xTaskGetTickCount();
#endif

    while (true) {
        size_t bytes_read = 0;
        const esp_err_t error = i2s_channel_read(
            rx_handle_, mic_dma_buffer_.data(),
            mic_dma_buffer_.size() * sizeof(mic_dma_buffer_[0]), &bytes_read,
            pdMS_TO_TICKS(250));
        if (error != ESP_OK) {
            if (error == ESP_ERR_TIMEOUT) {
                RecordAudioCounter(AudioCounter::kMicReadTimeout);
            } else {
                RecordAudioCounter(AudioCounter::kMicReadError);
                ESP_LOGW(kTag, "Microphone read failed: %s", esp_err_to_name(error));
            }
            continue;
        }

        const std::uint32_t generation = capture_generation_.load();
        if (generation != local_generation) {
            local_generation = generation;
            captured_samples = 0;
            esp_opus_enc_reset(encoder_);
        }

        const std::size_t samples = bytes_read / sizeof(mic_dma_buffer_[0]);
        for (std::size_t index = 0; index < samples; ++index) {
            const std::int16_t pcm = ToPcm16(mic_dma_buffer_[index]);
            detector_pcm_[index] = pcm;
#if CONFIG_VEETEE_MIC_DIAGNOSTICS
            sum_squares += static_cast<std::int64_t>(pcm) * pcm;
            ++diagnostic_samples;
#endif
            if (capture_enabled_.load() && captured_samples < capture_pcm_.size()) {
                capture_pcm_[captured_samples++] = pcm;
            }
        }
        taskENTER_CRITICAL(&diagnostics_mux_);
        diagnostics_.ObservePcm(
            detector_pcm_.data(), samples,
            static_cast<std::uint64_t>(esp_timer_get_time() / 1000));
        taskEXIT_CRITICAL(&diagnostics_mux_);
        if (!pcm_frame_sink_(detector_pcm_.data(), samples,
                             pcm_frame_context_)) {
            RecordAudioCounter(AudioCounter::kDetectorFrameDrop);
            ESP_LOGD(kTag, "Dropped local detector PCM frame");
        }

#if CONFIG_VEETEE_MIC_DIAGNOSTICS
        const TickType_t now = xTaskGetTickCount();
        if (now - last_report >= pdMS_TO_TICKS(1000) && diagnostic_samples > 0) {
            const double rms =
                std::sqrt(static_cast<double>(sum_squares) / diagnostic_samples);
            ESP_LOGI(kTag, "Mic PCM16: samples=%" PRIu32 " rms=%.1f capture=%s",
                     diagnostic_samples, rms,
                     capture_enabled_.load() ? "on" : "off");
            sum_squares = 0;
            diagnostic_samples = 0;
            last_report = now;
        }
#endif

        if (!capture_enabled_.load()) {
            captured_samples = 0;
            continue;
        }
        if (captured_samples != capture_pcm_.size()) continue;

        esp_audio_enc_in_frame_t input = {
            .buffer = reinterpret_cast<std::uint8_t*>(capture_pcm_.data()),
            .len = static_cast<std::uint32_t>(capture_pcm_.size() * sizeof(std::int16_t)),
        };
        esp_audio_enc_out_frame_t output = {
            .buffer = encoded_buffer_.data(),
            .len = static_cast<std::uint32_t>(encoded_buffer_.size()),
            .encoded_bytes = 0,
            .pts = 0,
        };
        const esp_audio_err_t encode_error =
            esp_opus_enc_process(encoder_, &input, &output);
        captured_samples = 0;
        if (encode_error != ESP_AUDIO_ERR_OK || output.encoded_bytes == 0 ||
            output.encoded_bytes > encoded_buffer_.size()) {
            RecordAudioCounter(AudioCounter::kOpusEncodeFailure);
            ESP_LOGW(kTag, "Opus encode failed: %d", encode_error);
            continue;
        }
        if (capture_enabled_.load() &&
            capture_generation_.load() == local_generation &&
            !encoded_sink_(encoded_buffer_.data(), output.encoded_bytes,
                           sink_context_)) {
            RecordAudioCounter(AudioCounter::kUplinkDrop);
            ESP_LOGD(kTag, "Dropped realtime uplink frame");
        }
    }
}

void I2sAudio::RunPlayback() {
#if CONFIG_VEETEE_BOOT_TONE
    if (play_boot_chime_) {
        PlayBootChime();
    } else {
        ESP_LOGI(kTag, "Startup chime suppressed after abnormal/software reset");
    }
#endif
    PlaybackItem item{};
    std::uint32_t decoder_generation = 0;
    while (true) {
#if CONFIG_VEETEE_KEEP_SPEAKER_CLOCKED
        if (xQueueReceive(playback_queue_, &item, 0) != pdTRUE) {
            WriteSilence();
            continue;
        }
#else
        if (xQueueReceive(playback_queue_, &item, portMAX_DELAY) != pdTRUE) {
            continue;
        }
#endif
        if (item.kind == PlaybackItemKind::kBegin) {
            decoder_generation = item.generation;
            esp_opus_dec_reset(decoder_);
            continue;
        }
        if (item.kind == PlaybackItemKind::kAbort) {
            decoder_generation = item.generation;
            esp_opus_dec_reset(decoder_);
            WriteSilence();
            continue;
        }
        if (item.generation != decoder_generation ||
            item.generation != playback_generation_.load()) {
            continue;
        }
        if (item.kind == PlaybackItemKind::kEnd) {
            WriteSilence();
            if (playback_finished_sink_ != nullptr) {
                playback_finished_sink_(sink_context_);
            }
            continue;
        }

        esp_audio_dec_in_raw_t raw = {
            .buffer = item.data.data(),
            .len = item.length,
            .consumed = 0,
            .frame_recover = ESP_AUDIO_DEC_RECOVERY_NONE,
        };
        esp_audio_dec_out_frame_t frame = {
            .buffer = reinterpret_cast<std::uint8_t*>(playback_pcm_.data()),
            .len = static_cast<std::uint32_t>(playback_pcm_.size() * sizeof(std::int16_t)),
            .needed_size = 0,
            .decoded_size = 0,
        };
        esp_audio_dec_info_t info{};
        const esp_audio_err_t decode_error =
            esp_opus_dec_decode(decoder_, &raw, &frame, &info);
        if (decode_error != ESP_AUDIO_ERR_OK || raw.consumed != item.length ||
            frame.decoded_size != playback_pcm_.size() * sizeof(std::int16_t)) {
            RecordAudioCounter(AudioCounter::kOpusDecodeFailure);
            ESP_LOGW(kTag, "Opus decode failed: error=%d consumed=%" PRIu32
                           " decoded=%" PRIu32,
                     decode_error, raw.consumed, frame.decoded_size);
            continue;
        }
        if (item.generation != playback_generation_.load()) continue;

        const int volume_percent = volume_percent_.load();
        for (std::size_t index = 0; index < playback_pcm_.size(); ++index) {
            speaker_dma_buffer_[index] =
                ToSpeakerSample(playback_pcm_[index], volume_percent);
        }
        size_t bytes_written = 0;
        const esp_err_t write_error = i2s_channel_write(
            tx_handle_, speaker_dma_buffer_.data(),
            speaker_dma_buffer_.size() * sizeof(speaker_dma_buffer_[0]),
            &bytes_written, pdMS_TO_TICKS(100));
        if (write_error != ESP_OK ||
            bytes_written != speaker_dma_buffer_.size() *
                                 sizeof(speaker_dma_buffer_[0])) {
            RecordAudioCounter(AudioCounter::kSpeakerWriteFailure);
            ESP_LOGW(kTag, "Speaker write failed: %s",
                     esp_err_to_name(write_error));
        }
    }
}

bool I2sAudio::WriteChimeNote(double frequency_hz, int frame_count) {
    constexpr double kAmplitude = 2600.0;
    const std::size_t total_samples =
        tone_dma_buffer_.size() * static_cast<std::size_t>(frame_count);
    for (int frame = 0; frame < frame_count; ++frame) {
        for (std::size_t index = 0; index < tone_dma_buffer_.size(); ++index) {
            const std::size_t sample =
                static_cast<std::size_t>(frame) * tone_dma_buffer_.size() + index;
            const double phase = 2.0 * kPi * frequency_hz * sample /
                                 board::kSpeakerSampleRate;
            const double envelope = std::sin(
                kPi * (static_cast<double>(sample) + 0.5) / total_samples);
            const std::int32_t pcm16 = static_cast<std::int32_t>(
                std::sin(phase) * envelope * envelope * kAmplitude);
            tone_dma_buffer_[index] = ToSpeakerSample(
                static_cast<std::int16_t>(pcm16), volume_percent_.load());
        }
        size_t bytes_written = 0;
        if (i2s_channel_write(tx_handle_, tone_dma_buffer_.data(),
                              tone_dma_buffer_.size() * sizeof(tone_dma_buffer_[0]),
                              &bytes_written, pdMS_TO_TICKS(100)) != ESP_OK) {
            return false;
        }
    }
    return true;
}

void I2sAudio::PlayBootChime() {
    constexpr int kFirstNoteFrames = 5;
    constexpr int kGapFrames = 1;
    constexpr int kSecondNoteFrames = 7;
    bool complete = WriteChimeNote(659.25, kFirstNoteFrames);
    if (complete) {
        tone_dma_buffer_.fill(0);
        for (int frame = 0; frame < kGapFrames && complete; ++frame) {
            size_t bytes_written = 0;
            complete = i2s_channel_write(
                           tx_handle_, tone_dma_buffer_.data(),
                           tone_dma_buffer_.size() * sizeof(tone_dma_buffer_[0]),
                           &bytes_written, pdMS_TO_TICKS(100)) == ESP_OK;
        }
    }
    if (complete) complete = WriteChimeNote(783.99, kSecondNoteFrames);
    WriteSilence();
    if (complete) {
        ESP_LOGI(kTag, "Startup chime complete");
    } else {
        ESP_LOGW(kTag, "Startup chime interrupted by an I2S write error");
    }
}

void I2sAudio::WriteSilence() {
    speaker_dma_buffer_.fill(0);
    size_t bytes_written = 0;
    i2s_channel_write(tx_handle_, speaker_dma_buffer_.data(),
                      speaker_dma_buffer_.size() * sizeof(speaker_dma_buffer_[0]),
                      &bytes_written, pdMS_TO_TICKS(100));
}

bool I2sAudio::QueuePlaybackControl(PlaybackItemKind kind,
                                    std::uint32_t generation) {
    if (playback_queue_ == nullptr) return false;
    const PlaybackItem item{.kind = kind, .generation = generation};
    return xQueueSend(playback_queue_, &item, kPlaybackControlTimeout) == pdTRUE;
}

void I2sAudio::RecordAudioCounter(AudioCounter counter) {
    taskENTER_CRITICAL(&diagnostics_mux_);
    diagnostics_.Increment(counter);
    taskEXIT_CRITICAL(&diagnostics_mux_);
}

void I2sAudio::ObservePlaybackQueueDepth() {
    const auto depth = static_cast<std::uint32_t>(
        uxQueueMessagesWaiting(playback_queue_));
    taskENTER_CRITICAL(&diagnostics_mux_);
    diagnostics_.ObservePlaybackQueueDepth(depth);
    taskEXIT_CRITICAL(&diagnostics_mux_);
}

}  // namespace veetee::audio
