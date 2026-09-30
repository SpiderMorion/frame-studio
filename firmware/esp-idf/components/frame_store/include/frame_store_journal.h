#pragma once

#include <stdbool.h>
#include <stdint.h>

#define FRAME_STORE_JOURNAL_RECORD_SIZE 80U

typedef struct {
    uint32_t generation;
    bool displayed;
    bool has_previous;
    uint8_t sha256[32];
    uint8_t previous_sha256[32];
} frame_store_state_t;

typedef struct {
    uint8_t bytes[FRAME_STORE_JOURNAL_RECORD_SIZE];
} frame_store_journal_record_t;

typedef struct {
    bool valid;
    uint8_t slot;
    frame_store_state_t state;
} frame_store_journal_selection_t;

uint32_t frame_store_crc32(const uint8_t *data, uint32_t size);
void frame_store_journal_encode(const frame_store_state_t *state,
                                frame_store_journal_record_t *record);
bool frame_store_journal_decode(const frame_store_journal_record_t *record,
                                frame_store_state_t *state);
frame_store_journal_selection_t frame_store_journal_select(
    const frame_store_journal_record_t *slot0,
    const frame_store_journal_record_t *slot1);
frame_store_state_t frame_store_state_staged(
    const frame_store_journal_selection_t *prior, const uint8_t sha256[32]);
frame_store_state_t frame_store_state_staged_preserving(
    const frame_store_journal_selection_t *prior, const uint8_t sha256[32],
    const uint8_t previous_sha256[32]);
bool frame_store_state_mark_displayed(
    const frame_store_journal_selection_t *prior, frame_store_state_t *next);
