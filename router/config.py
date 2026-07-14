"""Canonical JSON-compatible YAML configuration loading."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "routing.yaml"


def _read_config() -> Dict[str, Any]:
    document = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("routing.yaml must contain a JSON object")
    return document


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_canonical_config() -> Dict[str, Any]:
    """Return an independent mutable copy of the canonical configuration."""

    return copy.deepcopy(_read_config())


_CANONICAL_CONFIG: Mapping[str, Any] = _freeze(_read_config())


def canonical_config_snapshot() -> Mapping[str, Any]:
    """Return the recursively immutable import-time routing snapshot."""

    return _CANONICAL_CONFIG


def thaw(value: Any) -> Any:
    """Copy a recursively frozen JSON value into ordinary containers."""

    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return copy.deepcopy(value)
