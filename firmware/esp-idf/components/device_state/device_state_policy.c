#include "device_state/device_state.h"

#include <stddef.h>
#include <string.h>

bool device_state_sequence_is_newer(uint32_t current, uint32_t candidate) {
    return candidate > current;
}

bool device_state_valid_uuid(const char *value) {
    if (value == NULL || strlen(value) != DEVICE_STATE_FRAME_ID_LENGTH) {
        return false;
    }
    for (size_t index = 0; index < DEVICE_STATE_FRAME_ID_LENGTH; ++index) {
        const char character = value[index];
        const bool separator = index == 8U || index == 13U || index == 18U || index == 23U;
        if (separator) {
            if (character != '-') {
                return false;
            }
        } else if (!((character >= '0' && character <= '9') ||
                     (character >= 'a' && character <= 'f'))) {
            return false;
        }
    }
    return true;
}
