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
