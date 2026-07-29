#include "audio/experimental_aec.h"

#include <algorithm>
#include <cstring>

#include "audio/experimental_aec_policy.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "sdkconfig.h"

#if CONFIG_VEETEE_EXPERIMENTAL_AEC
#include "esp_aec.h"
#endif

namespace veetee::audio {
namespace {

constexpr char kTag[] = "veetee_aec";

#if CONFIG_VEETEE_EXPERIMENTAL_AEC
std::int16_t* AllocateAlignedSamples(std::size_t sample_count) {
    return static_cast<std::int16_t*>(heap_caps_aligned_calloc(
        16, sample_count, sizeof(std::int16_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
}
#endif

}  // namespace

ExperimentalAec::~ExperimentalAec() { Release(); }

esp_err_t ExperimentalAec::Initialize() {
#if !CONFIG_VEETEE_EXPERIMENTAL_AEC
    ESP_LOGI(kTag, "Experimental AEC disabled by build configuration");
    return ESP_OK;
#else
    if (handle_ != nullptr || enabled_.load()) return ESP_ERR_INVALID_STATE;
    static_assert(kAecSampleRateHz == 16000,
                  "ESP-SR direct AEC only supports 16 kHz");
    auto* handle = aec_create(static_cast<int>(kAecSampleRateHz), 4, 1,
                              AEC_MODE_SR_LOW_COST);
    if (handle == nullptr) return ESP_ERR_NO_MEM;
    const int chunk = aec_get_chunksize(handle);
    if (chunk <= 0 ||
        chunk > static_cast<int>(kMaximumAecChunkSamples)) {
        aec_destroy(handle);
        return ESP_ERR_NOT_SUPPORTED;
    }
    handle_ = handle;
    chunk_samples_ = static_cast<std::size_t>(chunk);
    microphone_chunk_ = AllocateAlignedSamples(chunk_samples_);
    reference_chunk_ = AllocateAlignedSamples(chunk_samples_);
    processed_chunk_ = AllocateAlignedSamples(chunk_samples_);
    processed_fifo_ = AllocateAlignedSamples(chunk_samples_ * 2);
    reference_ring_ = AllocateAlignedSamples(kReferenceRingSamples);
    reference_scratch_ =
        AllocateAlignedSamples(kMaximumMicrophoneFrameSamples);
    downsample_scratch_ =
        AllocateAlignedSamples(kMaximumDownsampledFrameSamples);
    if (microphone_chunk_ == nullptr || reference_chunk_ == nullptr ||
        processed_chunk_ == nullptr || processed_fifo_ == nullptr ||
        reference_ring_ == nullptr || reference_scratch_ == nullptr ||
        downsample_scratch_ == nullptr) {
        Release();
        return ESP_ERR_NO_MEM;
    }
    processing_generation_ = reference_generation_.load();
    ResetProcessingState(processing_generation_);
    enabled_.store(true);
    ESP_LOGW(kTag,
             "Experimental ESP-SR AEC enabled chunk=%u filter=4; protocol capability and speaking uplink remain disabled",
             static_cast<unsigned>(chunk_samples_));
    return ESP_OK;
#endif
}

void ExperimentalAec::Release() {
    enabled_.store(false);
    playback_active_.store(false);
#if CONFIG_VEETEE_EXPERIMENTAL_AEC
    if (handle_ != nullptr) {
        aec_destroy(static_cast<aec_handle_t*>(handle_));
    }
#endif
    handle_ = nullptr;
    heap_caps_free(microphone_chunk_);
    heap_caps_free(reference_chunk_);
    heap_caps_free(processed_chunk_);
    heap_caps_free(processed_fifo_);
    heap_caps_free(reference_ring_);
    heap_caps_free(reference_scratch_);
    heap_caps_free(downsample_scratch_);
    microphone_chunk_ = nullptr;
    reference_chunk_ = nullptr;
    processed_chunk_ = nullptr;
    processed_fifo_ = nullptr;
    reference_ring_ = nullptr;
    reference_scratch_ = nullptr;
    downsample_scratch_ = nullptr;
    chunk_samples_ = 0;
}

void ExperimentalAec::BeginPlayback() {
    if (!enabled_.load()) return;
    playback_active_.store(false);
    ResetReferenceRing();
    reference_generation_.fetch_add(1);
    playback_active_.store(true);
}

void ExperimentalAec::EndPlayback() {
    if (!enabled_.load()) return;
    playback_active_.store(false);
    ResetReferenceRing();
    reference_generation_.fetch_add(1);
}

bool ExperimentalAec::PushPlayback24k(const std::int16_t* samples,
                                      std::size_t sample_count,
                                      int volume_percent) {
    if (!enabled_.load()) return true;
    if (!playback_active_.load() || sample_count == 0 ||
        sample_count > kMaximumPlaybackFrameSamples) {
        return false;
    }
    const std::size_t downsampled = DownsampleAecReference24kTo16k(
        samples, sample_count, volume_percent, downsample_scratch_,
        kMaximumDownsampledFrameSamples);
    if (downsampled == 0) return false;

    taskENTER_CRITICAL(&reference_mux_);
    const std::size_t retained =
        std::min(downsampled, kReferenceRingSamples);
    const std::size_t overflow =
        reference_count_ + retained > kReferenceRingSamples
            ? reference_count_ + retained - kReferenceRingSamples
            : 0;
    reference_read_ =
        (reference_read_ + overflow) % kReferenceRingSamples;
    reference_count_ -= std::min(reference_count_, overflow);
    const std::size_t source_start = downsampled - retained;
    for (std::size_t index = source_start; index < downsampled; ++index) {
        reference_ring_[reference_write_] = downsample_scratch_[index];
        reference_write_ = (reference_write_ + 1) % kReferenceRingSamples;
        ++reference_count_;
    }
    taskEXIT_CRITICAL(&reference_mux_);
    return overflow == 0;
}

bool ExperimentalAec::ProcessMicrophone16k(std::int16_t* samples,
                                           std::size_t sample_count) {
#if !CONFIG_VEETEE_EXPERIMENTAL_AEC
    (void)samples;
    (void)sample_count;
    return false;
#else
    if (!enabled_.load() || !playback_active_.load()) return false;
    if (samples == nullptr || sample_count == 0 ||
        sample_count > kMaximumMicrophoneFrameSamples || handle_ == nullptr) {
        return false;
    }
    const std::uint32_t generation = reference_generation_.load();
    if (generation != processing_generation_) {
        ResetProcessingState(generation);
    }
    const std::size_t reference_samples =
        PopReference(reference_scratch_, sample_count);
    std::fill(reference_scratch_ + reference_samples,
              reference_scratch_ + sample_count, 0);

    for (std::size_t index = 0; index < sample_count; ++index) {
        microphone_chunk_[chunk_fill_] = samples[index];
        reference_chunk_[chunk_fill_] = reference_scratch_[index];
        ++chunk_fill_;
        if (chunk_fill_ == chunk_samples_) {
            aec_process(static_cast<aec_handle_t*>(handle_),
                        microphone_chunk_, reference_chunk_,
                        processed_chunk_);
            if (!AppendProcessedOutput(processed_chunk_, chunk_samples_)) {
                ResetProcessingState(generation);
            }
            chunk_fill_ = 0;
        }
        samples[index] = PopProcessedOutput();
    }
    return true;
#endif
}

void ExperimentalAec::ResetProcessingState(std::uint32_t generation) {
    chunk_fill_ = 0;
    processed_read_ = 0;
    processed_write_ = 0;
    processed_count_ = 0;
    processing_generation_ = generation;
    if (processed_fifo_ == nullptr || chunk_samples_ == 0) return;
    std::fill(processed_fifo_, processed_fifo_ + chunk_samples_ * 2, 0);
    processed_write_ = chunk_samples_;
    processed_count_ = chunk_samples_;
}

void ExperimentalAec::ResetReferenceRing() {
    taskENTER_CRITICAL(&reference_mux_);
    reference_read_ = 0;
    reference_write_ = 0;
    reference_count_ = 0;
    taskEXIT_CRITICAL(&reference_mux_);
}

std::size_t ExperimentalAec::PopReference(std::int16_t* destination,
                                          std::size_t sample_count) {
    if (destination == nullptr || reference_ring_ == nullptr) return 0;
    taskENTER_CRITICAL(&reference_mux_);
    const std::size_t available = std::min(sample_count, reference_count_);
    for (std::size_t index = 0; index < available; ++index) {
        destination[index] = reference_ring_[reference_read_];
        reference_read_ = (reference_read_ + 1) % kReferenceRingSamples;
    }
    reference_count_ -= available;
    taskEXIT_CRITICAL(&reference_mux_);
    return available;
}

bool ExperimentalAec::AppendProcessedOutput(const std::int16_t* samples,
                                            std::size_t sample_count) {
    const std::size_t capacity = chunk_samples_ * 2;
    if (samples == nullptr || processed_fifo_ == nullptr ||
        sample_count > capacity - processed_count_) {
        return false;
    }
    for (std::size_t index = 0; index < sample_count; ++index) {
        processed_fifo_[processed_write_] = samples[index];
        processed_write_ = (processed_write_ + 1) % capacity;
        ++processed_count_;
    }
    return true;
}

std::int16_t ExperimentalAec::PopProcessedOutput() {
    if (processed_fifo_ == nullptr || processed_count_ == 0) return 0;
    const std::int16_t sample = processed_fifo_[processed_read_];
    processed_read_ =
        (processed_read_ + 1) % (chunk_samples_ * 2);
    --processed_count_;
    return sample;
}

}  // namespace veetee::audio
