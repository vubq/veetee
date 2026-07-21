# Source map

Use this map only when the task needs comparison with the read-only Xiaozhi sources.

## Firmware

- App/runtime: `../../../references/xiaozhi-esp32/main/application.*`
- State rules: `../../../references/xiaozhi-esp32/main/device_state_machine.*`
- Wi-Fi AP fallback: `../../../references/xiaozhi-esp32/main/boards/common/wifi_board.*`
- Audio service/codecs: `../../../references/xiaozhi-esp32/main/audio/`
- ST7789 + INMP441/MAX98357A baseline: `../../../references/xiaozhi-esp32/main/boards/bread-compact-wifi-lcd/`
- Transport: `../../../references/xiaozhi-esp32/main/protocols/`
- OTA/activation: `../../../references/xiaozhi-esp32/main/ota.*`
- Device MCP: `../../../references/xiaozhi-esp32/main/mcp_server.*`
- Protocol docs: `../../../references/xiaozhi-esp32/docs/websocket.md`, `mqtt-udp.md`, `mcp-protocol.md`

## Backend

- WebSocket/session: `../../../references/xiaozhi-esp32-server/main/xiaozhi-server/core/websocket_server.py`, `connection.py`
- Conversation handlers: `../../../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- Providers: `../../../references/xiaozhi-esp32-server/main/xiaozhi-server/core/providers/`
- Plugins/tools: `../../../references/xiaozhi-esp32-server/main/xiaozhi-server/plugins_func/`
- Manager modules: `../../../references/xiaozhi-esp32-server/main/manager-api/src/main/java/xiaozhi/modules/`
- Manager routes/views: `../../../references/xiaozhi-esp32-server/main/manager-web/src/`

Do not import a source module solely because it exists. Extract the behavior/contract needed by the current milestone and rewrite it inside the correct Veetee boundary.
