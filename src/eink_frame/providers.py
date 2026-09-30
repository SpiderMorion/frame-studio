from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderKindDescriptor:
    kind: str
    label: str
    default: bool
    auth_modes: tuple[str, ...]
    implemented: bool

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["auth_modes"] = list(self.auth_modes)
        return result


PROVIDER_CATALOG = (
    ProviderKindDescriptor("comfyui", "Spark / ComfyUI", True, ("none", "bearer"), True),
    ProviderKindDescriptor("openai", "OpenAI", False, ("api-key", "codex-oauth"), False),
    ProviderKindDescriptor("openai-compatible", "OpenAI-compatible", False, ("api-key",), False),
    ProviderKindDescriptor("gemini", "Google Gemini / Imagen", False, ("api-key",), False),
    ProviderKindDescriptor("bfl", "Black Forest Labs", False, ("api-key",), False),
    ProviderKindDescriptor("stability", "Stability AI", False, ("api-key",), False),
    ProviderKindDescriptor("replicate", "Replicate", False, ("api-token",), False),
    ProviderKindDescriptor("fal", "fal.ai", False, ("api-key",), False),
)
