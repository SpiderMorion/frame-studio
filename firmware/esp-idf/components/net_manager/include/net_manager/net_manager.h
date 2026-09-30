#pragma once

#include "esp_err.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    char ssid[33];
    char password[65];
} net_credentials_t;

typedef struct {
    bool connected;
    int8_t rssi_dbm;
    uint32_t failed_attempts;
} net_status_t;

esp_err_t net_manager_init(void);
esp_err_t net_manager_connect(const net_credentials_t *credentials, uint32_t timeout_ms);
void net_manager_disconnect(void);
net_status_t net_manager_status(void);
