#include "firmware/frame_contract.hpp"
#include "firmware/journal.hpp"
#include "firmware/manifest.hpp"
#include "firmware/state_machine.hpp"

#include <array>
#include <cstdlib>
#include <iostream>
#include <string>
#include <type_traits>

#define CHECK(expr) do { if (!(expr)) { std::cerr << __FILE__ << ':' << __LINE__ << ": CHECK failed: " #expr "\n"; return false; } } while (false)

static_assert(!std::is_assignable_v<firmware::FrameManifest&, firmware::FrameManifest>);
static_assert(static_cast<std::uint16_t>(firmware::Error::invalid_profile_name) == 100U);
static_assert(static_cast<std::uint16_t>(firmware::Error::manifest_schema_mismatch) == 200U);
static_assert(static_cast<std::uint16_t>(firmware::Error::journal_invalid_magic) == 300U);
static_assert(static_cast<std::uint16_t>(firmware::Error::invalid_transition) == 400U);

namespace {

bool frame_profile_accepts_exact_contract() {
  const firmware::FrameProfile profile{"gdep133c02-native-transverse-v1", {0, 1, 2, 3, 5, 6}, firmware::NibbleOrder::high_first};
  CHECK(firmware::validate_profile(profile) == firmware::Error::ok);
  CHECK(firmware::FrameContract::width == 1200);
  CHECK(firmware::FrameContract::height == 1600);
  CHECK(firmware::FrameContract::byte_length == 960000);
  CHECK(firmware::FrameContract::bytes_per_row == 600);
  CHECK(firmware::FrameContract::bytes_per_controller_row == 300);
  CHECK(firmware::FrameContract::controller_row_offset(0, 0) == 0);
  CHECK(firmware::FrameContract::controller_row_offset(0, 1) == 300);
  CHECK(firmware::FrameContract::controller_row_offset(1, 0) == 600);
  return true;
}

bool frame_profile_rejects_duplicate_codes() {
  const firmware::FrameProfile profile{"duplicate", {0, 1, 2, 3, 4, 4}, firmware::NibbleOrder::low_first};
  CHECK(firmware::validate_profile(profile) == firmware::Error::duplicate_nibble_code);
  return true;
}

bool frame_profile_rejects_non_nibble_code() {
  const firmware::FrameProfile profile{"invalid", {0, 1, 2, 3, 4, 16}, firmware::NibbleOrder::high_first};
  CHECK(firmware::validate_profile(profile) == firmware::Error::invalid_nibble_code);
  const firmware::FrameProfile unnamed{"", {0, 1, 2, 3, 4, 5}, firmware::NibbleOrder::high_first};
  CHECK(firmware::validate_profile(unnamed) == firmware::Error::invalid_profile_name);
  return true;
}

bool nibble_order_is_profile_configurable() {
  const firmware::FrameProfile high{"high", {1, 2, 3, 4, 5, 6}, firmware::NibbleOrder::high_first};
  const firmware::FrameProfile low{"low", {1, 2, 3, 4, 5, 6}, firmware::NibbleOrder::low_first};
  CHECK(firmware::pack_logical_pair(high, 0, 5).value == 0x16U);
  CHECK(firmware::pack_logical_pair(low, 0, 5).value == 0x61U);
  CHECK(firmware::pack_logical_pair(high, 6, 0).error == firmware::Error::invalid_logical_color);
  return true;
}

bool manifest_rejects_replayed_sequence() {
  const firmware::FrameManifest manifest{1, "weather-frame", "GDEP133C02", 42, 960000,
      "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "gdep133c02-native-transverse-v1"};
  const firmware::ManifestPolicy policy{1, "weather-frame", "GDEP133C02", 42, "gdep133c02-native-transverse-v1"};
  CHECK(firmware::validate_manifest(manifest, policy) == firmware::Error::manifest_sequence_replay);
  return true;
}

bool manifest_enforces_immutable_contract() {
  constexpr auto hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
  const firmware::ManifestPolicy policy{1, "weather-frame", "GDEP133C02", 41, "gdep133c02-native-transverse-v1"};
  CHECK(firmware::validate_manifest({2, "weather-frame", "GDEP133C02", 42, 960000, hash, "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::manifest_schema_mismatch);
  CHECK(firmware::validate_manifest({1, "other", "GDEP133C02", 42, 960000, hash, "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::manifest_target_mismatch);
  CHECK(firmware::validate_manifest({1, "weather-frame", "unknown", 42, 960000, hash, "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::manifest_panel_mismatch);
  CHECK(firmware::validate_manifest({1, "weather-frame", "GDEP133C02", 42, 1, hash, "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::manifest_size_mismatch);
  CHECK(firmware::validate_manifest({1, "weather-frame", "GDEP133C02", 42, 960000, "ABC", "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::manifest_hash_invalid);
  CHECK(firmware::validate_manifest({1, "weather-frame", "GDEP133C02", 42, 960000, hash, "other"}, policy) == firmware::Error::manifest_profile_mismatch);
  CHECK(firmware::validate_manifest({1, "weather-frame", "GDEP133C02", 42, 960000, hash, "gdep133c02-native-transverse-v1"}, policy) == firmware::Error::ok);
  return true;
}

bool artifact_evidence_must_match_manifest() {
  constexpr auto hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
  const firmware::FrameManifest manifest{1, "weather-frame", "GDEP133C02", 42, 960000, hash, "profile"};
  CHECK(firmware::validate_artifact(manifest, {1, hash, "profile"}) == firmware::Error::manifest_size_mismatch);
  CHECK(firmware::validate_artifact(manifest, {960000, std::string(64, '0'), "profile"}) == firmware::Error::manifest_hash_invalid);
  CHECK(firmware::validate_artifact(manifest, {960000, hash, "other"}) == firmware::Error::manifest_profile_mismatch);
  CHECK(firmware::validate_artifact(manifest, {960000, hash, "profile"}) == firmware::Error::ok);
  return true;
}

bool journal_round_trips_and_rejects_corruption() {
  firmware::JournalEntry entry{7, {}, {}, firmware::JournalState::staged};
  entry.frame_id[0] = 0xA5U;
  entry.frame_hash[31] = 0x5AU;
  auto bytes = firmware::encode_journal(entry);
  const auto decoded = firmware::decode_journal(bytes);
  CHECK(decoded.error == firmware::Error::ok);
  CHECK(decoded.entry == entry);
  bytes[12] ^= 0x01U;
  CHECK(firmware::decode_journal(bytes).error == firmware::Error::journal_crc_mismatch);
  constexpr std::array<std::uint8_t, 9> vector{'1','2','3','4','5','6','7','8','9'};
  CHECK(firmware::crc32(vector.data(), vector.size()) == 0xCBF43926U);
  return true;
}

bool journal_selects_newest_valid_with_wrap_policy() {
  const firmware::JournalEntry before_wrap{0xFFFFFFFEU, {}, {}, firmware::JournalState::displayed};
  const firmware::JournalEntry after_wrap{1U, {}, {}, firmware::JournalState::staged};
  const auto slot0 = firmware::encode_journal(before_wrap);
  auto slot1 = firmware::encode_journal(after_wrap);
  auto selected = firmware::select_journal_slots(slot0, slot1);
  CHECK(selected.error == firmware::Error::ok);
  CHECK(selected.slot_index == 1U);
  slot1[20] ^= 1U;
  selected = firmware::select_journal_slots(slot0, slot1);
  CHECK(selected.error == firmware::Error::ok);
  CHECK(selected.slot_index == 0U);
  auto corrupt0 = slot0;
  corrupt0[20] ^= 1U;
  CHECK(firmware::select_journal_slots(corrupt0, slot1).error == firmware::Error::journal_no_valid_slot);
  const auto half = firmware::encode_journal({0x7FFFFFFEU, {}, {}, firmware::JournalState::staged});
  CHECK(firmware::select_journal_slots(slot0, half).error == firmware::Error::journal_ambiguous_generation);
  return true;
}

bool reducer_drives_full_happy_path_and_degrades_purely() {
  using firmware::Event;
  using firmware::State;
  State state = State::boot;
  for (const auto event : {Event::booted, Event::recovered, Event::connected,
                            Event::manifest_available, Event::downloaded,
                            Event::verified, Event::promoted, Event::displayed,
                            Event::committed}) {
    const auto result = firmware::reduce(state, event);
    CHECK(result.error == firmware::Error::ok);
    state = result.state;
  }
  CHECK(state == State::poll);
  CHECK(firmware::reduce(State::download, Event::failure).state == State::degraded);
  CHECK(firmware::reduce(State::boot, Event::downloaded).error == firmware::Error::invalid_transition);
  CHECK(firmware::reduce(State::boot, Event::downloaded).state == State::boot);
  CHECK(firmware::reduce(State::degraded, Event::retry).state == State::connect);
  return true;
}

}  // namespace

int main() {
  if (!frame_profile_accepts_exact_contract()) return EXIT_FAILURE;
  if (!frame_profile_rejects_duplicate_codes()) return EXIT_FAILURE;
  if (!frame_profile_rejects_non_nibble_code()) return EXIT_FAILURE;
  if (!nibble_order_is_profile_configurable()) return EXIT_FAILURE;
  if (!manifest_rejects_replayed_sequence()) return EXIT_FAILURE;
  if (!manifest_enforces_immutable_contract()) return EXIT_FAILURE;
  if (!artifact_evidence_must_match_manifest()) return EXIT_FAILURE;
  if (!journal_round_trips_and_rejects_corruption()) return EXIT_FAILURE;
  if (!journal_selects_newest_valid_with_wrap_policy()) return EXIT_FAILURE;
  if (!reducer_drives_full_happy_path_and_degrades_purely()) return EXIT_FAILURE;
  return EXIT_SUCCESS;
}
