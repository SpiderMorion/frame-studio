#include "net_manager/net_manager.h"

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include <string.h>

#define CONNECTED_BIT BIT0
#define FAILED_BIT BIT1

static const char *TAG = "net_manager";
static EventGroupHandle_t events;
static bool initialized;
static net_status_t current_status;

static void event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)argument;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        current_status.connected = false;
        current_status.failed_attempts++;
        xEventGroupSetBits(events, FAILED_BIT);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        current_status.connected = true;
        current_status.failed_attempts = 0;
        xEventGroupSetBits(events, CONNECTED_BIT);
    }
    (void)data;
}

esp_err_t net_manager_init(void) {
    if (initialized) {
        return ESP_OK;
    }
    events = xEventGroupCreate();
    if (events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif init failed");
    esp_err_t loop_result = esp_event_loop_create_default();
    if (loop_result != ESP_OK && loop_result != ESP_ERR_INVALID_STATE) {
        return loop_result;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        return ESP_ERR_NO_MEM;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init), TAG, "wifi init failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                   event_handler, NULL),
                        TAG, "wifi handler registration failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                   event_handler, NULL),
                        TAG, "IP handler registration failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "station mode failed");
    initialized = true;
    return ESP_OK;
}

esp_err_t net_manager_connect(const net_credentials_t *credentials, uint32_t timeout_ms) {
    if (!initialized || credentials == NULL || credentials->ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    wifi_config_t config = {0};
    strlcpy((char *)config.sta.ssid, credentials->ssid, sizeof(config.sta.ssid));
    strlcpy((char *)config.sta.password, credentials->password, sizeof(config.sta.password));
    config.sta.threshold.authmode = credentials->password[0] == '\0'
                                        ? WIFI_AUTH_OPEN
                                        : WIFI_AUTH_WPA2_PSK;
    config.sta.pmf_cfg.capable = true;
    xEventGroupClearBits(events, CONNECTED_BIT | FAILED_BIT);
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &config), TAG,
                        "station config failed");
    esp_err_t start_result = esp_wifi_start();
    if (start_result != ESP_OK && start_result != ESP_ERR_WIFI_NOT_STOPPED) {
        return start_result;
    }
    ESP_RETURN_ON_ERROR(esp_wifi_connect(), TAG, "station connect failed");
    const EventBits_t result = xEventGroupWaitBits(
        events, CONNECTED_BIT | FAILED_BIT, pdTRUE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    if ((result & CONNECTED_BIT) == 0U) {
        ESP_LOGW(TAG, "Wi-Fi connection attempt failed");
        return ESP_ERR_TIMEOUT;
    }
    wifi_ap_record_t access_point;
    if (esp_wifi_sta_get_ap_info(&access_point) == ESP_OK) {
        current_status.rssi_dbm = access_point.rssi;
    }
    return ESP_OK;
}

void net_manager_disconnect(void) {
    if (initialized) {
        esp_wifi_disconnect();
    }
    current_status.connected = false;
}

net_status_t net_manager_status(void) {
    if (initialized && current_status.connected) {
        wifi_ap_record_t access_point;
        if (esp_wifi_sta_get_ap_info(&access_point) == ESP_OK) {
            current_status.rssi_dbm = access_point.rssi;
        }
    }
    return current_status;
}
