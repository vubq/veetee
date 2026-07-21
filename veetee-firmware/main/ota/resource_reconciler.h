#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ota/resource_manifest.h"
#include "settings/resource_state_store.h"
#include "settings/settings_store.h"

namespace veetee::ota {

enum class ResourceReconcileEvent : std::uint8_t {
    kAlreadyActive,
    kPayloadStaged,
    kManifestRejected,
    kPayloadRejected,
    kTransportFailed,
};

struct ResourceReconcileNotification {
    ResourceReconcileEvent event;
    char desired_version[33] = {};
    char bundle_version[33] = {};
    char error_code[33] = {};
    std::uint32_t payload_bytes = 0;
    std::uint8_t target_slot = 0;
};

class ResourceReconciler {
public:
    using EventSink = bool (*)(const ResourceReconcileNotification& notification,
                               void* context);

    esp_err_t Initialize(settings::DeviceSettings* settings, EventSink sink,
                         void* context);
    bool Schedule(const char* desired_version, const char* manifest_url);
    void Cancel();
    [[nodiscard]] const char* ActivePartitionLabel() const;
    [[nodiscard]] const char* PreviousPartitionLabel() const;
    [[nodiscard]] const char* StagedPartitionLabel() const;
    [[nodiscard]] settings::ResourceRecordPhase phase() const;
    esp_err_t ActivateStaged();
    esp_err_t ConfirmActive();
    esp_err_t Rollback();

private:
    struct Target {
        std::uint32_t generation = 0;
        char desired_version[33] = {};
        char manifest_url[257] = {};
    };

    static void TaskEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);
    static esp_err_t PayloadHttpEventHandler(esp_http_client_event_t* event);

    void TaskLoop();
    void Reconcile(const Target& target);
    esp_err_t FetchManifest(const Target& target, char** document,
                            std::size_t* document_size);
    esp_err_t PrepareDownload(const VerifiedResourceManifest& manifest,
                              std::uint8_t* target_slot,
                              std::uint32_t* resume_bytes,
                              bool* already_staged,
                              bool* already_active);
    esp_err_t DownloadPayload(const Target& target,
                              const VerifiedResourceManifest& manifest,
                              std::uint8_t target_slot,
                              std::uint32_t resume_bytes);
    esp_err_t ErasePartition(const Target& target, std::uint8_t slot,
                             std::uint32_t offset);
    esp_err_t SaveDownloadProgress(const Target& target,
                                   std::uint32_t downloaded_bytes);
    esp_err_t ResetDownloadProgress(const Target& target);
    esp_err_t StageDownload(const Target& target);
    settings::ResourceRecord RecordSnapshot() const;
    bool Emit(ResourceReconcileEvent event, const Target& target,
              const char* bundle_version, const char* error_code) const;
    bool EmitWithRetry(ResourceReconcileEvent event, const Target& target,
                       const char* bundle_version, const char* error_code) const;
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;

    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t queue_ = nullptr;
    SemaphoreHandle_t state_mutex_ = nullptr;
    TaskHandle_t task_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<std::uint32_t> request_generation_{0};
    TrustedReleaseKey trusted_key_{};
    settings::ResourceStateStore resource_state_{};
    const esp_partition_t* resource_partitions_[2] = {};
    std::uint64_t resource_slot_bytes_ = 0;
    char* response_ = nullptr;
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
    char content_range_[96] = {};
    bool content_range_overflow_ = false;
    char hardware_id_[18] = {};
};

}  // namespace veetee::ota
