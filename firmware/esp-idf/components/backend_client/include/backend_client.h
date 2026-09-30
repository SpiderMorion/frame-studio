#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BACKEND_MANIFEST_MAX_JSON 2048U
#define BACKEND_FRAME_BYTE_LENGTH 960000U
#define BACKEND_ETAG_MAX_LENGTH 80U

typedef struct {
    uint32_t sequence;
    char frame_id[37];
    char planned_display_at[40];
    uint32_t byte_length;
    char sha256[65];
    char url[96];
} backend_manifest_t;

typedef enum {
    BACKEND_MANIFEST_UPDATED,
    BACKEND_MANIFEST_NOT_MODIFIED,
    BACKEND_MANIFEST_NO_CONTENT,
} backend_manifest_result_t;

typedef struct {
    const char *base_url;
    const char *bearer_token;
    int timeout_ms;
} backend_client_config_t;

typedef esp_err_t (*backend_frame_write_cb_t)(const uint8_t *data, size_t length,
                                               void *context);

typedef struct {
    const char *firmware_version;
    const char *last_frame_id;
    int wifi_rssi_dbm;
    int battery_mv;
    int battery_pct;
    const char *error_code;
    const char *error_detail;
} backend_device_status_t;

esp_err_t backend_manifest_parse(const char *json, size_t length,
                                 backend_manifest_t *manifest);

esp_err_t backend_client_get_manifest(const backend_client_config_t *config,
                                      const char *etag,
                                      backend_manifest_t *manifest,
                                      char *response_etag,
                                      size_t response_etag_size,
                                      backend_manifest_result_t *result);

esp_err_t backend_client_download_frame(const backend_client_config_t *config,
                                        const backend_manifest_t *manifest,
                                        backend_frame_write_cb_t write_cb,
                                        void *write_context);

esp_err_t backend_client_post_status(const backend_client_config_t *config,
                                     const backend_device_status_t *status);

#ifdef __cplusplus
}
#endif
