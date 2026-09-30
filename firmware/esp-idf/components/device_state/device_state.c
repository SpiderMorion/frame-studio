#include "device_state/device_state.h"

#include "nvs.h"

#include <string.h>

#define STATE_NAMESPACE "device_state"

esp_err_t device_state_load(device_state_record_t *record, bool *found) {
    if (record == NULL || found == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(record, 0, sizeof(*record));
    *found = false;
    nvs_handle_t handle;
    esp_err_t result = nvs_open(STATE_NAMESPACE, NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (result != ESP_OK) {
        return result;
    }
    size_t frame_length = sizeof(record->last_frame_id);
    size_t etag_length = sizeof(record->etag);
    result = nvs_get_u32(handle, "sequence", &record->last_sequence);
    if (result == ESP_OK) {
        result = nvs_get_str(handle, "frame_id", record->last_frame_id, &frame_length);
    }
    if (result == ESP_OK) {
        result = nvs_get_str(handle, "etag", record->etag, &etag_length);
    }
    nvs_close(handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        memset(record, 0, sizeof(*record));
        return ESP_OK;
    }
    if (result != ESP_OK || !device_state_valid_uuid(record->last_frame_id)) {
        return result != ESP_OK ? result : ESP_ERR_INVALID_STATE;
    }
    *found = true;
    return ESP_OK;
}

esp_err_t device_state_save(const device_state_record_t *record) {
    if (record == NULL || record->last_sequence == 0U ||
        !device_state_valid_uuid(record->last_frame_id) ||
        strnlen(record->etag, sizeof(record->etag)) >= sizeof(record->etag)) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t handle;
    esp_err_t result = nvs_open(STATE_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_set_u32(handle, "sequence", record->last_sequence);
    if (result == ESP_OK) {
        result = nvs_set_str(handle, "frame_id", record->last_frame_id);
    }
    if (result == ESP_OK) {
        result = nvs_set_str(handle, "etag", record->etag);
    }
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

esp_err_t device_state_clear(void) {
    nvs_handle_t handle;
    esp_err_t result = nvs_open(STATE_NAMESPACE, NVS_READWRITE, &handle);
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
