idf_component_register(
    SRCS "device_state.c" "device_state_policy.c"
    INCLUDE_DIRS "include"
    REQUIRES nvs_flash
)
