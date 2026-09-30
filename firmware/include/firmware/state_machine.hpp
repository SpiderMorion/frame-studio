#pragma once

#include "firmware/frame_contract.hpp"

#include <cstdint>

namespace firmware {

enum class State : std::uint8_t {
  boot,
  recover,
  connect,
  poll,
  download,
  verify,
  promote,
  display,
  commit,
  degraded,
};

enum class Event : std::uint8_t {
  booted,
  recovered,
  connected,
  manifest_available,
  downloaded,
  verified,
  promoted,
  displayed,
  committed,
  failure,
  retry,
};

struct Reduction final {
  State state;
  Error error;
};

[[nodiscard]] Reduction reduce(State current, Event event) noexcept;

}  // namespace firmware
