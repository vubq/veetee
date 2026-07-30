#include "diagnostics/runtime_stats_sampler.h"

#include "sdkconfig.h"

#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstddef>
#include <cstdint>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace veetee::diagnostics {
namespace {

constexpr char kTag[] = "veetee_cpu";
constexpr std::size_t kMaximumTasks = 48;
constexpr std::size_t kMaximumLoggedTasks = 12;

struct PreviousTask {
    TaskHandle_t handle = nullptr;
    // FreeRTOS may recycle a deleted task's TCB address. The task number keeps
    // a new task at the same handle from inheriting the previous counter.
    UBaseType_t task_number = 0;
    configRUN_TIME_COUNTER_TYPE runtime = 0;
};

struct TaskDelta {
    const char* name = nullptr;
    std::uint64_t runtime = 0;
    BaseType_t core_id = tskNO_AFFINITY;
    std::uint32_t stack_free_bytes = 0;
};

std::uint64_t CounterDelta(configRUN_TIME_COUNTER_TYPE current,
                           configRUN_TIME_COUNTER_TYPE previous) {
    // Unsigned subtraction handles one ESP timer wrap as long as the sample
    // interval remains well below the roughly 4290-second counter period.
    return static_cast<configRUN_TIME_COUNTER_TYPE>(current - previous);
}

}  // namespace

struct RuntimeStatsSampler::State {
    std::array<TaskStatus_t, kMaximumTasks> current{};
    std::array<PreviousTask, kMaximumTasks> previous{};
    std::array<TaskDelta, kMaximumTasks> deltas{};
    configRUN_TIME_COUNTER_TYPE previous_total = 0;
    std::size_t previous_count = 0;
    bool primed = false;
};

esp_err_t RuntimeStatsSampler::Initialize() {
    if (state_ != nullptr) return ESP_ERR_INVALID_STATE;
    state_ = static_cast<State*>(heap_caps_calloc(
        1, sizeof(State), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (state_ == nullptr) return ESP_ERR_NO_MEM;
    ESP_LOGW(kTag,
             "Benchmark runtime statistics enabled; disable for final release heap gate");
    return ESP_OK;
}

void RuntimeStatsSampler::Sample(const char* state_label) {
    if (state_ == nullptr) return;
    configRUN_TIME_COUNTER_TYPE total = 0;
    const UBaseType_t task_count = uxTaskGetSystemState(
        state_->current.data(), state_->current.size(), &total);
    if (task_count == 0 || task_count > state_->current.size()) {
        ESP_LOGW(kTag, "Runtime sample skipped tasks=%u capacity=%u",
                 static_cast<unsigned>(task_count),
                 static_cast<unsigned>(state_->current.size()));
        return;
    }

    if (!state_->primed) {
        state_->previous_count = task_count;
        state_->previous_total = total;
        for (std::size_t index = 0; index < task_count; ++index) {
            state_->previous[index] = {
                .handle = state_->current[index].xHandle,
                .task_number = state_->current[index].xTaskNumber,
                .runtime = state_->current[index].ulRunTimeCounter,
            };
        }
        state_->primed = true;
        ESP_LOGI(kTag, "Runtime baseline state=%s tasks=%u",
                 state_label == nullptr ? "unknown" : state_label,
                 static_cast<unsigned>(task_count));
        return;
    }

    const std::uint64_t total_delta =
        CounterDelta(total, state_->previous_total);
    if (total_delta == 0) return;

    std::size_t delta_count = 0;
    std::uint64_t idle_delta[2] = {};
    const TaskHandle_t idle_handle[2] = {
        xTaskGetIdleTaskHandleForCore(0),
        xTaskGetIdleTaskHandleForCore(1),
    };
    for (std::size_t index = 0; index < task_count; ++index) {
        const TaskStatus_t& current = state_->current[index];
        configRUN_TIME_COUNTER_TYPE previous_runtime =
            current.ulRunTimeCounter;
        for (std::size_t prior = 0; prior < state_->previous_count; ++prior) {
            if (state_->previous[prior].handle == current.xHandle &&
                state_->previous[prior].task_number == current.xTaskNumber) {
                previous_runtime = state_->previous[prior].runtime;
                break;
            }
        }
        const std::uint64_t runtime =
            CounterDelta(current.ulRunTimeCounter, previous_runtime);
        if (current.xHandle == idle_handle[0]) idle_delta[0] = runtime;
        if (current.xHandle == idle_handle[1]) idle_delta[1] = runtime;
        state_->deltas[delta_count++] = {
            .name = current.pcTaskName,
            .runtime = runtime,
            .core_id = current.xCoreID,
            .stack_free_bytes =
                static_cast<std::uint32_t>(current.usStackHighWaterMark),
        };
    }
    std::sort(state_->deltas.begin(),
              state_->deltas.begin() + delta_count,
              [](const TaskDelta& left, const TaskDelta& right) {
                  return left.runtime > right.runtime;
              });

    // uxTaskGetSystemState() returns the wall-clock run-time counter, not a
    // sum over both cores. Each idle task counter is therefore compared with
    // that same interval to obtain the busy percentage for its own core.
    const auto busy_x10 = [total_delta](std::uint64_t idle) {
        const std::uint64_t idle_x10 =
            std::min<std::uint64_t>(1000, idle * 1000 / total_delta);
        return static_cast<unsigned>(1000 - idle_x10);
    };
    ESP_LOGI(
        kTag,
        "Runtime state=%s interval_us=%" PRIu64
        " cpu0_x10=%u cpu1_x10=%u tasks=%u internal_free=%u largest=%u psram_free=%u",
        state_label == nullptr ? "unknown" : state_label, total_delta,
        busy_x10(idle_delta[0]), busy_x10(idle_delta[1]),
        static_cast<unsigned>(task_count),
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
        static_cast<unsigned>(
            heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)),
        static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));

    const std::size_t logged =
        std::min<std::size_t>(delta_count, kMaximumLoggedTasks);
    for (std::size_t index = 0; index < logged; ++index) {
        const TaskDelta& task = state_->deltas[index];
        // An SMP task may migrate, but it cannot execute on both cores at once.
        const unsigned cpu_x10 = static_cast<unsigned>(
            std::min<std::uint64_t>(1000,
                                    task.runtime * 1000 / total_delta));
        if (cpu_x10 == 0 && index >= 4) break;
        ESP_LOGI(kTag,
                 "Task name=%s core=%d cpu_x10=%u stack_free=%u",
                 task.name == nullptr ? "unknown" : task.name,
                 static_cast<int>(task.core_id), cpu_x10,
                 static_cast<unsigned>(task.stack_free_bytes));
    }

    state_->previous_count = task_count;
    state_->previous_total = total;
    for (std::size_t index = 0; index < task_count; ++index) {
        state_->previous[index] = {
            .handle = state_->current[index].xHandle,
            .task_number = state_->current[index].xTaskNumber,
            .runtime = state_->current[index].ulRunTimeCounter,
        };
    }
}

}  // namespace veetee::diagnostics

#else

namespace veetee::diagnostics {

esp_err_t RuntimeStatsSampler::Initialize() { return ESP_OK; }

void RuntimeStatsSampler::Sample(const char*) {}

}  // namespace veetee::diagnostics

#endif
