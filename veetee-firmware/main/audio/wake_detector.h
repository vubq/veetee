#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "audio/wake_detector_policy.h"
#include "esp_err.h"
#include "esp_wn_iface.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "model_path.h"

namespace veetee::audio {

struct DetectorProfile {
    DetectorRole role = DetectorRole::kDisabled;
    const char* profile_id = nullptr;
    const char* model_id = nullptr;
    std::uint32_t cooldown_ms = 0;
    float detection_threshold = 0.0F;
};

class WakeDetector {
public:
    using EventSink = bool (*)(DetectorRole role, const char* profile_id,
                               void* context);

    esp_err_t Initialize(const char* partition_label,
                         const DetectorProfile* profiles,
                         std::size_t profile_count,
                         EventSink event_sink,
                         void* context);
    esp_err_t Start();
    esp_err_t Stop();
    esp_err_t Reload(const char* partition_label,
                     const DetectorProfile* profiles,
                     std::size_t profile_count);

    bool SubmitPcm(const std::int16_t* samples, std::size_t sample_count);
    bool SetRole(DetectorRole role);
    [[nodiscard]] bool HasProfile(DetectorRole role) const;
    [[nodiscard]] bool healthy() const {
        return profile_count_ == 0 || task_running_.load(std::memory_order_acquire);
    }
    [[nodiscard]] DetectorRole role() const {
        return role_.load(std::memory_order_acquire);
    }
    [[nodiscard]] std::uint32_t dropped_frames() const {
        return dropped_frames_.load(std::memory_order_relaxed);
    }

private:
    static constexpr std::size_t kMaximumProfiles = 2;
    static constexpr std::size_t kMaximumModelChunkSamples = 2048;
    static constexpr std::size_t kPcmFrameSamples = 320;

    struct RuntimeProfile {
        DetectorRole role = DetectorRole::kDisabled;
        std::array<char, 65> profile_id{};
        std::array<char, 65> model_id{};
        std::uint32_t cooldown_ms = 0;
        const esp_wn_iface_t* interface = nullptr;
        model_iface_data_t* model = nullptr;
        std::size_t chunk_samples = 0;
        TickType_t last_detection_tick = 0;
        bool has_detected = false;
    };

    struct PcmFrame {
        DetectorRole role = DetectorRole::kDisabled;
        std::uint32_t generation = 0;
        std::uint16_t length = 0;
        std::array<std::int16_t, kPcmFrameSamples> samples{};
    };

    static void TaskEntry(void* context);

    void Run();
    RuntimeProfile* FindProfile(DetectorRole role);
    const RuntimeProfile* FindProfile(DetectorRole role) const;
    void ReleaseProfiles();
    void ReleaseRuntime();

    std::array<RuntimeProfile, kMaximumProfiles> profiles_{};
    std::size_t profile_count_ = 0;
    srmodel_list_t* model_list_ = nullptr;
    QueueHandle_t pcm_queue_ = nullptr;
    TaskHandle_t task_ = nullptr;
    std::int16_t* model_chunk_ = nullptr;
    std::size_t model_chunk_capacity_ = 0;
    EventSink event_sink_ = nullptr;
    void* event_context_ = nullptr;
    std::atomic<DetectorRole> role_{DetectorRole::kDisabled};
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<std::uint32_t> dropped_frames_{0};
    std::atomic<bool> stop_requested_{false};
    std::atomic<bool> task_running_{false};
};

}  // namespace veetee::audio
