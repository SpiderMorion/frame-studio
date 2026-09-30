#include "firmware/manifest.hpp"

#include <algorithm>

namespace firmware {
namespace {

bool is_lower_hex_sha256(const std::string_view value) noexcept {
  return value.size() == 64U && std::ranges::all_of(value, [](const char character) {
    return (character >= '0' && character <= '9') ||
           (character >= 'a' && character <= 'f');
  });
}

}  // namespace

Error validate_manifest(const FrameManifest& manifest,
                        const ManifestPolicy& policy) noexcept {
  if (manifest.schema_version != policy.schema_version) {
    return Error::manifest_schema_mismatch;
  }
  if (manifest.target != policy.target) {
    return Error::manifest_target_mismatch;
  }
  if (manifest.panel != policy.panel) {
    return Error::manifest_panel_mismatch;
  }
  if (manifest.sequence <= policy.last_accepted_sequence) {
    return Error::manifest_sequence_replay;
  }
  if (manifest.byte_length != FrameContract::byte_length) {
    return Error::manifest_size_mismatch;
  }
  if (!is_lower_hex_sha256(manifest.sha256)) {
    return Error::manifest_hash_invalid;
  }
  if (manifest.profile != policy.profile) {
    return Error::manifest_profile_mismatch;
  }
  return Error::ok;
}

Error validate_artifact(const FrameManifest& manifest,
                        const ArtifactEvidence& evidence) noexcept {
  if (evidence.byte_length != manifest.byte_length) {
    return Error::manifest_size_mismatch;
  }
  if (evidence.sha256 != manifest.sha256) {
    return Error::manifest_hash_invalid;
  }
  if (evidence.profile != manifest.profile) {
    return Error::manifest_profile_mismatch;
  }
  return Error::ok;
}

}  // namespace firmware
