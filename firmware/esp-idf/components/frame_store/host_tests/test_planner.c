#include "frame_store_planner.h"

#include <stdio.h>
#include <stdlib.h>

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #expr); return false; } } while (0)

static bool promotion_without_current_installs_temp(void) {
    frame_store_snapshot_t snapshot = {.temp_valid = true};
    frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    CHECK(plan.status == FRAME_STORE_PLAN_OK);
    CHECK(plan.count == 1U);
    CHECK(plan.steps[0] == FRAME_STORE_STEP_TEMP_TO_CURRENT);
    return true;
}

static bool promotion_preserves_current_as_previous(void) {
    frame_store_snapshot_t snapshot = {
        .temp_valid = true, .current_valid = true, .previous_valid = true};
    frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    CHECK(plan.status == FRAME_STORE_PLAN_OK);
    CHECK(plan.count == 3U);
    CHECK(plan.steps[0] == FRAME_STORE_STEP_REMOVE_PREVIOUS);
    CHECK(plan.steps[1] == FRAME_STORE_STEP_CURRENT_TO_PREVIOUS);
    CHECK(plan.steps[2] == FRAME_STORE_STEP_TEMP_TO_CURRENT);
    return true;
}

static bool invalid_temp_never_touches_known_good(void) {
    frame_store_snapshot_t snapshot = {
        .temp_valid = false, .current_valid = true, .previous_valid = true};
    frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    CHECK(plan.status == FRAME_STORE_PLAN_INVALID_TEMP);
    CHECK(plan.count == 0U);
    return true;
}

static bool promotion_removes_invalid_stale_previous_path(void) {
    frame_store_snapshot_t snapshot = {
        .temp_valid = true,
        .current_valid = true,
        .previous_valid = false,
        .previous_exists = true,
    };
    frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    CHECK(plan.count == 3U);
    CHECK(plan.steps[0] == FRAME_STORE_STEP_REMOVE_PREVIOUS);
    return true;
}

static bool promotion_removes_invalid_stale_current_path(void) {
    frame_store_snapshot_t snapshot = {
        .temp_valid = true,
        .current_valid = false,
        .current_exists = true,
        .previous_valid = true,
    };
    frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    CHECK(plan.count == 2U);
    CHECK(plan.steps[0] == FRAME_STORE_STEP_REMOVE_CURRENT);
    CHECK(plan.steps[1] == FRAME_STORE_STEP_TEMP_TO_CURRENT);
    return true;
}

static bool recovery_covers_every_promotion_rename_boundary(void) {
    const frame_store_snapshot_t before = {
        .temp_valid = true, .current_valid = true, .previous_valid = true};
    const frame_store_snapshot_t after_remove_previous = {
        .temp_valid = true, .current_valid = true, .previous_valid = false};
    const frame_store_snapshot_t after_current_to_previous = {
        .temp_valid = true, .current_valid = false, .previous_valid = true};
    const frame_store_snapshot_t after_temp_to_current = {
        .temp_valid = false, .current_valid = true, .previous_valid = true};
    CHECK(frame_store_plan_recovery(before) == FRAME_STORE_RECOVERY_CURRENT);
    CHECK(frame_store_plan_recovery(after_remove_previous) == FRAME_STORE_RECOVERY_CURRENT);
    CHECK(frame_store_plan_recovery(after_current_to_previous) == FRAME_STORE_RECOVERY_PREVIOUS);
    CHECK(frame_store_plan_recovery(after_temp_to_current) == FRAME_STORE_RECOVERY_CURRENT);
    return true;
}

static bool recovery_rejects_invalid_current_and_prefers_valid_previous(void) {
    frame_store_snapshot_t snapshot = {
        .temp_valid = true, .current_valid = false, .previous_valid = true};
    CHECK(frame_store_plan_recovery(snapshot) == FRAME_STORE_RECOVERY_PREVIOUS);
    snapshot.previous_valid = false;
    CHECK(frame_store_plan_recovery(snapshot) == FRAME_STORE_RECOVERY_NONE);
    return true;
}

static bool recovery_treats_prior_fallback_as_already_displayed(void) {
    CHECK(frame_store_recovery_is_displayed(false, false));
    CHECK(frame_store_recovery_is_displayed(false, true));
    CHECK(!frame_store_recovery_is_displayed(true, false));
    CHECK(frame_store_recovery_is_displayed(true, true));
    return true;
}

int main(void) {
    if (!promotion_without_current_installs_temp()) return EXIT_FAILURE;
    if (!promotion_preserves_current_as_previous()) return EXIT_FAILURE;
    if (!invalid_temp_never_touches_known_good()) return EXIT_FAILURE;
    if (!promotion_removes_invalid_stale_previous_path()) return EXIT_FAILURE;
    if (!promotion_removes_invalid_stale_current_path()) return EXIT_FAILURE;
    if (!recovery_covers_every_promotion_rename_boundary()) return EXIT_FAILURE;
    if (!recovery_rejects_invalid_current_and_prefers_valid_previous()) return EXIT_FAILURE;
    if (!recovery_treats_prior_fallback_as_already_displayed()) return EXIT_FAILURE;
    return EXIT_SUCCESS;
}
