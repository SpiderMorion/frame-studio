#pragma once

#include <stdbool.h>
#include <stddef.h>

typedef enum {
    FRAME_STORE_PLAN_OK = 0,
    FRAME_STORE_PLAN_INVALID_TEMP,
    FRAME_STORE_PLAN_NO_VALID_FRAME,
} frame_store_plan_status_t;

typedef enum {
    FRAME_STORE_STEP_REMOVE_PREVIOUS = 0,
    FRAME_STORE_STEP_REMOVE_CURRENT,
    FRAME_STORE_STEP_CURRENT_TO_PREVIOUS,
    FRAME_STORE_STEP_TEMP_TO_CURRENT,
} frame_store_step_t;

typedef struct {
    bool temp_valid;
    bool current_valid;
    bool current_exists;
    bool previous_valid;
    bool previous_exists;
} frame_store_snapshot_t;

typedef struct {
    frame_store_plan_status_t status;
    size_t count;
    frame_store_step_t steps[4];
} frame_store_plan_t;

typedef enum {
    FRAME_STORE_RECOVERY_NONE = 0,
    FRAME_STORE_RECOVERY_CURRENT,
    FRAME_STORE_RECOVERY_PREVIOUS,
} frame_store_recovery_t;

frame_store_plan_t frame_store_plan_promotion(frame_store_snapshot_t snapshot);
frame_store_recovery_t frame_store_plan_recovery(frame_store_snapshot_t snapshot);
bool frame_store_recovery_is_displayed(bool selected_new_frame,
                                       bool journal_displayed);
