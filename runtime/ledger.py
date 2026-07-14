"""Append-only JSONL storage for validated runtime delegation records."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from router.models import canonical_json
from router.schema import SchemaValidationError, validate_schema


class LedgerError(ValueError):
    """Raised when ledger history or an append request is invalid."""


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _ledger_path(path: os.PathLike[str] | str) -> Path:
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
        raise LedgerError("ledger path must be explicitly supplied")
    result = Path(path)
    if result.exists() and not result.is_file():
        raise LedgerError("ledger path must identify a file")
    return result


def _path_lock(path: Path) -> threading.RLock:
    key = path.resolve(strict=False)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_line(raw: bytes, line_number: int) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LedgerError(f"invalid ledger JSON at line {line_number}: {exc}") from exc
    if not isinstance(document, dict):
        raise LedgerError(f"ledger line {line_number} must be a JSON object")
    return document


def _validate_records(records: list[dict[str, Any]]) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    failure_counts: dict[str, int] = {}
    previous_sequence = -1
    for index, record in enumerate(records):
        try:
            validate_schema("ledger-record", record)
        except SchemaValidationError as exc:
            raise LedgerError(f"invalid ledger record {index}: {exc}") from exc

        record_id = record["record_id"]
        task_id = record["task_id"]
        snapshot = record["envelope_snapshot"]
        if task_id != snapshot["task_id"]:
            raise LedgerError(f"record {record_id} task_id differs from its envelope")
        if record_id in by_id:
            raise LedgerError(f"duplicate record_id: {record_id}")
        sequence = record["sequence"]
        if sequence <= previous_sequence:
            raise LedgerError("ledger sequence must increase globally")
        previous_sequence = sequence

        failure_count = snapshot["failure_count"]
        if failure_count < failure_counts.get(task_id, -1):
            raise LedgerError(f"failure_count decreased for task {task_id}")
        failure_counts[task_id] = failure_count

        supersedes = record["supersedes_record_id"]
        if supersedes is not None:
            target = by_id.get(supersedes)
            if target is None:
                raise LedgerError(
                    f"supersedes_record_id must reference an earlier record: {supersedes}"
                )
            if target["task_id"] != task_id:
                raise LedgerError("a correction must supersede a record for the same task")
        by_id[record_id] = record


def _decode(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise LedgerError("ledger ends with a partial record; history is not repaired")
    records = []
    for line_number, line in enumerate(raw[:-1].split(b"\n"), start=1):
        if not line:
            raise LedgerError(f"blank ledger record at line {line_number}")
        records.append(_load_line(line, line_number))
    _validate_records(records)
    return records


def _read_raw(path: Path) -> bytes:
    try:
        return b"" if not path.exists() else path.read_bytes()
    except OSError as exc:
        raise LedgerError(f"cannot read ledger: {path}") from exc


def read_records(path: os.PathLike[str] | str) -> tuple[dict[str, Any], ...]:
    """Read a validated ledger without creating or modifying it."""

    ledger_path = _ledger_path(path)
    with _path_lock(ledger_path):
        return tuple(copy.deepcopy(_decode(_read_raw(ledger_path))))


def append_record(
    path: os.PathLike[str] | str, record: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and append one record without rewriting historical bytes."""

    if not isinstance(record, Mapping):
        raise LedgerError("ledger record must be a mapping")
    ledger_path = _ledger_path(path)
    candidate = copy.deepcopy(dict(record))

    with _path_lock(ledger_path):
        before = _read_raw(ledger_path)
        records = _decode(before)
        _validate_records([*records, candidate])
        payload = (canonical_json(candidate) + "\n").encode("utf-8")

        flags = os.O_APPEND | os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(ledger_path, flags, 0o600)
        except OSError as exc:
            raise LedgerError(f"cannot open ledger for append: {ledger_path}") from exc
        try:
            actual_size = os.lseek(descriptor, 0, os.SEEK_END)
            if actual_size != len(before):
                raise LedgerError("ledger changed while preparing the append")
            tail = before[-4096:]
            if tail:
                os.lseek(descriptor, len(before) - len(tail), os.SEEK_SET)
                if os.read(descriptor, len(tail)) != tail:
                    raise LedgerError("ledger tail changed while preparing the append")
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("short append")
            os.fsync(descriptor)
        except OSError as exc:
            raise LedgerError(f"cannot append ledger record: {ledger_path}") from exc
        finally:
            os.close(descriptor)
    return copy.deepcopy(candidate)
