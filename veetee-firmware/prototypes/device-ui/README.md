# VeeTee Device UI prototype

HTML prototype for the 240x280 portrait ST7789 display. It is intentionally
separate from the firmware renderer so a visual direction can be approved
before committing to LVGL, fonts, assets, memory budgets, and redraw strategy.

The three concepts are:

- **Signal**: recommended; follows the Manager Web brand system.
- **Monolith**: high-contrast industrial instrument.
- **Quiet**: calm, interior-friendly light interface.

Open `index.html` directly or serve this directory with any static HTTP server.
Use the state controls to inspect the full conversation and recovery flow.

The HTML animations describe intent, not the final rendering technique. The
firmware implementation must bind visuals to real state-machine events and
must not fake progress, network health, wake detection, or AI activity.
