#pragma once

#include "firmware/frame_contract.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace firmware {

enum class JournalState : std::uint8_t { staged = 1, displayed = 2 };

struct JournalEntry final {
  std::uint32_t generation;
  std::array<std::uint8_t, 16> frame_id;
  std::array<std::uint8_t, 32> frame_hash;
  JournalState state;
  bool operator==(const JournalEntry&) const = default;
};

inline constexpr std::size_t journal_record_size = 64;
using JournalBytes = std::array<std::uint8_t, journal_record_size>;

struct JournalDecodeResult final {
  Error error;
  JournalEntry entry;
};

struct JournalSelection final {
  Error error;
  std::uint8_t slot_index;
  JournalEntry entry;
};

[[nodiscard]] std::uint32_t crc32(const std::uint8_t* data, std::size_t size) noexcept;
[[nodiscard]] JournalBytes encode_journal(const JournalEntry& entry) noexcept;
[[nodiscard]] JournalDecodeResult decode_journal(const JournalBytes& bytes) noexcept;
// One valid slot wins. Equal generations choose slot 1. Exactly half-range
// generations are ambiguous; all other uint32_t differences use serial order.
[[nodiscard]] JournalSelection select_journal_slots(const JournalBytes& slot0,
                                                    const JournalBytes& slot1) noexcept;

}  // namespace firmware
