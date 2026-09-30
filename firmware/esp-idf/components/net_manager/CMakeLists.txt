idf_component_register(
    SRCS "net_manager.c" "net_policy.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_event esp_netif esp_wifi freertos
)
