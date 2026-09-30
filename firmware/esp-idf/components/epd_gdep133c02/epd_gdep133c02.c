#include "epd_gdep133c02.h"

#include <stdbool.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
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

#define BYTES_PER_ROW 600U
#define BYTES_PER_CONTROLLER_ROW 300U
#define BUSY_TIMEOUT_MS 180000U

typedef enum { SELECT_CS0, SELECT_CS1, SELECT_BOTH } select_t;

static const char *TAG = "gdep133c02";
static spi_device_handle_t spi_device;
static bool initialized;

static void deselect_all(void) {
    gpio_set_level(EPD_CS0, 1);
    gpio_set_level(EPD_CS1, 1);
}

static void select_controller(select_t selected) {
    deselect_all();
    if (selected == SELECT_CS0 || selected == SELECT_BOTH) gpio_set_level(EPD_CS0, 0);
    if (selected == SELECT_CS1 || selected == SELECT_BOTH) gpio_set_level(EPD_CS1, 0);
}

static esp_err_t spi_write(const void *data, size_t length) {
    if (length == 0U) return ESP_OK;
    spi_transaction_t transaction = {.length = length * 8U, .tx_buffer = data};
    return spi_device_polling_transmit(spi_device, &transaction);
}

static esp_err_t command(select_t selected, uint8_t opcode, const uint8_t *payload,
                         size_t payload_length) {
    select_controller(selected);
    esp_err_t result = spi_write(&opcode, 1U);
    if (result == ESP_OK) result = spi_write(payload, payload_length);
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
    const gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << EPD_CS0) | (1ULL << EPD_CS1) |
                        (1ULL << EPD_RESET_N) | (1ULL << EPD_LOAD_SW),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&outputs), TAG, "output GPIO config failed");
    const gpio_config_t busy = {
        .pin_bit_mask = 1ULL << EPD_BUSY_N,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&busy), TAG, "BUSY_N GPIO config failed");
    deselect_all();
    gpio_set_level(EPD_RESET_N, 1);
    gpio_set_level(EPD_LOAD_SW, 1);

    const spi_bus_config_t bus = {
        .mosi_io_num = EPD_MOSI, .miso_io_num = EPD_MISO, .sclk_io_num = EPD_SCLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 32768,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO), TAG,
                        "SPI bus initialization failed");
    const spi_device_interface_config_t device = {
        .clock_speed_hz = 10 * 1000 * 1000, .mode = 0, .spics_io_num = -1,
        .queue_size = 1,
    };
    return spi_bus_add_device(SPI3_HOST, &device, &spi_device);
}

static esp_err_t panel_init(void) {
    static const uint8_t an_tm[] = {0xC0, 0x1E, 0x1E, 0xCE, 0xCE, 0xCE, 0x15, 0x15, 0x55};
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

esp_err_t gdep133c02_init(void) {
    if (initialized) return ESP_OK;
    ESP_RETURN_ON_ERROR(board_init(), TAG, "board initialization failed");
    /* Preserve the reset exercised by the verified diagnostic before vendor init. */
    hardware_reset();
    ESP_RETURN_ON_ERROR(panel_init(), TAG, "panel initialization failed");
    initialized = true;
    return ESP_OK;
}

static esp_err_t write_controller_stream(uint32_t controller,
                                          gdep133c02_read_cb_t read_cb,
                                          void *read_context) {
    const uint8_t data_start = 0x10;
    const select_t selected = controller == 0U ? SELECT_CS0 : SELECT_CS1;
    uint8_t row_data[BYTES_PER_CONTROLLER_ROW];
    select_controller(selected);
    esp_err_t result = spi_write(&data_start, 1U);
    for (uint32_t row = 0; result == ESP_OK && row < GDEP133C02_HEIGHT; ++row) {
        const size_t offset = (size_t)row * BYTES_PER_ROW +
                              controller * BYTES_PER_CONTROLLER_ROW;
        result = read_cb(offset, row_data, sizeof(row_data), read_context);
        if (result == ESP_OK) result = spi_write(row_data, sizeof(row_data));
    }
    deselect_all();
    return result;
}

esp_err_t gdep133c02_display(gdep133c02_read_cb_t read_cb, void *read_context) {
    if (!initialized) return ESP_ERR_INVALID_STATE;
    if (read_cb == NULL) return ESP_ERR_INVALID_ARG;
    static const uint8_t refresh_payload[] = {0x01};
    static const uint8_t power_off_payload[] = {0x00};
    ESP_RETURN_ON_ERROR(write_controller_stream(0U, read_cb, read_context), TAG,
                        "CS0 frame load failed");
    ESP_RETURN_ON_ERROR(write_controller_stream(1U, read_cb, read_context), TAG,
                        "CS1 frame load failed");
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x04, NULL, 0U), TAG, "power-on failed");
    ESP_RETURN_ON_ERROR(wait_ready("power-on"), TAG, "power-on timeout");
    vTaskDelay(pdMS_TO_TICKS(30));
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x12, refresh_payload, sizeof(refresh_payload)),
                        TAG, "refresh failed");
    ESP_RETURN_ON_ERROR(wait_ready("refresh"), TAG, "refresh timeout");
    ESP_RETURN_ON_ERROR(command(SELECT_BOTH, 0x02, power_off_payload,
                                sizeof(power_off_payload)), TAG, "power-off failed");
    return wait_ready("power-off");
}

typedef struct { FILE *file; } file_context_t;

static esp_err_t file_read(size_t offset, uint8_t *destination, size_t length, void *context) {
    file_context_t *file_context = context;
    if (fseek(file_context->file, (long)offset, SEEK_SET) != 0) return ESP_FAIL;
    return fread(destination, 1U, length, file_context->file) == length ? ESP_OK
                                                                        : ESP_ERR_INVALID_SIZE;
}

esp_err_t gdep133c02_display_file(const char *path) {
    if (path == NULL) return ESP_ERR_INVALID_ARG;
    FILE *file = fopen(path, "rb");
    if (file == NULL) return ESP_ERR_NOT_FOUND;
    esp_err_t result = ESP_OK;
    if (fseek(file, 0L, SEEK_END) != 0 || ftell(file) != (long)GDEP133C02_FRAME_BYTES) {
        result = ESP_ERR_INVALID_SIZE;
    }
    if (result == ESP_OK) {
        file_context_t context = {.file = file};
        result = gdep133c02_display(file_read, &context);
    }
    fclose(file);
    return result;
}
