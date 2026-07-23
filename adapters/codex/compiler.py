"""Compile canonical routing intent into native, probe-bounded Codex artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from router import load_canonical_config, validate_registry, validate_schema

from .capability_probe import CAPABILITY_ORDER, CodexProbeResult, probe_codex


ROOT = Path(__file__).resolve().parents[2]
LEAF_ROLE_ORDER = (
    "scout",
    "Explore",
    "mech-executor",
    "executor",
    "senior-executor",
    "verifier",
    "security-executor",
)
ROLE_DESCRIPTIONS = {
    "scout": (
        "Read-only reconnaissance for exact files, symbols, usages, configuration, "
        "and concise codebase facts with file:line evidence."
    ),
    "Explore": (
        "Read-only broad exploration across multiple files, directories, or naming "
        "conventions when the caller needs a synthesized map."
    ),
    "mech-executor": (
        "Mechanical execution of fully specified edits, convention-following tests, "
        "documentation, and other bounded replay work."
    ),
    "executor": (
        "Implementation requiring bounded engineering judgment for features, bug "
        "fixes, refactors, and integration work with stable done criteria."
    ),
    "senior-executor": (
        "Escalated implementation requiring architecture-sensitive judgment after "
        "the ordinary execution path is insufficient."
    ),
    "verifier": (
        "Fresh-context adversarial verification of completed work using independent "
        "read-and-run evidence without planning, editing, or fixing."
    ),
    "security-executor": (
        "Approved security-sensitive implementation for authentication, authorization, "
        "secrets, cryptography, validation, and hardening."
    ),
}
ROLE_INSTRUCTIONS = {
    "scout": (
        "Find and report facts without modifying files or making design judgments. "
        "Search broadly first, read only relevant excerpts, and lead with direct "
        "file:line evidence. State precisely when evidence is absent."
    ),
    "Explore": (
        "Sweep the requested read-only breadth and return a synthesized map of relevant "
        "code, configuration, and naming conventions. Distinguish repository facts from "
        "gaps and do not turn reconnaissance into implementation advice."
    ),
    "mech-executor": (
        "Carry out the complete stable execution contract exactly as written. Match "
        "repository conventions and verify every done criterion. Stop with precise "
        "evidence if ambiguity or an exception requires design judgment."
    ),
    "executor": (
        "Own bounded local implementation choices while preserving repository conventions. "
        "Implement the smallest complete authorized change and verify affected behavior. "
        "Stop when an architecture fork would expand the contract."
    ),
    "senior-executor": (
        "Trace the failed or high-risk seam, choose the smallest architecture-compatible "
        "solution, and preserve every security and verification constraint. Explain "
        "consequential decisions and stop on missing evidence or conflicting constraints."
    ),
    "verifier": (
        "Independently try to refute the completed-work claim. Reproduce checks and probe "
        "plausible edge cases. Never plan, edit, or fix. Return CONFIRMED only when all "
        "material claims survive; otherwise return REFUTED with a reproducible counterexample."
    ),
    "security-executor": (
        "Implement only the approved security-sensitive contract. Work defensively at every "
        "trust boundary, preserve confirmed failures as regression checks, use audited "
        "primitives, and never weaken a control to make validation pass."
    ),
}
CODEX_ROLE_MAP = {
    "scout": {
        "model": "gpt-5.6-terra",
        "model_reasoning_effort": "low",
        "sandbox_mode": "read-only",
    },
    "Explore": {
        "model": "gpt-5.6-terra",
        "model_reasoning_effort": "low",
        "sandbox_mode": "read-only",
    },
    "mech-executor": {
        "model": "gpt-5.6-luna",
        "model_reasoning_effort": "low",
        "sandbox_mode": None,
    },
    "executor": {
        "model": "gpt-5.6-terra",
        "model_reasoning_effort": "high",
        "sandbox_mode": None,
    },
    "senior-executor": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "high",
        "sandbox_mode": None,
    },
    "verifier": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "medium",
        "sandbox_mode": "read-only",
    },
    "security-executor": {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "high",
        "sandbox_mode": None,
    },
}
_SPAWN_TOOLS = frozenset({"Agent", "Workflow"})
_READ_ONLY_ROLES = frozenset({"scout", "Explore", "verifier"})


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


def _toml_string(value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise CodexCompileError("single-line TOML value must be non-empty")
    return json.dumps(value, ensure_ascii=False)


def _developer_instructions(name: str, definition: Mapping[str, Any]) -> str:
    allowed = tuple(definition["allowed_tools"])
    disallowed = tuple(definition["disallowed_tools"])
    if definition["can_spawn"] is not False or not _SPAWN_TOOLS <= set(disallowed):
        raise CodexCompileError(f"Codex custom agent must be a canonical leaf: {name}")
    paragraphs = [
        (
            f"You are the `{name}` Pilotfish leaf agent. Complete every part of the bounded "
            "delegation yourself. Do not spawn, delegate, or call subagent collaboration tools. "
            "If the task requires child agents, stop and report that it was mis-routed."
        ),
        ROLE_INSTRUCTIONS[name],
    ]
    if allowed:
        paragraphs.append(
            "Canonical positive tool intent is limited to: "
            + ", ".join(allowed)
            + ". This allowlist is prompt guidance; keep the native sandbox boundary authoritative."
        )
    if disallowed:
        paragraphs.append(
            "Canonical denied tool intent includes: " + ", ".join(disallowed) + "."
        )
    if name in _READ_ONLY_ROLES:
        paragraphs.append(
            "Remain read-only. Do not change source, configuration, state, or generated artifacts."
        )
    else:
        paragraphs.append(
            "Inherit the parent session permission boundary. Never broaden sandbox, approval, "
            "authentication, or write scope."
        )
    return "\n\n".join(paragraphs)


def _render_agent(name: str, definition: Mapping[str, Any]) -> bytes:
    if name not in CODEX_ROLE_MAP or name not in ROLE_DESCRIPTIONS:
        raise CodexCompileError(f"Codex mapping missing for role: {name}")
    mapping = CODEX_ROLE_MAP[name]
    if mapping["model_reasoning_effort"] != definition["effort"]:
        raise CodexCompileError(f"Codex reasoning intent drift: {name}")
    instructions = _developer_instructions(name, definition)
    if "'''" in instructions:
        raise CodexCompileError(f"Codex instructions cannot be rendered safely: {name}")
    lines = [
        f"name = {_toml_string(name)}",
        f"description = {_toml_string(ROLE_DESCRIPTIONS[name])}",
        "developer_instructions = '''",
        instructions,
        "'''",
        f"model = {_toml_string(str(mapping['model']))}",
        f"model_reasoning_effort = {_toml_string(str(mapping['model_reasoning_effort']))}",
    ]
    if mapping["sandbox_mode"] is not None:
        lines.append(f"sandbox_mode = {_toml_string(str(mapping['sandbox_mode']))}")
    lines.append("")
    content = "\n".join(lines).encode("utf-8")
    _validate_rendered_agent(name, definition, content)
    return content


def _validate_rendered_agent(
    name: str, definition: Mapping[str, Any], content: bytes
) -> None:
    try:
        text = content.decode("utf-8")
        parsed = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CodexCompileError(f"invalid Codex custom-agent TOML: {name}") from error
    if "\r" in text or not text.endswith("\n"):
        raise CodexCompileError(f"Codex custom-agent TOML is not canonical LF: {name}")
    mapping = CODEX_ROLE_MAP[name]
    expected_keys = [
        "name",
        "description",
        "developer_instructions",
        "model",
        "model_reasoning_effort",
    ]
    if mapping["sandbox_mode"] is not None:
        expected_keys.append("sandbox_mode")
    if list(parsed) != expected_keys:
        raise CodexCompileError(f"Codex custom-agent field drift: {name}")
    if parsed["name"] != name or parsed["description"] != ROLE_DESCRIPTIONS[name]:
        raise CodexCompileError(f"Codex custom-agent identity drift: {name}")
    for field in ("model", "model_reasoning_effort", "sandbox_mode"):
        if parsed.get(field) != mapping[field]:
            raise CodexCompileError(f"Codex custom-agent {field} drift: {name}")
    if "Do not spawn" not in parsed["developer_instructions"]:
        raise CodexCompileError(f"Codex custom-agent spawn boundary missing: {name}")
    if definition["can_spawn"] is not False:
        raise CodexCompileError(f"Codex custom-agent is not a leaf: {name}")
    lowered = text.casefold()
    for forbidden in (
        "dangerously-bypass",
        "bypass-hook-trust",
        "api_key",
        "auth.json",
        "mcp_servers",
    ):
        if forbidden in lowered:
            raise CodexCompileError(f"unsafe Codex custom-agent content: {name}")


def _version() -> str:
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise CodexCompileError("cannot read downstream VERSION") from error
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        raise CodexCompileError("invalid downstream VERSION marker")
    return version


def _policy_text(roles: Mapping[str, Mapping[str, Any]]) -> bytes:
    role_sections = []
    for name in LEAF_ROLE_ORDER:
        if name not in roles:
            raise CodexCompileError(f"Codex policy role missing: {name}")
        role_sections.extend((f"### {name}", "", ROLE_DESCRIPTIONS[name], ""))
    role_text = "\n".join(role_sections).rstrip()
    text = f"""<!-- pilotfish:begin -->
<!-- pilotfish v{_version()} -->
# Pilotfish Codex orchestration

This block governs the main Codex session. Every named role is a non-spawning leaf. If you are
running as a named leaf, complete the bounded assignment yourself and never create a child agent.

## Enforcement boundary

- native controls: each custom-agent file binds its model and reasoning effort; read-only roles
  narrow their sandbox.
- prompt guidance: leaf no-spawn behavior, positive tool allowlists, role selection, the
  dispatch eligibility brake, no-downgrade handling, and the fresh-context verifier procedure are not
  independent native enforcement controls. `agents.max_depth` is not used because Codex ignores it
  under multi-agent V2.
- The executable canonical router is authoritative. This prose cannot replace Task Envelope
  validation, deterministic routing, or the exact returned action and role.
- Never weaken approval, sandbox, authentication, validation, or repository policy to complete a
  task. Write-capable roles inherit the parent session permission boundary.

## Orchestrator lifecycle

The main session is the virtual orchestrator. It owns framing, ambiguity resolution, planning,
approval gates, integration, and final judgment. Not every task needs delegation.

1. Stabilize outcome, allowed scope, constraints, evidence format, and stop condition.
2. Run deterministic preclassification, then validate the canonical Task Envelope.
3. Apply the security pre-route before ordinary routing.
4. Call the canonical router with validated history.
5. Dispatch only for `DELEGATE`, and only to the exact returned named role.
6. Keep `REFINE`, `TAKEOVER`, and `BLOCK` in the main control plane; they are not agent roles.

## Dispatch eligibility brake

Do not delegate while observable success conditions are unstable, evidence changes during the
same diagnosis, write ownership overlaps, or integration and verification ownership are unclear.
Delegate only when a bounded context, isolated ownership, lower worker cost, real parallelism, or
fresh-context independence outweighs reconstruction and coordination cost.

For one unknown bug, keep trace-driven diagnosis, first-fix design, and live verification together
when they share one evolving code path. Use reconnaissance only for independent side questions.

## Security, escalation, and no-downgrade

Security-sensitive work routes to `security-executor` after the required authorization gate and
never downgrades to a general executor. Failure history is monotonic. A reroute may move ordinary
execution to `senior-executor`, but cannot erase prior failures, bypass the security lane, or turn a
control-plane outcome into implementation permission.

Increment parent failure count only for canonical execution failure, misroute/spec contradiction,
or verifier refutation. Do not increment it for user cancellation, infrastructure attestation
mismatch, or verifier runtime failure.

## Fresh-context verifier

Use `verifier` only after there is a concrete completed-work claim. Supply the claim, relevant diff
or paths, and reproduction commands without the implementer's reasoning narrative. The verifier
must remain read-only, independently try to refute the claim, and never fix what it finds.

## Named leaf roles

{role_text}

<!-- pilotfish:end -->
"""
    encoded = text.encode("utf-8")
    _validate_policy(encoded)
    return encoded


def _validate_policy(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CodexCompileError("Codex orchestration policy is not UTF-8") from error
    if "\r" in text or not text.endswith("\n"):
        raise CodexCompileError("Codex orchestration policy is not canonical LF")
    if text.count("<!-- pilotfish:begin -->") != 1:
        raise CodexCompileError("Codex policy needs one begin marker")
    if text.count("<!-- pilotfish:end -->") != 1:
        raise CodexCompileError("Codex policy needs one end marker")
    required = (
        "executable canonical router",
        "dispatch eligibility brake",
        "no-downgrade",
        "fresh-context verifier",
        "prompt guidance",
        "native controls",
    )
    if any(item not in text for item in required):
        raise CodexCompileError("Codex policy enforcement boundary is incomplete")
    for name in LEAF_ROLE_ORDER:
        if f"### {name}" not in text:
            raise CodexCompileError(f"Codex policy omits role: {name}")
    if "gpt-" in text.casefold() or "dangerously-bypass" in text.casefold():
        raise CodexCompileError("Codex policy owns a forbidden target implementation detail")


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
            default, surfaces, "approval_policy", "--ask-for-approval", "on-request"
        )
        _append_flag(
            verifier, surfaces, "approval_policy", "--ask-for-approval", "on-request"
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

    document = {
        "schema_version": "0.2",
        "target": "codex",
        "verified_controls": {
            name: True for name, available in probe.surfaces if available
        },
        "default_arguments": default,
        "verifier_arguments": verifier,
        "forbidden_arguments": (
            ["--dangerously-bypass-approvals-and-sandbox"]
            if surfaces["dangerous_bypass"]
            else []
        ),
        "role_enforcement": {
            "model_binding": "native_custom_agent",
            "read_role_sandbox": "native_read_only",
            "write_role_sandbox": "inherited",
            "child_spawn_control": "prompt_guidance",
            "positive_tool_allowlists": "prompt_guidance",
            "role_selection": "canonical_router_plus_prompt_guidance",
        },
        "attestation": {
            "runtime_model_observation": "UNKNOWN",
            "account_availability": "UNKNOWN",
            "observation_source": None,
        },
    }
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


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
    warnings.append(
        "configured target model and account availability remain UNKNOWN until runtime"
    )
    if unavailable:
        warnings.append(
            "required capabilities are not fully supported: " + ", ".join(unavailable)
        )
    document = {
        "schema_version": "0.2",
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
        "role_mappings": {
            name: {
                "model": CODEX_ROLE_MAP[name]["model"],
                "model_reasoning_effort": CODEX_ROLE_MAP[name][
                    "model_reasoning_effort"
                ],
                "sandbox_mode": CODEX_ROLE_MAP[name]["sandbox_mode"] or "inherited",
            }
            for name in LEAF_ROLE_ORDER
        },
        "runtime": {
            "model_availability": "UNKNOWN",
            "account_availability": "UNKNOWN",
        },
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
    """Compile native roles and only the runtime controls demonstrated by the probe."""

    if not isinstance(strict, bool):
        raise CodexCompileError("strict must be boolean")
    document = _load_spec(spec)
    roles = validate_registry(document)
    role_artifacts = tuple(
        CodexArtifact(f"agents/{name}.toml", _render_agent(name, roles[name]))
        for name in LEAF_ROLE_ORDER
    )
    if probe is None:
        selected_probe = probe_codex(
            agent_files={item.relative_path: item.content for item in role_artifacts}
        )
    else:
        selected_probe = probe
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
    artifacts = role_artifacts + (
        CodexArtifact("AGENTS.orchestration.md", _policy_text(roles)),
        CodexArtifact("invocation-policy.json", _invocation_policy(selected_probe)),
        CodexArtifact("verifier-output.schema.json", _verifier_schema()),
    )
    return CodexCompilation(artifacts=artifacts, capability_report=report)
