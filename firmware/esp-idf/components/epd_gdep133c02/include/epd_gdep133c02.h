#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define GDEP133C02_WIDTH 1200U
#define GDEP133C02_HEIGHT 1600U
#define GDEP133C02_FRAME_BYTES 960000U

typedef esp_err_t (*gdep133c02_read_cb_t)(size_t offset, uint8_t *destination,
                                          size_t length, void *context);

/** Configure the verified board transport and run the vendor init sequence. */
esp_err_t gdep133c02_init(void);

/** Stream exactly one native 960000-byte frame and power off after refresh. */
esp_err_t gdep133c02_display(gdep133c02_read_cb_t read_cb, void *read_context);

/** Display a native frame from a file path. */
esp_err_t gdep133c02_display_file(const char *path);

#ifdef __cplusplus
}
#endif
