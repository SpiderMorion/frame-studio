#pragma once

#include "esp_err.h"

#include <stdbool.h>
#include <stdint.h>

#define DEVICE_STATE_FRAME_ID_LENGTH 36U
#define DEVICE_STATE_ETAG_MAX_LENGTH 80U

typedef struct {
    uint32_t last_sequence;
    char last_frame_id[DEVICE_STATE_FRAME_ID_LENGTH + 1U];
    char etag[DEVICE_STATE_ETAG_MAX_LENGTH + 1U];
} device_state_record_t;

bool device_state_sequence_is_newer(uint32_t current, uint32_t candidate);
bool device_state_valid_uuid(const char *value);
esp_err_t device_state_load(device_state_record_t *record, bool *found);
esp_err_t device_state_save(const device_state_record_t *record);
esp_err_t device_state_clear(void);
