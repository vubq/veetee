import { describe, expect, it } from "vitest";

import kconfigSource from "../../../../../veetee-firmware/main/Kconfig.projbuild?raw";
import boardSource from "../../../../../veetee-firmware/main/board/veetee_board.cpp?raw";
import displaySource from "../../../../../veetee-firmware/main/display/st7789_display.cpp?raw";
import uiPackSource from "../../../../../veetee-firmware/main/display/ui_pack.cpp?raw";
import previewSource from "../components/device-ui/FirmwareDisplayPreview.vue?raw";
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
    // Executable OTA adds the built-in upgrading screen after the 13-state UI ABI.
    expect(displaySource).toContain("constexpr std::array<ScreenCopy, 14> kScreenCopy");
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

  it("keeps the web Mobile palette bit-identical to the built-in RGB565 firmware fallback", () => {
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
    expect(displaySource).toContain("CanvasRoundedRectangle(5, 3, 230, 274, 29, frame)");
    expect(displaySource).toContain("CanvasRoundedRectangle(18, 86, 204, 76, 20, style.accent)");
    expect(displaySource).toContain("CanvasRoundedRectangle(18, 170, 98, 52, 15, panel)");
    expect(displaySource).toContain("CanvasRoundedRectangle(124, 170, 98, 52, 15, panel)");
    expect(displaySource).toContain("CanvasRoundedRectangle(57, 246, 126, 22, 11, panel)");
    expect(displaySource).toContain("CanvasRoundedRectangle(16, 43, 208, 166, 28, panel)");
    expect(displaySource).toContain("CanvasRoundedRectangle(14, 47, 212, 158, 30, panel)");
    expect(displaySource).toContain("animation_frame % 8U == 7U");
    expect(displaySource).toContain("CanvasRoundedRectangle(78 - eye_width / 2 + look");

    expect(previewSource).toContain("roundedRectangle(5, 3, 230, 274, 29, face)");
    expect(previewSource).toContain("roundedRectangle(18, 86, 204, 76, 20, accent)");
    expect(previewSource).toContain("roundedRectangle(18, 170, 98, 52, 15, panel)");
    expect(previewSource).toContain("roundedRectangle(124, 170, 98, 52, 15, panel)");
    expect(previewSource).toContain("roundedRectangle(57, 246, 126, 22, 11, panel)");
    expect(previewSource).toContain("roundedRectangle(16, 43, 208, 166, 28, panel)");
    expect(previewSource).toContain("roundedRectangle(14, 47, 212, 158, 30, panel)");
    expect(previewSource).toContain("animationFrame % 8 === 7");
    expect(previewSource).toContain("roundedRectangle(78 - Math.floor(eyeWidth / 2) + look");

    expect(boardSource).toContain("constexpr TickType_t kDisplayAnimationPeriod = pdMS_TO_TICKS(500)");
    expect(previewSource).toContain("}, 500);");
  });
});
