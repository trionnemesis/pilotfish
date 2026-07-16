"""Bounded, no-auth discovery of verified Codex CLI surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


MINIMUM_CODEX_VERSION = "0.144.5"
_MINIMUM_VERSION_TUPLE = (0, 144, 5)
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
    "stable_version",
    "multi_agent",
    "generated_agent_config",
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
INCOMPATIBILITY_CLASSES = frozenset(
    {
        "missing_binary",
        "below_minimum",
        "prerelease",
        "unparsable_version",
        "required_surface",
    }
)
TARGET_CONFIGURATION_STATUSES = frozenset({"enabled", "disabled", "unknown"})
MAX_OUTPUT_CHARS = 262_144
_CONFIG_LOAD_SENTINEL = "generated agent config accepted\n"
_DEFAULT_PROBE_AGENT = b'''name = "pilotfish-probe"
description = "Validate the documented Codex custom-agent configuration surface."
developer_instructions = "Remain read-only and do not spawn child agents."
model = "gpt-5.6-terra"
model_reasoning_effort = "low"
sandbox_mode = "read-only"

[agents]
max_depth = 1
'''


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
    binary_available: bool
    version: str | None
    minimum_version: str
    stable_version: bool
    compatible: bool
    incompatibility: str | None
    config_load: bool
    target_configuration: str
    future_project_overrides: str
    commands: tuple[ProbeCommandEvidence, ...]
    surfaces: tuple[tuple[str, bool], ...]
    capabilities: tuple[tuple[str, str], ...]
    capability_evidence: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_version != MINIMUM_CODEX_VERSION:
            raise ValueError("Codex probe minimum version mismatch")
        if self.incompatibility not in INCOMPATIBILITY_CLASSES | {None}:
            raise ValueError("invalid Codex incompatibility class")
        if self.compatible != (self.incompatibility is None):
            raise ValueError("Codex compatibility state is inconsistent")
        if self.target_configuration not in TARGET_CONFIGURATION_STATUSES:
            raise ValueError("invalid target configuration status")
        if self.future_project_overrides != "unknown":
            raise ValueError("future project overrides must remain unknown")
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
            "binary_available": self.binary_available,
            "version": self.version,
            "minimum_version": self.minimum_version,
            "stable_version": self.stable_version,
            "compatible": self.compatible,
            "incompatibility": self.incompatibility,
            "config_load": self.config_load,
            "target_configuration": self.target_configuration,
            "future_project_overrides": self.future_project_overrides,
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


def _stable_version_tuple(version: str | None) -> tuple[int, int, int] | None:
    if version is None:
        return None
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _is_prerelease(version: str | None) -> bool:
    return bool(
        version
        and re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-[0-9A-Za-z.-]+",
            version,
        )
    )


def _multi_agent_enabled(output: str) -> bool:
    match = re.search(r"(?m)^\s*multi_agent\s+stable\s+(true|false)\s*$", output)
    return bool(match and match.group(1) == "true")


def _classify(
    executable: str,
    commands: tuple[ProbeCommandEvidence, ...],
    *,
    target_configuration: str,
) -> CodexProbeResult:
    by_name = {command.name: command for command in commands}
    version_evidence = by_name["version"]
    root_evidence = by_name["root_help"]
    exec_evidence = by_name["exec_help"]
    features_evidence = by_name["features"]
    config_evidence = by_name["config_load"]
    root_help = root_evidence.stdout if root_evidence.returncode == 0 else ""
    exec_help = exec_evidence.stdout if exec_evidence.returncode == 0 else ""

    binary_available = not all(
        command.error == "executable not found" for command in commands
    )
    available = bool(
        binary_available
        and version_evidence.returncode == 0
        and root_evidence.returncode == 0
        and root_help
    )
    version = _parse_version(version_evidence.stdout)
    version_tuple = _stable_version_tuple(version)
    stable_version = version_tuple is not None
    config_load = bool(
        config_evidence.returncode == 0
        and config_evidence.stdout == _CONFIG_LOAD_SENTINEL
    )
    multi_agent = bool(
        features_evidence.returncode == 0
        and _multi_agent_enabled(features_evidence.stdout)
    )
    headless = bool(
        re.search(r"(?m)^\s*exec\s+", root_help) and exec_evidence.returncode == 0
    )
    surfaces = {
        "stable_version": stable_version,
        "multi_agent": multi_agent,
        "generated_agent_config": config_load,
        "headless_execution": headless,
        "invocation_model_selection": _has_option(exec_help, "--model"),
        "sandbox_policy": _has_option(exec_help, "--sandbox"),
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
    required_surfaces = (
        "multi_agent",
        "generated_agent_config",
        "headless_execution",
        "invocation_model_selection",
        "sandbox_policy",
        "approval_policy",
        "structured_events",
        "output_schema",
        "ephemeral_sessions",
    )

    if not binary_available:
        incompatibility = "missing_binary"
    elif _is_prerelease(version):
        incompatibility = "prerelease"
    elif not stable_version:
        incompatibility = "unparsable_version"
    elif version_tuple < _MINIMUM_VERSION_TUPLE:
        incompatibility = "below_minimum"
    elif target_configuration == "disabled" or any(
        not surfaces[name] for name in required_surfaces
    ):
        incompatibility = "required_surface"
    else:
        incompatibility = None
    compatible = incompatibility is None

    capabilities = {
        "per_role_model_binding": "supported" if compatible else "unsupported",
        "per_role_tool_policy": "degraded" if compatible else "unsupported",
        "child_spawn_control": "supported" if compatible else "unsupported",
        "fresh_context_verifier": "supported" if compatible else "unsupported",
        "runtime_model_observation": "unsupported",
        "isolated_parallel_writes": "degraded" if compatible else "unsupported",
    }
    evidence = {
        "per_role_model_binding": (
            "documented standalone agent model field loaded by the isolated config probe"
            if compatible
            else "compatible standalone agent model binding was not verified"
        ),
        "per_role_tool_policy": (
            "sandbox mode is native; positive per-role tool allowlists are prompt guidance"
            if compatible
            else "compatible per-role sandbox configuration was not verified"
        ),
        "child_spawn_control": (
            "agents.max_depth loaded in isolated leaf configuration"
            if compatible
            else "compatible leaf depth configuration was not verified"
        ),
        "fresh_context_verifier": (
            "custom agent + ephemeral exec + read-only sandbox + output schema"
            if compatible
            else "required fresh verifier controls were not all verified"
        ),
        "runtime_model_observation": (
            "structured events do not promise an observed runtime model identifier"
        ),
        "isolated_parallel_writes": (
            "--cd and sandbox can target caller-managed worktrees; isolation is external"
            if compatible
            else "compatible worktree invocation controls were not verified"
        ),
    }
    warnings: list[str] = []
    if incompatibility is not None:
        warnings.append(f"Codex compatibility gate failed: {incompatibility}")
    warnings.extend(
        (
            "future project and managed overrides remain UNKNOWN until session evaluation",
            "positive per-role tool allowlists are not independently enforced",
            "runtime model observation remains UNKNOWN without sourced structured evidence",
            "parallel write isolation requires caller-managed worktrees",
        )
    )
    return CodexProbeResult(
        executable=Path(executable).name,
        available=available,
        binary_available=binary_available,
        version=version,
        minimum_version=MINIMUM_CODEX_VERSION,
        stable_version=stable_version,
        compatible=compatible,
        incompatibility=incompatibility,
        config_load=config_load,
        target_configuration=target_configuration,
        future_project_overrides="unknown",
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
    features_output: str = "",
    config_load_output: str = "",
    executable: str = "codex",
    returncodes: Sequence[int | None] = (0, 0, 0, 0, 0),
    errors: Sequence[str | None] = (None, None, None, None, None),
    target_configuration: str = "unknown",
) -> CodexProbeResult:
    """Classify recorded command output without invoking a subprocess."""

    if len(returncodes) == 3 and len(errors) == 3:
        returncodes = (*returncodes, None, None)
        errors = (*errors, "not probed", "not probed")
    if len(returncodes) != 5 or len(errors) != 5:
        raise ValueError("recorded probe needs three or five return codes and errors")
    if target_configuration not in TARGET_CONFIGURATION_STATUSES:
        raise ValueError("invalid target_configuration")
    outputs = (
        version_output,
        root_help,
        exec_help,
        features_output,
        config_load_output,
    )
    names = ("version", "root_help", "exec_help", "features", "config_load")
    commands = tuple(
        ProbeCommandEvidence(
            name=name,
            returncode=returncodes[index],
            stdout=outputs[index][:MAX_OUTPUT_CHARS],
            error=errors[index],
        )
        for index, name in enumerate(names)
    )
    return _classify(
        executable,
        commands,
        target_configuration=target_configuration,
    )


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


def _run_config_load(
    prefix: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: Path,
    environment: Mapping[str, str],
) -> ProbeCommandEvidence:
    """Load generated agents through app-server without retaining returned config."""

    try:
        process = subprocess.Popen(
            [*prefix, "app-server", "--stdio"],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
    except OSError as error:
        return ProbeCommandEvidence(
            "config_load", None, "", error=f"{type(error).__name__}: {error}"
        )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        return ProbeCommandEvidence(
            "config_load", None, "", error="app-server pipes unavailable"
        )

    messages: queue.Queue[str | None] = queue.Queue()
    stderr_seen = threading.Event()
    output_exceeded = threading.Event()

    def read_stdout() -> None:
        try:
            total = 0
            while True:
                line = process.stdout.readline(MAX_OUTPUT_CHARS + 1)
                if not line:
                    break
                total += len(line)
                if total > MAX_OUTPUT_CHARS:
                    output_exceeded.set()
                    break
                messages.put(line)
        finally:
            messages.put(None)

    def drain_stderr() -> None:
        for chunk in iter(lambda: process.stderr.read(8192), ""):
            if chunk:
                stderr_seen.set()

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout_seconds
    warning_seen = False

    def send(message: Mapping[str, Any]) -> None:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def wait_for(response_id: int) -> bool:
        nonlocal warning_seen
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError from error
            if line is None:
                return False
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                warning_seen = True
                continue
            if message.get("method") == "configWarning":
                warning_seen = True
            if message.get("id") == response_id:
                return "result" in message and "error" not in message

    error: str | None = None
    accepted = False
    timed_out = False
    try:
        send(
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "pilotfish_probe",
                        "title": "Pilotfish compatibility probe",
                        "version": "0.1.0",
                    }
                },
            }
        )
        if not wait_for(0):
            error = "config load initialization rejected"
        else:
            send({"method": "initialized", "params": {}})
            send({"method": "config/read", "id": 1, "params": {}})
            accepted = wait_for(1) and not warning_seen
            if not accepted:
                error = (
                    "config load output exceeded limit"
                    if output_exceeded.is_set()
                    else "generated agent config rejected"
                )
    except TimeoutError:
        timed_out = True
        error = "command timed out"
    except (BrokenPipeError, OSError, ValueError):
        error = "config load protocol failed"
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
        process.stdout.close()
        process.stderr.close()

    return ProbeCommandEvidence(
        name="config_load",
        returncode=None if timed_out else (0 if accepted else 1),
        stdout=_CONFIG_LOAD_SENTINEL if accepted else "",
        stderr_present=stderr_seen.is_set(),
        error=error,
    )


def _normalize_agent_files(
    agent_files: Mapping[str, bytes] | None,
) -> tuple[tuple[PurePosixPath, bytes], ...]:
    selected = (
        {"agents/pilotfish-probe.toml": _DEFAULT_PROBE_AGENT}
        if agent_files is None
        else dict(agent_files)
    )
    if not selected:
        raise ValueError("agent_files must not be empty")
    normalized: list[tuple[PurePosixPath, bytes]] = []
    total = 0
    for raw_path, content in sorted(selected.items()):
        if not isinstance(raw_path, str) or not isinstance(content, bytes):
            raise ValueError("agent_files must map relative strings to bytes")
        path = PurePosixPath(raw_path)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "agents"
            or path.suffix != ".toml"
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("agent_files are limited to agents/*.toml")
        total += len(content)
        if total > MAX_OUTPUT_CHARS:
            raise ValueError("agent_files exceed bounded probe size")
        normalized.append((path, content))
    return tuple(normalized)


def probe_codex(
    command: Sequence[str] = ("codex",),
    *,
    timeout_seconds: float = 5.0,
    environment: Mapping[str, str] | None = None,
    agent_files: Mapping[str, bytes] | None = None,
    target_codex_home: str | Path | None = None,
) -> CodexProbeResult:
    """Run at most five bounded commands with generated config in an isolated home."""

    if isinstance(command, (str, bytes)) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("command must be a non-empty argument sequence")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    normalized_agents = _normalize_agent_files(agent_files)
    executable = shutil.which(command[0])
    if executable is None:
        missing = tuple(
            ProbeCommandEvidence(name, None, "", error="executable not found")
            for name in ("version", "root_help", "exec_help", "features", "config_load")
        )
        return _classify(
            command[0],
            missing,
            target_configuration="unknown",
        )

    prefix = (executable, *command[1:])
    with tempfile.TemporaryDirectory(prefix="pilotfish-codex-probe-") as temp_dir:
        isolated_home = Path(temp_dir)
        for relative_path, content in normalized_agents:
            destination = isolated_home.joinpath(*relative_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        env = dict(os.environ if environment is None else environment)
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        for key in ("HOME", "USERPROFILE", "CODEX_HOME", "XDG_CONFIG_HOME"):
            env[key] = str(isolated_home)

        feature_env = dict(env)
        target_configuration = "unknown"
        if target_codex_home is not None:
            target = Path(target_codex_home)
            if target.is_dir() and not target.is_symlink():
                feature_env["CODEX_HOME"] = str(target.resolve())

        version = _run_probe_command(
            prefix,
            ("--version",),
            name="version",
            timeout_seconds=float(timeout_seconds),
            cwd=isolated_home,
            environment=env,
        )
        root_help = _run_probe_command(
            prefix,
            ("--help",),
            name="root_help",
            timeout_seconds=float(timeout_seconds),
            cwd=isolated_home,
            environment=env,
        )
        exec_help = _run_probe_command(
            prefix,
            ("exec", "--help"),
            name="exec_help",
            timeout_seconds=float(timeout_seconds),
            cwd=isolated_home,
            environment=env,
        )
        features = _run_probe_command(
            prefix,
            ("features", "list"),
            name="features",
            timeout_seconds=float(timeout_seconds),
            cwd=isolated_home,
            environment=feature_env,
        )
        if target_codex_home is not None and features.returncode == 0:
            target_configuration = (
                "enabled" if _multi_agent_enabled(features.stdout) else "disabled"
            )
        config_load = _run_config_load(
            prefix,
            timeout_seconds=float(timeout_seconds),
            cwd=isolated_home,
            environment=env,
        )
        commands = (version, root_help, exec_help, features, config_load)
    return _classify(
        executable,
        commands,
        target_configuration=target_configuration,
    )
