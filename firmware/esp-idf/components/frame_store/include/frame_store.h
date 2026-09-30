#pragma once

#include "esp_err.h"
#include "frame_store_journal.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FRAME_STORE_FRAME_BYTES 960000U
#define FRAME_STORE_MOUNT_POINT "/sd"
#define FRAME_STORE_DOWNLOAD_PATH "/sd/download.tmp"
#define FRAME_STORE_CURRENT_PATH "/sd/current.bin"
#define FRAME_STORE_PREVIOUS_PATH "/sd/previous.bin"

typedef struct {
    const char *path;
    uint8_t sha256[32];
    bool displayed;
} frame_store_recovery_result_t;

/* Uses the vendor board wiring: CS15, MOSI3, MISO5, SCK8. */
esp_err_t frame_store_mount(void);
esp_err_t frame_store_unmount(void);

/* Reopens and reads the closed file; size must be exactly 960000 bytes. */
esp_err_t frame_store_validate_file(const char *path,
                                    const uint8_t expected_sha256[32],
                                    uint8_t actual_sha256[32]);
esp_err_t frame_store_validate_download(
    const uint8_t expected_sha256[32]);

/* Validates download.tmp, journals staged state, then rotates and promotes. */
esp_err_t frame_store_promote(const uint8_t expected_sha256[32]);

/* Returns a SHA-verified current frame, otherwise a SHA-verified previous. */
esp_err_t frame_store_recover(frame_store_recovery_result_t *result);

/* Call only after panel refresh succeeds; hash binds the report to its frame. */
esp_err_t frame_store_mark_displayed(
    const uint8_t displayed_sha256[32]);

#ifdef __cplusplus
}
#endif
