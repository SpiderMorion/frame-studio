#include "frame_store.h"

#include "driver/spi_common.h"
#include "esp_vfs_fat.h"
#include "frame_store_planner.h"
#include "mbedtls/sha256.h"
#include "sdmmc_cmd.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define FRAME_STORE_SPI_HOST SPI2_HOST
#define FRAME_STORE_PIN_CS 15
#define FRAME_STORE_PIN_MOSI 3
#define FRAME_STORE_PIN_MISO 5
#define FRAME_STORE_PIN_SCK 8

#define STATE0_PATH "/sd/state0.bin"
#define STATE1_PATH "/sd/state1.bin"
#define STATE0_TEMP_PATH "/sd/state0.tmp"
#define STATE1_TEMP_PATH "/sd/state1.tmp"

static sdmmc_card_t *s_card;
static bool s_bus_initialized;

static bool path_exists(const char *path) {
    struct stat details;
    return stat(path, &details) == 0;
}

static esp_err_t remove_if_present(const char *path) {
    if (unlink(path) == 0 || errno == ENOENT) {
        return ESP_OK;
    }
    return ESP_FAIL;
}

static void metadata_barrier(void) {
    /* FatFs metadata calls are synchronous. fsync adds a barrier where VFS
       supports opening the mount directory, without rejecting valid FatFs
       implementations that return EINVAL for directory descriptors. */
    FILE *directory = fopen(FRAME_STORE_MOUNT_POINT, "r");
    if (directory != NULL) {
        (void)fsync(fileno(directory));
        (void)fclose(directory);
    }
}

static bool read_record(const char *path, frame_store_journal_record_t *record) {
    memset(record, 0, sizeof(*record));
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return false;
    }
    const bool complete = fread(record, 1U, sizeof(*record), file) == sizeof(*record) &&
                          fgetc(file) == EOF && !ferror(file);
    (void)fclose(file);
    return complete;
}

static frame_store_journal_selection_t load_journal(void) {
    frame_store_journal_record_t slot0;
    frame_store_journal_record_t slot1;
    (void)read_record(STATE0_PATH, &slot0);
    (void)read_record(STATE1_PATH, &slot1);
    return frame_store_journal_select(&slot0, &slot1);
}

static esp_err_t write_journal(const frame_store_journal_selection_t *prior,
                               const frame_store_state_t *state) {
    const uint8_t target = prior->valid ? (uint8_t)(prior->slot ^ 1U) : 0U;
    const char *path = target == 0U ? STATE0_PATH : STATE1_PATH;
    const char *temporary = target == 0U ? STATE0_TEMP_PATH : STATE1_TEMP_PATH;
    frame_store_journal_record_t record;
    frame_store_journal_encode(state, &record);

    esp_err_t error = remove_if_present(temporary);
    if (error != ESP_OK) {
        return error;
    }
    FILE *file = fopen(temporary, "wb");
    if (file == NULL) {
        return ESP_FAIL;
    }
    if (fwrite(&record, 1U, sizeof(record), file) != sizeof(record) ||
        fflush(file) != 0 || fsync(fileno(file)) != 0) {
        (void)fclose(file);
        return ESP_FAIL;
    }
    if (fclose(file) != 0) {
        return ESP_FAIL;
    }

    error = remove_if_present(path);
    if (error != ESP_OK) {
        return error;
    }
    metadata_barrier();
    if (rename(temporary, path) != 0) {
        return ESP_FAIL;
    }
    metadata_barrier();
    return ESP_OK;
}

static bool validates_as(const char *path, const uint8_t hash[32]) {
    return frame_store_validate_file(path, hash, NULL) == ESP_OK;
}

static const uint8_t *matching_known_hash(
    const char *path, const frame_store_journal_selection_t *journal) {
    if (!journal->valid) {
        return NULL;
    }
    if (validates_as(path, journal->state.sha256)) {
        return journal->state.sha256;
    }
    if (journal->state.has_previous &&
        validates_as(path, journal->state.previous_sha256)) {
        return journal->state.previous_sha256;
    }
    return NULL;
}

esp_err_t frame_store_mount(void) {
    if (s_card != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    spi_bus_config_t bus = {
        .mosi_io_num = FRAME_STORE_PIN_MOSI,
        .miso_io_num = FRAME_STORE_PIN_MISO,
        .sclk_io_num = FRAME_STORE_PIN_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4096,
    };
    esp_err_t error = spi_bus_initialize(FRAME_STORE_SPI_HOST, &bus, SDSPI_DEFAULT_DMA);
    if (error != ESP_OK) {
        return error;
    }
    s_bus_initialized = true;

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = FRAME_STORE_SPI_HOST;
    sdspi_device_config_t device = SDSPI_DEVICE_CONFIG_DEFAULT();
    device.host_id = FRAME_STORE_SPI_HOST;
    device.gpio_cs = FRAME_STORE_PIN_CS;
    esp_vfs_fat_sdmmc_mount_config_t mount = {
        .format_if_mount_failed = false,
        .max_files = 6,
        .allocation_unit_size = 16U * 1024U,
    };
    error = esp_vfs_fat_sdspi_mount(FRAME_STORE_MOUNT_POINT, &host, &device,
                                    &mount, &s_card);
    if (error != ESP_OK) {
        (void)spi_bus_free(FRAME_STORE_SPI_HOST);
        s_bus_initialized = false;
    }
    return error;
}

esp_err_t frame_store_unmount(void) {
    if (s_card == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = esp_vfs_fat_sdcard_unmount(FRAME_STORE_MOUNT_POINT, s_card);
    if (error != ESP_OK) {
        return error;
    }
    s_card = NULL;
    if (s_bus_initialized) {
        error = spi_bus_free(FRAME_STORE_SPI_HOST);
        if (error == ESP_OK) {
            s_bus_initialized = false;
        }
    }
    return error;
}

esp_err_t frame_store_validate_file(const char *path,
                                    const uint8_t expected_sha256[32],
                                    uint8_t actual_sha256[32]) {
    if (path == NULL || expected_sha256 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }

    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    int sha_error = mbedtls_sha256_starts(&context, 0);
    uint8_t buffer[4096];
    size_t total = 0U;
    while (sha_error == 0) {
        const size_t count = fread(buffer, 1U, sizeof(buffer), file);
        if (count > 0U) {
            total += count;
            if (total > FRAME_STORE_FRAME_BYTES) {
                break;
            }
            sha_error = mbedtls_sha256_update(&context, buffer, count);
        }
        if (count < sizeof(buffer)) {
            break;
        }
    }
    uint8_t digest[32];
    if (sha_error == 0) {
        sha_error = mbedtls_sha256_finish(&context, digest);
    }
    mbedtls_sha256_free(&context);
    const bool io_error = ferror(file) != 0;
    (void)fclose(file);

    if (sha_error != 0 || io_error) {
        return ESP_FAIL;
    }
    if (total != FRAME_STORE_FRAME_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (actual_sha256 != NULL) {
        memcpy(actual_sha256, digest, sizeof(digest));
    }
    return memcmp(digest, expected_sha256, sizeof(digest)) == 0
               ? ESP_OK
               : ESP_ERR_INVALID_CRC;
}

esp_err_t frame_store_validate_download(const uint8_t expected_sha256[32]) {
    return frame_store_validate_file(FRAME_STORE_DOWNLOAD_PATH,
                                     expected_sha256, NULL);
}

esp_err_t frame_store_promote(const uint8_t expected_sha256[32]) {
    if (s_card == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = frame_store_validate_download(expected_sha256);
    if (error != ESP_OK) {
        return error;
    }

    frame_store_journal_selection_t prior = load_journal();
    const uint8_t *current_hash =
        matching_known_hash(FRAME_STORE_CURRENT_PATH, &prior);
    const uint8_t *previous_hash =
        matching_known_hash(FRAME_STORE_PREVIOUS_PATH, &prior);
    const bool current_valid = current_hash != NULL;
    const bool previous_valid = previous_hash != NULL;
    frame_store_snapshot_t snapshot = {
        .temp_valid = true,
        .current_valid = current_valid,
        .current_exists = path_exists(FRAME_STORE_CURRENT_PATH),
        .previous_valid = previous_valid,
        .previous_exists = path_exists(FRAME_STORE_PREVIOUS_PATH),
    };
    const frame_store_plan_t plan = frame_store_plan_promotion(snapshot);
    if (plan.status != FRAME_STORE_PLAN_OK) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t *fallback_hash =
        current_valid ? current_hash : previous_hash;
    const frame_store_state_t staged = frame_store_state_staged_preserving(
        &prior, expected_sha256, fallback_hash);
    error = write_journal(&prior, &staged);
    if (error != ESP_OK) {
        return error;
    }

    for (size_t index = 0; index < plan.count; ++index) {
        switch (plan.steps[index]) {
            case FRAME_STORE_STEP_REMOVE_PREVIOUS:
                error = remove_if_present(FRAME_STORE_PREVIOUS_PATH);
                break;
            case FRAME_STORE_STEP_REMOVE_CURRENT:
                error = remove_if_present(FRAME_STORE_CURRENT_PATH);
                break;
            case FRAME_STORE_STEP_CURRENT_TO_PREVIOUS:
                error = rename(FRAME_STORE_CURRENT_PATH,
                               FRAME_STORE_PREVIOUS_PATH) == 0 ? ESP_OK : ESP_FAIL;
                break;
            case FRAME_STORE_STEP_TEMP_TO_CURRENT:
                error = rename(FRAME_STORE_DOWNLOAD_PATH,
                               FRAME_STORE_CURRENT_PATH) == 0 ? ESP_OK : ESP_FAIL;
                break;
            default:
                error = ESP_ERR_INVALID_STATE;
                break;
        }
        if (error != ESP_OK) {
            return error;
        }
        metadata_barrier();
    }
    return ESP_OK;
}

esp_err_t frame_store_recover(frame_store_recovery_result_t *result) {
    if (s_card == NULL || result == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    const frame_store_journal_selection_t journal = load_journal();
    if (!journal.valid) {
        return ESP_ERR_NOT_FOUND;
    }

    const bool current_is_new =
        validates_as(FRAME_STORE_CURRENT_PATH, journal.state.sha256);
    const bool current_is_prior = journal.state.has_previous &&
        validates_as(FRAME_STORE_CURRENT_PATH, journal.state.previous_sha256);
    const bool previous_is_prior = journal.state.has_previous &&
        validates_as(FRAME_STORE_PREVIOUS_PATH, journal.state.previous_sha256);
    const bool previous_is_new =
        validates_as(FRAME_STORE_PREVIOUS_PATH, journal.state.sha256);

    if (current_is_new || current_is_prior) {
        result->path = FRAME_STORE_CURRENT_PATH;
        memcpy(result->sha256,
               current_is_new ? journal.state.sha256 : journal.state.previous_sha256,
               sizeof(result->sha256));
        result->displayed = frame_store_recovery_is_displayed(
            current_is_new, journal.state.displayed);
        return ESP_OK;
    }
    if (previous_is_prior || previous_is_new) {
        result->path = FRAME_STORE_PREVIOUS_PATH;
        memcpy(result->sha256,
               previous_is_prior ? journal.state.previous_sha256 : journal.state.sha256,
               sizeof(result->sha256));
        result->displayed = frame_store_recovery_is_displayed(
            previous_is_new, journal.state.displayed);
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t frame_store_mark_displayed(const uint8_t displayed_sha256[32]) {
    if (s_card == NULL || displayed_sha256 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    const frame_store_journal_selection_t prior = load_journal();
    if (!prior.valid ||
        memcmp(prior.state.sha256, displayed_sha256, 32U) != 0 ||
        !validates_as(FRAME_STORE_CURRENT_PATH, displayed_sha256)) {
        return ESP_ERR_INVALID_STATE;
    }
    frame_store_state_t displayed;
    if (!frame_store_state_mark_displayed(&prior, &displayed)) {
        return ESP_ERR_INVALID_STATE;
    }
    return write_journal(&prior, &displayed);
}
