#include "backend_client.h"
#include "config_store.h"
#include "device_state/device_state.h"
#include "driver/gpio.h"
#include "epd_gdep133c02.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "frame_store.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net_manager/net_manager.h"
#include "net_manager/net_policy.h"
#include "nvs_flash.h"
#include "provisioning.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define FIRMWARE_VERSION "0.1.1"
#define RESET_CONFIG_GPIO GPIO_NUM_14
#define CONNECT_TIMEOUT_MS 20000U
#define POLL_INTERVAL_MS 60000U

#if CONFIG_ESP_MAIN_TASK_STACK_SIZE < 12288
#error "production app_main stack must cover synchronous SD/FAT/HTTP runtime"
#endif

static const char *TAG = "eink_production";
static bool panel_initialized;

static void log_stack_headroom(const char *stage) {
    ESP_LOGI(TAG, "main stack high-water after %s: %lu bytes", stage,
             (unsigned long)uxTaskGetStackHighWaterMark(NULL));
}

typedef struct {
    FILE *file;
    size_t bytes_written;
} download_context_t;

static esp_err_t write_download(const uint8_t *data, size_t length, void *opaque) {
    download_context_t *context = opaque;
    if (context == NULL || context->file == NULL || data == NULL ||
        context->bytes_written + length > FRAME_STORE_FRAME_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    if (fwrite(data, 1U, length, context->file) != length) {
        return ESP_FAIL;
    }
    context->bytes_written += length;
    return ESP_OK;
}

static bool decode_sha256(const char *hex, uint8_t output[32]) {
    if (hex == NULL || strlen(hex) != 64U) {
        return false;
    }
    for (size_t index = 0; index < 32U; ++index) {
        unsigned int byte = 0U;
        if (sscanf(hex + index * 2U, "%2x", &byte) != 1) {
            return false;
        }
        output[index] = (uint8_t)byte;
    }
    return true;
}

static esp_err_t initialize_nvs(void) {
    esp_err_t result = nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "NVS erase failed");
        result = nvs_flash_init();
    }
    return result;
}

static bool reset_requested(void) {
    gpio_config_t config = {
        .pin_bit_mask = 1ULL << RESET_CONFIG_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
    };
    if (gpio_config(&config) != ESP_OK) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    return gpio_get_level(RESET_CONFIG_GPIO) == 1;
}

static void run_provisioning(void) {
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    ESP_ERROR_CHECK(provisioning_start(mac));
    ESP_LOGI(TAG, "connect to the visible EInkFrame setup network and open http://192.168.4.1");
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

static esp_err_t connect_wifi(const config_store_credentials_t *credentials) {
    net_credentials_t network = {0};
    strlcpy(network.ssid, credentials->wifi_ssid, sizeof(network.ssid));
    strlcpy(network.password, credentials->wifi_password, sizeof(network.password));
    ESP_RETURN_ON_ERROR(net_manager_init(), TAG, "network manager init failed");
    for (uint32_t attempt = 0U;; ++attempt) {
        const esp_err_t result =
            net_manager_connect(&network, CONNECT_TIMEOUT_MS);
        if (result == ESP_OK) {
            memset(&network, 0, sizeof(network));
            return ESP_OK;
        }
        const uint32_t delay = net_retry_delay_ms(attempt);
        ESP_LOGW(TAG, "Wi-Fi unavailable; retrying in %lu ms",
                 (unsigned long)delay);
        vTaskDelay(pdMS_TO_TICKS(delay));
    }
}

static esp_err_t post_status(const backend_client_config_t *backend,
                             const device_state_record_t *state,
                             const char *error_code, const char *detail) {
    const net_status_t network = net_manager_status();
    const backend_device_status_t status = {
        .firmware_version = FIRMWARE_VERSION,
        .last_frame_id = state->last_sequence == 0U ? NULL : state->last_frame_id,
        .wifi_rssi_dbm = network.rssi_dbm,
        .battery_mv = 0,
        .battery_pct = 0,
        .error_code = error_code,
        .error_detail = detail,
    };
    return backend_client_post_status(backend, &status);
}

static esp_err_t ensure_panel_initialized(void) {
    if (!panel_initialized) {
        ESP_RETURN_ON_ERROR(gdep133c02_init(), TAG, "panel init failed");
        panel_initialized = true;
    }
    return ESP_OK;
}

static esp_err_t recover_staged_display(void) {
    frame_store_recovery_result_t recovery = {0};
    const esp_err_t result = frame_store_recover(&recovery);
    if (result == ESP_ERR_NOT_FOUND) {
        return ESP_OK;
    }
    if (result != ESP_OK || recovery.path == NULL || recovery.displayed) {
        return result;
    }
    ESP_RETURN_ON_ERROR(ensure_panel_initialized(), TAG,
                        "panel recovery init failed");
    ESP_RETURN_ON_ERROR(gdep133c02_display_file(recovery.path), TAG,
                        "recovered frame refresh failed");
    return frame_store_mark_displayed(recovery.sha256);
}

static esp_err_t download_promote_display(
    const backend_client_config_t *backend, const backend_manifest_t *manifest,
    const char *etag, device_state_record_t *state) {
    uint8_t expected_sha256[32];
    if (!decode_sha256(manifest->sha256, expected_sha256)) {
        return ESP_ERR_INVALID_ARG;
    }
    FILE *file = fopen(FRAME_STORE_DOWNLOAD_PATH, "wb");
    if (file == NULL) {
        return ESP_FAIL;
    }
    download_context_t context = {.file = file};
    esp_err_t result = backend_client_download_frame(
        backend, manifest, write_download, &context);
    if (result == ESP_OK && context.bytes_written != FRAME_STORE_FRAME_BYTES) {
        result = ESP_ERR_INVALID_SIZE;
    }
    if (result == ESP_OK && (fflush(file) != 0 || fsync(fileno(file)) != 0)) {
        result = ESP_FAIL;
    }
    if (fclose(file) != 0 && result == ESP_OK) {
        result = ESP_FAIL;
    }
    context.file = NULL;
    if (result != ESP_OK) {
        unlink(FRAME_STORE_DOWNLOAD_PATH);
        return result;
    }
    ESP_RETURN_ON_ERROR(frame_store_promote(expected_sha256), TAG,
                        "frame promotion failed");
    ESP_RETURN_ON_ERROR(ensure_panel_initialized(), TAG, "panel init failed");
    ESP_RETURN_ON_ERROR(gdep133c02_display_file(FRAME_STORE_CURRENT_PATH), TAG,
                        "panel refresh failed");
    ESP_RETURN_ON_ERROR(frame_store_mark_displayed(expected_sha256), TAG,
                        "display journal commit failed");

    state->last_sequence = manifest->sequence;
    strlcpy(state->last_frame_id, manifest->frame_id,
            sizeof(state->last_frame_id));
    strlcpy(state->etag, etag, sizeof(state->etag));
    return device_state_save(state);
}

void app_main(void) {
    ESP_ERROR_CHECK(initialize_nvs());
    if (reset_requested()) {
        ESP_LOGW(TAG, "SW4 held: clearing provisioned network and accepted manifest state");
        ESP_ERROR_CHECK(config_store_clear());
        ESP_ERROR_CHECK(device_state_clear());
    }

    config_store_credentials_t credentials = {0};
    bool configured = false;
    ESP_ERROR_CHECK(config_store_load(&credentials, &configured));
    if (!configured) {
        run_provisioning();
    }

    ESP_ERROR_CHECK(connect_wifi(&credentials));
    log_stack_headroom("Wi-Fi connect");
    const backend_client_config_t backend = {
        .base_url = credentials.backend_base_url,
        .bearer_token = credentials.device_bearer_token,
        .timeout_ms = 30000,
    };
    device_state_record_t state = {0};
    bool state_found = false;
    ESP_ERROR_CHECK(device_state_load(&state, &state_found));
    if (!state_found) {
        memset(&state, 0, sizeof(state));
    }
    esp_err_t storage_result;
    log_stack_headroom("SD mount entry");
    while ((storage_result = frame_store_mount()) != ESP_OK) {
        ESP_LOGE(TAG, "microSD mount failed: %s", esp_err_to_name(storage_result));
        post_status(&backend, &state, "sd_mount_failed",
                    esp_err_to_name(storage_result));
        vTaskDelay(pdMS_TO_TICKS(30000));
    }
    log_stack_headroom("SD mount");
    ESP_ERROR_CHECK(recover_staged_display());
    log_stack_headroom("frame recovery");

    while (true) {
        if (!net_manager_status().connected) {
            ESP_LOGW(TAG, "Wi-Fi disconnected; entering bounded reconnect loop");
            ESP_ERROR_CHECK(connect_wifi(&credentials));
        }
        backend_manifest_t manifest = {0};
        backend_manifest_result_t manifest_result;
        char response_etag[BACKEND_ETAG_MAX_LENGTH + 1U] = {0};
        const esp_err_t result = backend_client_get_manifest(
            &backend, state.etag[0] == '\0' ? NULL : state.etag, &manifest,
            response_etag, sizeof(response_etag), &manifest_result);
        if (result != ESP_OK) {
            ESP_LOGW(TAG, "manifest poll failed: %s", esp_err_to_name(result));
            post_status(&backend, &state, "manifest_poll_failed",
                        esp_err_to_name(result));
        } else if (manifest_result == BACKEND_MANIFEST_UPDATED &&
                   device_state_sequence_is_newer(state.last_sequence,
                                                  manifest.sequence)) {
            const esp_err_t update_result = download_promote_display(
                &backend, &manifest, response_etag, &state);
            if (update_result == ESP_OK) {
                post_status(&backend, &state, NULL, NULL);
                ESP_LOGI(TAG, "displayed frame sequence %lu",
                         (unsigned long)state.last_sequence);
            } else {
                ESP_LOGE(TAG, "frame update failed: %s",
                         esp_err_to_name(update_result));
                post_status(&backend, &state, "frame_update_failed",
                            esp_err_to_name(update_result));
            }
        } else {
            post_status(&backend, &state, NULL, NULL);
        }
        log_stack_headroom("manifest cycle");
        vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
    }
}
