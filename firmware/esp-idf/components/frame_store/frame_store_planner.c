#include "frame_store_planner.h"

frame_store_plan_t frame_store_plan_promotion(frame_store_snapshot_t snapshot) {
    frame_store_plan_t plan = {.status = FRAME_STORE_PLAN_INVALID_TEMP};
    if (!snapshot.temp_valid) {
        return plan;
    }
    plan.status = FRAME_STORE_PLAN_OK;
    if (snapshot.current_valid) {
        if (snapshot.previous_valid || snapshot.previous_exists) {
            plan.steps[plan.count++] = FRAME_STORE_STEP_REMOVE_PREVIOUS;
        }
        plan.steps[plan.count++] = FRAME_STORE_STEP_CURRENT_TO_PREVIOUS;
    } else if (snapshot.current_exists) {
        plan.steps[plan.count++] = FRAME_STORE_STEP_REMOVE_CURRENT;
    }
    plan.steps[plan.count++] = FRAME_STORE_STEP_TEMP_TO_CURRENT;
    return plan;
}

frame_store_recovery_t frame_store_plan_recovery(frame_store_snapshot_t snapshot) {
    if (snapshot.current_valid) {
        return FRAME_STORE_RECOVERY_CURRENT;
    }
    if (snapshot.previous_valid) {
        return FRAME_STORE_RECOVERY_PREVIOUS;
    }
    return FRAME_STORE_RECOVERY_NONE;
}

bool frame_store_recovery_is_displayed(bool selected_new_frame,
                                       bool journal_displayed) {
    return selected_new_frame ? journal_displayed : true;
}
