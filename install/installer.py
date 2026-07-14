"""Dependency-free, local-only installer for generated Claude artifacts.

The caller must supply ``target_home``.  No operation falls back to the process
HOME, fetches remote content, or changes Claude Code safety controls.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


BEGIN_MARKER = "<!-- pilotfish:begin -->"
END_MARKER = "<!-- pilotfish:end -->"
STATE_VERSION = 1
MANIFEST_VERSION = 1
OVERRIDE_ENV = "CLAUDE_CODE_SUBAGENT_MODEL"
MIN_CLAUDE_VERSION = (2, 1, 207)
PRIVATE_FILE_MODE = 0o600 if os.name != "nt" else 0o666
PRIVATE_DIRECTORY_MODE = 0o700 if os.name != "nt" else 0o777
ALLOWED_SETTINGS_KEYS = frozenset({"model", "fallbackModel", "availableModels"})
ALLOWED_AGENT_FILES = frozenset(
    {
        "Explore.md",
        "executor.md",
        "mech-executor.md",
        "scout.md",
        "security-executor.md",
        "senior-executor.md",
        "verifier.md",
    }
)
ALLOWED_AGENT_NAMES_BY_FILE = {
    "Explore.md": "Explore",
    "executor.md": "executor",
    "mech-executor.md": "mech-executor",
    "scout.md": "scout",
    "security-executor.md": "security-executor",
    "senior-executor.md": "senior-executor",
    "verifier.md": "verifier",
}
_EXPECTATION_UNSET = object()


class InstallerError(RuntimeError):
    """Base class for installer failures."""


class ApprovalRequired(InstallerError):
    """Raised before a write when explicit approval was not supplied."""


class PlanBlocked(InstallerError):
    """Raised when preflight found a conflict that requires human resolution."""


@dataclass(frozen=True)
class PlannedChange:
    path: str
    action: str
    detail: str
    before_sha256: str | None = None
    after_sha256: str | None = None
    before_mode: int | None = None
    after_mode: int | None = None


@dataclass(frozen=True)
class InstallPlan:
    operation: str
    target_home: str
    changes: tuple[PlannedChange, ...] = ()
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def will_write(self) -> bool:
        return bool(self.changes)

    @property
    def fingerprint(self) -> str:
        payload = {
            "operation": self.operation,
            "target_home": self.target_home,
            "changes": [asdict(change) for change in self.changes],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }
        return _sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target_home": self.target_home,
            "will_write": self.will_write,
            "fingerprint": self.fingerprint,
            "changes": [asdict(change) for change in self.changes],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class OperationResult:
    operation: str
    changed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest: str | None = None
    dry_run: bool = False
    plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Mutation:
    path: str
    kind: str
    after: bytes | None
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_before_sha256: str | None = None
    expected_before_mode: int | None = None


@dataclass
class _Prepared:
    plan: InstallPlan
    mutations: list[_Mutation]
    next_state: dict[str, Any] | None
    skipped: list[str] = field(default_factory=list)
    expected_state_sha256: str | None = None
    write_state: bool = False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _value_hash(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(canonical)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _state_bytes(state: Mapping[str, Any]) -> bytes:
    document = dict(state)
    document.pop("integrity_sha256", None)
    document["integrity_sha256"] = _sha256(_json_bytes(document))
    return _json_bytes(document)


def _windows_process_is_elevated() -> bool:
    """Read TokenElevation without invoking a shell or trusting environment flags."""

    if os.name != "nt":
        raise OSError("Windows token elevation is unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_elevation_class = 20

    class TokenElevation(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_token_information.restype = wintypes.BOOL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_process_token(
        get_current_process(), token_query, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        elevation = TokenElevation()
        returned = wintypes.DWORD()
        if not get_token_information(
            token,
            token_elevation_class,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(elevation.TokenIsElevated)
    finally:
        close_handle(token)


def _validate_windows_target_home(
    target_home: Path,
    *,
    profile_home: Path | None = None,
    elevation_probe: Callable[[], bool] | None = None,
) -> None:
    """Fail closed unless a non-elevated process targets its current profile."""

    probe = elevation_probe or _windows_process_is_elevated
    try:
        elevated = probe()
    except Exception as exc:
        raise ValueError(
            "Windows process elevation could not be verified; refusing to plan writes"
        ) from exc
    if elevated:
        raise ValueError(
            "elevated Windows installs are unsupported; run as the profile owner"
        )
    try:
        current_profile = (profile_home or Path.home()).resolve(strict=True)
        target_home.resolve(strict=True).relative_to(current_profile)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Windows target_home must resolve inside the current operator profile; "
            "cross-user installs are unsupported"
        ) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(raw: bytes, *, label: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise InstallerError(f"invalid JSON: {label}: {exc}") from exc


def _frontmatter_name(content: str) -> str | None:
    if content.startswith("\ufeff"):
        raise InstallerError("agent frontmatter must not contain a UTF-8 BOM")
    if not content.startswith("---\n"):
        if content.startswith("---"):
            raise InstallerError("agent frontmatter opener is malformed or not LF-only")
        return None
    if "\r" in content:
        raise InstallerError("agent frontmatter must use LF line endings")
    end = content.find("\n---\n", 4)
    if end < 0:
        raise InstallerError("agent frontmatter is missing its closing delimiter")
    names: list[str] = []
    for line in content[4:end].splitlines():
        match = re.fullmatch(r"\s*name\s*:\s*([^#]+?)\s*", line)
        if match:
            names.append(match.group(1).strip().strip("'\""))
        elif re.match(r"\s*name\s*:", line):
            raise InstallerError("agent frontmatter name is malformed")
    if len(names) != 1:
        raise InstallerError("agent frontmatter must declare exactly one name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", names[0]):
        raise InstallerError("agent frontmatter name contains unsupported characters")
    return names[0]


def _policy_block(content: str) -> tuple[int, int, str] | None:
    begin_count = content.count(BEGIN_MARKER)
    end_count = content.count(END_MARKER)
    if begin_count == end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise InstallerError(
            "CLAUDE.md must contain zero or one complete pilotfish marker pair"
        )
    start = content.index(BEGIN_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    if content.find(END_MARKER) < start:
        raise InstallerError("CLAUDE.md pilotfish markers are out of order")
    if end < len(content) and content[end] == "\n":
        end += 1
    return start, end, content[start:end]


def _replace_policy(content: str, replacement: str | None) -> str:
    return _replace_policy_with_separator(content, replacement)[0]


def _replace_policy_with_separator(
    content: str,
    replacement: str | None,
    *,
    separator_length: int | None = None,
) -> tuple[str, int]:
    """Replace the owned policy block and account for its exact separator.

    ``separator_length`` is persisted as an integer rather than content.  That
    lets uninstall/rollback remove or restore only the newlines added by this
    installer, without trimming user-owned whitespace.
    """

    if separator_length is not None and (
        isinstance(separator_length, bool)
        or not isinstance(separator_length, int)
        or not 0 <= separator_length <= 2
    ):
        raise InstallerError("policy separator length is invalid")
    block = _policy_block(content)
    if block is None:
        if replacement is None:
            return content, 0
        if separator_length is None:
            separator = (
                ""
                if not content or content.endswith("\n\n")
                else "\n" if content.endswith("\n") else "\n\n"
            )
        else:
            separator = "\n" * separator_length
        return content + separator + replacement, len(separator)
    start, end, _ = block
    if replacement is None and separator_length:
        separator_start = start - separator_length
        if (
            separator_start < 0
            or content[separator_start:start] != "\n" * separator_length
        ):
            raise InstallerError("owned policy separator changed after installation")
        start = separator_start
    return content[:start] + (replacement or "") + content[end:], (
        separator_length or 0
    )


class Installer:
    """Plan and apply a safe install against an explicitly supplied home."""

    def __init__(
        self,
        *,
        target_home: str | os.PathLike[str],
        source_root: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        version_probe: Callable[[], str | None] | None = None,
        windows_elevation_probe: Callable[[], bool] | None = None,
    ) -> None:
        if not str(target_home):
            raise ValueError("target_home is required")
        supplied_home = Path(target_home).expanduser()
        if (
            not supplied_home.exists()
            or supplied_home.is_symlink()
            or not supplied_home.is_dir()
        ):
            raise ValueError("target_home must be an existing, non-symlink directory")
        self.target_home = supplied_home.resolve()
        self._descriptor_paths = os.name == "posix"
        if self._descriptor_paths:
            required_dir_fd = (os.open, os.mkdir, os.stat, os.unlink, os.rename, os.rmdir)
            if (
                not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_DIRECTORY")
                or any(function not in os.supports_dir_fd for function in required_dir_fd)
                or os.stat not in os.supports_follow_symlinks
            ):
                raise InstallerError(
                    "POSIX platform lacks required descriptor-relative path controls"
                )
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            flags |= getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(self.target_home, flags)
            except OSError as exc:
                raise ValueError(
                    "target_home must remain an existing, non-symlink directory"
                ) from exc
            try:
                home_stat = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        else:
            home_stat = self.target_home.stat()
            if os.name == "nt":
                _validate_windows_target_home(
                    self.target_home,
                    elevation_probe=windows_elevation_probe,
                )
        self._target_home_identity = (home_stat.st_dev, home_stat.st_ino)
        self.source_root = (
            Path(source_root).resolve()
            if source_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self.env = dict(os.environ if env is None else env)
        self.version_probe = version_probe or self._probe_claude_version
        self._state_rel = ".claude/pilotfish/state.json"

    @staticmethod
    def _relative_parts(relative: str) -> tuple[str, ...]:
        relative_path = Path(relative)
        parts = relative_path.parts
        if (
            relative_path.is_absolute()
            or not parts
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise InstallerError(f"unsafe target path is not allowed: {relative}")
        return parts

    @staticmethod
    def _descriptor_flags(*, directory: bool = False) -> int:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        if directory:
            flags |= os.O_DIRECTORY
        return flags

    def _open_home_fd(self) -> int:
        try:
            descriptor = os.open(
                self.target_home, self._descriptor_flags(directory=True)
            )
        except OSError as exc:
            raise InstallerError("target_home path changed after initialization") from exc
        home_stat = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(home_stat.st_mode)
            or (home_stat.st_dev, home_stat.st_ino) != self._target_home_identity
        ):
            os.close(descriptor)
            raise InstallerError("target_home identity changed after initialization")
        return descriptor

    def _open_directory_fd_parts(
        self, parts: Sequence[str], *, create: bool
    ) -> int | None:
        descriptor = self._open_home_fd()
        traversed: list[str] = []
        try:
            for part in parts:
                traversed.append(part)
                try:
                    child = os.open(
                        part,
                        self._descriptor_flags(directory=True),
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        os.close(descriptor)
                        return None
                    private = tuple(traversed[:2]) == (".claude", "pilotfish")
                    try:
                        os.mkdir(
                            part,
                            PRIVATE_DIRECTORY_MODE if private else 0o777,
                            dir_fd=descriptor,
                        )
                    except FileExistsError:
                        pass
                    try:
                        child = os.open(
                            part,
                            self._descriptor_flags(directory=True),
                            dir_fd=descriptor,
                        )
                    except OSError as exc:
                        raise InstallerError(
                            "created directory component was replaced or is unsafe: "
                            + "/".join(traversed)
                        ) from exc
                except OSError as exc:
                    raise InstallerError(
                        "directory component is missing, non-directory, or a symlink: "
                        + "/".join(traversed)
                    ) from exc
                if tuple(traversed[:2]) == (".claude", "pilotfish"):
                    os.fchmod(child, PRIVATE_DIRECTORY_MODE)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def _open_parent_fd(
        self, relative: str, *, create: bool
    ) -> tuple[int | None, str]:
        parts = self._relative_parts(relative)
        return self._open_directory_fd_parts(parts[:-1], create=create), parts[-1]

    @staticmethod
    def _identity(file_stat: os.stat_result) -> tuple[int, int]:
        return file_stat.st_dev, file_stat.st_ino

    def _assert_parent_still_bound(self, relative: str, parent_fd: int) -> None:
        parts = self._relative_parts(relative)
        try:
            rebound = self._open_directory_fd_parts(parts[:-1], create=False)
        except InstallerError as exc:
            raise InstallerError(
                f"target parent changed during descriptor-relative operation: {relative}"
            ) from exc
        if rebound is None:
            raise InstallerError(
                f"target parent changed during descriptor-relative operation: {relative}"
            )
        try:
            if self._identity(os.fstat(rebound)) != self._identity(os.fstat(parent_fd)):
                raise InstallerError(
                    f"target parent changed during descriptor-relative operation: {relative}"
                )
        finally:
            os.close(rebound)

    @staticmethod
    def _stat_at(parent_fd: int, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def _read_file_at(
        self, parent_fd: int, name: str, relative: str
    ) -> tuple[bytes, int, os.stat_result] | None:
        entry = self._stat_at(parent_fd, name)
        if entry is None:
            return None
        if not stat.S_ISREG(entry.st_mode):
            raise InstallerError(f"target must be a regular file: {relative}")
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    name, self._descriptor_flags(), dir_fd=parent_fd
                )
            except OSError as exc:
                raise InstallerError(
                    f"target must be a regular non-symlink file: {relative}"
                ) from exc
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or self._identity(opened) != self._identity(entry)
            ):
                raise InstallerError(f"target changed while opening: {relative}")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            if not self._same_file_snapshot(opened, os.fstat(descriptor)):
                raise InstallerError(f"target changed while reading: {relative}")
            return b"".join(chunks), stat.S_IMODE(opened.st_mode), opened
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_descriptor_file(self, relative: str) -> tuple[bytes, int] | None:
        parent_fd, name = self._open_parent_fd(relative, create=False)
        if parent_fd is None:
            return None
        try:
            snapshot = self._read_file_at(parent_fd, name, relative)
            self._assert_parent_still_bound(relative, parent_fd)
            return None if snapshot is None else snapshot[:2]
        finally:
            os.close(parent_fd)

    @staticmethod
    def _same_file_snapshot(
        before: os.stat_result | None, after: os.stat_result | None
    ) -> bool:
        if before is None or after is None:
            return before is after
        return (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )

    def _descriptor_precondition(
        self,
        parent_fd: int,
        name: str,
        relative: str,
        *,
        expected_sha256: str | None | object,
        expected_mode: int | None | object,
    ) -> tuple[bytes, int, os.stat_result] | None:
        snapshot = self._read_file_at(parent_fd, name, relative)
        actual_sha256 = None if snapshot is None else _sha256(snapshot[0])
        actual_mode = None if snapshot is None else snapshot[1]
        if (
            expected_sha256 is not _EXPECTATION_UNSET
            and actual_sha256 != expected_sha256
        ) or (
            expected_mode is not _EXPECTATION_UNSET
            and actual_mode != expected_mode
        ):
            raise InstallerError(
                f"descriptor precondition changed after planning: {relative}"
            )
        return snapshot

    @staticmethod
    def _probe_claude_version() -> str | None:
        try:
            completed = subprocess.run(
                ["claude", "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or completed.stderr.strip() or None

    def _runtime_blocker(self) -> str | None:
        try:
            raw = self.version_probe()
        except Exception:
            raw = None
        if not raw:
            return (
                "Claude Code version could not be confirmed; version 2.1.207 or newer "
                "is required for enforced seven-role tool policies"
            )
        match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", raw)
        if not match:
            return (
                "Claude Code version could not be parsed; version 2.1.207 or newer "
                "is required for enforced seven-role tool policies"
            )
        version = tuple(int(part) for part in match.groups())
        if version < MIN_CLAUDE_VERSION:
            return (
                f"Claude Code {'.'.join(map(str, version))} is too old; 2.1.207 or "
                "newer is required for enforced seven-role tool policies"
            )
        return None

    def _path(self, relative: str) -> Path:
        relative_path = Path(*self._relative_parts(relative))
        candidate = self.target_home / relative
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.target_home)
        except ValueError as exc:
            raise InstallerError(f"target path escapes supplied home: {relative}") from exc
        current = self.target_home
        for part in relative_path.parts:
            current = current / part
            try:
                current_stat = current.lstat()
            except FileNotFoundError:
                continue
            reparse = getattr(current_stat, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            )
            if stat.S_ISLNK(current_stat.st_mode) or reparse:
                raise InstallerError(
                    f"symlink or reparse target component is not allowed: {relative}"
                )
        return candidate

    def _read_bytes(self, relative: str) -> bytes | None:
        if self._descriptor_paths:
            snapshot = self._read_descriptor_file(relative)
            return None if snapshot is None else snapshot[0]
        path = self._path(relative)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise InstallerError(f"target must be a regular file: {relative}")
        return path.read_bytes()

    def _mode(self, relative: str) -> int | None:
        if self._descriptor_paths:
            snapshot = self._read_descriptor_file(relative)
            return None if snapshot is None else snapshot[1]
        path = self._path(relative)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise InstallerError(f"target must be a regular file: {relative}")
        return stat.S_IMODE(path.stat().st_mode)

    def _bind_mutation_plan(
        self,
        changes: Sequence[PlannedChange],
        mutations: Sequence[_Mutation],
        *,
        next_state: Mapping[str, Any] | None,
    ) -> list[PlannedChange]:
        by_path = {mutation.path: mutation for mutation in mutations}
        result: list[PlannedChange] = []
        for change in changes:
            mutation = by_path.get(change.path)
            if mutation is not None:
                before = self._read_bytes(change.path)
                before_hash = None if before is None else _sha256(before)
                before_mode = self._mode(change.path)
                mutation.expected_before_sha256 = before_hash
                mutation.expected_before_mode = before_mode
                after_mode = (
                    None
                    if mutation.after is None
                    else before_mode if before_mode is not None else PRIVATE_FILE_MODE
                )
                result.append(
                    replace(
                        change,
                        before_sha256=before_hash,
                        after_sha256=(
                            None if mutation.after is None else _sha256(mutation.after)
                        ),
                        before_mode=before_mode,
                        after_mode=after_mode,
                    )
                )
            elif change.path == self._state_rel:
                before = self._read_bytes(self._state_rel)
                before_mode = self._mode(self._state_rel)
                state_after = None if next_state is None else _state_bytes(next_state)
                result.append(
                    replace(
                        change,
                        before_sha256=None if before is None else _sha256(before),
                        after_sha256=None if state_after is None else _sha256(state_after),
                        before_mode=before_mode,
                        after_mode=(
                            None
                            if state_after is None
                            else before_mode if before_mode is not None else PRIVATE_FILE_MODE
                        ),
                    )
                )
            else:
                result.append(change)
        return result

    def _read_json(self, relative: str, *, default: Any) -> Any:
        raw = self._read_bytes(relative)
        if raw is None:
            return default
        return _parse_json(raw, label=relative)

    def _load_state(self) -> dict[str, Any]:
        raw = self._read_bytes(self._state_rel)
        if raw is None:
            return {
                "schema_version": STATE_VERSION,
                "settings": {},
                "settings_file_created": False,
                "agents": {},
                "policy": None,
            }
        state = _parse_json(raw, label=self._state_rel)
        if not isinstance(state, dict) or state.get("schema_version") != STATE_VERSION:
            raise InstallerError("unsupported or malformed pilotfish installer state")
        integrity = state.pop("integrity_sha256", None)
        if not isinstance(integrity, str) or integrity != _sha256(_json_bytes(state)):
            raise InstallerError("pilotfish installer state integrity check failed")
        if set(state) != {
            "schema_version",
            "settings",
            "settings_file_created",
            "agents",
            "policy",
        }:
            raise InstallerError("pilotfish installer state contains unsupported fields")
        if not isinstance(state.get("settings", {}), dict):
            raise InstallerError("malformed pilotfish settings ownership state")
        if not isinstance(state.get("agents", {}), dict):
            raise InstallerError("malformed pilotfish agent ownership state")
        if not isinstance(state.get("settings_file_created"), bool):
            raise InstallerError("malformed pilotfish settings file ownership state")
        if state["settings_file_created"] and not state["settings"]:
            raise InstallerError("settings file ownership requires at least one owned key")
        if set(state["settings"]) - ALLOWED_SETTINGS_KEYS:
            raise InstallerError("pilotfish state claims unsupported settings keys")
        for ownership in state["settings"].values():
            if not isinstance(ownership, dict) or not re.fullmatch(
                r"[0-9a-f]{64}", str(ownership.get("hash", ""))
            ):
                raise InstallerError("malformed pilotfish setting ownership hash")
        for path, ownership in state["agents"].items():
            filename = Path(path).name
            if (
                path != f".claude/agents/{filename}"
                or filename not in ALLOWED_AGENT_FILES
                or not isinstance(ownership, dict)
                or ownership.get("name") != ALLOWED_AGENT_NAMES_BY_FILE[filename]
                or not re.fullmatch(r"[0-9a-f]{64}", str(ownership.get("hash", "")))
            ):
                raise InstallerError("malformed pilotfish agent ownership state")
        policy = state.get("policy")
        if policy is not None and (
            not isinstance(policy, dict)
            or set(policy) != {"hash", "file_created", "separator_length"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(policy.get("hash", "")))
            or not isinstance(policy.get("file_created"), bool)
            or isinstance(policy.get("separator_length"), bool)
            or not isinstance(policy.get("separator_length"), int)
            or not 0 <= policy.get("separator_length") <= 2
        ):
            raise InstallerError("malformed pilotfish policy ownership state")
        return state

    def _sources(self) -> tuple[dict[str, Any], dict[str, tuple[str, bytes]], bytes]:
        try:
            from adapters.claude import compile_claude

            compilation = compile_claude()
        except (ImportError, ValueError) as exc:
            raise InstallerError(f"cannot compile canonical Claude artifacts: {exc}") from exc

        emitted = compilation.artifacts
        settings_bytes = emitted.machine_settings_patch.content
        policy = emitted.orchestration_policy.content
        role_artifacts = emitted.role_definitions
        if len(role_artifacts) != 7:
            raise InstallerError("canonical Claude compiler must emit exactly seven roles")
        settings = _parse_json(settings_bytes, label="compiled settings patch")
        if not isinstance(settings, dict):
            raise InstallerError("settings template must be a JSON object")
        unknown_keys = set(settings) - ALLOWED_SETTINGS_KEYS
        if unknown_keys:
            raise InstallerError(
                "settings template contains unsupported keys: "
                + ", ".join(sorted(unknown_keys))
            )
        if not policy.decode("utf-8").startswith(BEGIN_MARKER):
            raise InstallerError("policy template must start with the pilotfish marker")
        _policy_block(policy.decode("utf-8"))
        agents: dict[str, tuple[str, bytes]] = {}
        for artifact in role_artifacts:
            path = Path(artifact.relative_path)
            if (
                len(path.parts) != 2
                or path.parts[0] != "agents"
                or path.name not in ALLOWED_AGENT_FILES
            ):
                raise InstallerError(
                    f"unsupported canonical agent artifact: {artifact.relative_path}"
                )
            content = artifact.content
            try:
                name = _frontmatter_name(content.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise InstallerError(f"agent template is not UTF-8: {path.name}") from exc
            if not name:
                raise InstallerError(f"agent template lacks frontmatter name: {path.name}")
            if name in {entry[0] for entry in agents.values()}:
                raise InstallerError(f"duplicate template role name: {name}")
            agents[path.name] = (name, content)

        golden = {
            self.source_root / "templates" / "settings.snippet.json": settings_bytes,
            self.source_root / "templates" / "claude-md.orchestration.md": policy,
        }
        golden.update(
            {
                self.source_root / "templates" / "agents" / filename: content
                for filename, (_, content) in agents.items()
            }
        )
        for path, expected in golden.items():
            try:
                actual = path.read_bytes()
            except OSError as exc:
                raise InstallerError(f"cannot read local golden artifact: {path}") from exc
            if actual != expected:
                raise InstallerError(
                    f"golden artifact is stale relative to canonical compiler: {path}"
                )
        return settings, agents, policy

    def _existing_agent_names(self) -> dict[str, list[str]]:
        directory = self._path(".claude/agents")
        found: dict[str, list[str]] = {}
        if not directory.exists():
            return found
        if directory.is_symlink() or not directory.is_dir():
            raise InstallerError(".claude/agents must be a regular directory")
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_symlink():
                raise InstallerError(
                    f"symlink in Claude agents directory blocks collision scan: {path.name}"
                )
            if not path.is_file():
                continue
            try:
                name = _frontmatter_name(path.read_bytes().decode("utf-8"))
            except (UnicodeDecodeError, OSError, InstallerError) as exc:
                raise InstallerError(
                    f"cannot conservatively parse agent file {path.name}: {exc}"
                ) from exc
            if name:
                found.setdefault(name, []).append(f".claude/agents/{path.name}")
        return found

    def plan_install(self) -> InstallPlan:
        return self._prepare_install("install").plan

    def plan_update(self) -> InstallPlan:
        return self._prepare_install("update").plan

    def _prepare_install(self, operation: str = "install") -> _Prepared:
        if operation not in {"install", "update"}:
            raise ValueError("operation must be install or update")
        settings_source, agent_sources, policy_source = self._sources()
        state = self._load_state()
        next_state = json.loads(json.dumps(state))
        next_state.setdefault("settings", {})
        next_state.setdefault("settings_file_created", False)
        next_state.setdefault("agents", {})
        next_state.setdefault("policy", None)
        changes: list[PlannedChange] = []
        mutations: list[_Mutation] = []
        warnings: list[str] = []
        blockers: list[str] = []
        skipped: list[str] = []

        runtime_blocker = self._runtime_blocker()
        if runtime_blocker:
            blockers.append(runtime_blocker)

        if OVERRIDE_ENV in self.env:
            warnings.append(
                f"{OVERRIDE_ENV} is set and overrides every per-role model binding; "
                "the installer will not unset it"
            )

        settings_rel = ".claude/settings.json"
        settings_raw = self._read_bytes(settings_rel)
        settings = self._read_json(settings_rel, default={})
        if not isinstance(settings, dict):
            raise InstallerError(".claude/settings.json must contain a JSON object")
        if "availableModels" in settings:
            allowlist = settings["availableModels"]
            required_aliases = {"best", "opus", "sonnet", "haiku"}
            if not isinstance(allowlist, list) or not all(
                isinstance(item, str) for item in allowlist
            ):
                blockers.append(
                    "availableModels must be a string list containing best, opus, sonnet, and haiku"
                )
            else:
                missing_aliases = required_aliases - set(allowlist)
                if missing_aliases:
                    blockers.append(
                        "availableModels blocks canonical aliases: "
                        + ", ".join(sorted(missing_aliases))
                        + "; update it explicitly, then rerun"
                    )
        merged = json.loads(json.dumps(settings))
        changed_keys: list[str] = []
        settings_state = next_state["settings"]
        for key, desired in settings_source.items():
            owned = settings_state.get(key)
            current_present = key in settings
            current = settings.get(key)
            if owned:
                expected = owned.get("hash")
                if not current_present or _value_hash(current) != expected:
                    warnings.append(
                        f"preserving user-modified owned setting {key!r}; it no longer "
                        "matches installer state"
                    )
                    skipped.append(f"{settings_rel}:{key}")
                    continue
                if current != desired:
                    merged[key] = desired
                    changed_keys.append(key)
                settings_state[key] = {"hash": _value_hash(desired)}
            elif not current_present:
                merged[key] = desired
                changed_keys.append(key)
                settings_state[key] = {"hash": _value_hash(desired)}
            elif current != desired:
                warnings.append(
                    f"preserving unowned setting {key!r}; desired template value was not applied"
                )
                skipped.append(f"{settings_rel}:{key}")
        if changed_keys:
            if settings_raw is None:
                next_state["settings_file_created"] = True
            after = _json_bytes(merged)
            action = "create" if settings_raw is None else "merge"
            detail = "merge owned keys: " + ", ".join(sorted(changed_keys))
            changes.append(
                PlannedChange(
                    settings_rel,
                    action,
                    detail,
                    None if settings_raw is None else _sha256(settings_raw),
                )
            )
            transitions = {
                key: {
                    "before_present": key in settings,
                    "before_sha256": _value_hash(settings[key]) if key in settings else None,
                    "after_present": True,
                    "after_sha256": _value_hash(merged[key]),
                }
                for key in changed_keys
            }
            mutations.append(
                _Mutation(
                    settings_rel,
                    "settings",
                    after,
                    detail,
                    {"key_transitions": transitions},
                    None if settings_raw is None else _sha256(settings_raw),
                )
            )

        existing_names = self._existing_agent_names()
        for filename, (name, source) in agent_sources.items():
            rel = f".claude/agents/{filename}"
            current = self._read_bytes(rel)
            owned = next_state["agents"].get(rel)
            same_name_paths = existing_names.get(name, [])
            other_paths = [path for path in same_name_paths if path != rel]
            if other_paths:
                blockers.append(
                    f"role name collision for {name!r}: " + ", ".join(other_paths)
                )
                continue
            if current is None:
                changes.append(PlannedChange(rel, "create", f"install role {name}"))
                mutations.append(_Mutation(rel, "agent", source, f"install role {name}", expected_before_sha256=None))
                next_state["agents"][rel] = {"name": name, "hash": _sha256(source)}
            elif current == source:
                if owned and owned.get("hash") == _sha256(current):
                    next_state["agents"][rel] = {"name": name, "hash": _sha256(source)}
                skipped.append(rel)
            elif owned and owned.get("hash") == _sha256(current):
                changes.append(PlannedChange(rel, "update", f"update owned role {name}", _sha256(current)))
                mutations.append(_Mutation(rel, "agent", source, f"update owned role {name}", expected_before_sha256=_sha256(current)))
                next_state["agents"][rel] = {"name": name, "hash": _sha256(source)}
            else:
                blockers.append(
                    f"role name collision for {name!r}: {rel} contains unowned or modified content"
                )

        policy_rel = ".claude/CLAUDE.md"
        policy_raw = self._read_bytes(policy_rel)
        try:
            existing_text = "" if policy_raw is None else policy_raw.decode("utf-8")
            desired_text = policy_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InstallerError("CLAUDE.md and policy template must be UTF-8") from exc
        existing_block = _policy_block(existing_text)
        policy_state = next_state.get("policy")
        if existing_block is None:
            new_text, separator_length = _replace_policy_with_separator(
                existing_text, desired_text
            )
            changes.append(
                PlannedChange(
                    policy_rel,
                    "create" if policy_raw is None else "append",
                    "install owned marker block",
                    None if policy_raw is None else _sha256(policy_raw),
                )
            )
            mutations.append(
                _Mutation(
                    policy_rel,
                    "policy",
                    new_text.encode("utf-8"),
                    "install owned marker block",
                    {
                        "before_block_sha256": None,
                        "after_block_sha256": _sha256(policy_source),
                        "separator_length": separator_length,
                    },
                    None if policy_raw is None else _sha256(policy_raw),
                )
            )
            next_state["policy"] = {
                "hash": _sha256(policy_source),
                "file_created": policy_raw is None,
                "separator_length": separator_length,
            }
        else:
            _, _, block = existing_block
            block_bytes = block.encode("utf-8")
            if block_bytes == policy_source:
                if policy_state and policy_state.get("hash") == _sha256(block_bytes):
                    next_state["policy"] = {
                        "hash": _sha256(policy_source),
                        "file_created": bool(policy_state.get("file_created")),
                        "separator_length": int(
                            policy_state.get("separator_length", 0)
                        ),
                    }
                skipped.append(policy_rel)
            elif policy_state and policy_state.get("hash") == _sha256(block_bytes):
                new_text = _replace_policy(existing_text, desired_text)
                changes.append(PlannedChange(policy_rel, "update", "replace owned marker block", _sha256(policy_raw or b"")))
                mutations.append(
                    _Mutation(
                        policy_rel,
                        "policy",
                        new_text.encode("utf-8"),
                        "replace owned marker block",
                        {
                            "before_block_sha256": _sha256(block_bytes),
                            "after_block_sha256": _sha256(policy_source),
                            "separator_length": int(
                                policy_state.get("separator_length", 0)
                            ),
                        },
                        _sha256(policy_raw or b""),
                    )
                )
                next_state["policy"] = {
                    "hash": _sha256(policy_source),
                    "file_created": bool(policy_state.get("file_created")),
                    "separator_length": int(
                        policy_state.get("separator_length", 0)
                    ),
                }
            else:
                blockers.append(
                    "CLAUDE.md contains an unowned or user-modified pilotfish marker block"
                )

        state_raw = self._read_bytes(self._state_rel)
        if mutations:
            changes.append(
                PlannedChange(
                    self._state_rel,
                    "update",
                    "record hash-only ownership state",
                    None if state_raw is None else _sha256(state_raw),
                )
            )
            changes.append(
                PlannedChange(
                    ".claude/pilotfish/manifests/<operation-id>.json",
                    "create",
                    "record rollback paths, hashes, and owned keys",
                )
            )
        changes = self._bind_mutation_plan(
            changes, mutations, next_state=next_state
        )
        plan = InstallPlan(
            operation,
            str(self.target_home),
            tuple(changes),
            tuple(warnings),
            tuple(blockers),
        )
        return _Prepared(
            plan,
            mutations,
            next_state,
            skipped,
            None if state_raw is None else _sha256(state_raw),
            bool(mutations),
        )

    def install(
        self, *, approval: str | None = None, dry_run: bool = False
    ) -> OperationResult:
        prepared = self._prepare_install("install")
        if dry_run:
            return OperationResult(
                "install",
                skipped=tuple(prepared.skipped),
                warnings=prepared.plan.warnings + prepared.plan.blockers,
                dry_run=True,
                plan=prepared.plan.to_dict(),
            )
        return self._apply_prepared(prepared, approval=approval)

    def update(
        self, *, approval: str | None = None, dry_run: bool = False
    ) -> OperationResult:
        prepared = self._prepare_install("update")
        if dry_run:
            return OperationResult(
                "update",
                skipped=tuple(prepared.skipped),
                warnings=prepared.plan.warnings + prepared.plan.blockers,
                dry_run=True,
                plan=prepared.plan.to_dict(),
            )
        return self._apply_prepared(prepared, approval=approval)

    def _apply_prepared(self, prepared: _Prepared, *, approval: str | None) -> OperationResult:
        if prepared.plan.blockers:
            raise PlanBlocked("; ".join(prepared.plan.blockers))
        if not prepared.mutations and not prepared.write_state:
            return OperationResult(
                prepared.plan.operation,
                skipped=tuple(prepared.skipped),
                warnings=prepared.plan.warnings,
            )
        if approval != prepared.plan.fingerprint:
            raise ApprovalRequired(
                "write operation requires the exact fingerprint from the reviewed plan"
            )
        operation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-") + uuid.uuid4().hex[:8]
        backup_root_rel = f".claude/pilotfish/backups/{operation_id}"
        records: list[dict[str, Any]] = []
        applied: list[dict[str, Any]] = []
        manifest_rel = f".claude/pilotfish/manifests/{operation_id}.json"
        planned_by_path = {change.path: change for change in prepared.plan.changes}
        prospective_paths = [mutation.path for mutation in prepared.mutations]
        prospective_paths.extend((self._state_rel, manifest_rel, backup_root_rel + "/sentinel"))
        missing_directories = self._missing_parent_directories(prospective_paths)
        try:
            for mutation in prepared.mutations:
                before = self._read_bytes(mutation.path)
                actual_before = None if before is None else _sha256(before)
                actual_before_mode = self._mode(mutation.path)
                if (
                    actual_before != mutation.expected_before_sha256
                    or actual_before_mode != mutation.expected_before_mode
                ):
                    raise InstallerError(
                        f"precondition changed after planning: {mutation.path}"
                    )
                after_mode = (
                    None
                    if mutation.after is None
                    else actual_before_mode if actual_before_mode is not None else PRIVATE_FILE_MODE
                )
                backup_rel: str | None = None
                if before is not None:
                    backup_rel = f"{backup_root_rel}/{mutation.path}"
                    self._atomic_write(
                        backup_rel,
                        before,
                        mode=PRIVATE_FILE_MODE,
                        expected_sha256=None,
                        expected_mode=None,
                    )
                record: dict[str, Any] = {
                    "path": mutation.path,
                    "kind": mutation.kind,
                    "existed_before": before is not None,
                    "before_sha256": actual_before,
                    "after_sha256": None if mutation.after is None else _sha256(mutation.after),
                    "before_mode": actual_before_mode,
                    "after_mode": after_mode,
                    "backup": backup_rel,
                }
                record.update(mutation.metadata)
                records.append(record)
                applied.append(record)
                self._write_or_delete(
                    mutation.path,
                    mutation.after,
                    expected_sha256=mutation.expected_before_sha256,
                    expected_mode=mutation.expected_before_mode,
                )
                actual_after = self._read_bytes(mutation.path)
                planned_change = planned_by_path[mutation.path]
                if (
                    (None if actual_after is None else _sha256(actual_after))
                    != planned_change.after_sha256
                    or self._mode(mutation.path) != planned_change.after_mode
                ):
                    raise InstallerError(
                        f"write result did not match approved plan: {mutation.path}"
                    )

            state_before = self._read_bytes(self._state_rel)
            actual_state_before = None if state_before is None else _sha256(state_before)
            actual_state_mode = self._mode(self._state_rel)
            planned_state_change = next(
                (change for change in prepared.plan.changes if change.path == self._state_rel),
                None,
            )
            if (
                actual_state_before != prepared.expected_state_sha256
                or (
                    planned_state_change is not None
                    and actual_state_mode != planned_state_change.before_mode
                )
            ):
                raise InstallerError("installer ownership state changed after planning")
            state_backup: str | None = None
            if state_before is not None:
                state_backup = f"{backup_root_rel}/{self._state_rel}"
                self._atomic_write(
                    state_backup,
                    state_before,
                    mode=PRIVATE_FILE_MODE,
                    expected_sha256=None,
                    expected_mode=None,
                )
            state_after = (
                None if prepared.next_state is None else _state_bytes(prepared.next_state)
            )
            state_record = {
                "path": self._state_rel,
                "kind": "state",
                "existed_before": state_before is not None,
                "before_sha256": actual_state_before,
                "after_sha256": None if state_after is None else _sha256(state_after),
                "before_mode": actual_state_mode,
                "after_mode": (
                    None
                    if state_after is None
                    else actual_state_mode if actual_state_mode is not None else PRIVATE_FILE_MODE
                ),
                "backup": state_backup,
            }
            records.append(state_record)
            applied.append(state_record)
            self._write_or_delete(
                self._state_rel,
                state_after,
                expected_sha256=prepared.expected_state_sha256,
                expected_mode=(
                    _EXPECTATION_UNSET
                    if planned_state_change is None
                    else planned_state_change.before_mode
                ),
            )
            state_plan = planned_by_path[self._state_rel]
            actual_state_after = self._read_bytes(self._state_rel)
            if (
                (None if actual_state_after is None else _sha256(actual_state_after))
                != state_plan.after_sha256
                or self._mode(self._state_rel) != state_plan.after_mode
            ):
                raise InstallerError("ownership state did not match approved plan")

            manifest = {
                "schema_version": MANIFEST_VERSION,
                "operation_id": operation_id,
                "operation": prepared.plan.operation,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "target": ".claude",
                "records": records,
            }
            manifest["integrity_sha256"] = _sha256(_json_bytes(manifest))
            self._atomic_write(
                manifest_rel,
                _json_bytes(manifest),
                mode=PRIVATE_FILE_MODE,
                expected_sha256=None,
                expected_mode=None,
            )
        except Exception as exc:
            rollback_errors = self._restore_applied_records(applied)
            if not rollback_errors:
                self._cleanup_failed_operation(
                    backup_root_rel, manifest_rel, missing_directories
                )
            suffix = (
                "; automatic rollback errors: " + "; ".join(rollback_errors)
                if rollback_errors
                else "; applied writes were rolled back"
            )
            if isinstance(exc, InstallerError):
                raise InstallerError(str(exc) + suffix) from exc
            raise InstallerError(f"install operation failed: {exc}{suffix}") from exc
        return OperationResult(
            prepared.plan.operation,
            changed=tuple(record["path"] for record in records),
            skipped=tuple(prepared.skipped),
            warnings=prepared.plan.warnings,
            manifest=manifest_rel,
            plan=prepared.plan.to_dict(),
        )

    def _restore_applied_records(self, records: Sequence[Mapping[str, Any]]) -> list[str]:
        errors: list[str] = []
        for record in reversed(records):
            try:
                path = str(record["path"])
                current = self._read_bytes(path)
                current_sha256 = None if current is None else _sha256(current)
                current_mode = self._mode(path)
                before_state = (
                    record.get("before_sha256"),
                    record.get("before_mode"),
                )
                after_state = (
                    record.get("after_sha256"),
                    record.get("after_mode"),
                )
                current_state = (current_sha256, current_mode)
                if current_state == before_state:
                    continue
                if current_state != after_state:
                    raise InstallerError(
                        f"concurrent change preserved during automatic rollback: {path}"
                    )
                if record.get("existed_before"):
                    backup = record.get("backup")
                    if not isinstance(backup, str):
                        raise InstallerError(f"missing automatic rollback backup for {path}")
                    raw = self._read_bytes(backup)
                    if raw is None or _sha256(raw) != record.get("before_sha256"):
                        raise InstallerError(f"automatic rollback backup mismatch for {path}")
                    before_mode = record.get("before_mode")
                    if not isinstance(before_mode, int):
                        raise InstallerError(f"missing automatic rollback mode for {path}")
                    self._atomic_write(
                        path,
                        raw,
                        mode=before_mode,
                        preserve_existing_mode=False,
                        expected_sha256=record.get("after_sha256"),
                        expected_mode=record.get("after_mode"),
                    )
                else:
                    self._write_or_delete(
                        path,
                        None,
                        expected_sha256=record.get("after_sha256"),
                        expected_mode=record.get("after_mode"),
                    )
            except Exception as exc:  # best-effort recovery must report every failure
                errors.append(f"{record.get('path')}: {exc}")
        return errors

    def _missing_parent_directories(self, relatives: Sequence[str]) -> set[Path]:
        missing: set[Path] = set()
        if self._descriptor_paths:
            prefixes: set[tuple[str, ...]] = set()
            for relative in relatives:
                parts = self._relative_parts(relative)
                prefixes.update(parts[:index] for index in range(1, len(parts)))
            for parts in prefixes:
                descriptor = self._open_directory_fd_parts(parts, create=False)
                if descriptor is None:
                    missing.add(self.target_home.joinpath(*parts))
                else:
                    os.close(descriptor)
            return missing
        for relative in relatives:
            path = self._path(relative).parent
            while path != self.target_home:
                if not path.exists():
                    missing.add(path)
                path = path.parent
        return missing

    def _cleanup_failed_operation(
        self,
        backup_root_rel: str,
        manifest_rel: str,
        missing_directories: set[Path],
    ) -> None:
        if self._descriptor_paths:
            self._write_or_delete(manifest_rel, None)
            self._remove_tree_descriptor(backup_root_rel)
            for path in sorted(
                missing_directories,
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                try:
                    relative = path.relative_to(self.target_home).as_posix()
                    self._remove_empty_directory_descriptor(relative)
                except (FileNotFoundError, OSError, InstallerError, ValueError):
                    pass
            return
        manifest_path = self._path(manifest_rel)
        if manifest_path.exists() and manifest_path.is_file() and not manifest_path.is_symlink():
            manifest_path.unlink()
        backup_root = self._path(backup_root_rel)
        if backup_root.exists() and backup_root.is_dir() and not backup_root.is_symlink():
            shutil.rmtree(backup_root)
        for path in sorted(missing_directories, key=lambda item: len(item.parts), reverse=True):
            try:
                path.rmdir()
            except (FileNotFoundError, OSError):
                pass

    def plan_uninstall(self) -> InstallPlan:
        return self._prepare_uninstall().plan

    def _prepare_uninstall(self) -> _Prepared:
        state = self._load_state()
        next_state = json.loads(json.dumps(state))
        changes: list[PlannedChange] = []
        mutations: list[_Mutation] = []
        warnings: list[str] = []
        skipped: list[str] = []

        settings_rel = ".claude/settings.json"
        settings = self._read_json(settings_rel, default={})
        if not isinstance(settings, dict):
            raise InstallerError(".claude/settings.json must contain a JSON object")
        cleaned = json.loads(json.dumps(settings))
        removed_keys: list[str] = []
        for key, owned in list(next_state.get("settings", {}).items()):
            if key not in settings:
                next_state["settings"].pop(key, None)
            elif _value_hash(settings[key]) == owned.get("hash"):
                cleaned.pop(key, None)
                removed_keys.append(key)
                next_state["settings"].pop(key, None)
            else:
                warnings.append(f"preserving modified owned setting {key!r}")
                skipped.append(f"{settings_rel}:{key}")
        if not settings and not next_state["settings"]:
            next_state["settings_file_created"] = False
        if removed_keys:
            detail = "remove unchanged owned keys: " + ", ".join(sorted(removed_keys))
            settings_raw = self._read_bytes(settings_rel)
            changes.append(
                PlannedChange(
                    settings_rel,
                    "merge",
                    detail,
                    None if settings_raw is None else _sha256(settings_raw),
                )
            )
            transitions = {
                key: {
                    "before_present": True,
                    "before_sha256": _value_hash(settings[key]),
                    "after_present": False,
                    "after_sha256": None,
                }
                for key in removed_keys
            }
            if not next_state["settings"]:
                remove_settings_file = bool(
                    next_state.get("settings_file_created") and not cleaned
                )
                next_state["settings_file_created"] = False
            else:
                remove_settings_file = False
            mutations.append(
                _Mutation(
                    settings_rel,
                    "settings",
                    None if remove_settings_file else _json_bytes(cleaned),
                    detail,
                    {"key_transitions": transitions},
                    None if settings_raw is None else _sha256(settings_raw),
                )
            )

        for rel, owned in list(next_state.get("agents", {}).items()):
            current = self._read_bytes(rel)
            if current is not None and _sha256(current) == owned.get("hash"):
                changes.append(PlannedChange(rel, "remove", "remove unchanged owned role", _sha256(current)))
                mutations.append(
                    _Mutation(
                        rel,
                        "agent",
                        None,
                        "remove unchanged owned role",
                        expected_before_sha256=_sha256(current),
                    )
                )
                next_state["agents"].pop(rel, None)
            elif current is None:
                next_state["agents"].pop(rel, None)
            else:
                warnings.append(f"preserving user-modified owned role {rel}")
                skipped.append(rel)

        policy_rel = ".claude/CLAUDE.md"
        policy_owner = next_state.get("policy")
        if policy_owner:
            raw = self._read_bytes(policy_rel)
            if raw is None:
                next_state["policy"] = None
            else:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise InstallerError("CLAUDE.md must be UTF-8") from exc
                block = _policy_block(text)
                if block and _sha256(block[2].encode("utf-8")) == policy_owner.get("hash"):
                    separator_length = int(policy_owner.get("separator_length", 0))
                    cleaned_text, _ = _replace_policy_with_separator(
                        text, None, separator_length=separator_length
                    )
                    after = (
                        None
                        if not cleaned_text and policy_owner.get("file_created")
                        else cleaned_text.encode("utf-8")
                    )
                    changes.append(
                        PlannedChange(
                            policy_rel,
                            "remove",
                            "remove unchanged owned marker block",
                            _sha256(raw),
                        )
                    )
                    mutations.append(
                        _Mutation(
                            policy_rel,
                            "policy",
                            after,
                            "remove unchanged owned marker block",
                            {
                                "before_block_sha256": policy_owner.get("hash"),
                                "after_block_sha256": None,
                                "separator_length": separator_length,
                            },
                            _sha256(raw),
                        )
                    )
                    next_state["policy"] = None
                else:
                    warnings.append("preserving user-modified owned CLAUDE.md marker block")
                    skipped.append(policy_rel)

        still_owned = bool(next_state.get("settings") or next_state.get("agents") or next_state.get("policy"))
        state_changed = next_state != state
        state_raw = self._read_bytes(self._state_rel)
        if mutations or state_changed:
            state_after = next_state if still_owned else None
            changes.append(
                PlannedChange(
                    self._state_rel,
                    "update",
                    "update hash-only ownership state",
                    None if state_raw is None else _sha256(state_raw),
                )
            )
            changes.append(
                PlannedChange(
                    ".claude/pilotfish/manifests/<operation-id>.json",
                    "create",
                    "record rollback paths, hashes, and owned keys",
                )
            )
        else:
            state_after = next_state
        changes = self._bind_mutation_plan(
            changes, mutations, next_state=state_after
        )
        plan = InstallPlan(
            "uninstall", str(self.target_home), tuple(changes), tuple(warnings), ()
        )
        return _Prepared(
            plan,
            mutations,
            state_after,
            skipped,
            None if state_raw is None else _sha256(state_raw),
            bool(mutations or state_changed),
        )

    def uninstall(
        self, *, approval: str | None = None, dry_run: bool = False
    ) -> OperationResult:
        prepared = self._prepare_uninstall()
        if dry_run:
            return OperationResult(
                "uninstall",
                skipped=tuple(prepared.skipped),
                warnings=prepared.plan.warnings,
                dry_run=True,
                plan=prepared.plan.to_dict(),
            )
        return self._apply_prepared(prepared, approval=approval)

    def plan_rollback(self, manifest: str | os.PathLike[str]) -> InstallPlan:
        return self._prepare_rollback(manifest)[2]

    def rollback(
        self,
        manifest: str | os.PathLike[str],
        *,
        approval: str | None = None,
        dry_run: bool = False,
    ) -> OperationResult:
        manifest_rel, actions, plan, skipped = self._prepare_rollback(manifest)
        warnings = tuple(
            f"preserving modified content during rollback: {entry}" for entry in skipped
        )
        if dry_run:
            return OperationResult(
                "rollback",
                skipped=tuple(skipped),
                warnings=warnings,
                manifest=manifest_rel,
                dry_run=True,
                plan=plan.to_dict(),
            )
        if actions and approval != plan.fingerprint:
            raise ApprovalRequired(
                "rollback requires the exact fingerprint from the reviewed plan"
            )
        undo: list[
            tuple[str, bytes | None, int | None, str | None, int | None]
        ] = []
        changed: list[str] = []
        planned_by_path = {change.path: change for change in plan.changes}
        try:
            for record, keys in actions:
                path = str(record["path"])
                current = self._read_bytes(path)
                current_mode = self._mode(path)
                planned = planned_by_path[path]
                if (
                    (None if current is None else _sha256(current))
                    != planned.before_sha256
                    or current_mode != planned.before_mode
                ):
                    raise InstallerError(
                        f"rollback precondition changed after planning: {path}"
                    )
                if record["kind"] == "settings":
                    self._rollback_settings(
                        record,
                        keys or [],
                        expected_sha256=planned.before_sha256,
                        expected_mode=planned.before_mode,
                    )
                elif record["kind"] == "policy":
                    self._rollback_policy(
                        record,
                        expected_sha256=planned.before_sha256,
                        expected_mode=planned.before_mode,
                    )
                else:
                    self._restore_generic_record(
                        record,
                        expected_sha256=planned.before_sha256,
                        expected_mode=planned.before_mode,
                    )
                undo.append(
                    (
                        path,
                        current,
                        current_mode,
                        planned.after_sha256,
                        planned.after_mode,
                    )
                )
                actual_after = self._read_bytes(path)
                if (
                    (None if actual_after is None else _sha256(actual_after))
                    != planned.after_sha256
                    or self._mode(path) != planned.after_mode
                ):
                    raise InstallerError(
                        f"rollback result did not match approved plan: {path}"
                    )
                changed.append(path)
        except Exception as exc:
            errors: list[str] = []
            for path, content, mode, expected_sha256, expected_mode in reversed(undo):
                try:
                    recovery_current = self._read_bytes(path)
                    current_state = (
                        None
                        if recovery_current is None
                        else _sha256(recovery_current),
                        self._mode(path),
                    )
                    original_state = (
                        None if content is None else _sha256(content),
                        mode,
                    )
                    if current_state == original_state:
                        continue
                    if current_state != (expected_sha256, expected_mode):
                        raise InstallerError(
                            f"concurrent change preserved during rollback recovery: {path}"
                        )
                    if content is None:
                        self._write_or_delete(
                            path,
                            None,
                            expected_sha256=expected_sha256,
                            expected_mode=expected_mode,
                        )
                    else:
                        self._atomic_write(
                            path,
                            content,
                            mode=int(mode),
                            preserve_existing_mode=False,
                            expected_sha256=expected_sha256,
                            expected_mode=expected_mode,
                        )
                except Exception as undo_exc:
                    errors.append(f"{path}: {undo_exc}")
            suffix = "; rollback changes were reverted" if not errors else "; recovery errors: " + "; ".join(errors)
            raise InstallerError(f"rollback failed: {exc}{suffix}") from exc
        return OperationResult(
            "rollback",
            changed=tuple(changed),
            skipped=tuple(skipped),
            warnings=warnings,
            manifest=manifest_rel,
            plan=plan.to_dict(),
        )

    def _prepare_rollback(
        self, manifest: str | os.PathLike[str]
    ) -> tuple[
        str,
        list[tuple[dict[str, Any], list[str] | None]],
        InstallPlan,
        list[str],
    ]:
        manifest_rel = self._manifest_relative(manifest)
        document = self._read_json(manifest_rel, default=None)
        records = self._validate_manifest(manifest_rel, document)
        actions: list[tuple[dict[str, Any], list[str] | None]] = []
        skipped: list[str] = []
        changes: list[PlannedChange] = []
        for record in reversed(records):
            path = str(record["path"])
            kind = record["kind"]
            if kind == "settings":
                keys = self._rollbackable_settings_keys(record)
                for key in record["key_transitions"]:
                    if key not in keys:
                        skipped.append(f"{path}:{key}")
                if keys:
                    actions.append((record, keys))
                    predicted = self._predict_settings_rollback(record, keys)
                    changes.append(
                        self._rollback_planned_change(
                            path,
                            "rollback-keys",
                            "restore owned keys: " + ", ".join(sorted(keys)),
                            predicted,
                            record,
                        )
                    )
            elif kind == "policy":
                if self._policy_record_can_rollback(record):
                    actions.append((record, None))
                    predicted = self._predict_policy_rollback(record)
                    changes.append(
                        self._rollback_planned_change(
                            path,
                            "rollback-block",
                            "restore marker block only",
                            predicted,
                            record,
                        )
                    )
                else:
                    skipped.append(path)
            else:
                current = self._read_bytes(path)
                expected = record.get("after_sha256")
                if (current is None and expected is None) or (
                    current is not None and _sha256(current) == expected
                ):
                    actions.append((record, None))
                    predicted = self._read_backup_bytes(record)
                    changes.append(
                        self._rollback_planned_change(
                            path,
                            "rollback",
                            "restore recorded pre-operation state",
                            predicted,
                            record,
                        )
                    )
                else:
                    skipped.append(path)
        if any(entry != self._state_rel for entry in skipped):
            actions = [
                action for action in actions if action[0].get("kind") != "state"
            ]
            changes = [change for change in changes if change.path != self._state_rel]
            if self._state_rel not in skipped:
                skipped.append(self._state_rel)
        warnings = tuple(
            f"preserving modified content during rollback: {entry}" for entry in skipped
        )
        plan = InstallPlan(
            "rollback", str(self.target_home), tuple(changes), warnings, ()
        )
        return manifest_rel, actions, plan, skipped

    def _rollback_planned_change(
        self,
        path: str,
        action: str,
        detail: str,
        predicted: bytes | None,
        record: Mapping[str, Any],
    ) -> PlannedChange:
        current = self._read_bytes(path)
        current_mode = self._mode(path)
        if predicted is None:
            after_mode = None
        elif record.get("existed_before"):
            after_mode = int(record["before_mode"])
        else:
            after_mode = current_mode if current_mode is not None else PRIVATE_FILE_MODE
        return PlannedChange(
            path,
            action,
            detail,
            None if current is None else _sha256(current),
            None if predicted is None else _sha256(predicted),
            current_mode,
            after_mode,
        )

    def _predict_settings_rollback(
        self, record: Mapping[str, Any], keys: Sequence[str]
    ) -> bytes | None:
        current = self._read_json(str(record["path"]), default={})
        before = self._read_backup_json(record)
        if not isinstance(current, dict) or not isinstance(before, dict):
            raise InstallerError("settings rollback prediction requires JSON objects")
        predicted = json.loads(json.dumps(current))
        for key in keys:
            if key in before:
                predicted[key] = before[key]
            else:
                predicted.pop(key, None)
        if not record.get("existed_before") and not predicted:
            return None
        return _json_bytes(predicted)

    def _predict_policy_rollback(self, record: Mapping[str, Any]) -> bytes | None:
        current_raw = self._read_bytes(str(record["path"]))
        current = "" if current_raw is None else current_raw.decode("utf-8")
        before_raw = self._read_backup_bytes(record)
        before = "" if before_raw is None else before_raw.decode("utf-8")
        before_block = _policy_block(before)
        replacement = None if before_block is None else before_block[2]
        predicted, _ = _replace_policy_with_separator(
            current,
            replacement,
            separator_length=int(record.get("separator_length", 0)),
        )
        if not predicted and not record.get("existed_before"):
            return None
        return predicted.encode("utf-8")

    def _rollbackable_settings_keys(self, record: Mapping[str, Any]) -> list[str]:
        current = self._read_json(str(record["path"]), default={})
        if not isinstance(current, dict):
            return []
        result: list[str] = []
        for key, transition in record["key_transitions"].items():
            after_present = transition["after_present"]
            if after_present:
                if key in current and _value_hash(current[key]) == transition["after_sha256"]:
                    result.append(key)
            elif key not in current:
                result.append(key)
        return result

    def _rollback_settings(
        self,
        record: Mapping[str, Any],
        keys: Sequence[str],
        *,
        expected_sha256: str | None,
        expected_mode: int | None,
    ) -> None:
        path = str(record["path"])
        current = self._read_json(path, default={})
        before = self._read_backup_json(record)
        if not isinstance(current, dict) or not isinstance(before, dict):
            raise InstallerError(f"settings rollback requires JSON objects: {path}")
        for key in keys:
            if key in before:
                current[key] = before[key]
            else:
                current.pop(key, None)
        if not record.get("existed_before") and not current:
            self._write_or_delete(
                path,
                None,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
        else:
            mode = record.get("before_mode") if record.get("existed_before") else PRIVATE_FILE_MODE
            self._atomic_write(
                path,
                _json_bytes(current),
                mode=int(mode),
                preserve_existing_mode=not bool(record.get("existed_before")),
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )

    def _policy_record_can_rollback(self, record: Mapping[str, Any]) -> bool:
        raw = self._read_bytes(str(record["path"]))
        text = "" if raw is None else raw.decode("utf-8")
        block = _policy_block(text)
        expected = record.get("after_block_sha256")
        return (block is None and expected is None) or (
            block is not None and _sha256(block[2].encode("utf-8")) == expected
        )

    def _rollback_policy(
        self,
        record: Mapping[str, Any],
        *,
        expected_sha256: str | None,
        expected_mode: int | None,
    ) -> None:
        path = str(record["path"])
        current_raw = self._read_bytes(path)
        current = "" if current_raw is None else current_raw.decode("utf-8")
        before_raw = self._read_backup_bytes(record)
        before = "" if before_raw is None else before_raw.decode("utf-8")
        before_block = _policy_block(before)
        replacement = None if before_block is None else before_block[2]
        result, _ = _replace_policy_with_separator(
            current,
            replacement,
            separator_length=int(record.get("separator_length", 0)),
        )
        if not result and not record.get("existed_before"):
            self._write_or_delete(
                path,
                None,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
        elif record.get("existed_before"):
            before_mode = record.get("before_mode")
            if not isinstance(before_mode, int):
                raise InstallerError(f"missing rollback mode for {path}")
            self._atomic_write(
                path,
                result.encode("utf-8"),
                mode=before_mode,
                preserve_existing_mode=False,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
        else:
            self._write_or_delete(
                path,
                result.encode("utf-8"),
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )

    def _restore_generic_record(
        self,
        record: Mapping[str, Any],
        *,
        expected_sha256: str | None,
        expected_mode: int | None,
    ) -> None:
        path = str(record["path"])
        before = self._read_backup_bytes(record)
        if before is None:
            self._write_or_delete(
                path,
                None,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
            return
        before_mode = record.get("before_mode")
        if not isinstance(before_mode, int):
            raise InstallerError(f"missing rollback mode for {path}")
        self._atomic_write(
            path,
            before,
            mode=before_mode,
            preserve_existing_mode=False,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
        )

    def _read_backup_bytes(self, record: Mapping[str, Any]) -> bytes | None:
        if not record.get("existed_before"):
            return None
        backup = record.get("backup")
        if not isinstance(backup, str):
            raise InstallerError(f"missing rollback backup for {record['path']}")
        raw = self._read_bytes(backup)
        if raw is None or _sha256(raw) != record.get("before_sha256"):
            raise InstallerError(f"rollback backup hash mismatch for {record['path']}")
        return raw

    def _read_backup_json(self, record: Mapping[str, Any]) -> Any:
        raw = self._read_backup_bytes(record)
        return {} if raw is None else _parse_json(raw, label=str(record["backup"]))

    def _validate_manifest(
        self, manifest_rel: str, document: Any
    ) -> list[dict[str, Any]]:
        manifest_mode = self._mode(manifest_rel)
        if manifest_mode is None:
            raise InstallerError("rollback manifest is missing")
        if os.name != "nt" and manifest_mode & 0o077:
            raise InstallerError("rollback manifest permissions are not private")
        if not isinstance(document, dict) or document.get("schema_version") != MANIFEST_VERSION:
            raise InstallerError("invalid or unsupported rollback manifest")
        integrity = document.get("integrity_sha256")
        unsigned = dict(document)
        unsigned.pop("integrity_sha256", None)
        if not isinstance(integrity, str) or integrity != _sha256(_json_bytes(unsigned)):
            raise InstallerError("rollback manifest integrity check failed")
        operation_id = document.get("operation_id")
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", operation_id):
            raise InstallerError("invalid rollback operation id")
        expected_manifest = f".claude/pilotfish/manifests/{operation_id}.json"
        if manifest_rel != expected_manifest:
            raise InstallerError("rollback manifest path does not match operation id")
        if document.get("operation") not in {"install", "update", "uninstall"} or document.get("target") != ".claude":
            raise InstallerError("rollback manifest scope is invalid")
        records = document.get("records")
        if not isinstance(records, list) or not records:
            raise InstallerError("rollback manifest records must be a non-empty list")
        seen: set[str] = set()
        validated: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                raise InstallerError("malformed rollback record")
            path = record.get("path")
            kind = record.get("kind")
            if not isinstance(path, str) or path in seen:
                raise InstallerError("rollback paths must be unique strings")
            seen.add(path)
            allowed = (
                (kind == "settings" and path == ".claude/settings.json")
                or (kind == "policy" and path == ".claude/CLAUDE.md")
                or (kind == "state" and path == self._state_rel)
                or (
                    kind == "agent"
                    and path.startswith(".claude/agents/")
                    and Path(path).name in ALLOWED_AGENT_FILES
                    and len(Path(path).parts) == 3
                )
            )
            if not allowed:
                raise InstallerError(f"rollback record is outside installer ownership: {path}")
            self._relative_parts(path)
            for field_name in ("before_sha256", "after_sha256"):
                value = record.get(field_name)
                if value is not None and not (
                    isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                ):
                    raise InstallerError(f"invalid {field_name} for {path}")
            for field_name in ("before_mode", "after_mode"):
                value = record.get(field_name)
                if value is not None and not (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 0o7777
                ):
                    raise InstallerError(f"invalid {field_name} for {path}")
            existed = record.get("existed_before")
            backup = record.get("backup")
            if not isinstance(existed, bool):
                raise InstallerError(f"invalid existed_before for {path}")
            if existed != (record.get("before_mode") is not None):
                raise InstallerError(f"before mode does not match existence for {path}")
            if (record.get("after_sha256") is None) != (
                record.get("after_mode") is None
            ):
                raise InstallerError(f"after mode does not match content for {path}")
            expected_backup = f".claude/pilotfish/backups/{operation_id}/{path}"
            if (existed and backup != expected_backup) or (not existed and backup is not None):
                raise InstallerError(f"invalid backup path for {path}")
            if existed:
                backup_raw = self._read_bytes(expected_backup)
                if backup_raw is None or _sha256(backup_raw) != record.get("before_sha256"):
                    raise InstallerError(f"rollback backup hash mismatch for {path}")
                backup_mode = self._mode(expected_backup)
                if backup_mode is None:
                    raise InstallerError(f"rollback backup is missing: {path}")
                if os.name != "nt" and backup_mode & 0o077:
                    raise InstallerError(f"rollback backup permissions are not private: {path}")
            if kind == "settings":
                transitions = record.get("key_transitions")
                if not isinstance(transitions, dict) or not transitions:
                    raise InstallerError("settings rollback record lacks key transitions")
                if set(transitions) - ALLOWED_SETTINGS_KEYS:
                    raise InstallerError("settings rollback record contains unowned keys")
                for transition in transitions.values():
                    if not isinstance(transition, dict):
                        raise InstallerError("malformed settings key transition")
                    if not isinstance(transition.get("before_present"), bool) or not isinstance(transition.get("after_present"), bool):
                        raise InstallerError("settings key transition presence flags are invalid")
                    for prefix in ("before", "after"):
                        value = transition.get(f"{prefix}_sha256")
                        present = transition[f"{prefix}_present"]
                        if present != (isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))):
                            raise InstallerError("settings key transition hash is invalid")
            if kind == "policy":
                for field_name in ("before_block_sha256", "after_block_sha256"):
                    value = record.get(field_name)
                    if value is not None and not (
                        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                    ):
                        raise InstallerError(f"invalid policy segment hash: {field_name}")
                separator_length = record.get("separator_length")
                if (
                    isinstance(separator_length, bool)
                    or not isinstance(separator_length, int)
                    or not 0 <= separator_length <= 2
                ):
                    raise InstallerError("invalid policy separator length")
            validated.append(record)
        if validated[-1].get("kind") != "state":
            raise InstallerError("rollback manifest must end with ownership state")
        return validated

    def _manifest_relative(self, manifest: str | os.PathLike[str]) -> str:
        path = Path(manifest)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self.target_home).as_posix()
            except ValueError as exc:
                raise InstallerError("rollback manifest must be inside target_home") from exc
        return path.as_posix()

    def _write_or_delete(
        self,
        relative: str,
        content: bytes | None,
        *,
        expected_sha256: str | None | object = _EXPECTATION_UNSET,
        expected_mode: int | None | object = _EXPECTATION_UNSET,
    ) -> None:
        if self._descriptor_paths:
            if content is None:
                self._unlink_descriptor(
                    relative,
                    expected_sha256=expected_sha256,
                    expected_mode=expected_mode,
                )
            else:
                self._atomic_write(
                    relative,
                    content,
                    mode=PRIVATE_FILE_MODE,
                    expected_sha256=expected_sha256,
                    expected_mode=expected_mode,
                )
            return
        self._check_path_precondition(
            relative,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
        )
        path = self._path(relative)
        if content is None:
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise InstallerError(f"refusing to remove non-file target: {relative}")
                path.unlink()
            return
        self._atomic_write(
            relative,
            content,
            mode=PRIVATE_FILE_MODE,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
        )

    def _check_path_precondition(
        self,
        relative: str,
        *,
        expected_sha256: str | None | object,
        expected_mode: int | None | object,
    ) -> None:
        if (
            expected_sha256 is _EXPECTATION_UNSET
            and expected_mode is _EXPECTATION_UNSET
        ):
            return
        current = self._read_bytes(relative)
        actual_sha256 = None if current is None else _sha256(current)
        actual_mode = self._mode(relative)
        if (
            expected_sha256 is not _EXPECTATION_UNSET
            and actual_sha256 != expected_sha256
        ) or (
            expected_mode is not _EXPECTATION_UNSET
            and actual_mode != expected_mode
        ):
            raise InstallerError(f"precondition changed after planning: {relative}")

    @staticmethod
    def _fsync_directory(descriptor: int) -> None:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)}:
                raise

    def _stage_descriptor_file(
        self, parent_fd: int, name: str, content: bytes, mode: int
    ) -> str:
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=parent_fd,
            )
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while creating installer artifact")
                view = view[written:]
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            return temporary_name
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise

    def _compensate_descriptor_write(
        self,
        parent_fd: int,
        name: str,
        relative: str,
        before: tuple[bytes, int, os.stat_result] | None,
        after_content: bytes,
        after_mode: int,
    ) -> None:
        current = self._read_file_at(parent_fd, name, relative)
        if (
            current is None
            or _sha256(current[0]) != _sha256(after_content)
            or current[1] != after_mode
        ):
            raise InstallerError(
                f"cannot compensate changed descriptor target: {relative}"
            )
        if not self._same_file_snapshot(current[2], self._stat_at(parent_fd, name)):
            raise InstallerError(
                f"target changed before descriptor compensation: {relative}"
            )
        if before is None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            temporary = self._stage_descriptor_file(
                parent_fd, name, before[0], before[1]
            )
            try:
                os.rename(
                    temporary,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
        self._fsync_directory(parent_fd)

    def _compensate_descriptor_unlink(
        self,
        parent_fd: int,
        name: str,
        relative: str,
        before: tuple[bytes, int, os.stat_result],
    ) -> None:
        if self._stat_at(parent_fd, name) is not None:
            raise InstallerError(
                f"cannot compensate recreated descriptor target: {relative}"
            )
        temporary = self._stage_descriptor_file(
            parent_fd, name, before[0], before[1]
        )
        try:
            if self._stat_at(parent_fd, name) is not None:
                raise InstallerError(
                    f"target recreated before descriptor compensation: {relative}"
                )
            os.rename(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            self._fsync_directory(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    def _unlink_descriptor(
        self,
        relative: str,
        *,
        expected_sha256: str | None | object = _EXPECTATION_UNSET,
        expected_mode: int | None | object = _EXPECTATION_UNSET,
    ) -> None:
        parent_fd, name = self._open_parent_fd(relative, create=False)
        if parent_fd is None:
            if (
                expected_sha256 is not _EXPECTATION_UNSET
                and expected_sha256 is not None
            ) or (
                expected_mode is not _EXPECTATION_UNSET
                and expected_mode is not None
            ):
                raise InstallerError(
                    f"descriptor precondition changed after planning: {relative}"
                )
            return
        unlinked = False
        try:
            before = self._descriptor_precondition(
                parent_fd,
                name,
                relative,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
            if before is None:
                return
            current = self._stat_at(parent_fd, name)
            if not self._same_file_snapshot(before[2], current):
                raise InstallerError(f"target changed before removal: {relative}")
            os.unlink(name, dir_fd=parent_fd)
            unlinked = True
            self._fsync_directory(parent_fd)
            self._assert_parent_still_bound(relative, parent_fd)
        except Exception as exc:
            if unlinked:
                try:
                    self._compensate_descriptor_unlink(
                        parent_fd, name, relative, before
                    )
                except Exception as compensation_exc:
                    raise InstallerError(
                        f"{exc}; descriptor compensation failed: {compensation_exc}"
                    ) from exc
            if isinstance(exc, OSError):
                raise InstallerError(f"cannot safely remove target: {relative}") from exc
            raise
        finally:
            os.close(parent_fd)

    def _atomic_write(
        self,
        relative: str,
        content: bytes,
        *,
        mode: int,
        preserve_existing_mode: bool = True,
        expected_sha256: str | None | object = _EXPECTATION_UNSET,
        expected_mode: int | None | object = _EXPECTATION_UNSET,
    ) -> None:
        if self._descriptor_paths:
            self._atomic_write_descriptor(
                relative,
                content,
                mode=mode,
                preserve_existing_mode=preserve_existing_mode,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
            return
        self._atomic_write_windows(
            relative,
            content,
            mode=mode,
            preserve_existing_mode=preserve_existing_mode,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
        )

    def _atomic_write_descriptor(
        self,
        relative: str,
        content: bytes,
        *,
        mode: int,
        preserve_existing_mode: bool,
        expected_sha256: str | None | object,
        expected_mode: int | None | object,
    ) -> None:
        if (
            isinstance(mode, bool)
            or not isinstance(mode, int)
            or not 0 <= mode <= 0o7777
        ):
            raise InstallerError(f"invalid target mode for {relative}")
        parent_fd, name = self._open_parent_fd(relative, create=True)
        if parent_fd is None:  # create=True guarantees a descriptor
            raise InstallerError(f"cannot create target parent: {relative}")
        temporary_name: str | None = None
        renamed = False
        try:
            before = self._descriptor_precondition(
                parent_fd,
                name,
                relative,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
            desired_mode = (
                before[1]
                if before is not None and preserve_existing_mode
                else mode
            )
            temporary_name = self._stage_descriptor_file(
                parent_fd, name, content, desired_mode
            )
            current = self._stat_at(parent_fd, name)
            before_stat = None if before is None else before[2]
            if not self._same_file_snapshot(before_stat, current):
                raise InstallerError(f"target changed before replacement: {relative}")
            os.rename(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            renamed = True
            self._fsync_directory(parent_fd)
            self._assert_parent_still_bound(relative, parent_fd)
        except Exception as exc:
            if renamed:
                try:
                    self._compensate_descriptor_write(
                        parent_fd,
                        name,
                        relative,
                        before,
                        content,
                        desired_mode,
                    )
                except Exception as compensation_exc:
                    raise InstallerError(
                        f"{exc}; descriptor compensation failed: {compensation_exc}"
                    ) from exc
            if isinstance(exc, OSError):
                raise InstallerError(f"cannot safely replace target: {relative}") from exc
            raise
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    def _atomic_write_windows(
        self,
        relative: str,
        content: bytes,
        *,
        mode: int,
        preserve_existing_mode: bool,
        expected_sha256: str | None | object,
        expected_mode: int | None | object,
    ) -> None:
        self._check_path_precondition(
            relative,
            expected_sha256=expected_sha256,
            expected_mode=expected_mode,
        )
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after mkdir to close the ordinary symlink substitution gap.
        path = self._path(relative)
        if relative.startswith(".claude/pilotfish/"):
            private_root = self._path(".claude/pilotfish")
            current = private_root
            if current.exists():
                os.chmod(current, PRIVATE_DIRECTORY_MODE)
            for part in Path(relative).parts[2:-1]:
                current = current / part
                if current.exists():
                    os.chmod(current, PRIVATE_DIRECTORY_MODE)
        existing_mode = None
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise InstallerError(f"refusing to replace non-file target: {relative}")
            existing_mode = stat.S_IMODE(path.stat().st_mode)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(
                temporary,
                existing_mode
                if existing_mode is not None and preserve_existing_mode
                else mode,
            )
            self._check_path_precondition(
                relative,
                expected_sha256=expected_sha256,
                expected_mode=expected_mode,
            )
            path = self._path(relative)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _remove_tree_descriptor(self, relative: str) -> None:
        parent_fd, name = self._open_parent_fd(relative, create=False)
        if parent_fd is None:
            return
        try:
            entry = self._stat_at(parent_fd, name)
            if entry is None:
                return
            if not stat.S_ISDIR(entry.st_mode):
                raise InstallerError(f"refusing to remove non-directory tree: {relative}")
            if not shutil.rmtree.avoids_symlink_attacks:
                raise InstallerError("platform lacks descriptor-safe recursive cleanup")
            shutil.rmtree(name, dir_fd=parent_fd)
            self._fsync_directory(parent_fd)
            self._assert_parent_still_bound(relative, parent_fd)
        finally:
            os.close(parent_fd)

    def _remove_empty_directory_descriptor(self, relative: str) -> None:
        parent_fd, name = self._open_parent_fd(relative, create=False)
        if parent_fd is None:
            return
        try:
            entry = self._stat_at(parent_fd, name)
            if entry is None:
                return
            if not stat.S_ISDIR(entry.st_mode):
                raise InstallerError(
                    f"refusing to remove non-directory target: {relative}"
                )
            os.rmdir(name, dir_fd=parent_fd)
            self._fsync_directory(parent_fd)
            self._assert_parent_still_bound(relative, parent_fd)
        finally:
            os.close(parent_fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and apply local pilotfish Claude configuration"
    )
    parser.add_argument("command", choices=("plan", "install", "update", "uninstall", "rollback"))
    parser.add_argument("--target-home", required=True, help="explicit home directory to operate on")
    parser.add_argument("--source-root", help="local pilotfish checkout; defaults to this checkout")
    parser.add_argument(
        "--approve",
        metavar="PLAN_SHA256",
        help="approve only the plan with this exact fingerprint",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--manifest", help="rollback manifest path (inside target home)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        installer = Installer(
            target_home=args.target_home,
            source_root=args.source_root,
        )
        if args.command == "plan":
            output: Any = installer.plan_install().to_dict()
        elif args.command == "install":
            output = installer.install(approval=args.approve, dry_run=args.dry_run).to_dict()
        elif args.command == "update":
            output = installer.update(approval=args.approve, dry_run=args.dry_run).to_dict()
        elif args.command == "uninstall":
            output = installer.uninstall(approval=args.approve, dry_run=args.dry_run).to_dict()
        else:
            if not args.manifest:
                raise InstallerError("rollback requires --manifest")
            output = installer.rollback(
                args.manifest, approval=args.approve, dry_run=args.dry_run
            ).to_dict()
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (InstallerError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
