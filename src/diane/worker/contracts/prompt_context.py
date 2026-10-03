"""Prompt envelope and cache-provenance contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from diane.worker.core.contracts import OutputContract


def stable_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def schema_hash(schema: dict[str, Any] | None) -> str:
    return stable_hash(schema or {})


def output_contract_hash(contract: OutputContract | dict[str, Any] | None) -> str:
    if contract is None:
        return ""
    payload = contract.model_dump(mode="json", exclude_none=True) if isinstance(contract, OutputContract) else dict(contract)
    return stable_hash(payload)


class PromptContext(BaseModel):
    """Stable and dynamic prompt provenance tracked by the runtime."""

    prompt_architecture_id: str = "universal-cell-prompt-v1"
    system_constitution_id: str = "universal-cell-system-v1"
    tool_schema_hash: str = ""
    policy_schema_hash: str = ""
    output_contract_hash: str = ""
    slot_values: dict[str, Any] = Field(default_factory=dict)
    dynamic_snapshot_refs: list[str] = Field(default_factory=list)
    rendered_prompt_provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def stable_fingerprint(self) -> str:
        """Fingerprint architecture/schema ids only; dynamic context does not affect it."""

        return stable_hash(
            {
                "prompt_architecture_id": self.prompt_architecture_id,
                "system_constitution_id": self.system_constitution_id,
                "tool_schema_hash": self.tool_schema_hash,
                "policy_schema_hash": self.policy_schema_hash,
                "output_contract_hash": self.output_contract_hash,
            }
        )

    def slot_fingerprint(self) -> str:
        """Fingerprint the rendered slot values while excluding dynamic snapshot refs."""

        return stable_hash({"stable": self.stable_fingerprint(), "slot_values": self.slot_values})

    def full_fingerprint(self) -> str:
        """Fingerprint stable prompt context plus dynamic refs for trace/debug use."""

        return stable_hash(
            {
                "stable": self.stable_fingerprint(),
                "slot_values": self.slot_values,
                "dynamic_snapshot_refs": self.dynamic_snapshot_refs,
                "rendered_prompt_provenance": self.rendered_prompt_provenance,
            }
        )


class PromptEnvelope(BaseModel):
    """Rendered prompt text plus explicit provenance."""

    system_prompt: str
    user_prompt: str
    context: PromptContext = Field(default_factory=PromptContext)
    metadata: dict[str, Any] = Field(default_factory=dict)
