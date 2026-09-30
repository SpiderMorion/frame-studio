#include "config_store.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "nvs.h"

#define CONFIG_NAMESPACE "eink_config"
#define CONFIG_BLOB_KEY "credentials"
#define CONFIG_MAGIC UINT32_C(0x45494E4B)
#define CONFIG_VERSION UINT32_C(1)

typedef struct {
    uint32_t magic;
    uint32_t version;
    config_store_credentials_t credentials;
} stored_config_t;

static bool credentials_are_terminated(const config_store_credentials_t *credentials) {
    return credentials != NULL &&
           memchr(credentials->wifi_ssid, '\0', sizeof(credentials->wifi_ssid)) != NULL &&
           memchr(credentials->wifi_password, '\0', sizeof(credentials->wifi_password)) != NULL &&
           memchr(credentials->backend_base_url, '\0',
                  sizeof(credentials->backend_base_url)) != NULL &&
           memchr(credentials->device_bearer_token, '\0',
                  sizeof(credentials->device_bearer_token)) != NULL;
}

esp_err_t config_store_load(config_store_credentials_t *credentials, bool *found) {
    if (credentials == NULL || found == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(credentials, 0, sizeof(*credentials));
    *found = false;

    nvs_handle_t handle;
    esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (result != ESP_OK) {
        return result;
    }

    stored_config_t stored = {0};
    size_t length = sizeof(stored);
    result = nvs_get_blob(handle, CONFIG_BLOB_KEY, &stored, &length);
    nvs_close(handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (result != ESP_OK) {
        return result;
    }
    if (length != sizeof(stored) || stored.magic != CONFIG_MAGIC ||
        stored.version != CONFIG_VERSION || !credentials_are_terminated(&stored.credentials)) {
        memset(&stored, 0, sizeof(stored));
        return ESP_ERR_INVALID_STATE;
    }

    *credentials = stored.credentials;
    *found = true;
    memset(&stored, 0, sizeof(stored));
    return ESP_OK;
}

esp_err_t config_store_save(const config_store_credentials_t *credentials) {
    if (!credentials_are_terminated(credentials)) {
        return ESP_ERR_INVALID_ARG;
    }
    stored_config_t stored = {
        .magic = CONFIG_MAGIC,
        .version = CONFIG_VERSION,
        .credentials = *credentials,
    };

    nvs_handle_t handle;
    esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        memset(&stored, 0, sizeof(stored));
        return result;
    }
    result = nvs_set_blob(handle, CONFIG_BLOB_KEY, &stored, sizeof(stored));
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    memset(&stored, 0, sizeof(stored));
    return result;
}

esp_err_t config_store_clear(void) {
    nvs_handle_t handle;
    esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_erase_all(handle);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}
