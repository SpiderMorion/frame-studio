#pragma once

#include "firmware/frame_contract.hpp"

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace firmware {

struct FrameManifest final {
  const std::uint32_t schema_version;
  const std::string target;
  const std::string panel;
  const std::uint64_t sequence;
  const std::size_t byte_length;
  const std::string sha256;
  const std::string profile;
};

struct ManifestPolicy final {
  const std::uint32_t schema_version;
  const std::string_view target;
  const std::string_view panel;
  const std::uint64_t last_accepted_sequence;
  const std::string_view profile;
};

struct ArtifactEvidence final {
  std::size_t byte_length;
  std::string_view sha256;
  std::string_view profile;
};

[[nodiscard]] Error validate_manifest(const FrameManifest& manifest,
                                      const ManifestPolicy& policy) noexcept;
[[nodiscard]] Error validate_artifact(const FrameManifest& manifest,
                                      const ArtifactEvidence& evidence) noexcept;

}  // namespace firmware
