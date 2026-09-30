#ifndef PROVISIONING_H
#define PROVISIONING_H

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Start the protected setup SoftAP and local HTTP provisioning server.
 *
 * The setup SSID/password suffix is derived from base_mac. The caller must
 * initialize NVS flash before starting this component.
 */
esp_err_t provisioning_start(const uint8_t base_mac[6]);

/** Stop the HTTP server and Wi-Fi started by provisioning_start. */
esp_err_t provisioning_stop(void);

#ifdef __cplusplus
}
#endif

#endif
