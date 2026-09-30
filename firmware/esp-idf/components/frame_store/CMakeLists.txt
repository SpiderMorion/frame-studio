idf_component_register(
    SRCS "frame_store.c" "frame_store_planner.c" "frame_store_journal.c"
    INCLUDE_DIRS "include"
    REQUIRES driver fatfs sdmmc mbedtls
)
