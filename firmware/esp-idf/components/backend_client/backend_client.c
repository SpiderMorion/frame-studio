#include "backend_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "cJSON.h"
#include "esp_http_client.h"
#include "mbedtls/sha256.h"

#define HTTP_READ_CHUNK 4096U

typedef struct {
    char *etag;
    size_t etag_size;
    esp_err_t error;
} header_context_t;

static esp_err_t header_event(esp_http_client_event_t *event) {
    if (event->event_id != HTTP_EVENT_ON_HEADER || event->header_key == NULL ||
        strcasecmp(event->header_key, "ETag") != 0) return ESP_OK;
    header_context_t *context = event->user_data;
    if (context == NULL || context->etag == NULL || context->etag_size == 0U) return ESP_OK;
    const size_t length = strlen(event->header_value);
    if (length >= context->etag_size) {
        context->error = ESP_ERR_INVALID_SIZE;
        return ESP_FAIL;
    }
    memcpy(context->etag, event->header_value, length + 1U);
    return ESP_OK;
}

static char *make_url(const backend_client_config_t *config, const char *path) {
    if (config == NULL || config->base_url == NULL || config->bearer_token == NULL ||
        path == NULL || path[0] != '/') return NULL;
    const size_t base_length = strlen(config->base_url);
    const size_t path_length = strlen(path);
    if (base_length == 0U || base_length > 512U || path_length > 256U ||
        base_length > SIZE_MAX - path_length - 1U) return NULL;
    const bool trim_slash = config->base_url[base_length - 1U] == '/';
    char *url = malloc(base_length + path_length + 1U);
    if (url == NULL) return NULL;
    memcpy(url, config->base_url, base_length - (trim_slash ? 1U : 0U));
    strcpy(url + base_length - (trim_slash ? 1U : 0U), path);
    return url;
}

static esp_err_t set_auth(esp_http_client_handle_t client,
                          const backend_client_config_t *config) {
    const size_t token_length = strlen(config->bearer_token);
    if (token_length == 0U || token_length > 1024U) return ESP_ERR_INVALID_ARG;
    char *authorization = malloc(token_length + sizeof("Bearer "));
    if (authorization == NULL) return ESP_ERR_NO_MEM;
    memcpy(authorization, "Bearer ", sizeof("Bearer ") - 1U);
    memcpy(authorization + sizeof("Bearer ") - 1U, config->bearer_token, token_length + 1U);
    const esp_err_t result = esp_http_client_set_header(client, "Authorization", authorization);
    memset(authorization, 0, token_length + sizeof("Bearer "));
    free(authorization);
    return result;
}

static esp_http_client_handle_t open_client(const backend_client_config_t *config,
                                            const char *path,
                                            esp_http_client_method_t method,
                                            esp_err_t (*event_handler)(esp_http_client_event_t *),
                                            void *user_data,
                                            char **allocated_url) {
    *allocated_url = make_url(config, path);
    if (*allocated_url == NULL) return NULL;
    const esp_http_client_config_t http_config = {
        .url = *allocated_url,
        .method = method,
        .timeout_ms = config->timeout_ms > 0 ? config->timeout_ms : 30000,
        .event_handler = event_handler,
        .user_data = user_data,
        .buffer_size = (int)HTTP_READ_CHUNK,
        .buffer_size_tx = 1024,
    };
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (client == NULL || set_auth(client, config) != ESP_OK) {
        if (client != NULL) esp_http_client_cleanup(client);
        free(*allocated_url);
        *allocated_url = NULL;
        return NULL;
    }
    return client;
}

esp_err_t backend_client_get_manifest(const backend_client_config_t *config,
                                      const char *etag,
                                      backend_manifest_t *manifest,
                                      char *response_etag,
                                      size_t response_etag_size,
                                      backend_manifest_result_t *result) {
    if (config == NULL || manifest == NULL || result == NULL ||
        response_etag == NULL || response_etag_size == 0U) return ESP_ERR_INVALID_ARG;
    response_etag[0] = '\0';
    header_context_t headers = {.etag = response_etag, .etag_size = response_etag_size,
                                .error = ESP_OK};
    char *url = NULL;
    esp_http_client_handle_t client = open_client(config, "/api/v1/device/manifest",
                                                   HTTP_METHOD_GET, header_event,
                                                   &headers, &url);
    if (client == NULL) return ESP_ERR_INVALID_ARG;
    esp_err_t error = ESP_OK;
    if (etag != NULL && etag[0] != '\0') {
        error = esp_http_client_set_header(client, "If-None-Match", etag);
    }
    if (error == ESP_OK) error = esp_http_client_open(client, 0);
    if (error == ESP_OK && esp_http_client_fetch_headers(client) < 0) error = ESP_FAIL;
    const int status = error == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    if (error == ESP_OK && headers.error != ESP_OK) error = headers.error;
    if (error == ESP_OK && status == 304) {
        *result = BACKEND_MANIFEST_NOT_MODIFIED;
    } else if (error == ESP_OK && status == 204) {
        *result = BACKEND_MANIFEST_NO_CONTENT;
    } else if (error == ESP_OK && status == 200) {
        char body[BACKEND_MANIFEST_MAX_JSON + 1U];
        size_t total = 0U;
        while (total <= BACKEND_MANIFEST_MAX_JSON) {
            const int read_count = esp_http_client_read(client, body + total,
                BACKEND_MANIFEST_MAX_JSON + 1U - total);
            if (read_count < 0) { error = ESP_FAIL; break; }
            if (read_count == 0) break;
            total += (size_t)read_count;
        }
        if (error == ESP_OK && total > BACKEND_MANIFEST_MAX_JSON) error = ESP_ERR_INVALID_SIZE;
        if (error == ESP_OK) error = backend_manifest_parse(body, total, manifest);
        if (error == ESP_OK) *result = BACKEND_MANIFEST_UPDATED;
    } else if (error == ESP_OK) {
        error = ESP_ERR_HTTP_BASE + status;
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    free(url);
    return error;
}

static bool digest_matches(const uint8_t digest[32], const char expected[65]) {
    static const char hex[] = "0123456789abcdef";
    unsigned difference = 0U;
    for (size_t index = 0; index < 32U; ++index) {
        difference |= (unsigned)(hex[digest[index] >> 4U] ^ expected[index * 2U]);
        difference |= (unsigned)(hex[digest[index] & 0x0FU] ^ expected[index * 2U + 1U]);
    }
    return difference == 0U;
}

esp_err_t backend_client_download_frame(const backend_client_config_t *config,
                                        const backend_manifest_t *manifest,
                                        backend_frame_write_cb_t write_cb,
                                        void *write_context) {
    if (config == NULL || manifest == NULL || write_cb == NULL ||
        manifest->byte_length != BACKEND_FRAME_BYTE_LENGTH) return ESP_ERR_INVALID_ARG;
    char *url = NULL;
    esp_http_client_handle_t client = open_client(config, manifest->url, HTTP_METHOD_GET,
                                                   NULL, NULL, &url);
    if (client == NULL) return ESP_ERR_INVALID_ARG;
    esp_err_t error = esp_http_client_open(client, 0);
    const int64_t content_length = error == ESP_OK ? esp_http_client_fetch_headers(client) : -1;
    if (error == ESP_OK && esp_http_client_get_status_code(client) != 200) {
        error = ESP_ERR_INVALID_RESPONSE;
    }
    if (error == ESP_OK && content_length != (int64_t)manifest->byte_length) {
        error = ESP_ERR_INVALID_SIZE;
    }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    if (error == ESP_OK && mbedtls_sha256_starts(&sha, 0) != 0) error = ESP_FAIL;
    uint8_t buffer[HTTP_READ_CHUNK];
    size_t total = 0U;
    while (error == ESP_OK && total < manifest->byte_length) {
        size_t wanted = manifest->byte_length - total;
        if (wanted > sizeof(buffer)) wanted = sizeof(buffer);
        const int read_count = esp_http_client_read(client, (char *)buffer, (int)wanted);
        if (read_count <= 0) { error = ESP_ERR_INVALID_SIZE; break; }
        if (mbedtls_sha256_update(&sha, buffer, (size_t)read_count) != 0) {
            error = ESP_FAIL;
            break;
        }
        error = write_cb(buffer, (size_t)read_count, write_context);
        total += (size_t)read_count;
    }
    uint8_t digest[32];
    if (error == ESP_OK && total == manifest->byte_length &&
        mbedtls_sha256_finish(&sha, digest) != 0) error = ESP_FAIL;
    if (error == ESP_OK && !digest_matches(digest, manifest->sha256)) {
        error = ESP_ERR_INVALID_CRC;
    }
    mbedtls_sha256_free(&sha);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    free(url);
    return error;
}

static void add_nullable_string(cJSON *root, const char *name, const char *value) {
    if (value == NULL) cJSON_AddNullToObject(root, name);
    else cJSON_AddStringToObject(root, name, value);
}

esp_err_t backend_client_post_status(const backend_client_config_t *config,
                                     const backend_device_status_t *status) {
    if (config == NULL || status == NULL) return ESP_ERR_INVALID_ARG;
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) return ESP_ERR_NO_MEM;
    add_nullable_string(root, "firmware_version", status->firmware_version);
    add_nullable_string(root, "last_frame_id", status->last_frame_id);
    cJSON_AddNumberToObject(root, "wifi_rssi_dbm", status->wifi_rssi_dbm);
    cJSON_AddNumberToObject(root, "battery_mv", status->battery_mv);
    cJSON_AddNumberToObject(root, "battery_pct", status->battery_pct);
    add_nullable_string(root, "error_code", status->error_code);
    add_nullable_string(root, "error_detail", status->error_detail);
    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (body == NULL) return ESP_ERR_NO_MEM;

    char *url = NULL;
    esp_http_client_handle_t client = open_client(config, "/api/v1/device/status",
                                                   HTTP_METHOD_POST, NULL, NULL, &url);
    esp_err_t error = client == NULL ? ESP_ERR_INVALID_ARG : ESP_OK;
    if (error == ESP_OK) error = esp_http_client_set_header(client, "Content-Type", "application/json");
    if (error == ESP_OK) error = esp_http_client_set_post_field(client, body, (int)strlen(body));
    if (error == ESP_OK) error = esp_http_client_perform(client);
    if (error == ESP_OK && esp_http_client_get_status_code(client) != 204) {
        error = ESP_ERR_INVALID_RESPONSE;
    }
    memset(body, 0, strlen(body));
    free(body);
    if (client != NULL) esp_http_client_cleanup(client);
    free(url);
    return error;
}
