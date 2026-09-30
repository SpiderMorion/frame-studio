#include "net_manager/net_policy.h"

#include <assert.h>
#include <stdint.h>

int main(void) {
    assert(net_retry_delay_ms(0) == 1000U);
    assert(net_retry_delay_ms(1) == 2000U);
    assert(net_retry_delay_ms(2) == 4000U);
    assert(net_retry_delay_ms(3) == 8000U);
    assert(net_retry_delay_ms(4) == 16000U);
    assert(net_retry_delay_ms(5) == 30000U);
    assert(net_retry_delay_ms(31) == 30000U);
    return 0;
}
