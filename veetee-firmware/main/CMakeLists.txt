set(VEETEE_SRCS
    "app_main.cpp"
    "app/state_machine.cpp"
    "audio/i2s_audio.cpp"
    "board/veetee_board.cpp"
    "display/st7789_display.cpp"
    "input/button.cpp"
    "network/endpoint_url.cpp"
    "network/provisioning_portal.cpp"
    "network/wifi_manager.cpp"
    "ota/bootstrap_client.cpp"
    "settings/activation_record.cpp"
    "settings/settings_store.cpp"
    "transport/protocol_v1.cpp"
    "transport/websocket_transport.cpp"
)

idf_component_register(
    SRCS ${VEETEE_SRCS}
    INCLUDE_DIRS "."
    REQUIRES
        esp_driver_gpio
        esp_driver_i2s
        esp_driver_spi
        esp_app_format
        esp_event
        esp_http_client
        esp_http_server
        esp_lcd
        esp_netif
        esp_psram
        esp_timer
        esp_wifi
        esp_audio_codec
        esp_websocket_client
        mbedtls
        nvs_flash
)

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)

# ESP-IDF 6 defaults to the newest language mode. Veetee keeps its own source
# surface C++17-compatible so firmware behavior does not depend on newer syntax.
set_source_files_properties(${VEETEE_SRCS} PROPERTIES COMPILE_OPTIONS "-std=gnu++17")
