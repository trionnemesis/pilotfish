"""Bounded, read-only discovery of verified Codex CLI surfaces."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CAPABILITY_ORDER = (
    "per_role_model_binding",
    "per_role_tool_policy",
    "child_spawn_control",
    "fresh_context_verifier",
    "runtime_model_observation",
    "isolated_parallel_writes",
)
CAPABILITY_STATUSES = frozenset({"supported", "degraded", "unsupported"})
SURFACE_ORDER = (
    "headless_execution",
    "invocation_model_selection",
    "sandbox_policy",
    "approval_policy",
    "structured_events",
    "output_schema",
    "ephemeral_sessions",
    "user_config_isolation",
    "working_directory",
    "additional_writable_directory",
    "dangerous_bypass",
)
MAX_OUTPUT_CHARS = 262_144


@dataclass(frozen=True)
class ProbeCommandEvidence:
    name: str
    returncode: int | None
    stdout: str
    stderr_present: bool = False
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "returncode": self.returncode,
            "stdout_sha256": hashlib.sha256(self.stdout.encode("utf-8")).hexdigest(),
            "stderr_present": self.stderr_present,
            "error": self.error,
        }


@dataclass(frozen=True)
class CodexProbeResult:
    executable: str
    available: bool
    version: str | None
    commands: tuple[ProbeCommandEvidence, ...]
    surfaces: tuple[tuple[str, bool], ...]
    capabilities: tuple[tuple[str, str], ...]
    capability_evidence: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.surfaces) != SURFACE_ORDER:
            raise ValueError("Codex probe surface vocabulary/order mismatch")
        if tuple(name for name, _ in self.capabilities) != CAPABILITY_ORDER:
            raise ValueError("Codex capability vocabulary/order mismatch")
        for name, status in self.capabilities:
            if status not in CAPABILITY_STATUSES:
                raise ValueError(f"invalid capability status for {name}: {status}")

    def surface_map(self) -> dict[str, bool]:
        return dict(self.surfaces)

    def capability_map(self) -> dict[str, str]:
        return dict(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "available": self.available,
            "version": self.version,
            "commands": [command.summary() for command in self.commands],
            "surfaces": dict(self.surfaces),
            "capabilities": dict(self.capabilities),
            "capability_evidence": dict(self.capability_evidence),
            "warnings": list(self.warnings),
        }


def _has_option(text: str, option: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(option)}(?=[\s,=<]|$)", text) is not None


def _parse_version(output: str) -> str | None:
    match = re.search(r"(?m)^codex-cli\s+([^\s]+)\s*$", output)
    return match.group(1) if match else None


def _classify(
    executable: str, commands: tuple[ProbeCommandEvidence, ...]
) -> CodexProbeResult:
    by_name = {command.name: command for command in commands}
    version_evidence = by_name["version"]
    root_evidence = by_name["root_help"]
    exec_evidence = by_name["exec_help"]
    root_help = root_evidence.stdout if root_evidence.returncode == 0 else ""
    exec_help = exec_evidence.stdout if exec_evidence.returncode == 0 else ""
    available = version_evidence.returncode == 0 and bool(root_help)
    version = _parse_version(version_evidence.stdout)

    headless = bool(
        re.search(r"(?m)^\s*exec\s+", root_help) and exec_evidence.returncode == 0
    )
    surfaces = {
        "headless_execution": headless,
        "invocation_model_selection": _has_option(exec_help, "--model"),
        "sandbox_policy": _has_option(exec_help, "--sandbox"),
        # The observed CLI exposes approval policy as a root option before `exec`.
        "approval_policy": _has_option(root_help, "--ask-for-approval"),
        "structured_events": _has_option(exec_help, "--json"),
        "output_schema": _has_option(exec_help, "--output-schema"),
        "ephemeral_sessions": _has_option(exec_help, "--ephemeral"),
        "user_config_isolation": _has_option(exec_help, "--ignore-user-config"),
        "working_directory": _has_option(exec_help, "--cd"),
        "additional_writable_directory": _has_option(exec_help, "--add-dir"),
        "dangerous_bypass": _has_option(
            exec_help, "--dangerously-bypass-approvals-and-sandbox"
        ),
    }

    capabilities = {
        "per_role_model_binding": (
            "degraded"
            if headless and surfaces["invocation_model_selection"]
            else "unsupported"
        ),
        "per_role_tool_policy": (
            "degraded"
            if surfaces["sandbox_policy"] and surfaces["approval_policy"]
            else "unsupported"
        ),
        "child_spawn_control": "unsupported",
        "fresh_context_verifier": (
            "supported"
            if headless
            and surfaces["ephemeral_sessions"]
            and surfaces["sandbox_policy"]
            and surfaces["output_schema"]
            else "unsupported"
        ),
        # JSONL availability does not prove that an observed model id is emitted.
        "runtime_model_observation": "unsupported",
        "isolated_parallel_writes": (
            "degraded"
            if surfaces["working_directory"] and surfaces["sandbox_policy"]
            else "unsupported"
        ),
    }
    evidence = {
        "per_role_model_binding": (
            "--model is invocation-scoped; no named-role binding surface was observed"
        ),
        "per_role_tool_policy": (
            "--sandbox and root-level --ask-for-approval are invocation-wide, not per-role"
        ),
        "child_spawn_control": "no verified CLI flag controls child-agent spawning",
        "fresh_context_verifier": (
            "exec + --ephemeral + read-only sandbox + --output-schema"
            if capabilities["fresh_context_verifier"] == "supported"
            else "required fresh verifier controls were not all observed"
        ),
        "runtime_model_observation": (
            "--json exists but help does not promise an observed model identifier"
        ),
        "isolated_parallel_writes": (
            "--cd and sandbox can target an external worktree; worktree isolation is external"
        ),
    }
    warnings = []
    if not available:
        warnings.append("Codex CLI version/help probe is unavailable or incomplete")
    if version is None:
        warnings.append("Codex CLI version could not be parsed")
    warnings.extend(
        (
            "canonical model aliases cannot be translated into Codex model ids by this probe",
            "per-role tool policy is prompt-level because verified controls are invocation-wide",
            "runtime model observation remains UNKNOWN without separate structured evidence",
            "parallel write isolation requires caller-managed worktrees",
        )
    )
    return CodexProbeResult(
        executable=Path(executable).name,
        available=available,
        version=version,
        commands=commands,
        surfaces=tuple((name, surfaces[name]) for name in SURFACE_ORDER),
        capabilities=tuple((name, capabilities[name]) for name in CAPABILITY_ORDER),
        capability_evidence=tuple((name, evidence[name]) for name in CAPABILITY_ORDER),
        warnings=tuple(warnings),
    )


def probe_from_outputs(
    *,
    version_output: str,
    root_help: str,
    exec_help: str,
    executable: str = "codex",
    returncodes: Sequence[int | None] = (0, 0, 0),
    errors: Sequence[str | None] = (None, None, None),
) -> CodexProbeResult:
    """Classify recorded command output without invoking a subprocess."""

    if len(returncodes) != 3 or len(errors) != 3:
        raise ValueError("recorded probe needs three return codes and errors")
    outputs = (version_output, root_help, exec_help)
    names = ("version", "root_help", "exec_help")
    commands = tuple(
        ProbeCommandEvidence(
            name=name,
            returncode=returncodes[index],
            stdout=outputs[index][:MAX_OUTPUT_CHARS],
            error=errors[index],
        )
        for index, name in enumerate(names)
    )
    return _classify(executable, commands)


def _run_probe_command(
    prefix: Sequence[str],
    arguments: Sequence[str],
    *,
    name: str,
    timeout_seconds: float,
    cwd: Path,
    environment: Mapping[str, str],
) -> ProbeCommandEvidence:
    try:
        completed = subprocess.run(
            [*prefix, *arguments],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ProbeCommandEvidence(name, None, "", error="command timed out")
    except OSError as error:
        return ProbeCommandEvidence(
            name, None, "", error=f"{type(error).__name__}: {error}"
        )
    return ProbeCommandEvidence(
        name=name,
        returncode=completed.returncode,
        stdout=completed.stdout[:MAX_OUTPUT_CHARS],
        stderr_present=bool(completed.stderr),
    )


def probe_codex(
    command: Sequence[str] = ("codex",),
    *,
    timeout_seconds: float = 5.0,
    environment: Mapping[str, str] | None = None,
) -> CodexProbeResult:
    """Probe version and help only, using an isolated temporary HOME."""

    if isinstance(command, (str, bytes)) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("command must be a non-empty argument sequence")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    executable = shutil.which(command[0])
    if executable is None:
        missing = tuple(
            ProbeCommandEvidence(name, None, "", error="executable not found")
            for name in ("version", "root_help", "exec_help")
        )
        return _classify(command[0], missing)

    prefix = (executable, *command[1:])
    with tempfile.TemporaryDirectory(prefix="pilotfish-codex-probe-") as temp_dir:
        isolated_home = Path(temp_dir)
        env = dict(os.environ if environment is None else environment)
        for key in ("HOME", "USERPROFILE", "CODEX_HOME", "XDG_CONFIG_HOME"):
            env[key] = str(isolated_home)
        commands = (
            _run_probe_command(
                prefix,
                ("--version",),
                name="version",
                timeout_seconds=float(timeout_seconds),
                cwd=isolated_home,
                environment=env,
            ),
            _run_probe_command(
                prefix,
                ("--help",),
                name="root_help",
                timeout_seconds=float(timeout_seconds),
                cwd=isolated_home,
                environment=env,
            ),
            _run_probe_command(
                prefix,
                ("exec", "--help"),
                name="exec_help",
                timeout_seconds=float(timeout_seconds),
                cwd=isolated_home,
                environment=env,
            ),
        )
    return _classify(executable, commands)
