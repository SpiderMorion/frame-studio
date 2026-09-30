cmake_minimum_required(VERSION 3.16)
project(backend_manifest_parser_tests C)

enable_testing()
set(CJSON_DIR "$ENV{IDF_PATH}/components/json/cJSON")
if(NOT EXISTS "${CJSON_DIR}/cJSON.c")
  message(FATAL_ERROR "Set IDF_PATH to an ESP-IDF checkout")
endif()
add_executable(test_backend_manifest
  test_backend_manifest.c
  ../backend_manifest_parser.c
  ${CJSON_DIR}/cJSON.c
)
target_include_directories(test_backend_manifest PRIVATE
  ../include
  include
  ${CJSON_DIR}
)
target_compile_options(test_backend_manifest PRIVATE -Wall -Wextra -Werror -pedantic)
target_link_libraries(test_backend_manifest PRIVATE m)
add_test(NAME backend_manifest_parser COMMAND test_backend_manifest)
