#include "net_manager/net_policy.h"

uint32_t net_retry_delay_ms(uint32_t failed_attempts) {
    if (failed_attempts >= 5U) {
        return 30000U;
    }
    return 1000U << failed_attempts;
}
