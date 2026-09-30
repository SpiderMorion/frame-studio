#ifndef PROVISIONING_FORM_H
#define PROVISIONING_FORM_H

#include <stddef.h>

#include "config_store.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PROVISIONING_VALIDATE_OK = 0,
    PROVISIONING_VALIDATE_SSID,
    PROVISIONING_VALIDATE_WIFI_PASSWORD,
    PROVISIONING_VALIDATE_BACKEND_URL,
    PROVISIONING_VALIDATE_DEVICE_TOKEN,
} provisioning_validate_result_t;

typedef enum {
    PROVISIONING_FORM_OK = 0,
    PROVISIONING_FORM_MALFORMED,
    PROVISIONING_FORM_UNKNOWN_FIELD,
    PROVISIONING_FORM_DUPLICATE_FIELD,
    PROVISIONING_FORM_MISSING_FIELD,
    PROVISIONING_FORM_FIELD_TOO_LONG,
} provisioning_form_result_t;

typedef enum {
    PROVISIONING_ESCAPE_OK = 0,
    PROVISIONING_ESCAPE_OUTPUT_TOO_SMALL,
    PROVISIONING_ESCAPE_INVALID_ARGUMENT,
} provisioning_escape_result_t;

#define PROVISIONING_AP_SSID_LEN 16U
#define PROVISIONING_AP_PASSWORD_LEN 12U

typedef struct {
    char ssid[PROVISIONING_AP_SSID_LEN + 1U];
    char password[PROVISIONING_AP_PASSWORD_LEN + 1U];
} provisioning_ap_identity_t;

provisioning_validate_result_t provisioning_validate_credentials(
    const config_store_credentials_t *credentials);
provisioning_form_result_t provisioning_parse_form(const char *body, size_t body_length,
                                                   config_store_credentials_t *credentials);
provisioning_escape_result_t provisioning_html_escape(const char *input, char *output,
                                                       size_t output_capacity);
void provisioning_derive_ap_identity(const unsigned char base_mac[6],
                                     provisioning_ap_identity_t *identity);

#ifdef __cplusplus
}
#endif

#endif
