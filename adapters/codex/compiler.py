"""Compile canonical routing intent into probe-bounded Codex artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from router import load_canonical_config, validate_registry, validate_schema

from .capability_probe import CAPABILITY_ORDER, CodexProbeResult, probe_codex


ROLE_ORDER = (
    "orchestrator",
    "scout",
    "Explore",
    "mech-executor",
    "executor",
    "senior-executor",
    "verifier",
    "security-executor",
)


class CodexCompileError(ValueError):
    """Canonical intent or required capability cannot be represented safely."""


@dataclass(frozen=True)
class CodexArtifact:
    relative_path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def text(self) -> str:
        return self.content.decode("utf-8")


@dataclass(frozen=True)
class CodexCapabilityReport:
    document: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.document))

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                self.document,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True)
class CodexCompilation:
    artifacts: tuple[CodexArtifact, ...]
    capability_report: CodexCapabilityReport

    def emitted_files(self) -> tuple[CodexArtifact, ...]:
        return self.artifacts + (
            CodexArtifact("capability-report.json", self.capability_report.to_bytes()),
        )


def _load_spec(spec: Mapping[str, Any] | str | Path | None) -> Mapping[str, Any]:
    if spec is None:
        document: Any = load_canonical_config()
    elif isinstance(spec, Mapping):
        document = dict(spec)
    elif isinstance(spec, (str, Path)):
        path = Path(spec)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CodexCompileError(f"cannot load canonical spec: {path}") from error
    else:
        raise CodexCompileError("spec must be a mapping, path, or None")
    try:
        validate_schema("role-registry", document)
        validate_registry(document)
    except (TypeError, ValueError) as error:
        raise CodexCompileError(f"invalid canonical role registry: {error}") from error
    return document


def _normalize_required(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise CodexCompileError("required_capabilities must be a sequence")
    required = tuple(values)
    if any(not isinstance(item, str) or not item for item in required):
        raise CodexCompileError("required capability names must be non-empty strings")
    if len(required) != len(set(required)):
        raise CodexCompileError("required capabilities must not contain duplicates")
    unknown = sorted(set(required) - set(CAPABILITY_ORDER))
    if unknown:
        raise CodexCompileError("unknown required capabilities: " + ", ".join(unknown))
    return tuple(name for name in CAPABILITY_ORDER if name in required)


def _policy_text(roles: Mapping[str, Mapping[str, Any]]) -> bytes:
    lines = [
        "# Codex routing policy (generated)",
        "",
        "This artifact carries canonical routing intent into Codex. Named-role "
        "model and tool controls are prompt-level because the probe found "
        "invocation-wide controls only.",
        "",
        "## Invariants",
        "",
        "- Validate the Task Envelope and use the deterministic router before dispatch.",
        "- Never translate canonical model aliases into Codex model IDs without "
        "an explicit user mapping.",
        "- Never use dangerous approval or sandbox bypass flags.",
        "- Treat missing runtime model observation as UNKNOWN, never MATCHED.",
        "- Leaf roles cannot spawn child agents; this is policy intent, not a "
        "verified CLI control.",
        "- Verifier work uses a fresh ephemeral, read-only invocation when the "
        "verified controls are available.",
        "",
        "## Canonical role intent",
        "",
    ]
    for name in ROLE_ORDER:
        definition = roles[name]
        allowed = ", ".join(definition["allowed_tools"]) or "none declared"
        disallowed = ", ".join(definition["disallowed_tools"]) or "none declared"
        lines.extend(
            (
                f"### {name}",
                "",
                f"- model alias intent: `{definition['model_alias']}` (not a Codex model ID)",
                f"- effort intent: `{definition['effort']}`",
                f"- allowed tools intent: {allowed}",
                f"- disallowed tools intent: {disallowed}",
                f"- can spawn intent: `{str(definition['can_spawn']).lower()}`",
                "",
            )
        )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _append_flag(
    arguments: list[str],
    surfaces: Mapping[str, bool],
    surface: str,
    *flag: str,
) -> None:
    if surfaces.get(surface):
        arguments.extend(flag)


def _invocation_policy(probe: CodexProbeResult) -> bytes:
    surfaces = probe.surface_map()
    default: list[str] = []
    verifier: list[str] = []
    if surfaces["headless_execution"]:
        _append_flag(
            default,
            surfaces,
            "approval_policy",
            "--ask-for-approval",
            "on-request",
        )
        _append_flag(
            verifier,
            surfaces,
            "approval_policy",
            "--ask-for-approval",
            "on-request",
        )
        default.append("exec")
        verifier.append("exec")
    if default:
        _append_flag(default, surfaces, "ephemeral_sessions", "--ephemeral")
        _append_flag(default, surfaces, "sandbox_policy", "--sandbox", "workspace-write")
        _append_flag(default, surfaces, "structured_events", "--json")
    if verifier:
        _append_flag(verifier, surfaces, "ephemeral_sessions", "--ephemeral")
        _append_flag(verifier, surfaces, "sandbox_policy", "--sandbox", "read-only")
        _append_flag(
            verifier,
            surfaces,
            "output_schema",
            "--output-schema",
            "verifier-output.schema.json",
        )
        _append_flag(verifier, surfaces, "structured_events", "--json")

    verified_controls = {
        name: True for name, available in probe.surfaces if available
    }
    document = {
        "schema_version": "0.1",
        "target": "codex",
        "verified_controls": verified_controls,
        "default_arguments": default,
        "verifier_arguments": verifier,
        "forbidden_arguments": (
            ["--dangerously-bypass-approvals-and-sandbox"]
            if surfaces["dangerous_bypass"]
            else []
        ),
        "role_enforcement": {
            "model_binding": "prompt_only",
            "model_alias_mapping": None,
            "tool_policy": "global_invocation_only",
            "child_spawn_control": "prompt_only",
        },
        "attestation": {
            "runtime_model_observation": "UNKNOWN",
            "observation_source": None,
        },
    }
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _verifier_schema() -> bytes:
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["verdict"],
        "properties": {"verdict": {"enum": ["CONFIRMED", "REFUTED"]}},
        "additionalProperties": False,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _capability_report(
    probe: CodexProbeResult,
    required: Sequence[str],
    unavailable: Sequence[str],
) -> CodexCapabilityReport:
    probe_document = probe.to_dict()
    warnings = list(probe.warnings)
    if unavailable:
        warnings.append(
            "required capabilities are not fully supported: " + ", ".join(unavailable)
        )
    document = {
        "schema_version": "0.1",
        "target": "codex",
        "cli": {
            "available": probe.available,
            "binary_available": probe.binary_available,
            "executable": probe.executable,
            "version": probe.version,
            "minimum_supported": probe.minimum_version,
            "stable_version": probe.stable_version,
            "compatible": probe.compatible,
            "incompatibility": probe.incompatibility,
        },
        "probe": {
            "commands": probe_document["commands"],
            "surfaces": probe_document["surfaces"],
            "config_load": probe.config_load,
        },
        "target_configuration": probe.target_configuration,
        "future_project_overrides": probe.future_project_overrides,
        "capabilities": probe_document["capabilities"],
        "evidence": probe_document["capability_evidence"],
        "required_capabilities": list(required),
        "warnings": warnings,
    }
    return CodexCapabilityReport(document)


def compile_codex(
    spec: Mapping[str, Any] | str | Path | None = None,
    *,
    probe: CodexProbeResult | None = None,
    strict: bool = False,
    required_capabilities: Iterable[str] = (),
) -> CodexCompilation:
    """Compile only controls demonstrated by the supplied or live probe."""

    if not isinstance(strict, bool):
        raise CodexCompileError("strict must be boolean")
    document = _load_spec(spec)
    roles = validate_registry(document)
    selected_probe = probe_codex() if probe is None else probe
    if not isinstance(selected_probe, CodexProbeResult):
        raise CodexCompileError("probe must be a CodexProbeResult")
    if strict and not selected_probe.compatible:
        raise CodexCompileError(
            "strict Codex compile rejected incompatible probe: "
            + str(selected_probe.incompatibility)
        )
    required = _normalize_required(required_capabilities)
    capabilities = selected_probe.capability_map()
    unavailable = tuple(name for name in required if capabilities[name] != "supported")
    if strict and unavailable:
        raise CodexCompileError(
            "strict Codex compile requires supported capabilities: "
            + ", ".join(unavailable)
        )

    report = _capability_report(selected_probe, required, unavailable)
    artifacts = (
        CodexArtifact("codex-policy.md", _policy_text(roles)),
        CodexArtifact("invocation-policy.json", _invocation_policy(selected_probe)),
        CodexArtifact("verifier-output.schema.json", _verifier_schema()),
    )
    return CodexCompilation(artifacts=artifacts, capability_report=report)
