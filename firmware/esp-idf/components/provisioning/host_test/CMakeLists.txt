cmake_minimum_required(VERSION 3.16)
project(provisioning_host_tests C)

enable_testing()
add_executable(provisioning_host_tests
    test_provisioning.c
    ../provisioning_form.c
)
target_include_directories(provisioning_host_tests PRIVATE stubs ../include ../../config_store/include)
target_compile_features(provisioning_host_tests PRIVATE c_std_11)
target_compile_options(provisioning_host_tests PRIVATE -Wall -Wextra -Werror -pedantic)
add_test(NAME provisioning_host_tests COMMAND provisioning_host_tests)
