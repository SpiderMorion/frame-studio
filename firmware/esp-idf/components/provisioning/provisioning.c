#include "provisioning.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "config_store.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "provisioning_form.h"

#define MAX_FORM_BODY_LENGTH 2048U
#define MAX_RECEIVE_TIMEOUTS 3U
#define AP_CHANNEL 1U
#define AP_MAX_CONNECTIONS 4U

static const char *TAG = "provisioning";
static httpd_handle_t server;
static esp_netif_t *ap_netif;
static bool wifi_initialized;
static provisioning_ap_identity_t ap_identity;

static const char PAGE_HEAD[] =
    "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
    "content=\"width=device-width,initial-scale=1\"><title>EInkFrame setup</title>"
    "<style>body{font:16px system-ui;max-width:32rem;margin:2rem auto;padding:0 1rem}"
    "label{display:block;margin-top:1rem}input{box-sizing:border-box;width:100%;padding:.7rem}"
    "button{margin-top:1.5rem;padding:.8rem 1.2rem}</style></head><body>"
    "<h1>EInkFrame setup</h1><p>Connected to <strong>";
static const char PAGE_FORM[] =
    "</strong></p><form method=\"post\" action=\"/configure\">"
    "<label>Wi-Fi SSID<input name=\"ssid\" maxlength=\"32\" required></label>"
    "<label>Wi-Fi password<input name=\"password\" type=\"password\" maxlength=\"63\"></label>"
    "<label>Backend base URL<input name=\"backend_url\" type=\"url\" maxlength=\"255\" "
    "placeholder=\"http://frame-host.local:8787\" required></label>"
    "<label>Device bearer token<input name=\"token\" type=\"password\" maxlength=\"255\" "
    "required></label><button type=\"submit\">Save configuration</button></form></body></html>";

static void set_security_headers(httpd_req_t *request) {
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Content-Security-Policy",
                       "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'");
    httpd_resp_set_hdr(request, "X-Content-Type-Options", "nosniff");
    httpd_resp_set_hdr(request, "Referrer-Policy", "no-referrer");
}

static esp_err_t root_handler(httpd_req_t *request) {
    char escaped_ssid[64];
    if (provisioning_html_escape(ap_identity.ssid, escaped_ssid, sizeof(escaped_ssid)) !=
        PROVISIONING_ESCAPE_OK) {
        return httpd_resp_send_500(request);
    }
    set_security_headers(request);
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    esp_err_t result = httpd_resp_send_chunk(request, PAGE_HEAD, HTTPD_RESP_USE_STRLEN);
    if (result == ESP_OK) {
        result = httpd_resp_send_chunk(request, escaped_ssid, HTTPD_RESP_USE_STRLEN);
    }
    if (result == ESP_OK) {
        result = httpd_resp_send_chunk(request, PAGE_FORM, HTTPD_RESP_USE_STRLEN);
    }
    if (result == ESP_OK) {
        result = httpd_resp_send_chunk(request, NULL, 0U);
    }
    return result;
}

static esp_err_t send_bad_request(httpd_req_t *request, const char *message) {
    set_security_headers(request);
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, message);
}

static bool form_content_type(httpd_req_t *request) {
    char content_type[80];
    if (httpd_req_get_hdr_value_str(request, "Content-Type", content_type,
                                    sizeof(content_type)) != ESP_OK) {
        return false;
    }
    static const char expected[] = "application/x-www-form-urlencoded";
    return strncmp(content_type, expected, sizeof(expected) - 1U) == 0 &&
           (content_type[sizeof(expected) - 1U] == '\0' ||
            content_type[sizeof(expected) - 1U] == ';');
}

static esp_err_t configure_handler(httpd_req_t *request) {
    if (!form_content_type(request) || request->content_len == 0U ||
        request->content_len > MAX_FORM_BODY_LENGTH) {
        return send_bad_request(request, "Invalid form request");
    }

    char *body = calloc(request->content_len + 1U, 1U);
    if (body == NULL) {
        return httpd_resp_send_500(request);
    }
    size_t received = 0U;
    unsigned receive_timeouts = 0U;
    while (received < request->content_len) {
        const int count = httpd_req_recv(request, body + received,
                                         request->content_len - received);
        if (count == HTTPD_SOCK_ERR_TIMEOUT && ++receive_timeouts <= MAX_RECEIVE_TIMEOUTS) {
            continue;
        }
        if (count <= 0) {
            memset(body, 0, request->content_len + 1U);
            free(body);
            return ESP_FAIL;
        }
        received += (size_t)count;
    }

    config_store_credentials_t credentials = {0};
    const provisioning_form_result_t parse_result =
        provisioning_parse_form(body, received, &credentials);
    memset(body, 0, request->content_len + 1U);
    free(body);
    if (parse_result != PROVISIONING_FORM_OK) {
        memset(&credentials, 0, sizeof(credentials));
        return send_bad_request(request, "Malformed or oversized form fields");
    }

    const provisioning_validate_result_t validation =
        provisioning_validate_credentials(&credentials);
    if (validation != PROVISIONING_VALIDATE_OK) {
        memset(&credentials, 0, sizeof(credentials));
        switch (validation) {
            case PROVISIONING_VALIDATE_SSID:
                return send_bad_request(request, "Wi-Fi SSID is required");
            case PROVISIONING_VALIDATE_WIFI_PASSWORD:
                return send_bad_request(request, "Wi-Fi password must be empty or 8-63 characters");
            case PROVISIONING_VALIDATE_BACKEND_URL:
                return send_bad_request(request, "Backend URL must use http:// and a private IPv4 address");
            case PROVISIONING_VALIDATE_DEVICE_TOKEN:
                return send_bad_request(request, "Device token is required");
            default:
                return send_bad_request(request, "Invalid configuration");
        }
    }

    const esp_err_t save_result = config_store_save(&credentials);
    memset(&credentials, 0, sizeof(credentials));
    if (save_result != ESP_OK) {
        ESP_LOGE(TAG, "configuration persistence failed: %s", esp_err_to_name(save_result));
        return httpd_resp_send_500(request);
    }

    ESP_LOGI(TAG, "configuration saved");
    set_security_headers(request);
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    return httpd_resp_sendstr(request,
                              "<!doctype html><meta charset=\"utf-8\"><title>Saved</title>"
                              "<h1>Configuration saved</h1><p>You may reboot the device.</p>");
}

static esp_err_t redirect_handler(httpd_req_t *request) {
    httpd_resp_set_status(request, "302 Found");
    httpd_resp_set_hdr(request, "Location", "/");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_send(request, NULL, 0U);
}

static esp_err_t register_http_handlers(void) {
    static const httpd_uri_t handlers[] = {
        {.uri = "/", .method = HTTP_GET, .handler = root_handler},
        {.uri = "/configure", .method = HTTP_POST, .handler = configure_handler},
        {.uri = "/generate_204", .method = HTTP_GET, .handler = redirect_handler},
        {.uri = "/hotspot-detect.html", .method = HTTP_GET, .handler = redirect_handler},
        {.uri = "/ncsi.txt", .method = HTTP_GET, .handler = redirect_handler},
        {.uri = "/connecttest.txt", .method = HTTP_GET, .handler = redirect_handler},
    };
    for (size_t index = 0U; index < sizeof(handlers) / sizeof(handlers[0]); ++index) {
        const esp_err_t result = httpd_register_uri_handler(server, &handlers[index]);
        if (result != ESP_OK) {
            return result;
        }
    }
    return ESP_OK;
}

esp_err_t provisioning_start(const uint8_t base_mac[6]) {
    if (base_mac == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (server != NULL || wifi_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    provisioning_derive_ap_identity(base_mac, &ap_identity);

    esp_err_t result = esp_netif_init();
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }
    result = esp_event_loop_create_default();
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }
    ap_netif = esp_netif_create_default_wifi_ap();
    if (ap_netif == NULL) {
        return ESP_FAIL;
    }

    const wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    result = esp_wifi_init(&init_config);
    if (result != ESP_OK) {
        esp_netif_destroy_default_wifi(ap_netif);
        ap_netif = NULL;
        return result;
    }
    wifi_initialized = true;

    wifi_config_t wifi_config = {0};
    memcpy(wifi_config.ap.ssid, ap_identity.ssid, sizeof(ap_identity.ssid));
    memcpy(wifi_config.ap.password, ap_identity.password, sizeof(ap_identity.password));
    wifi_config.ap.ssid_len = PROVISIONING_AP_SSID_LEN;
    wifi_config.ap.channel = AP_CHANNEL;
    wifi_config.ap.max_connection = AP_MAX_CONNECTIONS;
    wifi_config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.ap.pmf_cfg.capable = true;
    wifi_config.ap.pmf_cfg.required = false;

    result = esp_wifi_set_mode(WIFI_MODE_AP);
    if (result == ESP_OK) {
        result = esp_wifi_set_config(WIFI_IF_AP, &wifi_config);
    }
    if (result == ESP_OK) {
        result = esp_wifi_start();
    }
    memset(&wifi_config, 0, sizeof(wifi_config));
    if (result != ESP_OK) {
        provisioning_stop();
        return result;
    }

    httpd_config_t http_config = HTTPD_DEFAULT_CONFIG();
    http_config.max_uri_handlers = 8U;
    result = httpd_start(&server, &http_config);
    if (result == ESP_OK) {
        result = register_http_handlers();
    }
    if (result != ESP_OK) {
        provisioning_stop();
        return result;
    }

    ESP_LOGI(TAG, "protected setup network started: %s", ap_identity.ssid);
    return ESP_OK;
}

esp_err_t provisioning_stop(void) {
    esp_err_t first_error = ESP_OK;
    if (server != NULL) {
        const esp_err_t result = httpd_stop(server);
        if (result != ESP_OK) {
            first_error = result;
        }
        server = NULL;
    }
    if (wifi_initialized) {
        esp_err_t result = esp_wifi_stop();
        if (result != ESP_OK && result != ESP_ERR_WIFI_NOT_STARTED && first_error == ESP_OK) {
            first_error = result;
        }
        result = esp_wifi_deinit();
        if (result != ESP_OK && first_error == ESP_OK) {
            first_error = result;
        }
        wifi_initialized = false;
    }
    if (ap_netif != NULL) {
        esp_netif_destroy_default_wifi(ap_netif);
        ap_netif = NULL;
    }
    memset(&ap_identity, 0, sizeof(ap_identity));
    return first_error;
}
