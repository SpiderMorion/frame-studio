idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES
        backend_client
        config_store
        device_state
        driver
        epd_gdep133c02
        frame_store
        net_manager
        nvs_flash
        provisioning
)
