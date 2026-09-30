#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define EPD_CS0 GPIO_NUM_18
#define EPD_CS1 GPIO_NUM_17
#define EPD_SCLK GPIO_NUM_9
#define EPD_MOSI GPIO_NUM_41
#define EPD_MISO GPIO_NUM_40
#define EPD_BUSY_N GPIO_NUM_7
#define EPD_RESET_N GPIO_NUM_6
#define EPD_LOAD_SW GPIO_NUM_45

#define PANEL_WIDTH 1200U
#define PANEL_HEIGHT 1600U
#define BYTES_PER_ROW 600U
#define BYTES_PER_CONTROLLER_ROW 300U
#define FRAME_BYTES 960000U
#define BUSY_TIMEOUT_MS 180000U

#define COLOR_BLACK 0x0U
#define COLOR_WHITE 0x1U
#define COLOR_YELLOW 0x2U
#define COLOR_RED 0x3U
#define COLOR_BLUE 0x5U
#define COLOR_GREEN 0x6U

static const char *TAG = "eink_diag";
static spi_device_handle_t spi_device;

typedef enum {
    SELECT_CS0,
    SELECT_CS1,
    SELECT_BOTH,
} select_t;

static void deselect_all(void) {
    gpio_set_level(EPD_CS0, 1);
    gpio_set_level(EPD_CS1, 1);
}

static void select_controller(select_t selected) {
    deselect_all();
    if (selected == SELECT_CS0 || selected == SELECT_BOTH) {
        gpio_set_level(EPD_CS0, 0);
    }
    if (selected == SELECT_CS1 || selected == SELECT_BOTH) {
        gpio_set_level(EPD_CS1, 0);
    }
}

static esp_err_t spi_write(const void *data, size_t length) {
    if (length == 0U) {
        return ESP_OK;
    }
    spi_transaction_t transaction = {
        .length = length * 8U,
        .tx_buffer = data,
    };
    return spi_device_polling_transmit(spi_device, &transaction);
}

static esp_err_t command(select_t selected, uint8_t opcode, const uint8_t *payload,
                         size_t payload_length) {
    select_controller(selected);
    esp_err_t result = spi_write(&opcode, 1U);
    if (result == ESP_OK) {
        result = spi_write(payload, payload_length);
    }
    deselect_all();
    return result;
}

static esp_err_t wait_ready(const char *phase) {
    const int64_t deadline = esp_timer_get_time() + (int64_t)BUSY_TIMEOUT_MS * 1000LL;
    while (gpio_get_level(EPD_BUSY_N) == 0) {
        if (esp_timer_get_time() >= deadline) {
            ESP_LOGE(TAG, "BUSY_N timeout during %s", phase);
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return ESP_OK;
}

static void hardware_reset(void) {
    gpio_set_level(EPD_RESET_N, 0);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(EPD_RESET_N, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
}

static esp_err_t board_init(void) {
    gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << EPD_CS0) | (1ULL << EPD_CS1) |
                        (1ULL << EPD_RESET_N) | (1ULL << EPD_LOAD_SW),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&outputs), TAG, "output GPIO config failed");
    gpio_config_t busy = {
        .pin_bit_mask = 1ULL << EPD_BUSY_N,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&busy), TAG, "BUSY_N GPIO config failed");
    deselect_all();
    gpio_set_level(EPD_RESET_N, 1);
    gpio_set_level(EPD_LOAD_SW, 1);

    spi_bus_config_t bus = {
        .mosi_io_num = EPD_MOSI,
        .miso_io_num = EPD_MISO,
        .sclk_io_num = EPD_SCLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32768,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO), TAG,
                        "SPI bus initialization failed");
    spi_device_interface_config_t device = {
        .clock_speed_hz = 10 * 1000 * 1000,
        .mode = 0,
        .spics_io_num = -1,
        .queue_size = 1,
    };
    return spi_bus_add_device(SPI3_HOST, &device, &spi_device);
}

static esp_err_t panel_init(void) {
    static const uint8_t an_tm[] = {0xC0, 0x1E, 0x1E, 0xCE, 0xCE,
                                    0xCE, 0x15, 0x15, 0x55};
    static const uint8_t cmd_f0[] = {0x49, 0x55, 0x13, 0x5D, 0x05, 0x10};
    static const uint8_t psr[] = {0xDF, 0x69};
    static const uint8_t cdi[] = {0xF7};
    static const uint8_t tcon[] = {0x03, 0x03};
    static const uint8_t agid[] = {0x10};
    static const uint8_t pws[] = {0x22};
    static const uint8_t ccset[] = {0x01};
    static const uint8_t tres[] = {0x04, 0xB0, 0x03, 0x20};
    static const uint8_t pwr[] = {0x0F, 0x00, 0x28, 0x2C, 0x28, 0x38};
    static const uint8_t en_buf[] = {0x07};
    static const uint8_t btst_p[] = {0xE8, 0x28};
    static const uint8_t boost_vddp[] = {0x01};
    static const uint8_t btst_n[] = {0xE8, 0x28};
    static const uint8_t buck_vddn[] = {0x01};
    static const uint8_t tft_vcom[] = {0x02};

    hardware_reset();
    ESP_RETURN_ON_ERROR(wait_ready("reset"), TAG, "panel did not become ready");
#define SEND(sel, op, data) ESP_RETURN_ON_ERROR(command(sel, op, data, sizeof(data)), TAG, "command 0x%02X failed", op)
    SEND(SELECT_CS0, 0x74, an_tm);
    SEND(SELECT_BOTH, 0xF0, cmd_f0);
    SEND(SELECT_BOTH, 0x00, psr);
    SEND(SELECT_BOTH, 0x50, cdi);
    SEND(SELECT_BOTH, 0x60, tcon);
    SEND(SELECT_BOTH, 0x86, agid);
    SEND(SELECT_BOTH, 0xE3, pws);
    SEND(SELECT_BOTH, 0xE0, ccset);
    SEND(SELECT_BOTH, 0x61, tres);
    SEND(SELECT_CS0, 0x01, pwr);
    SEND(SELECT_CS0, 0xB6, en_buf);
    SEND(SELECT_CS0, 0x06, btst_p);
    SEND(SELECT_CS0, 0xB7, boost_vddp);
    SEND(SELECT_CS0, 0x05, btst_n);
    SEND(SELECT_CS0, 0xB0, buck_vddn);
    SEND(SELECT_CS0, 0xB1, tft_vcom);
#undef SEND
    return ESP_OK;
}

static void set_pixel(uint8_t *frame, uint32_t x, uint32_t y, uint8_t color) {
    const uint32_t pair_start = x & ~1U;
    const size_t offset = (size_t)y * BYTES_PER_ROW + (1198U - pair_start) / 2U;
    if ((x & 1U) == 0U) {
        frame[offset] = (uint8_t)((frame[offset] & 0x0FU) | (color << 4U));
    } else {
        frame[offset] = (uint8_t)((frame[offset] & 0xF0U) | color);
    }
}

static void build_diagnostic_frame(uint8_t *frame) {
    memset(frame, 0, FRAME_BYTES);
    for (uint32_t y = 0; y < PANEL_HEIGHT; ++y) {
        for (uint32_t x = 0; x < PANEL_WIDTH; ++x) {
            uint8_t color;
            if (x < PANEL_WIDTH / 2U && y < PANEL_HEIGHT / 2U) {
                color = COLOR_RED;
            } else if (x >= PANEL_WIDTH / 2U && y < PANEL_HEIGHT / 2U) {
                color = COLOR_GREEN;
            } else if (x < PANEL_WIDTH / 2U) {
                color = COLOR_BLUE;
            } else {
                color = COLOR_YELLOW;
            }
            if (x < 12U || y < 12U || x >= PANEL_WIDTH - 12U ||
                y >= PANEL_HEIGHT - 12U) {
                color = COLOR_WHITE;
            }
            if ((x >= 594U && x < 606U) || (y >= 794U && y < 806U)) {
                color = COLOR_BLACK;
            }
            set_pixel(frame, x, y, color);
        }
    }
}

static esp_err_t write_controller_stream(const uint8_t *frame, uint32_t controller) {
    const uint8_t data_start = 0x10;
    const select_t selected = controller == 0U ? SELECT_CS0 : SELECT_CS1;
    select_controller(selected);
    esp_err_t result = spi_write(&data_start, 1U);
    for (uint32_t row = 0; result == ESP_OK && row < PANEL_HEIGHT; ++row) {
        const size_t offset = (size_t)row * BYTES_PER_ROW +
                              controller * BYTES_PER_CONTROLLER_ROW;
        result = spi_write(frame + offset, BYTES_PER_CONTROLLER_ROW);
    }
    deselect_all();
    return result;
}

static esp_err_t display_frame(const uint8_t *frame) {
    static const uint8_t refresh_payload[] = {0x01};
    static const uint8_t power_off_payload[] = {0x00};
    ESP_RETURN_ON_ERROR(write_controller_stream(frame, 0), TAG, "CS0 frame load failed");
    ESP_RETURN_ON_ERROR(write_controller_stream(frame, 1), TAG, "CS1 frame load failed");
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x04, NULL, 0), TAG, "power-on failed");
    ESP_RETURN_ON_ERROR(wait_ready("power-on"), TAG, "power-on timeout");
    vTaskDelay(pdMS_TO_TICKS(30));
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x12, refresh_payload,
                                sizeof(refresh_payload)),
                        TAG, "refresh failed");
    ESP_RETURN_ON_ERROR(wait_ready("refresh"), TAG, "refresh timeout");
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x02, power_off_payload,
                                sizeof(power_off_payload)),
                        TAG, "power-off failed");
    return wait_ready("power-off");
}

void app_main(void) {
    ESP_LOGI(TAG, "GDEP133C02 diagnostic firmware starting");
    ESP_ERROR_CHECK(board_init());
    hardware_reset();
    uint8_t *frame = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (frame == NULL) {
        ESP_LOGE(TAG, "failed to allocate %u-byte PSRAM frame", FRAME_BYTES);
        abort();
    }
    build_diagnostic_frame(frame);
    ESP_ERROR_CHECK(panel_init());
    ESP_ERROR_CHECK(display_frame(frame));
    free(frame);
    ESP_LOGI(TAG, "diagnostic frame displayed; panel powered off with command 0x02");
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
