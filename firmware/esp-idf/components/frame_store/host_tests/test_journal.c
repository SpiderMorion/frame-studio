#include "frame_store_journal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #expr); return false; } } while (0)

static frame_store_state_t state(uint32_t generation, bool displayed, uint8_t hash_byte) {
    frame_store_state_t value = {.generation = generation, .displayed = displayed};
    memset(value.sha256, hash_byte, sizeof(value.sha256));
    return value;
}

static bool journal_round_trips_and_rejects_torn_record(void) {
    const frame_store_state_t input = state(7U, false, 0xa5U);
    frame_store_journal_record_t record;
    frame_store_journal_encode(&input, &record);
    frame_store_state_t output;
    CHECK(frame_store_journal_decode(&record, &output));
    CHECK(output.generation == input.generation);
    CHECK(!output.displayed);
    CHECK(memcmp(output.sha256, input.sha256, sizeof(output.sha256)) == 0);
    record.bytes[20] ^= 1U;
    CHECK(!frame_store_journal_decode(&record, &output));
    return true;
}

static bool journal_selects_newest_valid_slot_with_wrap(void) {
    frame_store_journal_record_t slot0;
    frame_store_journal_record_t slot1;
    frame_store_state_t old = state(UINT32_MAX, true, 1U);
    frame_store_state_t newest = state(0U, false, 2U);
    frame_store_journal_encode(&old, &slot0);
    frame_store_journal_encode(&newest, &slot1);
    frame_store_journal_selection_t selected =
        frame_store_journal_select(&slot0, &slot1);
    CHECK(selected.valid);
    CHECK(selected.slot == 1U);
    CHECK(selected.state.generation == 0U);
    slot1.bytes[12] ^= 1U;
    selected = frame_store_journal_select(&slot0, &slot1);
    CHECK(selected.valid);
    CHECK(selected.slot == 0U);
    return true;
}

static bool displayed_state_requires_explicit_success_report(void) {
    uint8_t hash[32];
    memset(hash, 0x5a, sizeof(hash));
    frame_store_journal_selection_t none = {0};
    frame_store_state_t staged = frame_store_state_staged(&none, hash);
    CHECK(!staged.displayed);
    CHECK(staged.generation == 0U);

    frame_store_journal_selection_t selected = {
        .valid = true, .slot = 0U, .state = staged};
    frame_store_state_t displayed;
    CHECK(frame_store_state_mark_displayed(&selected, &displayed));
    CHECK(displayed.displayed);
    CHECK(displayed.generation == 1U);
    CHECK(memcmp(displayed.sha256, hash, sizeof(hash)) == 0);

    selected.state = displayed;
    CHECK(!frame_store_state_mark_displayed(&selected, &displayed));
    CHECK(!frame_store_state_mark_displayed(&none, &displayed));
    return true;
}

static bool staged_state_preserves_selected_known_good_hash(void) {
    uint8_t new_hash[32];
    uint8_t fallback_hash[32];
    memset(new_hash, 0x11, sizeof(new_hash));
    memset(fallback_hash, 0x22, sizeof(fallback_hash));
    frame_store_journal_selection_t prior = {
        .valid = true, .slot = 1U, .state = state(5U, true, 0x33U)};
    frame_store_state_t staged = frame_store_state_staged_preserving(
        &prior, new_hash, fallback_hash);
    CHECK(staged.has_previous);
    CHECK(memcmp(staged.previous_sha256, fallback_hash,
                 sizeof(fallback_hash)) == 0);
    CHECK(memcmp(staged.sha256, new_hash, sizeof(new_hash)) == 0);
    return true;
}

static bool journal_preserves_previous_frame_hash(void) {
    frame_store_state_t input = state(9U, false, 0xa5U);
    input.has_previous = true;
    memset(input.previous_sha256, 0x3c, sizeof(input.previous_sha256));
    frame_store_journal_record_t record;
    frame_store_journal_encode(&input, &record);
    frame_store_state_t output;
    CHECK(frame_store_journal_decode(&record, &output));
    CHECK(output.has_previous);
    CHECK(memcmp(output.previous_sha256, input.previous_sha256,
                 sizeof(output.previous_sha256)) == 0);
    return true;
}

int main(void) {
    if (!journal_round_trips_and_rejects_torn_record()) return EXIT_FAILURE;
    if (!staged_state_preserves_selected_known_good_hash()) return EXIT_FAILURE;
    if (!journal_preserves_previous_frame_hash()) return EXIT_FAILURE;
    if (!journal_selects_newest_valid_slot_with_wrap()) return EXIT_FAILURE;
    if (!displayed_state_requires_explicit_success_report()) return EXIT_FAILURE;
    return EXIT_SUCCESS;
}
