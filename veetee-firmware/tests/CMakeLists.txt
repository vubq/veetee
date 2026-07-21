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
