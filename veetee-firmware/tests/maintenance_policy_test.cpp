#include <cassert>
#include <iostream>

#include "maintenance/maintenance_policy.h"

namespace {

void TestRealtimeDefersEveryMaintenanceLane() {
    using namespace veetee::maintenance;
    for (const MaintenanceJobKind kind : kMaintenancePriority) {
        assert(!CanRunMaintenanceJob(kind, true, false));
        assert(!CanRunMaintenanceJob(kind, true, true));
    }
}

void TestFirmwareBoundaryIsExclusiveButKeepsDurableReporting() {
    using namespace veetee::maintenance;
    assert(CanRunMaintenanceJob(MaintenanceJobKind::kFirmware, false, true));
    assert(CanRunMaintenanceJob(MaintenanceJobKind::kReporter, false, true));
    assert(!CanRunMaintenanceJob(MaintenanceJobKind::kBootstrap, false, true));
    assert(!CanRunMaintenanceJob(MaintenanceJobKind::kDeviceConfig, false, true));
    assert(!CanRunMaintenanceJob(MaintenanceJobKind::kWakeResource, false, true));
    assert(!CanRunMaintenanceJob(MaintenanceJobKind::kUiPack, false, true));
}

void TestReporterAndFirmwarePriorityCannotBeStarvedByDownloads() {
    using namespace veetee::maintenance;
    assert(MaintenancePriorityRank(MaintenanceJobKind::kReporter) == 0);
    assert(MaintenancePriorityRank(MaintenanceJobKind::kFirmware) == 1);
    assert(MaintenancePriorityRank(MaintenanceJobKind::kReporter) <
           MaintenancePriorityRank(MaintenanceJobKind::kWakeResource));
    assert(MaintenancePriorityRank(MaintenanceJobKind::kReporter) <
           MaintenancePriorityRank(MaintenanceJobKind::kUiPack));
}

void TestIdleAdmitsEveryRegisteredLane() {
    using namespace veetee::maintenance;
    for (const MaintenanceJobKind kind : kMaintenancePriority) {
        assert(CanRunMaintenanceJob(kind, false, false));
        assert(IsValidMaintenanceJob(kind));
    }
    assert(!IsValidMaintenanceJob(MaintenanceJobKind::kCount));
}

void TestGateEpochRejectsAJobSelectedBeforeRealtime() {
    using namespace veetee::maintenance;
    constexpr std::uint32_t selected_epoch = 7;
    assert(IsMaintenanceDispatchCurrent(MaintenanceJobKind::kBootstrap,
                                        selected_epoch, selected_epoch, false,
                                        false));
    assert(!IsMaintenanceDispatchCurrent(MaintenanceJobKind::kBootstrap,
                                         selected_epoch, selected_epoch + 1,
                                         true, false));
    assert(!IsMaintenanceDispatchCurrent(MaintenanceJobKind::kBootstrap,
                                         selected_epoch, selected_epoch + 1,
                                         false, true));
}

void TestOnlyExclusiveFirmwareHttpCannotBePreempted() {
    using namespace veetee::maintenance;
    for (const MaintenanceJobKind kind : kMaintenancePriority) {
        assert(CanPreemptMaintenanceHttp(kind, false));
    }
    assert(!CanPreemptMaintenanceHttp(MaintenanceJobKind::kFirmware, true));
    assert(CanPreemptMaintenanceHttp(MaintenanceJobKind::kReporter, true));
    assert(!CanPreemptMaintenanceHttp(MaintenanceJobKind::kCount, false));
}

void TestRealtimeBarrierIsFiniteAndWithinWakeBudget() {
    using namespace veetee::maintenance;
    assert(kRealtimeMaintenanceBarrierMs > 0);
    assert(kRealtimeMaintenanceBarrierMs <= 150);
    assert(IsRealtimeMaintenanceBarrierWithinBudget(0));
    assert(IsRealtimeMaintenanceBarrierWithinBudget(150));
    assert(!IsRealtimeMaintenanceBarrierWithinBudget(151));
    assert(RemainingRealtimeMaintenanceBarrierMs(0) == 150);
    assert(RemainingRealtimeMaintenanceBarrierMs(149) == 1);
    assert(RemainingRealtimeMaintenanceBarrierMs(150) == 0);
    assert(RemainingRealtimeMaintenanceBarrierMs(151) == 0);
}

}  // namespace

int main() {
    TestRealtimeDefersEveryMaintenanceLane();
    TestFirmwareBoundaryIsExclusiveButKeepsDurableReporting();
    TestReporterAndFirmwarePriorityCannotBeStarvedByDownloads();
    TestIdleAdmitsEveryRegisteredLane();
    TestGateEpochRejectsAJobSelectedBeforeRealtime();
    TestOnlyExclusiveFirmwareHttpCannotBePreempted();
    TestRealtimeBarrierIsFiniteAndWithinWakeBudget();
    std::cout << "maintenance_policy_test: passed\n";
    return 0;
}
