cmake_minimum_required(VERSION 3.16)
project(veetee_firmware_host_tests LANGUAGES C CXX)

enable_testing()

add_executable(
    state_machine_test
    state_machine_test.cpp
    ../main/app/state_machine.cpp
)
target_include_directories(state_machine_test PRIVATE ../main)
target_compile_features(state_machine_test PRIVATE cxx_std_17)
target_compile_options(state_machine_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME state_machine_test COMMAND state_machine_test)

add_executable(
    wake_detector_policy_test
    wake_detector_policy_test.cpp
    ../main/audio/wake_detector_policy.cpp
)
target_include_directories(wake_detector_policy_test PRIVATE ../main)
target_compile_features(wake_detector_policy_test PRIVATE cxx_std_17)
target_compile_options(wake_detector_policy_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME wake_detector_policy_test COMMAND wake_detector_policy_test)

add_executable(
    endpoint_url_test
    endpoint_url_test.cpp
    ../main/network/endpoint_url.cpp
)
target_include_directories(endpoint_url_test PRIVATE ../main)
target_compile_features(endpoint_url_test PRIVATE cxx_std_17)
target_compile_options(endpoint_url_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME endpoint_url_test COMMAND endpoint_url_test)

add_executable(
    activation_record_test
    activation_record_test.cpp
    ../main/settings/activation_record.cpp
)
target_include_directories(activation_record_test PRIVATE ../main)
target_compile_features(activation_record_test PRIVATE cxx_std_17)
target_compile_options(activation_record_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME activation_record_test COMMAND activation_record_test)

add_executable(
    wifi_profile_record_test
    wifi_profile_record_test.cpp
    ../main/settings/wifi_profile_record.cpp
)
target_include_directories(wifi_profile_record_test PRIVATE ../main)
target_compile_features(wifi_profile_record_test PRIVATE cxx_std_17)
target_compile_options(wifi_profile_record_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME wifi_profile_record_test COMMAND wifi_profile_record_test)

add_executable(
    wifi_candidate_order_test
    wifi_candidate_order_test.cpp
    ../main/network/wifi_candidate_order.cpp
    ../main/settings/wifi_profile_record.cpp
)
target_include_directories(wifi_candidate_order_test PRIVATE ../main)
target_compile_features(wifi_candidate_order_test PRIVATE cxx_std_17)
target_compile_options(wifi_candidate_order_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME wifi_candidate_order_test COMMAND wifi_candidate_order_test)

add_library(
    cjson_host STATIC
    ../managed_components/espressif__cjson/cJSON/cJSON.c
)
target_include_directories(
    cjson_host PUBLIC
    ../managed_components/espressif__cjson/cJSON
)
target_compile_options(cjson_host PRIVATE -w)

add_executable(
    protocol_v1_test
    protocol_v1_test.cpp
    ../main/transport/protocol_v1.cpp
)
target_include_directories(
    protocol_v1_test PRIVATE
    ../main
    ../managed_components/espressif__cjson/cJSON
)
target_compile_definitions(
    protocol_v1_test PRIVATE
    VEETEE_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../.."
)
target_compile_features(protocol_v1_test PRIVATE cxx_std_17)
target_compile_options(protocol_v1_test PRIVATE -Wall -Wextra -Werror)
target_link_libraries(protocol_v1_test PRIVATE cjson_host)

add_test(NAME protocol_v1_test COMMAND protocol_v1_test)

add_executable(
    device_mcp_test
    device_mcp_test.cpp
    ../main/mcp/device_mcp.cpp
)
target_include_directories(
    device_mcp_test PRIVATE
    ../main
    ../managed_components/espressif__cjson/cJSON
)
target_compile_definitions(
    device_mcp_test PRIVATE
    VEETEE_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../.."
)
target_compile_features(device_mcp_test PRIVATE cxx_std_17)
target_compile_options(device_mcp_test PRIVATE -Wall -Wextra -Werror)
target_link_libraries(device_mcp_test PRIVATE cjson_host)

add_test(NAME device_mcp_test COMMAND device_mcp_test)

add_library(
    monocypher_host STATIC
    ../components/monocypher/monocypher.c
    ../components/monocypher/monocypher-ed25519.c
)
target_include_directories(
    monocypher_host PUBLIC
    ../components/monocypher/include
)
target_compile_options(monocypher_host PRIVATE -w)

add_executable(
    resource_manifest_test
    resource_manifest_test.cpp
    ../main/network/endpoint_url.cpp
    ../main/ota/resource_manifest.cpp
    ../main/security/signed_json.cpp
)
target_include_directories(
    resource_manifest_test PRIVATE
    ../main
    ../managed_components/espressif__cjson/cJSON
    ../components/monocypher/include
)
target_compile_definitions(
    resource_manifest_test PRIVATE
    VEETEE_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../.."
)
target_compile_features(resource_manifest_test PRIVATE cxx_std_17)
target_compile_options(resource_manifest_test PRIVATE -Wall -Wextra -Werror)
target_link_libraries(resource_manifest_test PRIVATE cjson_host monocypher_host)

add_test(NAME resource_manifest_test COMMAND resource_manifest_test)

add_executable(
    resource_record_test
    resource_record_test.cpp
    ../main/settings/resource_record.cpp
)
target_include_directories(resource_record_test PRIVATE ../main)
target_compile_features(resource_record_test PRIVATE cxx_std_17)
target_compile_options(resource_record_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME resource_record_test COMMAND resource_record_test)

add_executable(
    reported_state_record_test
    reported_state_record_test.cpp
    ../main/settings/reported_state_record.cpp
)
target_include_directories(reported_state_record_test PRIVATE ../main)
target_compile_features(reported_state_record_test PRIVATE cxx_std_17)
target_compile_options(reported_state_record_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME reported_state_record_test COMMAND reported_state_record_test)

add_executable(
    reported_state_outbox_test
    reported_state_outbox_test.cpp
    ../main/settings/reported_state_record.cpp
    ../main/telemetry/reported_state_outbox.cpp
)
target_include_directories(reported_state_outbox_test PRIVATE ../main)
target_compile_features(reported_state_outbox_test PRIVATE cxx_std_17)
target_compile_options(reported_state_outbox_test PRIVATE -Wall -Wextra -Werror)

add_test(NAME reported_state_outbox_test COMMAND reported_state_outbox_test)
