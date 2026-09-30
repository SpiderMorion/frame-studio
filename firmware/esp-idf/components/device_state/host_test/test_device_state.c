#include "device_state/device_state.h"

#include <assert.h>

int main(void) {
    assert(device_state_sequence_is_newer(0U, 1U));
    assert(device_state_sequence_is_newer(41U, 42U));
    assert(!device_state_sequence_is_newer(42U, 42U));
    assert(!device_state_sequence_is_newer(42U, 41U));
    assert(!device_state_sequence_is_newer(0xffffffffU, 1U));
    assert(device_state_valid_uuid("a6bbb332-b3ab-50d7-a52d-cb6a40c1421f"));
    assert(!device_state_valid_uuid("A6BBB332-B3AB-50D7-A52D-CB6A40C1421F"));
    assert(!device_state_valid_uuid("not-a-uuid"));
    return 0;
}
