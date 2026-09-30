cmake_minimum_required(VERSION 3.20)
project(eink_firmware_core LANGUAGES CXX)

option(FIRMWARE_ENABLE_SANITIZERS "Enable ASan and UBSan" OFF)

add_library(firmware_core)
target_sources(firmware_core PRIVATE
    src/frame_contract.cpp
    src/manifest.cpp
    src/journal.cpp
    src/state_machine.cpp
)
target_include_directories(firmware_core PUBLIC include)
target_compile_features(firmware_core PUBLIC cxx_std_20)
target_compile_options(firmware_core PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion)

if(FIRMWARE_ENABLE_SANITIZERS AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(firmware_core PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(firmware_core PUBLIC -fsanitize=address,undefined)
endif()

enable_testing()
add_executable(firmware_core_tests tests/test_main.cpp)
target_link_libraries(firmware_core_tests PRIVATE firmware_core)
target_compile_options(firmware_core_tests PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion)
add_test(NAME firmware_core_tests COMMAND firmware_core_tests)
