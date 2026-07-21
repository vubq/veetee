set(VEETEE_SRCS
    "app_main.cpp"
    "app/state_machine.cpp"
    "audio/i2s_audio.cpp"
    "audio/wake_detector.cpp"
    "audio/wake_detector_policy.cpp"
    "board/veetee_board.cpp"
    "display/st7789_display.cpp"
    "input/button.cpp"
    "mcp/device_mcp.cpp"
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
        esp-sr
        esp_websocket_client
        mbedtls
        nvs_flash
)

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)

# ESP-IDF 6 defaults to the newest language mode. Veetee keeps its own source
# surface C++17-compatible so firmware behavior does not depend on newer syntax.
set_source_files_properties(${VEETEE_SRCS} PROPERTIES COMPILE_OPTIONS "-std=gnu++17")

if(CONFIG_VEETEE_ESP_SR_BRINGUP)
    idf_component_get_property(ESP_SR_COMPONENT_DIR espressif__esp-sr COMPONENT_DIR)
    idf_build_get_property(VEETEE_BUILD_DIR BUILD_DIR)
    set(VEETEE_SR_MODEL_IMAGE "${VEETEE_BUILD_DIR}/srmodels/srmodels.bin")
    set(VEETEE_BRINGUP_MODEL_DIR
        "${ESP_SR_COMPONENT_DIR}/model/wakenet_model/wn9s_hiesp")

    partition_table_get_partition_info(
        VEETEE_RESOURCE_SIZE
        "--partition-name ${CONFIG_VEETEE_WAKE_MODEL_PARTITION}"
        "size"
    )
    partition_table_get_partition_info(
        VEETEE_RESOURCE_OFFSET
        "--partition-name ${CONFIG_VEETEE_WAKE_MODEL_PARTITION}"
        "offset"
    )
    if(NOT VEETEE_RESOURCE_SIZE OR NOT VEETEE_RESOURCE_OFFSET)
        message(FATAL_ERROR
            "Missing ${CONFIG_VEETEE_WAKE_MODEL_PARTITION} partition for ESP-SR bring-up")
    endif()

    add_custom_command(
        OUTPUT "${VEETEE_SR_MODEL_IMAGE}"
        COMMENT "Packing Veetee ESP-SR bring-up model"
        COMMAND "${PYTHON}" "${ESP_SR_COMPONENT_DIR}/model/movemodel.py"
            -d1 "${SDKCONFIG}"
            -d2 "${ESP_SR_COMPONENT_DIR}"
            -d3 "${VEETEE_BUILD_DIR}"
        COMMAND "${CMAKE_COMMAND}"
            -DIMAGE=${VEETEE_SR_MODEL_IMAGE}
            -DMAX_BYTES=${VEETEE_RESOURCE_SIZE}
            -P "${CMAKE_CURRENT_LIST_DIR}/../cmake/check_binary_size.cmake"
        DEPENDS
            "${SDKCONFIG}"
            "${ESP_SR_COMPONENT_DIR}/model/movemodel.py"
            "${ESP_SR_COMPONENT_DIR}/model/pack_model.py"
            "${VEETEE_BRINGUP_MODEL_DIR}/_MODEL_INFO_"
            "${VEETEE_BRINGUP_MODEL_DIR}/wn9_data"
            "${VEETEE_BRINGUP_MODEL_DIR}/wn9_index"
        VERBATIM
    )
    add_custom_target(veetee_srmodels_bin ALL DEPENDS "${VEETEE_SR_MODEL_IMAGE}")
    add_dependencies(flash veetee_srmodels_bin)
    esptool_py_flash_to_partition(
        flash
        "${CONFIG_VEETEE_WAKE_MODEL_PARTITION}"
        "${VEETEE_SR_MODEL_IMAGE}"
    )
endif()
