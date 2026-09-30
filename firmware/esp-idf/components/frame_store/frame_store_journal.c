#include "frame_store_journal.h"

#include <string.h>

#define CRC_OFFSET 76U

static void store_u32(uint8_t *bytes, uint32_t value) {
    for (uint32_t i = 0; i < 4U; ++i) {
        bytes[i] = (uint8_t)(value >> (i * 8U));
    }
}

static uint32_t load_u32(const uint8_t *bytes) {
    uint32_t value = 0;
    for (uint32_t i = 0; i < 4U; ++i) {
        value |= (uint32_t)bytes[i] << (i * 8U);
    }
    return value;
}

uint32_t frame_store_crc32(const uint8_t *data, uint32_t size) {
    uint32_t crc = UINT32_MAX;
    for (uint32_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (uint8_t bit = 0; bit < 8U; ++bit) {
            const uint32_t mask = 0U - (crc & 1U);
            crc = (crc >> 1U) ^ (0xedb88320U & mask);
        }
    }
    return ~crc;
}

void frame_store_journal_encode(const frame_store_state_t *state,
                                frame_store_journal_record_t *record) {
    memset(record, 0, sizeof(*record));
    memcpy(record->bytes, "FSTR", 4U);
    record->bytes[4] = 1U;
    record->bytes[5] = (state->displayed ? 1U : 0U) |
                       (state->has_previous ? 2U : 0U);
    store_u32(&record->bytes[8], state->generation);
    memcpy(&record->bytes[12], state->sha256, sizeof(state->sha256));
    memcpy(&record->bytes[44], state->previous_sha256,
           sizeof(state->previous_sha256));
    store_u32(&record->bytes[CRC_OFFSET],
              frame_store_crc32(record->bytes, CRC_OFFSET));
}

bool frame_store_journal_decode(const frame_store_journal_record_t *record,
                                frame_store_state_t *state) {
    if (memcmp(record->bytes, "FSTR", 4U) != 0 || record->bytes[4] != 1U ||
        (record->bytes[5] & 0xfcU) != 0U ||
        load_u32(&record->bytes[CRC_OFFSET]) !=
            frame_store_crc32(record->bytes, CRC_OFFSET)) {
        return false;
    }
    state->displayed = (record->bytes[5] & 1U) != 0U;
    state->has_previous = (record->bytes[5] & 2U) != 0U;
    state->generation = load_u32(&record->bytes[8]);
    memcpy(state->sha256, &record->bytes[12], sizeof(state->sha256));
    memcpy(state->previous_sha256, &record->bytes[44],
           sizeof(state->previous_sha256));
    return true;
}

frame_store_journal_selection_t frame_store_journal_select(
    const frame_store_journal_record_t *slot0,
    const frame_store_journal_record_t *slot1) {
    frame_store_journal_selection_t first = {.slot = 0U};
    frame_store_journal_selection_t second = {.slot = 1U};
    first.valid = frame_store_journal_decode(slot0, &first.state);
    second.valid = frame_store_journal_decode(slot1, &second.state);
    if (!first.valid) {
        return second;
    }
    if (!second.valid) {
        return first;
    }
    const uint32_t difference = second.state.generation - first.state.generation;
    return (difference == 0U || difference < 0x80000000U) ? second : first;
}

frame_store_state_t frame_store_state_staged_preserving(
    const frame_store_journal_selection_t *prior, const uint8_t sha256[32],
    const uint8_t previous_sha256[32]) {
    frame_store_state_t next = {
        .generation = prior->valid ? prior->state.generation + 1U : 0U,
        .displayed = false,
        .has_previous = previous_sha256 != NULL,
    };
    memcpy(next.sha256, sha256, sizeof(next.sha256));
    if (previous_sha256 != NULL) {
        memcpy(next.previous_sha256, previous_sha256,
               sizeof(next.previous_sha256));
    }
    return next;
}

frame_store_state_t frame_store_state_staged(
    const frame_store_journal_selection_t *prior, const uint8_t sha256[32]) {
    return frame_store_state_staged_preserving(
        prior, sha256, prior->valid ? prior->state.sha256 : NULL);
}

bool frame_store_state_mark_displayed(
    const frame_store_journal_selection_t *prior, frame_store_state_t *next) {
    if (!prior->valid || prior->state.displayed) {
        return false;
    }
    *next = prior->state;
    next->generation += 1U;
    next->displayed = true;
    return true;
}
