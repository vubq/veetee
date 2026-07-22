import { describe, expect, it } from "vitest";

import kconfigSource from "../../../../../veetee-firmware/main/Kconfig.projbuild?raw";
import displaySource from "../../../../../veetee-firmware/main/display/st7789_display.cpp?raw";
import uiPackSource from "../../../../../veetee-firmware/main/display/ui_pack.cpp?raw";
import {
  DEVICE_UI_TARGET,
  FIRMWARE_SCREEN_COPY,
  FIRMWARE_STATE_IDS,
  FIRMWARE_THEMES,
} from "./firmware-contract";

function hexToRgb565(hex: string): number {
  const rgb = Number.parseInt(hex.slice(1), 16);
  return (((rgb >> 19) & 0x1f) << 11) | (((rgb >> 10) & 0x3f) << 5) | ((rgb >> 3) & 0x1f);
}

describe("Device UI firmware contract", () => {
  it("tracks the exact firmware state order and operational screen copy", () => {
    let previousStateOffset = -1;
    for (const state of FIRMWARE_STATE_IDS) {
      const offset = uiPackSource.indexOf(`"${state}"`, previousStateOffset + 1);
      expect(offset, `${state} is missing or out of order in ui_pack.cpp`).toBeGreaterThan(previousStateOffset);
      previousStateOffset = offset;

      const copy = FIRMWARE_SCREEN_COPY[state];
      expect(displaySource).toContain(
        `{"${copy.number}", "${copy.kicker}", "${copy.title}", "${copy.hint}"}`,
      );
    }
    expect(displaySource).toContain("constexpr std::array<ScreenCopy, 13> kScreenCopy");
    expect(uiPackSource).toContain("constexpr std::array<const char*, 13> kStateNames");
  });

  it("uses the real ST7789 target, ABI and three compiled compositions", () => {
    expect(kconfigSource).toMatch(/config VEETEE_LCD_WIDTH[\s\S]*?default 240/);
    expect(kconfigSource).toMatch(/config VEETEE_LCD_HEIGHT[\s\S]*?default 280/);
    expect(uiPackSource).toContain(`std::strcmp(board->valuestring, "${DEVICE_UI_TARGET.board}")`);
    expect(uiPackSource).toContain(`std::strcmp(display->valuestring, "${DEVICE_UI_TARGET.display}")`);
    expect(uiPackSource).toContain(`resource_abi->valueint != ${DEVICE_UI_TARGET.resourceAbi}`);
    expect(uiPackSource).toContain(`ui_abi->valueint != ${DEVICE_UI_TARGET.uiAbi}`);

    for (const theme of FIRMWARE_THEMES) {
      expect(uiPackSource).toContain(`std::strcmp(composition->valuestring, "${theme.composition}")`);
      expect(theme.palette).toHaveProperty("starting");
      expect(theme.palette).toHaveProperty("closing");
    }
  });

  it("keeps the web Signal palette bit-identical to the built-in RGB565 firmware fallback", () => {
    const styles = uiPackSource.match(/BuiltInSignalTheme\(\)[\s\S]*?styles = \{\{([\s\S]*?)\}\};/)?.[1];
    expect(styles).toBeTruthy();
    const firmwareColors = [...(styles ?? "").matchAll(/0x([0-9A-Fa-f]{4})/g)].map((match) => Number.parseInt(match[1]!, 16));
    const signal = FIRMWARE_THEMES.find((theme) => theme.id === "signal")!;
    const webColors = FIRMWARE_STATE_IDS.flatMap((state) => {
      const palette = signal.palette[state];
      return [palette.background, palette.foreground, palette.accent].map(hexToRgb565);
    });
    expect(firmwareColors).toEqual(webColors);
  });

  it("mirrors the firmware renderer geometry rather than a conceptual mockup", () => {
    for (const renderer of ["RenderSignal", "RenderMonolith", "RenderQuiet"]) {
      expect(displaySource).toContain(`St7789Display::${renderer}`);
    }
    expect(displaySource).toContain("CanvasRectangle(16, 18, 30, 3, style.accent)");
    expect(displaySource).toContain("const int center_y = 162");
    expect(displaySource).toContain("CanvasRectangle(0, 0, 9, CONFIG_VEETEE_LCD_HEIGHT, style.accent)");
    expect(displaySource).toContain("const int center_y = 137");
  });
});
