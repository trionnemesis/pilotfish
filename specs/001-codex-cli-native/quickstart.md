# Quickstart validation: Codex CLI-native orchestration

Use a pinned local checkout. These scenarios intentionally use a temporary explicit target and do
not touch the live Codex home.

## 1. Verify prerequisites

```sh
python3 --version
codex --version
```

Expected: Python 3.11 or newer and stable Codex CLI 0.144.5 or newer. An older or pre-release Codex
binary is expected to produce a blocking installer plan.

## 2. Run the regression baseline

```sh
python3 -m unittest discover -s tests -v
```

Expected: all tests pass; the two Windows-only boundary integrations may skip on non-Windows hosts.

## 3. Create an isolated target

Create a new temporary directory outside any real Codex home, then substitute its absolute path for
`<CODEX_HOME>` in every command below. For the Codex target, this argument is the exact config root:
the installer writes `AGENTS.md`, `agents/`, and `pilotfish/` directly inside it and never appends
`.codex`. The directory must already exist and must not be a symlink.

## 4. Preview Codex installation

```sh
python3 -m install.installer install \
  --target codex \
  --target-home <CODEX_HOME> \
  --dry-run
```

Expected:

- JSON reports seven custom-agent files, one marker-owned `AGENTS.md` block, ownership state, and a
  future manifest path;
- `will_write` is true;
- `blockers` is empty on a supported stable CLI;
- a non-empty `<CODEX_HOME>/AGENTS.override.md` or disabled target `multi_agent` produces a blocker;
- the target directory remains unchanged;
- one `fingerprint` is available for review.

## 5. Apply the reviewed plan

```sh
python3 -m install.installer install \
  --target codex \
  --target-home <CODEX_HOME> \
  --approve <REVIEWED_FINGERPRINT>
```

Expected: the returned `changed` paths stay inside the exact target and include only `agents/*.toml`,
`AGENTS.md`, and `pilotfish/`; the result includes a private rollback manifest path. Review each
agent's model, reasoning, read-only-or-inherited sandbox posture, and explicit leaf depth, plus the
owned block in `AGENTS.md`.

Re-run the dry-run command. Expected: a no-op plan with no active-file changes.

## 6. Validate update and stale approval

Preview `update`, then change one unrelated line outside the pilotfish policy block. Applying the old
fingerprint must fail. A new preview must preserve the unrelated line and produce a new fingerprint
only when owned changes exist.

## 7. Validate uninstall

```sh
python3 -m install.installer uninstall \
  --target codex \
  --target-home <CODEX_HOME> \
  --dry-run
```

Apply the reviewed fingerprint with the same command plus `--approve`. Expected: unchanged owned
agents and the policy block are removed; unrelated instructions and agents remain.

## 8. Validate rollback

Use the manifest returned by a successful install/update/uninstall:

```sh
python3 -m install.installer rollback \
  --target codex \
  --target-home <CODEX_HOME> \
  --manifest pilotfish/manifests/<operation-id>.json \
  --dry-run
```

Apply only the reviewed rollback fingerprint. Expected: only unchanged recorded after-state is
restored; later user edits are skipped and reported.

## 9. Final repository validation

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q adapters install router runtime evals tests
git diff --check
```

Expected: all commands succeed. Inspect `git status --short` to confirm no generated runtime state or
temporary target was added to the repository.
