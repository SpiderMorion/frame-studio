#include "firmware/journal.hpp"

#include <algorithm>

namespace firmware {
namespace {

constexpr std::array<std::uint8_t, 4> magic{'E', 'J', 'N', 'L'};
constexpr std::uint16_t version = 1;
constexpr std::size_t crc_offset = 60;

void store_u32(JournalBytes& bytes, const std::size_t offset,
               const std::uint32_t value) noexcept {
  for (std::size_t index = 0; index < 4; ++index) {
    bytes[offset + index] = static_cast<std::uint8_t>(value >> (index * 8U));
  }
}

std::uint32_t load_u32(const JournalBytes& bytes,
                       const std::size_t offset) noexcept {
  std::uint32_t value = 0;
  for (std::size_t index = 0; index < 4; ++index) {
    value |= static_cast<std::uint32_t>(bytes[offset + index]) << (index * 8U);
  }
  return value;
}

}  // namespace

std::uint32_t crc32(const std::uint8_t* data, const std::size_t size) noexcept {
  std::uint32_t crc = 0xFFFFFFFFU;
  for (std::size_t index = 0; index < size; ++index) {
    crc ^= data[index];
    for (std::uint8_t bit = 0; bit < 8U; ++bit) {
      const auto mask = static_cast<std::uint32_t>(
          0U - static_cast<std::uint32_t>(crc & 1U));
      crc = (crc >> 1U) ^ (0xEDB88320U & mask);
    }
  }
  return ~crc;
}

JournalBytes encode_journal(const JournalEntry& entry) noexcept {
  JournalBytes bytes{};
  std::ranges::copy(magic, bytes.begin());
  bytes[4] = static_cast<std::uint8_t>(version);
  bytes[5] = static_cast<std::uint8_t>(version >> 8U);
  bytes[6] = static_cast<std::uint8_t>(entry.state);
  store_u32(bytes, 8, entry.generation);
  std::ranges::copy(entry.frame_id, bytes.begin() + 12);
  std::ranges::copy(entry.frame_hash, bytes.begin() + 28);
  store_u32(bytes, crc_offset, crc32(bytes.data(), crc_offset));
  return bytes;
}

JournalDecodeResult decode_journal(const JournalBytes& bytes) noexcept {
  JournalEntry entry{};
  if (!std::equal(magic.begin(), magic.end(), bytes.begin())) {
    return {Error::journal_invalid_magic, entry};
  }
  const auto stored_version = static_cast<std::uint16_t>(bytes[4]) |
      static_cast<std::uint16_t>(static_cast<std::uint16_t>(bytes[5]) << 8U);
  if (stored_version != version) {
    return {Error::journal_invalid_version, entry};
  }
  if (bytes[6] != static_cast<std::uint8_t>(JournalState::staged) &&
      bytes[6] != static_cast<std::uint8_t>(JournalState::displayed)) {
    return {Error::journal_invalid_state, entry};
  }
  if (load_u32(bytes, crc_offset) != crc32(bytes.data(), crc_offset)) {
    return {Error::journal_crc_mismatch, entry};
  }
  entry.generation = load_u32(bytes, 8);
  std::copy_n(bytes.begin() + 12, entry.frame_id.size(), entry.frame_id.begin());
  std::copy_n(bytes.begin() + 28, entry.frame_hash.size(), entry.frame_hash.begin());
  entry.state = static_cast<JournalState>(bytes[6]);
  return {Error::ok, entry};
}

JournalSelection select_journal_slots(const JournalBytes& slot0,
                                      const JournalBytes& slot1) noexcept {
  const auto first = decode_journal(slot0);
  const auto second = decode_journal(slot1);
  const bool first_valid = first.error == Error::ok;
  const bool second_valid = second.error == Error::ok;
  if (!first_valid && !second_valid) {
    return {Error::journal_no_valid_slot, 0, {}};
  }
  if (first_valid && !second_valid) {
    return {Error::ok, 0, first.entry};
  }
  if (!first_valid && second_valid) {
    return {Error::ok, 1, second.entry};
  }
  const auto difference = second.entry.generation - first.entry.generation;
  if (difference == 0x80000000U) {
    return {Error::journal_ambiguous_generation, 0, {}};
  }
  if (difference == 0U || difference < 0x80000000U) {
    return {Error::ok, 1, second.entry};
  }
  return {Error::ok, 0, first.entry};
}

}  // namespace firmware
