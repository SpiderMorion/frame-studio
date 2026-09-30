#ifndef CONFIG_STORE_H
#define CONFIG_STORE_H

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CONFIG_STORE_WIFI_SSID_MAX_LEN 32U
#define CONFIG_STORE_WIFI_PASSWORD_MAX_LEN 63U
#define CONFIG_STORE_BACKEND_URL_MAX_LEN 255U
#define CONFIG_STORE_DEVICE_TOKEN_MAX_LEN 255U

typedef struct {
    char wifi_ssid[CONFIG_STORE_WIFI_SSID_MAX_LEN + 1U];
    char wifi_password[CONFIG_STORE_WIFI_PASSWORD_MAX_LEN + 1U];
    char backend_base_url[CONFIG_STORE_BACKEND_URL_MAX_LEN + 1U];
    char device_bearer_token[CONFIG_STORE_DEVICE_TOKEN_MAX_LEN + 1U];
} config_store_credentials_t;

esp_err_t config_store_load(config_store_credentials_t *credentials, bool *found);
esp_err_t config_store_save(const config_store_credentials_t *credentials);
esp_err_t config_store_clear(void);

#ifdef __cplusplus
}
#endif

#endif
