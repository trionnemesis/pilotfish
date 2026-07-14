# pilotfish — Safe Local Installer Runbook

This runbook installs the canonical Claude adapter from a **local, pinned pilotfish checkout**. The installer never downloads templates, never falls back to `main`, never writes API keys, and never disables WebFetch, sandbox, approval, or prompt-injection protections.

The installer changes only these targets below an explicitly supplied home:

| Target | Owned change |
|---|---|
| `.claude/settings.json` | Merge missing keys from the compiler-emitted settings patch; preserve unrelated and conflicting user keys |
| `.claude/agents/*.md` | Install exactly seven compiler-emitted leaf roles |
| `.claude/CLAUDE.md` | Insert or update only the `pilotfish:begin` / `pilotfish:end` marker block |
| `.claude/pilotfish/` | Private hash-only ownership state, rollback manifests, and local backups |

The seven roles are `scout`, `Explore`, `mech-executor`, `executor`, `senior-executor`, `verifier`, and `security-executor`. The orchestrator is virtual and does not have an agent file.

## 0. Pin and inspect the local checkout

Before installing a fork or tag, verify tags with Git rather than relying only on a Releases page:

```sh
git fetch --tags --force
git tag -l
git show-ref --tags
git rev-parse HEAD
```

Run every command below from the reviewed checkout. `install/installer.py` calls the local canonical Claude compiler, requires exactly seven emitted roles, and byte-compares every emitted settings, agent, and policy artifact with the checked-in golden files. A stale or hand-edited golden blocks installation.

## 1. Read-only preflight and dry-run

Choose the target home explicitly. On POSIX, descriptor-relative operations remain bound to that supplied home. On Windows, `target_home` must resolve inside the current operator's profile; elevated or cross-user installs are intentionally unsupported and fail before planning. Temp-HOME tests must therefore use a directory under that current profile.

```sh
TARGET_HOME=/explicit/user/home
python3 -m install.installer install \
  --target-home "$TARGET_HOME" \
  --dry-run
```

Preflight is read-only. It:

1. runs `claude --version` without a shell and requires Claude Code `2.1.207` or newer, the minimum runtime for enforced tool allowlists and denylists on these seven roles;
2. compiles and verifies the local canonical artifacts;
3. parses `settings.json` as strict JSON, rejecting duplicate keys and non-object roots;
4. validates marker pairing in `CLAUDE.md`;
5. scans the frontmatter `name:` of every regular file in `.claude/agents/`, not just expected filenames;
6. reports role collisions and preserves unowned or user-modified content;
7. warns when the `CLAUDE_CODE_SUBAGENT_MODEL` environment key is present, without printing its value or unsetting it; and
8. prints an exact plan with before/after hashes, file modes, warnings, blockers, and a plan `fingerprint`.

An unavailable, unparseable, or older Claude Code runtime blocks install/update. It does not block uninstall or rollback. A marker error, role collision, symlinked target component, malformed JSON, stale golden artifact, or ownership-state integrity failure also blocks before writes.

`--dry-run` never creates `.claude/`, a backup, a state file, or a manifest.

## 2. Review and bind approval to the exact plan

Review every `changes`, `warnings`, and `blockers` entry from the dry-run. Writes require the exact 64-character `fingerprint` from that plan:

```sh
python3 -m install.installer install \
  --target-home "$TARGET_HOME" \
  --approve <reviewed-plan-fingerprint>
```

A bare approval is not accepted. The installer recomputes the plan and compares the supplied fingerprint. It also compares every target's pre-write hash and mode immediately before applying. Any change after review fails closed; already-applied files are automatically restored.

## 3. What apply guarantees

- `settings.json` is semantically merged one owned top-level key at a time. Existing unrelated keys and existing conflicting values are preserved.
- An owned setting is updated only when its current value still matches the prior installed hash.
- Every modified existing file is copied to a private backup before replacement. On POSIX, backup/manifest/state files are forced to `0600` and metadata directories to `0700`. Windows is restricted to the current operator's resolved profile and relies on that profile's ACL because POSIX group/world mode bits are not meaningful there; elevated or cross-user writes are not supported, while mode compare-and-swap still uses the mode observable through Python.
- The manifest stores paths, modes, hashes, ownership, and key/segment transitions—not raw settings or environment values. A settings backup can contain the user's original JSON and must remain private.
- Writes use a same-directory temporary file, `fsync`, and atomic replace. Existing target modes are preserved.
- The ownership state and manifest carry integrity hashes. Rollback manifests are constrained to canonical pilotfish paths; absolute, traversal, unexpected agent, unexpected setting-key, mismatched backup, or non-private metadata is rejected.
- A partially failed apply automatically restores the pre-operation bytes and modes and removes that failed operation's new backup artifacts and empty directories. If recovery itself fails, backups are retained and every recovery error is reported.
- Re-running the same install is idempotent. An all-current install performs no writes and creates no new manifest.

Restart Claude Code after a successful install so the seven agent definitions and machine settings are reloaded.

## 4. Update

Update uses the same canonical compiler, preflight, ownership, and approval rules as install:

```sh
python3 -m install.installer update \
  --target-home "$TARGET_HOME" \
  --dry-run

python3 -m install.installer update \
  --target-home "$TARGET_HOME" \
  --approve <reviewed-update-fingerprint>
```

Only an unchanged installer-owned agent, setting value, or marker block is updated. User-modified owned content is preserved and reported. A different file declaring one of the seven canonical frontmatter names is a blocking collision.

## 5. Rollback one operation

Every successful write operation reports a manifest path such as:

```text
.claude/pilotfish/manifests/20260714T070000.000000Z-1234abcd.json
```

Plan rollback first, using that path inside the same supplied target home:

```sh
python3 -m install.installer rollback \
  --target-home "$TARGET_HOME" \
  --manifest .claude/pilotfish/manifests/<operation-id>.json \
  --dry-run

python3 -m install.installer rollback \
  --target-home "$TARGET_HOME" \
  --manifest .claude/pilotfish/manifests/<operation-id>.json \
  --approve <reviewed-rollback-fingerprint>
```

Rollback uses compare-and-swap at the owned setting-key and policy-segment level. Unrelated settings and text added after install are preserved. A modified owned key, marker block, agent, or state file is skipped and reported rather than overwritten.

## 6. Uninstall

Uninstall also requires a dry-run and fingerprint-bound approval:

```sh
python3 -m install.installer uninstall \
  --target-home "$TARGET_HOME" \
  --dry-run

python3 -m install.installer uninstall \
  --target-home "$TARGET_HOME" \
  --approve <reviewed-uninstall-fingerprint>
```

Uninstall removes only unchanged installer-owned setting keys, agent files, and the owned marker block. It preserves:

- unrelated settings and `CLAUDE.md` text;
- unowned agent files;
- any owned content changed by the user after install; and
- rollback manifests and backups, because they may contain the only recovery copy of pre-install content.

When the installer created an otherwise empty `settings.json` or `CLAUDE.md`, uninstall removes that file. A pre-existing empty file is preserved. If modified owned content remains, hash-only ownership state remains so a later uninstall can retry safely; otherwise the active state file is removed.
