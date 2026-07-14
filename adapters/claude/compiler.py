"""Compile the canonical role registry into byte-stable Claude artifacts.

The canonical registry owns role names, model aliases, effort, and tool policy.
Claude-specific prose lives beside this module as target templates.  Checked-in
files under ``templates/`` are golden outputs, never compiler inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from router import load_canonical_config, validate_registry, validate_schema


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
POLICY_TEMPLATE_PATH = TEMPLATE_ROOT / "orchestration-policy.md"
SETTINGS_TEMPLATE_PATH = TEMPLATE_ROOT / "settings.patch.json"

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
        "Read-only reconnaissance for locating files, symbols, usages, config "
        "values, and concise codebase facts with file:line evidence."
    ),
    "Explore": (
        "Read-only broad exploration across multiple files, directories, or "
        "naming conventions when the caller needs a synthesized map."
    ),
    "mech-executor": (
        "Mechanical execution of fully specified work such as pattern-based "
        "edits, convention-following tests, documentation, and bounded replay."
    ),
    "executor": (
        "Implementation requiring bounded engineering judgment for features, "
        "bug fixes, refactors, and integration work with stable done criteria."
    ),
    "senior-executor": (
        "Escalated implementation requiring architecture-sensitive judgment "
        "after the ordinary execution path is insufficient."
    ),
    "verifier": (
        "Fresh-context adversarial verification of completed work using "
        "read-and-run evidence without planning, editing, or fixing."
    ),
    "security-executor": (
        "Approved security-sensitive implementation for authentication, "
        "authorization, secrets, crypto, validation, and hardening."
    ),
}

CAPABILITY_ORDER = (
    "per_role_model_binding",
    "per_role_tool_policy",
    "child_spawn_control",
    "fresh_context_verifier",
    "runtime_model_observation",
    "isolated_parallel_writes",
)

CLAUDE_CAPABILITIES = {
    "per_role_model_binding": "supported",
    "per_role_tool_policy": "supported",
    "child_spawn_control": "supported",
    "fresh_context_verifier": "supported",
    # Runtime observation is best-effort and belongs to the attestation phase.
    "runtime_model_observation": "degraded",
    "isolated_parallel_writes": "supported",
}

CAPABILITY_STATUSES = frozenset(("supported", "degraded", "unsupported"))
SPAWN_TOOLS = frozenset(("Agent", "Workflow"))


class ClaudeCompileError(ValueError):
    """The canonical input cannot be represented by the Claude adapter."""


@dataclass(frozen=True)
class AdapterArtifact:
    """One deterministic target artifact."""

    relative_path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def text(self) -> str:
        return self.content.decode("utf-8")


@dataclass(frozen=True)
class AdapterArtifacts:
    """Claude artifacts in deterministic emission order."""

    machine_settings_patch: AdapterArtifact
    role_definitions: Tuple[AdapterArtifact, ...]
    orchestration_policy: AdapterArtifact

    def all(self) -> Tuple[AdapterArtifact, ...]:
        return (
            (self.machine_settings_patch,)
            + self.role_definitions
            + (self.orchestration_policy,)
        )

    def by_path(self, relative_path: str) -> AdapterArtifact:
        for artifact in self.all():
            if artifact.relative_path == relative_path:
                return artifact
        raise KeyError(relative_path)


@dataclass(frozen=True)
class CapabilityReport:
    """Canonical capability vocabulary for one Claude compilation."""

    schema_version: str
    target: str
    capabilities: Tuple[Tuple[str, str], ...]
    runtime_requirements: Tuple[Tuple[str, str], ...]
    evidence: Tuple[Tuple[str, str], ...]
    required_capabilities: Tuple[str, ...]
    warnings: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target": self.target,
            "capabilities": dict(self.capabilities),
            "runtime_requirements": dict(self.runtime_requirements),
            "evidence": dict(self.evidence),
            "required_capabilities": list(self.required_capabilities),
            "warnings": list(self.warnings),
        }

    def to_bytes(self) -> bytes:
        document = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return (document + "\n").encode("utf-8")


@dataclass(frozen=True)
class ParsedAgentDefinition:
    """Semantic view of generated Claude agent frontmatter and prompt."""

    name: str
    description: str
    model: str
    effort: str
    tools: Tuple[str, ...]
    disallowed_tools: Tuple[str, ...]
    body: str


@dataclass(frozen=True)
class ClaudeCompilation:
    """The target artifacts and their explicit capability report."""

    artifacts: AdapterArtifacts
    capability_report: CapabilityReport

    def emitted_files(self) -> Tuple[AdapterArtifact, ...]:
        report = AdapterArtifact(
            relative_path="capability-report.json",
            content=self.capability_report.to_bytes(),
        )
        return self.artifacts.all() + (report,)


SpecInput = Optional[Union[Mapping[str, Any], str, Path]]


def _load_spec(spec: SpecInput) -> Mapping[str, Any]:
    if spec is None:
        document: Any = load_canonical_config()
    elif isinstance(spec, Mapping):
        document = dict(spec)
    elif isinstance(spec, (str, Path)):
        path = Path(spec)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ClaudeCompileError(f"cannot load canonical spec: {path}") from exc
    else:
        raise ClaudeCompileError("spec must be a mapping, path, or None")

    try:
        validate_schema("role-registry", document)
        validate_registry(document)
    except (TypeError, ValueError) as exc:
        raise ClaudeCompileError(f"invalid canonical role registry: {exc}") from exc
    return document


def _read_template(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ClaudeCompileError(f"cannot read Claude template: {path.name}") from exc
    if content.startswith("\ufeff"):
        raise ClaudeCompileError(f"Claude template has a UTF-8 BOM: {path.name}")
    if "\r" in content:
        raise ClaudeCompileError(f"Claude template must use LF endings: {path.name}")
    if not content.endswith("\n"):
        raise ClaudeCompileError(f"Claude template needs a final newline: {path.name}")
    return content


def _frontmatter_line(key: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise ClaudeCompileError(f"invalid frontmatter value for {key}")
    # JSON strings are valid YAML scalars and make punctuation unambiguous.
    if key == "description":
        return f"{key}: {json.dumps(value, ensure_ascii=False)}"
    return f"{key}: {value}"


def _render_agent(name: str, definition: Mapping[str, Any]) -> bytes:
    if name not in ROLE_DESCRIPTIONS:
        raise ClaudeCompileError(f"Claude prose template missing for role: {name}")

    allowed = tuple(definition["allowed_tools"])
    disallowed = tuple(definition["disallowed_tools"])
    if definition["can_spawn"] is not False:
        raise ClaudeCompileError(f"Claude agent role must be a leaf: {name}")
    if not SPAWN_TOOLS <= frozenset(disallowed):
        raise ClaudeCompileError(f"Claude leaf role must deny Agent and Workflow: {name}")

    lines = [
        "---",
        _frontmatter_line("name", name),
        _frontmatter_line("description", ROLE_DESCRIPTIONS[name]),
        _frontmatter_line("model", definition["model_alias"]),
        _frontmatter_line("effort", definition["effort"]),
    ]
    if allowed:
        lines.append(_frontmatter_line("tools", ", ".join(allowed)))
    if disallowed:
        lines.append(
            _frontmatter_line("disallowedTools", ", ".join(disallowed))
        )
    lines.extend(("---", ""))

    preamble = _read_template(TEMPLATE_ROOT / "leaf-preamble.md")
    body = _read_template(TEMPLATE_ROOT / f"{name}.md")
    # Templates each end with exactly one newline; the compiler owns the
    # paragraph separator so source files do not need a trailing blank line.
    content = "\n".join(lines) + preamble + "\n" + body
    encoded = content.encode("utf-8")
    _validate_rendered_agent(name, definition, encoded)
    return encoded


def _parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    if not content.startswith("---\n"):
        raise ClaudeCompileError("agent definition is missing opening frontmatter")
    try:
        _, raw_frontmatter, body = content.split("---", 2)
    except ValueError as exc:
        raise ClaudeCompileError("agent definition has incomplete frontmatter") from exc

    fields: Dict[str, str] = {}
    for raw_line in raw_frontmatter.strip().splitlines():
        if ":" not in raw_line:
            raise ClaudeCompileError("agent frontmatter contains an invalid line")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if key in fields:
            raise ClaudeCompileError(f"duplicate agent frontmatter field: {key}")
        fields[key] = value.strip()
    return fields, body.lstrip("\n")


def _split_tools(value: str) -> Tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(","))


def parse_agent_definition(content: Union[bytes, str]) -> ParsedAgentDefinition:
    """Parse the supported Claude frontmatter fields without a YAML dependency."""

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClaudeCompileError("agent definition is not UTF-8") from exc
    elif isinstance(content, str):
        text = content
    else:
        raise ClaudeCompileError("agent definition must be bytes or text")

    fields, body = _parse_frontmatter(text)
    required = ("name", "description", "model", "effort")
    missing = tuple(field for field in required if field not in fields)
    unknown = tuple(
        field
        for field in fields
        if field not in required + ("tools", "disallowedTools")
    )
    if missing or unknown:
        raise ClaudeCompileError(
            f"invalid agent frontmatter fields; missing={missing}, unknown={unknown}"
        )
    try:
        description = json.loads(fields["description"])
    except json.JSONDecodeError as exc:
        raise ClaudeCompileError("agent description must be a quoted YAML scalar") from exc
    if not isinstance(description, str) or not description:
        raise ClaudeCompileError("agent description must be non-empty text")
    return ParsedAgentDefinition(
        name=fields["name"],
        description=description,
        model=fields["model"],
        effort=fields["effort"],
        tools=_split_tools(fields.get("tools", "")),
        disallowed_tools=_split_tools(fields.get("disallowedTools", "")),
        body=body,
    )


def _validate_rendered_agent(
    name: str, definition: Mapping[str, Any], content: bytes
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaudeCompileError(f"agent definition is not UTF-8: {name}") from exc
    if "\r" in text or not text.endswith("\n"):
        raise ClaudeCompileError(f"agent definition is not canonical LF text: {name}")

    fields, _ = _parse_frontmatter(text)
    parsed = parse_agent_definition(text)
    expected_keys = ["name", "description", "model", "effort"]
    if definition["allowed_tools"]:
        expected_keys.append("tools")
    if definition["disallowed_tools"]:
        expected_keys.append("disallowedTools")
    if list(fields) != expected_keys:
        raise ClaudeCompileError(f"agent frontmatter field drift: {name}")

    if parsed.name != name or parsed.description != ROLE_DESCRIPTIONS[name]:
        raise ClaudeCompileError(f"agent identity drift: {name}")
    if parsed.model != definition["model_alias"]:
        raise ClaudeCompileError(f"agent model binding drift: {name}")
    if parsed.effort != definition["effort"]:
        raise ClaudeCompileError(f"agent effort binding drift: {name}")
    if parsed.tools != tuple(definition["allowed_tools"]):
        raise ClaudeCompileError(f"agent tool allowlist drift: {name}")
    if parsed.disallowed_tools != tuple(definition["disallowed_tools"]):
        raise ClaudeCompileError(f"agent tool denylist drift: {name}")
    if (
        "You are a leaf agent" not in parsed.body
        or "Never delegate" not in parsed.body
    ):
        raise ClaudeCompileError(f"agent leaf boundary missing from prompt: {name}")
    if any(line.startswith("model:") for line in parsed.body.splitlines()):
        raise ClaudeCompileError(f"agent body contains a second model binding: {name}")


def _compile_machine_settings_patch() -> bytes:
    content = _read_template(SETTINGS_TEMPLATE_PATH)
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ClaudeCompileError("machine settings patch must be valid JSON") from exc
    if document != {"model": "best", "fallbackModel": ["opus", "sonnet"]}:
        raise ClaudeCompileError("machine settings patch differs from v0.1 policy")
    return content.encode("utf-8")


def _validate_policy(content: bytes, roles: Mapping[str, Mapping[str, Any]]) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaudeCompileError("orchestration policy is not UTF-8") from exc
    if "\r" in text or not text.endswith("\n"):
        raise ClaudeCompileError("orchestration policy is not canonical LF text")
    if text.count("<!-- pilotfish:begin -->") != 1:
        raise ClaudeCompileError("orchestration policy needs one begin marker")
    if text.count("<!-- pilotfish:end -->") != 1:
        raise ClaudeCompileError("orchestration policy needs one end marker")
    if "omit the `model` argument entirely" not in text:
        raise ClaudeCompileError("orchestration policy permits named-role model drift")
    if "Task Envelope" not in text or "DELEGATE" not in text:
        raise ClaudeCompileError("orchestration policy omits canonical routing contract")
    if "REFINE" not in text or "TAKEOVER" not in text or "BLOCK" not in text:
        raise ClaudeCompileError("orchestration policy invents roles for control outcomes")
    if "orchestrator" not in text or "virtual" not in text:
        raise ClaudeCompileError("orchestration policy omits virtual control plane")
    for name in LEAF_ROLE_ORDER:
        if name not in roles or f"`{name}`" not in text:
            raise ClaudeCompileError(f"orchestration policy omits role: {name}")
    for alias in ("haiku", "sonnet", "opus"):
        if alias in text.casefold():
            raise ClaudeCompileError(
                "policy layer must not own concrete role model bindings"
            )


def _normalize_required(required_capabilities: Iterable[str]) -> Tuple[str, ...]:
    if isinstance(required_capabilities, (str, bytes)):
        raise ClaudeCompileError("required_capabilities must be an iterable of names")
    try:
        requested = tuple(required_capabilities)
    except TypeError as exc:
        raise ClaudeCompileError("required_capabilities must be iterable") from exc
    if any(not isinstance(name, str) or not name for name in requested):
        raise ClaudeCompileError("required capability names must be non-empty strings")
    unknown = sorted(set(requested) - set(CAPABILITY_ORDER))
    if unknown:
        raise ClaudeCompileError(f"unknown required capabilities: {unknown}")
    return tuple(name for name in CAPABILITY_ORDER if name in set(requested))


def _capability_report(
    schema_version: str,
    required_capabilities: Iterable[str],
    strict: bool,
) -> CapabilityReport:
    required = _normalize_required(required_capabilities)
    capabilities = tuple((name, CLAUDE_CAPABILITIES[name]) for name in CAPABILITY_ORDER)
    for name, status in capabilities:
        if status not in CAPABILITY_STATUSES:
            raise ClaudeCompileError(f"invalid capability status for {name}: {status}")

    unavailable = tuple(
        name for name in required if CLAUDE_CAPABILITIES[name] != "supported"
    )
    if strict and unavailable:
        raise ClaudeCompileError(
            "strict Claude compile requires supported capabilities: "
            + ", ".join(unavailable)
        )

    warnings = [
        "supported tool-policy and child-spawn controls require Claude Code "
        ">=2.1.207; the compiler does not live-probe the runtime",
        "runtime_model_observation is degraded until optional runtime "
        "attestation is configured"
    ]
    if unavailable:
        warnings.append(
            "required capabilities are not fully supported: " + ", ".join(unavailable)
        )
    return CapabilityReport(
        schema_version=schema_version,
        target="claude",
        capabilities=capabilities,
        runtime_requirements=(("claude_code", ">=2.1.207"),),
        evidence=(
            (
                "tool_enforcement_baseline",
                "upstream-declared verified baseline; not live-probed by this compiler",
            ),
        ),
        required_capabilities=required,
        warnings=tuple(warnings),
    )


def compile_claude(
    spec: SpecInput = None,
    *,
    strict: bool = False,
    required_capabilities: Iterable[str] = (),
) -> ClaudeCompilation:
    """Compile one canonical registry into Claude role and policy artifacts."""

    if not isinstance(strict, bool):
        raise ClaudeCompileError("strict must be boolean")
    document = _load_spec(spec)
    roles = validate_registry(document)

    report = _capability_report(
        schema_version=document["schema_version"],
        required_capabilities=required_capabilities,
        strict=strict,
    )

    role_artifacts = tuple(
        AdapterArtifact(
            relative_path=f"agents/{name}.md",
            content=_render_agent(name, roles[name]),
        )
        for name in LEAF_ROLE_ORDER
    )
    policy_content = _read_template(POLICY_TEMPLATE_PATH).encode("utf-8")
    _validate_policy(policy_content, roles)
    policy_artifact = AdapterArtifact(
        relative_path="claude-md.orchestration.md",
        content=policy_content,
    )
    return ClaudeCompilation(
        artifacts=AdapterArtifacts(
            machine_settings_patch=AdapterArtifact(
                relative_path="settings.patch.json",
                content=_compile_machine_settings_patch(),
            ),
            role_definitions=role_artifacts,
            orchestration_policy=policy_artifact,
        ),
        capability_report=report,
    )


def compile_adapter(
    spec: SpecInput = None,
    target: str = "claude",
    *,
    strict: bool = False,
    required_capabilities: Iterable[str] = (),
) -> ClaudeCompilation:
    """Compile ``spec`` for a named target; v0.1 implements Claude here."""

    if target != "claude":
        raise ClaudeCompileError(f"unsupported adapter target: {target!r}")
    return compile_claude(
        spec,
        strict=strict,
        required_capabilities=required_capabilities,
    )
