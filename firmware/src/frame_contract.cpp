#include "firmware/frame_contract.hpp"

namespace firmware {

Error validate_profile(const FrameProfile& profile) noexcept {
  if (profile.name.empty()) {
    return Error::invalid_profile_name;
  }
  for (const auto code : profile.nibble_codes) {
    if (code > 0x0FU) {
      return Error::invalid_nibble_code;
    }
  }
  for (std::size_t left = 0; left < profile.nibble_codes.size(); ++left) {
    for (std::size_t right = left + 1; right < profile.nibble_codes.size(); ++right) {
      if (profile.nibble_codes[left] == profile.nibble_codes[right]) {
        return Error::duplicate_nibble_code;
      }
    }
  }
  return Error::ok;
}

PackResult pack_logical_pair(const FrameProfile& profile,
                             const std::uint8_t left,
                             const std::uint8_t right) noexcept {
  if (left >= FrameContract::logical_color_count ||
      right >= FrameContract::logical_color_count) {
    return {Error::invalid_logical_color, 0};
  }
  const auto left_code = profile.nibble_codes[left];
  const auto right_code = profile.nibble_codes[right];
  if (profile.nibble_order == NibbleOrder::high_first) {
    return {Error::ok, static_cast<std::uint8_t>((left_code << 4U) | right_code)};
  }
  return {Error::ok, static_cast<std::uint8_t>((right_code << 4U) | left_code)};
}

}  // namespace firmware
