# VeeTee Device UI prototype

HTML prototype for the 240x280 portrait ST7789 display. It is intentionally
separate from the firmware renderer so a visual direction can be approved
before committing to LVGL, fonts, assets, memory budgets, and redraw strategy.

This folder preserves the earlier pre-firmware concept review. The synchronized
production software twin now lives in Manager Web and mirrors the C++ renderer.
The shipped directions are:

- **Mobile** (`signal`): phone-like information cards and built-in fallback.
- **Companion** (`monolith`): animated Vee character.
- **Robot Face** (`quiet`): animated navy/lime eyes matching the device card.

Do not use the archived HTML for acceptance. Use Manager Web `Display / UI`, which
renders the exact 240x280 RGB565 geometry and animation frames used by firmware.

The HTML animations describe intent, not the final rendering technique. The
firmware implementation must bind visuals to real state-machine events and
must not fake progress, network health, wake detection, or AI activity.
