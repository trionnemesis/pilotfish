"""Immutable value objects returned by the canonical router."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class RoutingDecision:
    action: str
    role: Optional[str]
    reason_code: str

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "action": self.action,
            "role": self.role,
            "reason_code": self.reason_code,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class RunHandle:
    task_id: str
    role: str
    spec_ref: str
    model_alias: str
    effort: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "spec_ref": self.spec_ref,
            "model_alias": self.model_alias,
            "effort": self.effort,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())
