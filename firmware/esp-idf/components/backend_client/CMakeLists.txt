idf_component_register(
    SRCS "backend_client.c" "backend_manifest_parser.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_http_client json mbedtls
)
