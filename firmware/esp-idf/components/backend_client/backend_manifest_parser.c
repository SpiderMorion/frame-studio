#include "backend_client.h"

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"

#define MANIFEST_FIELD_COUNT 10U

static bool is_lower_hex(char value) {
    return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
}

static bool valid_uuid(const char *value) {
    if (value == NULL || strlen(value) != 36U) return false;
    for (size_t index = 0; index < 36U; ++index) {
        if (index == 8U || index == 13U || index == 18U || index == 23U) {
            if (value[index] != '-') return false;
        } else if (!is_lower_hex(value[index])) {
            return false;
        }
    }
    return true;
}

static bool valid_sha256(const char *value) {
    if (value == NULL || strlen(value) != 64U) return false;
    for (size_t index = 0; index < 64U; ++index) {
        if (!is_lower_hex(value[index])) return false;
    }
    return true;
}

static bool digits(const char *value, size_t count) {
    for (size_t index = 0; index < count; ++index) {
        if (value[index] < '0' || value[index] > '9') return false;
    }
    return true;
}

static unsigned two_digits(const char *value) {
    return (unsigned)(value[0] - '0') * 10U + (unsigned)(value[1] - '0');
}

static bool valid_datetime(const char *value) {
    if (value == NULL) return false;
    const size_t length = strlen(value);
    if (length < 20U || length >= sizeof(((backend_manifest_t *)0)->planned_display_at)) return false;
    if (!digits(value, 4U) || value[4] != '-' || !digits(value + 5, 2U) ||
        value[7] != '-' || !digits(value + 8, 2U) || value[10] != 'T' ||
        !digits(value + 11, 2U) || value[13] != ':' || !digits(value + 14, 2U) ||
        value[16] != ':' || !digits(value + 17, 2U)) return false;
    const unsigned month = two_digits(value + 5);
    const unsigned day = two_digits(value + 8);
    if (month < 1U || month > 12U || day < 1U || day > 31U ||
        two_digits(value + 11) > 23U || two_digits(value + 14) > 59U ||
        two_digits(value + 17) > 60U) return false;
    size_t index = 19U;
    if (index < length && value[index] == '.') {
        ++index;
        const size_t fraction_start = index;
        while (index < length && value[index] >= '0' && value[index] <= '9') ++index;
        if (index == fraction_start) return false;
    }
    if (index < length && value[index] == 'Z') return index + 1U == length;
    if (index + 6U != length || (value[index] != '+' && value[index] != '-') ||
        !digits(value + index + 1U, 2U) || value[index + 3U] != ':' ||
        !digits(value + index + 4U, 2U)) return false;
    return two_digits(value + index + 1U) <= 23U && two_digits(value + index + 4U) <= 59U;
}

static bool valid_url(const char *url, const char *frame_id) {
    char expected[96];
    const int written = snprintf(expected, sizeof(expected),
                                 "/api/v1/frames/%s/frame.bin", frame_id);
    return written > 0 && (size_t)written < sizeof(expected) && strcmp(url, expected) == 0;
}

static bool field_name_allowed(const char *name) {
    static const char *const names[] = {
        "schema_version", "target", "panel", "sequence", "frame_id",
        "planned_display_at", "byte_length", "sha256", "profile", "url",
    };
    for (size_t index = 0; index < MANIFEST_FIELD_COUNT; ++index) {
        if (strcmp(name, names[index]) == 0) return true;
    }
    return false;
}

static bool exact_object_shape(const cJSON *root) {
    size_t count = 0U;
    for (const cJSON *item = root->child; item != NULL; item = item->next) {
        if (item->string == NULL || !field_name_allowed(item->string)) return false;
        for (const cJSON *other = item->next; other != NULL; other = other->next) {
            if (other->string != NULL && strcmp(item->string, other->string) == 0) return false;
        }
        ++count;
    }
    return count == MANIFEST_FIELD_COUNT;
}

static const char *required_string(const cJSON *root, const char *name) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsString(item) && item->valuestring != NULL ? item->valuestring : NULL;
}

static bool exact_number(const cJSON *root, const char *name, double expected) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsNumber(item) && item->valuedouble == expected;
}

esp_err_t backend_manifest_parse(const char *json, size_t length,
                                 backend_manifest_t *manifest) {
    if (json == NULL || manifest == NULL) return ESP_ERR_INVALID_ARG;
    if (length == 0U || length > BACKEND_MANIFEST_MAX_JSON) return ESP_ERR_INVALID_SIZE;

    const char *parse_end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(json, length, &parse_end, false);
    if (root == NULL || !cJSON_IsObject(root) || parse_end != json + length ||
        !exact_object_shape(root)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    const cJSON *sequence_item = cJSON_GetObjectItemCaseSensitive(root, "sequence");
    const char *target = required_string(root, "target");
    const char *panel = required_string(root, "panel");
    const char *frame_id = required_string(root, "frame_id");
    const char *planned = required_string(root, "planned_display_at");
    const char *sha256 = required_string(root, "sha256");
    const char *profile = required_string(root, "profile");
    const char *url = required_string(root, "url");

    const bool sequence_valid = cJSON_IsNumber(sequence_item) &&
        isfinite(sequence_item->valuedouble) && sequence_item->valuedouble >= 1.0 &&
        sequence_item->valuedouble <= 4294967295.0 &&
        floor(sequence_item->valuedouble) == sequence_item->valuedouble;
    const bool valid = exact_number(root, "schema_version", 1.0) &&
        target != NULL && strcmp(target, "weather-frame") == 0 &&
        panel != NULL && strcmp(panel, "GDEP133C02") == 0 && sequence_valid &&
        frame_id != NULL && valid_uuid(frame_id) && valid_datetime(planned) &&
        exact_number(root, "byte_length", (double)BACKEND_FRAME_BYTE_LENGTH) &&
        valid_sha256(sha256) && profile != NULL &&
        strcmp(profile, "gdep133c02-native-transverse-v1") == 0 &&
        url != NULL && valid_url(url, frame_id);
    if (!valid) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    backend_manifest_t parsed = {0};
    parsed.sequence = (uint32_t)sequence_item->valuedouble;
    parsed.byte_length = BACKEND_FRAME_BYTE_LENGTH;
    (void)snprintf(parsed.frame_id, sizeof(parsed.frame_id), "%s", frame_id);
    (void)snprintf(parsed.planned_display_at, sizeof(parsed.planned_display_at), "%s", planned);
    (void)snprintf(parsed.sha256, sizeof(parsed.sha256), "%s", sha256);
    (void)snprintf(parsed.url, sizeof(parsed.url), "%s", url);
    *manifest = parsed;
    cJSON_Delete(root);
    return ESP_OK;
}
