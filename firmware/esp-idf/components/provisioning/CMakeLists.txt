idf_component_register(
    SRCS "provisioning.c" "provisioning_form.c"
    INCLUDE_DIRS "include"
    REQUIRES config_store esp_event esp_http_server esp_netif esp_wifi
)
