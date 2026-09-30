#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "config_store.h"
#include "provisioning_form.h"

static config_store_credentials_t valid_credentials(void) {
    config_store_credentials_t credentials = {0};
    strcpy(credentials.wifi_ssid, "Office WiFi");
    strcpy(credentials.wifi_password, "correct horse");
    strcpy(credentials.backend_base_url, "http://192.168.10.20:8080/api");
    strcpy(credentials.device_bearer_token, "device-secret");
    return credentials;
}

static void test_valid_credentials_are_accepted(void) {
    const config_store_credentials_t credentials = valid_credentials();
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_OK);
}

static void test_ssid_boundaries_are_enforced(void) {
    config_store_credentials_t credentials = valid_credentials();
    memset(credentials.wifi_ssid, 's', CONFIG_STORE_WIFI_SSID_MAX_LEN);
    credentials.wifi_ssid[CONFIG_STORE_WIFI_SSID_MAX_LEN] = '\0';
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_OK);
    credentials.wifi_ssid[CONFIG_STORE_WIFI_SSID_MAX_LEN] = 's';
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_SSID);
}

static void test_wifi_password_is_empty_or_wpa2_length(void) {
    config_store_credentials_t credentials = valid_credentials();
    strcpy(credentials.wifi_password, "short");
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_WIFI_PASSWORD);
    credentials.wifi_password[0] = '\0';
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_OK);
    memset(credentials.wifi_password, 'p', CONFIG_STORE_WIFI_PASSWORD_MAX_LEN);
    credentials.wifi_password[CONFIG_STORE_WIFI_PASSWORD_MAX_LEN] = '\0';
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_OK);
}

static void test_only_private_ipv4_http_backend_urls_are_accepted(void) {
    static const char *accepted[] = {
        "http://10.0.0.1", "http://172.16.0.1:3000/api", "http://172.31.255.254",
        "http://192.168.1.2/backend"
    };
    static const char *rejected[] = {
        "https://192.168.1.2", "http://8.8.8.8", "http://172.32.0.1",
        "http://example.com", "http://127.0.0.1", "http://169.254.1.1",
        "http://192.168.001.2", "http://192.168.1.2@evil.test",
        "http://192.168.1.2:0", "http://192.168.1.2:65536",
        "http://192.168.1.2/path\nheader", "http://192.168.1.2/#fragment"
    };
    config_store_credentials_t credentials = valid_credentials();
    for (size_t index = 0; index < sizeof(accepted) / sizeof(accepted[0]); ++index) {
        strcpy(credentials.backend_base_url, accepted[index]);
        assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_OK);
    }
    for (size_t index = 0; index < sizeof(rejected) / sizeof(rejected[0]); ++index) {
        strcpy(credentials.backend_base_url, rejected[index]);
        assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_BACKEND_URL);
    }
}

static void test_token_must_be_nonempty_and_terminated(void) {
    config_store_credentials_t credentials = valid_credentials();
    credentials.device_bearer_token[0] = '\0';
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_DEVICE_TOKEN);
    strcpy(credentials.device_bearer_token, "token\r\nInjected: value");
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_DEVICE_TOKEN);
    memset(credentials.device_bearer_token, 't', sizeof(credentials.device_bearer_token));
    assert(provisioning_validate_credentials(&credentials) == PROVISIONING_VALIDATE_DEVICE_TOKEN);
}

static void test_setup_identity_uses_uppercase_last_three_mac_bytes(void) {
    static const unsigned char mac[6] = {0x10, 0x20, 0x30, 0x0a, 0xb4, 0xff};
    provisioning_ap_identity_t identity = {0};
    provisioning_derive_ap_identity(mac, &identity);
    assert(strcmp(identity.ssid, "EInkFrame-0AB4FF") == 0);
    assert(strcmp(identity.password, "setup-0AB4FF") == 0);
    assert(strlen(identity.password) == 12U);
}

static void test_form_parser_decodes_url_encoding(void) {
    static const char body[] =
        "ssid=Office+WiFi&password=a%2Bb%25cdef&backend_url=http%3A%2F%2F192.168.1.2%3A8080%2Fapi&token=t%26%3D%3C%22";
    config_store_credentials_t credentials = {0};
    assert(provisioning_parse_form(body, strlen(body), &credentials) == PROVISIONING_FORM_OK);
    assert(strcmp(credentials.wifi_ssid, "Office WiFi") == 0);
    assert(strcmp(credentials.wifi_password, "a+b%cdef") == 0);
    assert(strcmp(credentials.backend_base_url, "http://192.168.1.2:8080/api") == 0);
    assert(strcmp(credentials.device_bearer_token, "t&=<\"") == 0);
}

static void test_form_parser_rejects_malformed_duplicate_missing_and_oversized_fields(void) {
    config_store_credentials_t credentials = {0};
    static const char malformed[] =
        "ssid=x%ZZ&password=abcdefgh&backend_url=http%3A%2F%2F10.0.0.1&token=t";
    assert(provisioning_parse_form(malformed, strlen(malformed), &credentials) ==
           PROVISIONING_FORM_MALFORMED);
    static const char duplicate[] =
        "ssid=a&ssid=b&password=abcdefgh&backend_url=http%3A%2F%2F10.0.0.1&token=t";
    assert(provisioning_parse_form(duplicate, strlen(duplicate), &credentials) ==
           PROVISIONING_FORM_DUPLICATE_FIELD);
    static const char missing[] = "ssid=a&password=abcdefgh&backend_url=http%3A%2F%2F10.0.0.1";
    assert(provisioning_parse_form(missing, strlen(missing), &credentials) ==
           PROVISIONING_FORM_MISSING_FIELD);
    static const char trailing_separator[] =
        "ssid=a&password=abcdefgh&backend_url=http%3A%2F%2F10.0.0.1&token=t&";
    assert(provisioning_parse_form(trailing_separator, strlen(trailing_separator), &credentials) ==
           PROVISIONING_FORM_MALFORMED);
    char oversized[CONFIG_STORE_WIFI_SSID_MAX_LEN + 128U];
    int written = snprintf(oversized, sizeof(oversized),
                           "ssid=123456789012345678901234567890123&password=abcdefgh&backend_url=http%%3A%%2F%%2F10.0.0.1&token=t");
    assert(written > 0);
    assert(provisioning_parse_form(oversized, (size_t)written, &credentials) ==
           PROVISIONING_FORM_FIELD_TOO_LONG);
}

static void test_html_escape_encodes_all_attribute_metacharacters(void) {
    char escaped[64] = {0};
    assert(provisioning_html_escape("<&>\"'", escaped, sizeof(escaped)) == PROVISIONING_ESCAPE_OK);
    assert(strcmp(escaped, "&lt;&amp;&gt;&quot;&#39;") == 0);
    char too_small[4] = "xxx";
    assert(provisioning_html_escape("<", too_small, sizeof(too_small)) ==
           PROVISIONING_ESCAPE_OUTPUT_TOO_SMALL);
    assert(too_small[0] == '\0');
}

int main(void) {
    test_valid_credentials_are_accepted();
    test_ssid_boundaries_are_enforced();
    test_wifi_password_is_empty_or_wpa2_length();
    test_only_private_ipv4_http_backend_urls_are_accepted();
    test_token_must_be_nonempty_and_terminated();
    test_setup_identity_uses_uppercase_last_three_mac_bytes();
    test_form_parser_decodes_url_encoding();
    test_form_parser_rejects_malformed_duplicate_missing_and_oversized_fields();
    test_html_escape_encodes_all_attribute_metacharacters();
    puts("provisioning host tests passed");
    return 0;
}
