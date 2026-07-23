import { describe, expect, it } from "vitest";

import managerShell from "./components/ManagerShell.vue?raw";
import agentsPage from "./components/pages/AgentsPage.vue?raw";
import devicesPage from "./components/pages/DevicesPage.vue?raw";
import deviceUiPage from "./components/pages/DeviceUiPage.vue?raw";
import deviceDiagnosticsPanel from "./components/device-ui/DeviceDiagnosticsPanel.vue?raw";
import realtimeLabPage from "./components/pages/RealtimeLabPage.vue?raw";
import firmwareContract from "./device-ui/firmware-contract.ts?raw";

describe("Vue-native Manager Web", () => {
  it("routes every primary product area through Vue page components", () => {
    for (const component of [
      "OverviewPage",
      "DevicesPage",
      "AgentsPage",
      "ProvidersPage",
      "RealtimeLabPage",
      "ResourcesPage",
    ]) {
      expect(managerShell).toContain(component);
    }
    expect(managerShell).not.toContain("prototypePage");
    expect(managerShell).not.toContain("v-html");
    expect(managerShell).not.toContain("initializePrototype");
    for (const devicePanel of ["DeviceUiPage", "DeviceWakePanel", "McpPage", "TelemetryPage"]) {
      expect(devicesPage).toContain(devicePanel);
    }
    expect(devicesPage).toContain("DeviceDiagnosticsPanel");
    expect(deviceDiagnosticsPanel).toContain("raw audio không được lưu hoặc truyền");
    expect(deviceDiagnosticsPanel).toContain("runSelfTest");
  });

  it("keeps Signal as the default UI and all three built-in themes", () => {
    expect(deviceUiPage).toContain('ref<FirmwareComposition>("signal")');
    expect(firmwareContract).toContain('firmwareTheme(signalTheme, "01", "Signal"');
    expect(firmwareContract).toContain('firmwareTheme(monolithTheme, "02", "Monolith"');
    expect(firmwareContract).toContain('firmwareTheme(quietTheme, "03", "Quiet"');
    expect(deviceUiPage).toContain("stageStandardUiPack");
    expect(deviceUiPage).toContain("data-ui-pack-file");
  });

  it("implements Realtime Lab as Vue state instead of DOM selectors", () => {
    expect(realtimeLabPage).toContain("useRealtimeLab");
    expect(realtimeLabPage).not.toContain("querySelector");
    expect(realtimeLabPage).not.toContain("innerHTML");
  });

  it("edits a versioned prompt template and personality catalog without semantic branches", () => {
    expect(managerShell).toContain("agentPromptCatalog");
    expect(agentsPage).toContain("promptCatalog?.variables");
    expect(agentsPage).toContain("personalityPresets");
    expect(agentsPage).toContain("promptTemplate");
    expect(agentsPage).toContain("agent_name: form.name");
    expect(agentsPage).toContain("language: form.language");
    expect(agentsPage).toContain("createPersonalityPreset");
    expect(agentsPage).toContain("deletePersonalityPreset");
  });
});
