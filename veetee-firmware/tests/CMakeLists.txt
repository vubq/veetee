cmake_minimum_required(VERSION 3.16)
project(veetee_firmware_host_tests LANGUAGES CXX)

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
