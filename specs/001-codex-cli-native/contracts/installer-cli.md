# Contract: target-aware installer CLI

## Command shape

```text
python3 -m install.installer <command> \
  --target <claude|codex> \
  --target-home <existing-explicit-target-root> \
  [--source-root <pinned-local-checkout>] \
  [--dry-run] \
  [--approve <plan-sha256>] \
  [--manifest <path-inside-target-home>]
```

Commands remain `plan`, `install`, `update`, `uninstall`, and `rollback`.

## Compatibility rule

- Omitting `--target` is exactly equivalent to `--target claude`.
- The existing `Installer(...)` API defaults to the Claude profile.
- For `claude`, `--target-home` retains its legacy meaning: the OS home containing `.claude/`.
- For `codex`, `--target-home` is the exact `CODEX_HOME`; the installer never appends `.codex` and
  never consults ambient `HOME`, `CODEX_HOME`, or `USERPROFILE` to choose the target.
- Claude output paths, version floor, sources, state, and manifests remain byte-compatible unless a
  separate Claude feature explicitly changes them.

## Codex target scope

Allowed active paths are limited to:

- `AGENTS.md` (one marker-owned block);
- the seven allowlisted `agents/*.toml` files;
- `pilotfish/state.json`;
- `pilotfish/manifests/<operation-id>.json`;
- `pilotfish/backups/<operation-id>/...`.

`config.toml`, `AGENTS.override.md`, auth, sessions, logs, plugins, MCP, and unrelated skills/agents
are never owned or changed. Preflight may inspect only whether `AGENTS.override.md` is non-empty and
whether the exact target reports `multi_agent` disabled; it never reports raw target content.

## Plan/apply behavior

- `plan` and `--dry-run` do not write.
- A plan always reports changes, warnings, blockers, `will_write`, and one fingerprint.
- A blocker prevents apply even with a matching fingerprint.
- A non-empty `AGENTS.override.md` is a blocker for install/update because it shadows `AGENTS.md`.
- Apply requires the exact current fingerprint and rechecks bytes, modes, parent binding, and target
  identity immediately before every write.
- No-op install/update returns success without creating state or a manifest.

## Lifecycle behavior

- `install`: create absent owned artifacts; block unowned conflicts.
- `update`: replace only unchanged installer-owned artifacts; preserve modified owned content.
- `uninstall`: remove only unchanged owned artifacts and owned policy segment; preserve everything
  else.
- `rollback`: accept only a private, integrity-valid manifest inside the same target profile; restore
  only records whose current after-state still matches.

All successful mutating operations return JSON with changed/skipped paths, warnings, manifest path,
and approved plan. Errors return JSON on stderr and exit status 2 without leaking target content.
