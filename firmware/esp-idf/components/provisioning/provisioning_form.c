#include "provisioning_form.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

static int hex_value(char value) {
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

static provisioning_form_result_t decode_value(const char *encoded, size_t encoded_length,
                                                char *decoded, size_t decoded_capacity) {
    size_t output_index = 0U;
    for (size_t index = 0U; index < encoded_length; ++index) {
        unsigned char value = (unsigned char)encoded[index];
        if (value == '%') {
            if (index + 2U >= encoded_length) {
                return PROVISIONING_FORM_MALFORMED;
            }
            const int high = hex_value(encoded[index + 1U]);
            const int low = hex_value(encoded[index + 2U]);
            if (high < 0 || low < 0) {
                return PROVISIONING_FORM_MALFORMED;
            }
            value = (unsigned char)((high << 4) | low);
            index += 2U;
        } else if (value == '+') {
            value = ' ';
        }
        if (value == '\0') {
            return PROVISIONING_FORM_MALFORMED;
        }
        if (output_index + 1U >= decoded_capacity) {
            return PROVISIONING_FORM_FIELD_TOO_LONG;
        }
        decoded[output_index++] = (char)value;
    }
    decoded[output_index] = '\0';
    return PROVISIONING_FORM_OK;
}

provisioning_form_result_t provisioning_parse_form(const char *body, size_t body_length,
                                                   config_store_credentials_t *credentials) {
    if (body == NULL || credentials == NULL) {
        return PROVISIONING_FORM_MALFORMED;
    }
    memset(credentials, 0, sizeof(*credentials));
    if (body_length > 0U && body[body_length - 1U] == '&') {
        return PROVISIONING_FORM_MALFORMED;
    }
    unsigned fields = 0U;
    size_t pair_start = 0U;
    while (pair_start < body_length) {
        size_t pair_end = pair_start;
        while (pair_end < body_length && body[pair_end] != '&') {
            ++pair_end;
        }
        size_t equals = pair_start;
        while (equals < pair_end && body[equals] != '=') {
            ++equals;
        }
        if (equals == pair_start || equals == pair_end) {
            memset(credentials, 0, sizeof(*credentials));
            return PROVISIONING_FORM_MALFORMED;
        }

        const char *key = body + pair_start;
        const size_t key_length = equals - pair_start;
        char *destination = NULL;
        size_t capacity = 0U;
        unsigned field_bit = 0U;
        if (key_length == 4U && memcmp(key, "ssid", 4U) == 0) {
            destination = credentials->wifi_ssid;
            capacity = sizeof(credentials->wifi_ssid);
            field_bit = 1U;
        } else if (key_length == 8U && memcmp(key, "password", 8U) == 0) {
            destination = credentials->wifi_password;
            capacity = sizeof(credentials->wifi_password);
            field_bit = 2U;
        } else if (key_length == 11U && memcmp(key, "backend_url", 11U) == 0) {
            destination = credentials->backend_base_url;
            capacity = sizeof(credentials->backend_base_url);
            field_bit = 4U;
        } else if (key_length == 5U && memcmp(key, "token", 5U) == 0) {
            destination = credentials->device_bearer_token;
            capacity = sizeof(credentials->device_bearer_token);
            field_bit = 8U;
        } else {
            memset(credentials, 0, sizeof(*credentials));
            return PROVISIONING_FORM_UNKNOWN_FIELD;
        }
        if ((fields & field_bit) != 0U) {
            memset(credentials, 0, sizeof(*credentials));
            return PROVISIONING_FORM_DUPLICATE_FIELD;
        }
        const provisioning_form_result_t result =
            decode_value(body + equals + 1U, pair_end - equals - 1U, destination, capacity);
        if (result != PROVISIONING_FORM_OK) {
            memset(credentials, 0, sizeof(*credentials));
            return result;
        }
        fields |= field_bit;
        pair_start = pair_end + 1U;
    }
    if (fields != 15U) {
        memset(credentials, 0, sizeof(*credentials));
        return PROVISIONING_FORM_MISSING_FIELD;
    }
    return PROVISIONING_FORM_OK;
}

provisioning_escape_result_t provisioning_html_escape(const char *input, char *output,
                                                       size_t output_capacity) {
    if (input == NULL || output == NULL || output_capacity == 0U) {
        return PROVISIONING_ESCAPE_INVALID_ARGUMENT;
    }
    size_t used = 0U;
    output[0] = '\0';
    while (*input != '\0') {
        const char *replacement = NULL;
        switch (*input) {
            case '&': replacement = "&amp;"; break;
            case '<': replacement = "&lt;"; break;
            case '>': replacement = "&gt;"; break;
            case '\"': replacement = "&quot;"; break;
            case '\'': replacement = "&#39;"; break;
            default: break;
        }
        const size_t amount = replacement == NULL ? 1U : strlen(replacement);
        if (used + amount >= output_capacity) {
            output[0] = '\0';
            return PROVISIONING_ESCAPE_OUTPUT_TOO_SMALL;
        }
        if (replacement == NULL) {
            output[used++] = *input;
        } else {
            memcpy(output + used, replacement, amount);
            used += amount;
        }
        ++input;
    }
    output[used] = '\0';
    return PROVISIONING_ESCAPE_OK;
}

void provisioning_derive_ap_identity(const unsigned char base_mac[6],
                                     provisioning_ap_identity_t *identity) {
    static const char hex[] = "0123456789ABCDEF";
    static const char ssid_prefix[] = "EInkFrame-";
    static const char password_prefix[] = "setup-";
    if (base_mac == NULL || identity == NULL) {
        return;
    }
    memcpy(identity->ssid, ssid_prefix, sizeof(ssid_prefix) - 1U);
    memcpy(identity->password, password_prefix, sizeof(password_prefix) - 1U);
    for (size_t index = 0U; index < 3U; ++index) {
        const unsigned char byte = base_mac[index + 3U];
        identity->ssid[10U + index * 2U] = hex[byte >> 4U];
        identity->ssid[11U + index * 2U] = hex[byte & 0x0FU];
        identity->password[6U + index * 2U] = hex[byte >> 4U];
        identity->password[7U + index * 2U] = hex[byte & 0x0FU];
    }
    identity->ssid[PROVISIONING_AP_SSID_LEN] = '\0';
    identity->password[PROVISIONING_AP_PASSWORD_LEN] = '\0';
}

static bool terminated_within(const char *value, size_t capacity, size_t *length) {
    for (size_t index = 0; index < capacity; ++index) {
        if (value[index] == '\0') {
            if (length != NULL) {
                *length = index;
            }
            return true;
        }
    }
    return false;
}

static bool parse_decimal(const char **cursor, const char *end, unsigned maximum,
                          unsigned *value) {
    const char *start = *cursor;
    unsigned result = 0U;
    while (*cursor < end && **cursor >= '0' && **cursor <= '9') {
        result = result * 10U + (unsigned)(**cursor - '0');
        if (result > maximum) {
            return false;
        }
        ++*cursor;
    }
    if (*cursor == start || (*cursor - start > 1 && *start == '0')) {
        return false;
    }
    *value = result;
    return true;
}

static bool private_backend_url(const char *url) {
    static const char prefix[] = "http://";
    for (const unsigned char *character = (const unsigned char *)url; *character != '\0';
         ++character) {
        if (*character <= 0x20U || *character >= 0x7FU) {
            return false;
        }
    }
    if (strncmp(url, prefix, sizeof(prefix) - 1U) != 0) {
        return false;
    }

    const char *cursor = url + sizeof(prefix) - 1U;
    const char *authority_end = cursor;
    while (*authority_end != '\0' && *authority_end != '/') {
        if (*authority_end == '@' || *authority_end == '?' || *authority_end == '#') {
            return false;
        }
        ++authority_end;
    }

    unsigned octets[4] = {0};
    for (size_t index = 0; index < 4U; ++index) {
        if (!parse_decimal(&cursor, authority_end, 255U, &octets[index])) {
            return false;
        }
        if (index < 3U) {
            if (cursor == authority_end || *cursor != '.') {
                return false;
            }
            ++cursor;
        }
    }

    if (cursor < authority_end && *cursor == ':') {
        ++cursor;
        unsigned port = 0U;
        if (!parse_decimal(&cursor, authority_end, 65535U, &port) || port == 0U) {
            return false;
        }
    }
    if (cursor != authority_end) {
        return false;
    }

    for (const char *path = authority_end; *path != '\0'; ++path) {
        if (*path == '?' || *path == '#' || *path == '\\' || *path == ' ') {
            return false;
        }
    }

    return octets[0] == 10U ||
           (octets[0] == 172U && octets[1] >= 16U && octets[1] <= 31U) ||
           (octets[0] == 192U && octets[1] == 168U);
}

static bool bearer_token_syntax(const char *token) {
    bool padding_started = false;
    for (const unsigned char *character = (const unsigned char *)token; *character != '\0';
         ++character) {
        if (*character == '=') {
            padding_started = true;
            continue;
        }
        const bool allowed = (*character >= 'A' && *character <= 'Z') ||
                             (*character >= 'a' && *character <= 'z') ||
                             (*character >= '0' && *character <= '9') ||
                             *character == '-' || *character == '.' || *character == '_' ||
                             *character == '~' || *character == '+' || *character == '/';
        if (!allowed || padding_started) {
            return false;
        }
    }
    return true;
}

provisioning_validate_result_t provisioning_validate_credentials(
    const config_store_credentials_t *credentials) {
    if (credentials == NULL) {
        return PROVISIONING_VALIDATE_SSID;
    }

    size_t length = 0U;
    if (!terminated_within(credentials->wifi_ssid, sizeof(credentials->wifi_ssid), &length) ||
        length == 0U) {
        return PROVISIONING_VALIDATE_SSID;
    }
    if (!terminated_within(credentials->wifi_password, sizeof(credentials->wifi_password),
                           &length) ||
        (length != 0U && length < 8U)) {
        return PROVISIONING_VALIDATE_WIFI_PASSWORD;
    }
    if (!terminated_within(credentials->backend_base_url,
                           sizeof(credentials->backend_base_url), &length) ||
        length == 0U || !private_backend_url(credentials->backend_base_url)) {
        return PROVISIONING_VALIDATE_BACKEND_URL;
    }
    if (!terminated_within(credentials->device_bearer_token,
                           sizeof(credentials->device_bearer_token), &length) ||
        length == 0U || !bearer_token_syntax(credentials->device_bearer_token)) {
        return PROVISIONING_VALIDATE_DEVICE_TOKEN;
    }
    return PROVISIONING_VALIDATE_OK;
}
