#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace firmware {

enum class Error : std::uint16_t {
  ok = 0,
  invalid_profile_name = 100,
  invalid_nibble_code = 101,
  duplicate_nibble_code = 102,
  invalid_logical_color = 103,
  manifest_schema_mismatch = 200,
  manifest_target_mismatch = 201,
  manifest_panel_mismatch = 202,
  manifest_sequence_replay = 203,
  manifest_size_mismatch = 204,
  manifest_hash_invalid = 205,
  manifest_profile_mismatch = 206,
  journal_invalid_magic = 300,
  journal_invalid_version = 301,
  journal_invalid_state = 302,
  journal_crc_mismatch = 303,
  journal_no_valid_slot = 304,
  journal_ambiguous_generation = 305,
  invalid_transition = 400,
};

enum class NibbleOrder : std::uint8_t { high_first = 0, low_first = 1 };

struct FrameContract final {
  static constexpr std::uint32_t width = 1200;
  static constexpr std::uint32_t height = 1600;
  static constexpr std::size_t bytes_per_row = width / 2U;
  static constexpr std::size_t controller_count = 2;
  static constexpr std::size_t bytes_per_controller_row = bytes_per_row / controller_count;
  static constexpr std::size_t byte_length = bytes_per_row * height;
  static constexpr std::size_t logical_color_count = 6;

  [[nodiscard]] static constexpr std::size_t controller_row_offset(
      std::size_t row, std::size_t controller) noexcept {
    return row * bytes_per_row + controller * bytes_per_controller_row;
  }
};

struct FrameProfile final {
  std::string_view name;
  std::array<std::uint8_t, FrameContract::logical_color_count> nibble_codes;
  NibbleOrder nibble_order;
};

struct PackResult final {
  Error error;
  std::uint8_t value;
};

[[nodiscard]] Error validate_profile(const FrameProfile& profile) noexcept;
[[nodiscard]] PackResult pack_logical_pair(const FrameProfile& profile,
                                           std::uint8_t left,
                                           std::uint8_t right) noexcept;

}  // namespace firmware
