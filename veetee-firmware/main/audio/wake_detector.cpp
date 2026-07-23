#include "audio/wake_detector.h"

#include <algorithm>
#include <cinttypes>
#include <cstring>

#include "esp_heap_caps.h"
#include "board/board_config.h"
#include "esp_log.h"
#include "esp_wn_models.h"
#include "freertos/idf_additions.h"

namespace veetee::audio {
namespace {

constexpr char kTag[] = "veetee_wake";
constexpr UBaseType_t kPcmQueueDepth = 8;
constexpr std::uint32_t kMinimumCooldownMs = 250;
constexpr std::uint32_t kMaximumCooldownMs = 10000;
constexpr std::uint32_t kStopTimeoutMs = 1000;

bool CopyId(const char* source, std::array<char, 65>* target) {
    if (source == nullptr || target == nullptr) return false;
    const std::size_t length = std::strlen(source);
    if (length == 0 || length >= target->size()) return false;
    std::memcpy(target->data(), source, length + 1);
    return true;
}

bool IsThresholdValid(float threshold) {
    return threshold == 0.0F || (threshold >= 0.4F && threshold <= 0.9999F);
}

}  // namespace

esp_err_t WakeDetector::Initialize(const char* partition_label,
                                   const DetectorProfile* profiles,
                                   std::size_t profile_count,
                                   EventSink event_sink,
                                   void* context) {
    if (event_sink == nullptr || task_ != nullptr || pcm_queue_ != nullptr ||
        model_list_ != nullptr || profile_count > kMaximumProfiles ||
        (profile_count > 0 && (partition_label == nullptr || profiles == nullptr))) {
        return ESP_ERR_INVALID_ARG;
    }
    event_sink_ = event_sink;
    event_context_ = context;
    if (profile_count == 0) {
        ESP_LOGW(kTag, "No local detector profile is configured; button wake remains available");
        return ESP_OK;
    }

    model_list_ = esp_srmodel_init(partition_label);
    if (model_list_ == nullptr) {
        ESP_LOGE(kTag, "Unable to load ESP-SR model pack from %s", partition_label);
        return ESP_ERR_NOT_FOUND;
    }

    for (std::size_t index = 0; index < profile_count; ++index) {
        const DetectorProfile& source = profiles[index];
        if (source.role == DetectorRole::kDisabled ||
            FindProfile(source.role) != nullptr ||
            source.cooldown_ms < kMinimumCooldownMs ||
            source.cooldown_ms > kMaximumCooldownMs ||
            !IsThresholdValid(source.detection_threshold)) {
            ReleaseProfiles();
            return ESP_ERR_INVALID_ARG;
        }

        RuntimeProfile& runtime = profiles_[profile_count_];
        if (!CopyId(source.profile_id, &runtime.profile_id) ||
            !CopyId(source.model_id, &runtime.model_id)) {
            ReleaseProfiles();
            return ESP_ERR_INVALID_ARG;
        }

        const char* resolved_model = nullptr;
        for (int model_index = 0; model_index < model_list_->num; ++model_index) {
            if (std::strcmp(model_list_->model_name[model_index],
                            runtime.model_id.data()) == 0) {
                resolved_model = model_list_->model_name[model_index];
                break;
            }
        }
        if (resolved_model == nullptr) {
            ESP_LOGE(kTag, "Detector model %s is absent from %s",
                     runtime.model_id.data(), partition_label);
            ReleaseProfiles();
            return ESP_ERR_NOT_FOUND;
        }

        runtime.role = source.role;
        runtime.cooldown_ms = source.cooldown_ms;
        runtime.interface = esp_wn_handle_from_name(resolved_model);
        if (runtime.interface == nullptr) {
            ReleaseProfiles();
            return ESP_ERR_NOT_SUPPORTED;
        }
        runtime.model = runtime.interface->create(resolved_model, DET_MODE_90);
        if (runtime.model == nullptr) {
            ReleaseProfiles();
            return ESP_ERR_NO_MEM;
        }

        const int chunk_samples =
            runtime.interface->get_samp_chunksize(runtime.model);
        const int sample_rate = runtime.interface->get_samp_rate(runtime.model);
        const int channel_count = runtime.interface->get_channel_num(runtime.model);
        if (chunk_samples <= 0 ||
            chunk_samples > static_cast<int>(kMaximumModelChunkSamples) ||
            sample_rate != static_cast<int>(board::kMicSampleRate) ||
            channel_count != 1) {
            ESP_LOGE(kTag,
                     "Unsupported detector audio contract model=%s rate=%d channels=%d chunk=%d",
                     runtime.model_id.data(), sample_rate, channel_count,
                     chunk_samples);
            ReleaseProfiles();
            return ESP_ERR_NOT_SUPPORTED;
        }
        runtime.chunk_samples = static_cast<std::size_t>(chunk_samples);
        model_chunk_capacity_ = std::max(model_chunk_capacity_, runtime.chunk_samples);

        if (source.detection_threshold != 0.0F &&
            runtime.interface->set_det_threshold(runtime.model,
                                                  source.detection_threshold,
                                                  1) != 1) {
            ReleaseProfiles();
            return ESP_ERR_INVALID_ARG;
        }

        ++profile_count_;
        ESP_LOGI(kTag,
                 "Loaded profile=%s role=%s model=%s chunk=%u cooldown=%" PRIu32 " ms",
                 runtime.profile_id.data(), ToString(runtime.role),
                 runtime.model_id.data(),
                 static_cast<unsigned>(runtime.chunk_samples),
                 runtime.cooldown_ms);
    }

    model_chunk_ = static_cast<std::int16_t*>(heap_caps_calloc(
        model_chunk_capacity_, sizeof(std::int16_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    pcm_queue_ = xQueueCreateWithCaps(kPcmQueueDepth, sizeof(PcmFrame),
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (model_chunk_ == nullptr || pcm_queue_ == nullptr) {
        ESP_LOGE(kTag,
                 "Unable to allocate detector buffers chunk=%p queue=%p internal=%u psram=%u",
                 model_chunk_, pcm_queue_,
                 static_cast<unsigned>(
                     heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                 static_cast<unsigned>(
                     heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
        ReleaseProfiles();
        if (pcm_queue_ != nullptr) {
            vQueueDeleteWithCaps(pcm_queue_);
            pcm_queue_ = nullptr;
        }
        heap_caps_free(model_chunk_);
        model_chunk_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t WakeDetector::Start() {
    if (task_running_.load(std::memory_order_acquire) || task_ != nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (profile_count_ == 0) return ESP_OK;
    if (pcm_queue_ == nullptr || model_chunk_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    stop_requested_.store(false, std::memory_order_release);
    stack_free_bytes_.store(0, std::memory_order_relaxed);
    task_running_.store(true, std::memory_order_release);
    if (xTaskCreateWithCaps(&WakeDetector::TaskEntry, "veetee_wake", 12 * 1024,
                            this, 5, &task_,
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        ESP_LOGE(kTag,
                 "Unable to allocate detector task internal=%u psram=%u",
                 static_cast<unsigned>(
                     heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                 static_cast<unsigned>(
                     heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
        task_running_.store(false, std::memory_order_release);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t WakeDetector::Stop() {
    SetRole(DetectorRole::kDisabled);
    stop_requested_.store(true, std::memory_order_release);
    if (task_ == nullptr) return ESP_OK;
    if (pcm_queue_ != nullptr) {
        xQueueReset(pcm_queue_);
        PcmFrame wake{};
        xQueueSend(pcm_queue_, &wake, 0);
    }
    const TickType_t started = xTaskGetTickCount();
    const TickType_t timeout = pdMS_TO_TICKS(kStopTimeoutMs);
    while (task_running_.load(std::memory_order_acquire)) {
        if (xTaskGetTickCount() - started >= timeout) return ESP_ERR_TIMEOUT;
        vTaskDelay(1);
    }
    const TaskHandle_t stopped_task = task_;
    while (eTaskGetState(stopped_task) != eSuspended) {
        if (xTaskGetTickCount() - started >= timeout) return ESP_ERR_TIMEOUT;
        vTaskDelay(1);
    }
    vTaskDeleteWithCaps(stopped_task);
    task_ = nullptr;
    return ESP_OK;
}

esp_err_t WakeDetector::Reload(const char* partition_label,
                               const DetectorProfile* profiles,
                               std::size_t profile_count) {
    if (event_sink_ == nullptr) return ESP_ERR_INVALID_STATE;
    esp_err_t error = Stop();
    if (error != ESP_OK) return error;
    ReleaseRuntime();
    error = Initialize(partition_label, profiles, profile_count, event_sink_,
                       event_context_);
    if (error != ESP_OK) return error;
    return Start();
}

bool WakeDetector::SubmitPcm(const std::int16_t* samples,
                             std::size_t sample_count) {
    if (samples == nullptr || sample_count == 0 ||
        sample_count > kPcmFrameSamples) {
        return false;
    }
    const DetectorRole active_role = role_.load(std::memory_order_acquire);
    if (active_role == DetectorRole::kDisabled) return true;
    if (pcm_queue_ == nullptr) return false;

    PcmFrame frame{};
    frame.role = active_role;
    frame.generation = generation_.load(std::memory_order_acquire);
    frame.length = static_cast<std::uint16_t>(sample_count);
    std::memcpy(frame.samples.data(), samples,
                sample_count * sizeof(std::int16_t));
    if (xQueueSend(pcm_queue_, &frame, 0) == pdTRUE) return true;

    PcmFrame discarded{};
    if (xQueueReceive(pcm_queue_, &discarded, 0) != pdTRUE ||
        xQueueSend(pcm_queue_, &frame, 0) != pdTRUE) {
        dropped_frames_.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    dropped_frames_.fetch_add(1, std::memory_order_relaxed);
    return true;
}

bool WakeDetector::SetRole(DetectorRole role) {
    if (role != DetectorRole::kDisabled && !HasProfile(role)) return false;
    const DetectorRole previous =
        role_.exchange(role, std::memory_order_acq_rel);
    if (previous != role) {
        generation_.fetch_add(1, std::memory_order_acq_rel);
        if (pcm_queue_ != nullptr) {
            xQueueReset(pcm_queue_);
        }
        ESP_LOGI(kTag, "Detector role %s -> %s", ToString(previous),
                 ToString(role));
    }
    return true;
}

bool WakeDetector::HasProfile(DetectorRole role) const {
    return FindProfile(role) != nullptr;
}

void WakeDetector::TaskEntry(void* context) {
    static_cast<WakeDetector*>(context)->Run();
}

void WakeDetector::Run() {
    PcmFrame frame{};
    RuntimeProfile* active_profile = nullptr;
    std::uint32_t active_generation = generation_.load(std::memory_order_acquire);
    std::size_t buffered_samples = 0;

    stack_free_bytes_.store(
        static_cast<std::uint32_t>(uxTaskGetStackHighWaterMark(nullptr)),
        std::memory_order_relaxed);
    while (xQueueReceive(pcm_queue_, &frame, portMAX_DELAY) == pdTRUE) {
        stack_free_bytes_.store(
            static_cast<std::uint32_t>(uxTaskGetStackHighWaterMark(nullptr)),
            std::memory_order_relaxed);
        if (stop_requested_.load(std::memory_order_acquire)) break;
        const DetectorRole current_role = role_.load(std::memory_order_acquire);
        const std::uint32_t current_generation =
            generation_.load(std::memory_order_acquire);
        if (current_generation != active_generation ||
            active_profile == nullptr || active_profile->role != current_role) {
            active_generation = current_generation;
            active_profile = FindProfile(current_role);
            buffered_samples = 0;
        }
        if (active_profile == nullptr || frame.role != current_role ||
            frame.generation != current_generation) {
            continue;
        }

        std::size_t consumed = 0;
        while (consumed < frame.length) {
            const std::size_t copied = std::min(
                active_profile->chunk_samples - buffered_samples,
                static_cast<std::size_t>(frame.length) - consumed);
            std::memcpy(model_chunk_ + buffered_samples,
                        frame.samples.data() + consumed,
                        copied * sizeof(std::int16_t));
            buffered_samples += copied;
            consumed += copied;
            if (buffered_samples != active_profile->chunk_samples) continue;

            const wakenet_state_t result =
                active_profile->interface->detect(active_profile->model,
                                                  model_chunk_);
            buffered_samples = 0;
            if (result != WAKENET_DETECTED ||
                generation_.load(std::memory_order_acquire) != active_generation ||
                role_.load(std::memory_order_acquire) != active_profile->role) {
                continue;
            }

            const TickType_t now = xTaskGetTickCount();
            const TickType_t cooldown =
                pdMS_TO_TICKS(active_profile->cooldown_ms);
            if (active_profile->has_detected &&
                now - active_profile->last_detection_tick < cooldown) {
                continue;
            }
            active_profile->has_detected = true;
            active_profile->last_detection_tick = now;
            ESP_LOGI(kTag, "Detected profile=%s role=%s generation=%" PRIu32,
                     active_profile->profile_id.data(),
                     ToString(active_profile->role), active_generation);
            if (!event_sink_(active_profile->role,
                             active_profile->profile_id.data(),
                             event_context_)) {
                ESP_LOGW(kTag, "Application rejected detector event");
            }
        }
    }
    stack_free_bytes_.store(0, std::memory_order_relaxed);
    task_running_.store(false, std::memory_order_release);
    // Stop() owns deletion so the task stack is reclaimed before hot reload.
    vTaskSuspend(nullptr);
    vTaskDeleteWithCaps(nullptr);
}

WakeDetector::RuntimeProfile* WakeDetector::FindProfile(DetectorRole role) {
    for (std::size_t index = 0; index < profile_count_; ++index) {
        if (profiles_[index].role == role) return &profiles_[index];
    }
    return nullptr;
}

const WakeDetector::RuntimeProfile* WakeDetector::FindProfile(
    DetectorRole role) const {
    for (std::size_t index = 0; index < profile_count_; ++index) {
        if (profiles_[index].role == role) return &profiles_[index];
    }
    return nullptr;
}

void WakeDetector::ReleaseProfiles() {
    for (std::size_t index = 0; index < profile_count_ + 1 &&
                                index < profiles_.size();
         ++index) {
        RuntimeProfile& runtime = profiles_[index];
        if (runtime.model != nullptr && runtime.interface != nullptr) {
            runtime.interface->destroy(runtime.model);
        }
        runtime = RuntimeProfile{};
    }
    profile_count_ = 0;
    model_chunk_capacity_ = 0;
    if (model_list_ != nullptr) {
        esp_srmodel_deinit(model_list_);
        model_list_ = nullptr;
    }
}

void WakeDetector::ReleaseRuntime() {
    ReleaseProfiles();
    if (pcm_queue_ != nullptr) {
        vQueueDeleteWithCaps(pcm_queue_);
        pcm_queue_ = nullptr;
    }
    heap_caps_free(model_chunk_);
    model_chunk_ = nullptr;
    model_chunk_capacity_ = 0;
}

}  // namespace veetee::audio
