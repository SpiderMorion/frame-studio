#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "backend_client.h"

#define VALID_MANIFEST \
    "{\"schema_version\":1,\"target\":\"weather-frame\",\"panel\":\"GDEP133C02\"," \
    "\"sequence\":1770000000,\"frame_id\":\"01234567-89ab-4def-8abc-0123456789ab\"," \
    "\"planned_display_at\":\"2026-08-23T08:00:00Z\",\"byte_length\":960000," \
    "\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\"," \
    "\"profile\":\"gdep133c02-native-transverse-v1\"," \
    "\"url\":\"/api/v1/frames/01234567-89ab-4def-8abc-0123456789ab/frame.bin\"}"

static int failures;

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #condition); \
        ++failures; \
    } \
} while (0)

static void test_valid_manifest(void) {
    backend_manifest_t result = {0};
    CHECK(backend_manifest_parse(VALID_MANIFEST, strlen(VALID_MANIFEST), &result) == ESP_OK);
    CHECK(result.sequence == UINT32_C(1770000000));
    CHECK(result.byte_length == BACKEND_FRAME_BYTE_LENGTH);
    CHECK(strcmp(result.frame_id, "01234567-89ab-4def-8abc-0123456789ab") == 0);
    CHECK(strcmp(result.planned_display_at, "2026-08-23T08:00:00Z") == 0);
    CHECK(strcmp(result.sha256, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef") == 0);
    CHECK(strcmp(result.url, "/api/v1/frames/01234567-89ab-4def-8abc-0123456789ab/frame.bin") == 0);
}

static void expect_invalid_replacement(const char *needle, const char *replacement) {
    char json[BACKEND_MANIFEST_MAX_JSON + 1];
    const char *position = strstr(VALID_MANIFEST, needle);
    CHECK(position != NULL);
    if (position == NULL) return;
    const size_t prefix = (size_t)(position - VALID_MANIFEST);
    const int count = snprintf(json, sizeof(json), "%.*s%s%s", (int)prefix, VALID_MANIFEST,
                               replacement, position + strlen(needle));
    CHECK(count > 0 && (size_t)count < sizeof(json));
    backend_manifest_t result;
    CHECK(backend_manifest_parse(json, strlen(json), &result) == ESP_ERR_INVALID_RESPONSE);
}

static void test_rejects_wrong_fixed_schema_values(void) {
    expect_invalid_replacement("\"schema_version\":1", "\"schema_version\":2");
    expect_invalid_replacement("weather-frame", "other-target");
    expect_invalid_replacement("GDEP133C02", "GDEP133C01");
    expect_invalid_replacement("gdep133c02-native-transverse-v1", "spectra6-vendor-v1");
    expect_invalid_replacement("\"byte_length\":960000", "\"byte_length\":959999");
}

static void test_rejects_invalid_sequence(void) {
    expect_invalid_replacement("1770000000", "0");
    expect_invalid_replacement("1770000000", "4294967296");
    expect_invalid_replacement("1770000000", "1.5");
}

static void test_rejects_invalid_identity_and_location(void) {
    expect_invalid_replacement("01234567-89ab-4def-8abc-0123456789ab\",\"planned", "not-a-uuid\",\"planned");
    expect_invalid_replacement("2026-08-23T08:00:00Z", "2026-08-23 08:00:00");
    expect_invalid_replacement("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "A123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
    expect_invalid_replacement("/api/v1/frames/01234567-89ab-4def-8abc-0123456789ab/frame.bin", "https://evil.invalid/frame.bin");
    expect_invalid_replacement("/api/v1/frames/01234567-89ab-4def-8abc-0123456789ab/frame.bin", "/api/v1/frames/ffffffff-ffff-4fff-8fff-ffffffffffff/frame.bin");
}

static void test_rejects_missing_unknown_and_duplicate_fields(void) {
    expect_invalid_replacement("\"target\":\"weather-frame\",", "");
    expect_invalid_replacement("\"target\":\"weather-frame\",", "\"target\":\"weather-frame\",\"extra\":1,");
    expect_invalid_replacement("\"target\":\"weather-frame\",", "\"target\":\"weather-frame\",\"target\":\"weather-frame\",");
}

static void test_rejects_malformed_or_oversized_input(void) {
    backend_manifest_t result;
    CHECK(backend_manifest_parse("{} trailing", 11, &result) == ESP_ERR_INVALID_RESPONSE);
    CHECK(backend_manifest_parse(NULL, 0, &result) == ESP_ERR_INVALID_ARG);
    CHECK(backend_manifest_parse(VALID_MANIFEST, strlen(VALID_MANIFEST), NULL) == ESP_ERR_INVALID_ARG);
    char *large = calloc(BACKEND_MANIFEST_MAX_JSON + 1, 1);
    CHECK(large != NULL);
    if (large != NULL) {
        memset(large, ' ', BACKEND_MANIFEST_MAX_JSON + 1);
        CHECK(backend_manifest_parse(large, BACKEND_MANIFEST_MAX_JSON + 1, &result) == ESP_ERR_INVALID_SIZE);
        free(large);
    }
}

int main(void) {
    test_valid_manifest();
    test_rejects_wrong_fixed_schema_values();
    test_rejects_invalid_sequence();
    test_rejects_invalid_identity_and_location();
    test_rejects_missing_unknown_and_duplicate_fields();
    test_rejects_malformed_or_oversized_input();
    if (failures != 0) {
        fprintf(stderr, "%d test assertion(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    puts("backend manifest parser tests passed");
    return EXIT_SUCCESS;
}
