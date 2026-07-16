# Tasks: Codex CLI-native orchestration

**Input**: Design documents from `specs/001-codex-cli-native/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, and
`quickstart.md`

**Tests**: Required by the project constitution for probe, compilation, installer, rollback, and
documentation-contract behavior. Every behavioral slice starts red and is committed only after its
targeted and related suites pass.

## Format

Every task uses `[ID] [P?] [Story?]` with an exact repository path. Requirement identifiers in each
description are the traceability keys used by consistency analysis.

## Phase 1: Spec Kit baseline and readiness

**Purpose**: Finish the branch-first specification checkpoint before production code.

- [x] T001 Record live stable-version, official-doc, model-map, `CODEX_HOME`, override, recursion, and enforcement-boundary decisions in `specs/001-codex-cli-native/research.md` and `specs/001-codex-cli-native/contracts/` (FR-002, FR-003, FR-004, CSR-001, CSR-002)
- [x] T002 Validate all 16 requirement-quality items and record remediation evidence in `specs/001-codex-cli-native/checklists/requirements.md` (SC-005)
- [x] T003 Run the existing 214-test repository baseline and record the command/result in `specs/001-codex-cli-native/tasks.md` before the spec commit (FR-012, SC-006)
- [x] T004 Run cross-artifact consistency analysis over `specs/001-codex-cli-native/spec.md`, `specs/001-codex-cli-native/plan.md`, and `specs/001-codex-cli-native/tasks.md`, resolving every CRITICAL/HIGH issue before the spec commit (SC-005)

**Checkpoint**: Commit `.agents/`, `.specify/`, and `specs/001-codex-cli-native/` as
`spec: define Codex CLI-native orchestration`. No production file may enter this commit.

---

## Phase 2: Foundational boundaries (already provided)

**Purpose**: Reuse the validated provider-neutral router, schemas, ledger, attestation model, and
hardened installer engine. No new shared framework is required; story work must not change canonical
role names or target-independent routing semantics.

**Checkpoint**: `routing.yaml`, `router/`, `runtime/`, and schemas remain provider-neutral.

---

## Phase 3: User Story 3 - Detect Codex compatibility (Priority: P1)

**Goal**: Fail closed on an incompatible binary or required surface before native artifacts or
target writes.

**Independent Test**: Recorded fixtures cover the supported stable CLI and exactly five incompatible
classes; an isolated generated-config probe accepts 0.144.5 and never touches the live Codex home.

### Tests for User Story 3

- [ ] T005 [US3] Add red fixtures for missing binary, below-floor, prerelease, unparsable version, missing/disabled `multi_agent`, partial/timeout normalization, and supported 0.144.5 in `tests/test_codex_adapter.py` (FR-004, FR-005, FR-006, CSR-001, SC-003)
- [ ] T006 [US3] Add red assertions for command hashes, minimum version, binary capability, exact-target status, future-project `unknown`, config-load result, deterministic warnings, and no raw output leakage in `tests/test_codex_adapter.py` (FR-004, FR-005, CSR-002, CSR-005)

### Implementation for User Story 3

- [ ] T007 [US3] Extend `adapters/codex/capability_probe.py` with stable SemVer/prerelease handling, `codex features list`, exact five-case classification, bounded isolated agent-config loading, and deterministic evidence (FR-004, FR-005, FR-006, CSR-001)
- [ ] T008 [US3] Update capability-report construction and strict gating in `adapters/codex/compiler.py` without yet changing the legacy artifact inventory (FR-005, FR-006, CSR-002, SC-003)
- [ ] T009 [P] [US3] Export the minimum-version and probe contracts from `adapters/codex/__init__.py` and preserve generic adapter dispatch compatibility in `adapters/__init__.py` (FR-004, FR-012)
- [ ] T010 [US3] Run `python3 -m unittest tests.test_codex_adapter -v` and verify live/isolated probes create no user-state diff in `tests/test_codex_adapter.py` (FR-006, CSR-005, SC-003)

**Checkpoint**: Commit as `feat(codex): add native compatibility gate`.

---

## Phase 4: User Story 2 - Compile native Codex roles (Priority: P2)

**Goal**: Emit seven deterministic Codex custom-agent definitions and an honest orchestration policy
without putting provider IDs into the canonical registry.

**Independent Test**: All generated TOML parses, maps one canonical leaf exactly once, loads under
the pinned CLI schema, narrows read roles, preserves parent permissions for write roles, and binds
leaf depth to one.

### Tests for User Story 2

- [ ] T011 [US2] Add red inventory/order, `tomllib`, name/model/reasoning/sandbox/depth, no-orchestrator, LF, determinism, and dangerous-content tests in `tests/test_codex_adapter.py` (FR-001, FR-002, FR-003, CSR-004, SC-002)
- [ ] T012 [US2] Add red policy and invocation-contract tests that distinguish native fields from prompt guidance and keep runtime model/account availability `UNKNOWN` in `tests/test_codex_adapter.py` (FR-003, FR-005, CSR-002)

### Implementation for User Story 2

- [ ] T013 [US2] Implement the reviewable Sol/Terra/Luna role map, developer instructions, explicit read-only roles, inherited write-role sandbox, and `[agents] max_depth = 1` in `adapters/codex/compiler.py` (FR-001, FR-002, CSR-002, SC-002)
- [ ] T014 [P] [US2] Add reviewable byte-stable goldens in `adapters/codex/templates/AGENTS.orchestration.md` and `adapters/codex/templates/agents/*.toml` with LF enforcement in `.gitattributes` (FR-001, FR-003, CSR-004)
- [ ] T015 [US2] Replace prompt-only legacy artifacts with the contracted seven TOMLs, `AGENTS.orchestration.md`, invocation policy, verifier schema, and capability report in `adapters/codex/compiler.py` (FR-001, FR-003, FR-005)
- [ ] T016 [US2] Validate generated agents against isolated `@openai/codex@0.144.5`, run `python3 -m unittest tests.test_codex_adapter -v`, and confirm no Codex model ID entered `routing.yaml` (CSR-001, SC-002, SC-003)

**Checkpoint**: Commit as `feat(codex): compile native custom agents`.

---

## Phase 5: User Story 1 - Safely install native Codex roles (Priority: P3)

**Goal**: Preview, approve, install, update, uninstall, and rollback only pilotfish-owned content in
an exact explicit `CODEX_HOME`, while preserving the legacy Claude target byte-for-byte.

**Independent Test**: A temporary exact Codex root with unrelated instructions/agent content
survives the full lifecycle; stale approval, shadowing override, incompatible CLI, collision,
symlink, or modified owned content produces zero unsafe writes.

### Tests for User Story 1

- [ ] T017 [US1] Add red exact-`CODEX_HOME`, dry-run/apply/no-op, seven-agent, active-policy, and private-state tests in `tests/test_codex_installer.py` (FR-007, FR-008, FR-010, CSR-003, SC-001)
- [ ] T018 [US1] Add red override-shadow, disabled target feature, path/symlink/identity, case/name collision, stale fingerprint, modified-owned, no-content-leak, and out-of-scope byte-preservation tests in `tests/test_codex_installer.py` (FR-006, FR-009, CSR-003, CSR-005, SC-004)
- [ ] T019 [US1] Add red update/uninstall/rollback, missing-old-CLI recovery, and cross-target/cross-home manifest rejection tests in `tests/test_codex_installer.py` (FR-007, FR-009, FR-010, FR-012)

### Implementation for User Story 1

- [ ] T020 [US1] Introduce an immutable Claude/Codex target profile in `install/installer.py`, retaining legacy Claude `--target-home` semantics while treating Codex `--target-home` as the exact `CODEX_HOME` (FR-007, FR-012)
- [ ] T021 [US1] Make compiler sources, runtime gate, allowed agent names, policy markers, private state/manifests/backups, and collision parsing profile-driven in `install/installer.py` (FR-006, FR-009, FR-010)
- [ ] T022 [US1] Reuse fingerprint, descriptor/CAS, atomic apply, selective update/uninstall, and rollback machinery for Codex while blocking non-empty `AGENTS.override.md` and preserving `config.toml` plus unrelated state in `install/installer.py` (FR-008, FR-009, FR-010, CSR-003, CSR-005)
- [ ] T023 [US1] Add `--target {claude,codex}` and target-aware help/error JSON in `install/installer.py`, keeping omitted `--target` byte-compatible with Claude behavior (FR-007, FR-012)
- [ ] T024 [P] [US1] Write the exact-root preview/approval/update/uninstall/rollback runbook in `install/CODEX-INSTALL.md` without live-home commands or unsafe bypasses (FR-011, CSR-003, CSR-004, SC-001)
- [ ] T025 [US1] Run `python3 -m unittest tests.test_codex_installer tests.test_installer -v` and verify the existing Claude golden/state/manifest behavior remains unchanged (FR-012, SC-004, SC-006)

**Checkpoint**: Commit as `feat(installer): add safe Codex target`.

---

## Phase 6: User Story 4 - Publish the Codex-first release surface (Priority: P4)

**Goal**: Make public English and Taiwan Traditional Chinese guidance match the implemented Codex
target while keeping Claude as the CLI-default compatibility path.

**Independent Test**: Both READMEs and the Codex runbook resolve to real files/commands, describe the
same artifact set and limits, and pass the full legacy plus Codex suite.

### Tests for User Story 4

- [ ] T026 [US4] Add red bilingual command/path/version/model/enforcement-boundary and stale-Claude-primary assertions in `tests/test_codex_docs.py` (FR-011, FR-012, FR-013, SC-007)

### Implementation for User Story 4

- [ ] T027 [P] [US4] Rewrite the primary architecture, capability table, exact-root installation, and compatibility section in `README.md` (FR-011, FR-012, SC-007)
- [ ] T028 [P] [US4] Mirror the implemented facts and commands in Taiwan Traditional Chinese in `README.zh-TW.md` (FR-011, FR-012, SC-007)
- [ ] T029 [US4] Align enforcement evidence and architecture in `docs/adapter-capabilities.md`, `docs/design.md`, `SPEC.md`, and `RELEASING.md` (FR-003, FR-005, FR-011, CSR-002)
- [ ] T030 [US4] Set `VERSION` to `2.0.0-trionnemesis.1` and add the Codex-first release entry to `CHANGELOG.md` without creating a tag or release (FR-013)
- [ ] T031 [US4] Run `python3 -m unittest tests.test_codex_docs tests.test_phase2_docs -v` and verify README/runbook command parity (FR-011, FR-012, SC-007)

**Checkpoint**: Do not commit until the final verification phase below is green; then commit this
slice as `docs: publish Codex-first 2.0 surface`.

---

## Phase 7: Convergence and final verification

**Purpose**: Prove the implementation converges with every requirement before the final feature
commit and branch push.

- [ ] T032 Re-run spec/task convergence and confirm every FR/CSR/buildable SC maps to completed work in `specs/001-codex-cli-native/tasks.md` (SC-005)
- [ ] T033 Run `python3 -m unittest discover -s tests -v` and record a fully green result with no new skip hiding a failure in `specs/001-codex-cli-native/tasks.md` (FR-012, SC-006)
- [ ] T034 Run `python3 -m compileall -q adapters install router runtime evals tests` and `git diff --check`, recording both green results in `specs/001-codex-cli-native/tasks.md` (SC-006)
- [ ] T035 Execute the isolated quickstart against a temporary exact Codex root and record whether preview/install/inspect/uninstall completes within five minutes in `specs/001-codex-cli-native/quickstart.md` (SC-001, SC-004)
- [ ] T036 Perform an independent fresh-context review of the final diff and resolve all correctness/security findings before the last commit in `specs/001-codex-cli-native/tasks.md` (CSR-002, CSR-003, CSR-004)

## Dependencies & Execution Order

### Phase dependencies

- Spec baseline (Phase 1) blocks every production edit and commit.
- US3 compatibility (Phase 3) blocks native role compilation and all installer writes.
- US2 role compilation (Phase 4) blocks the Codex installer because install sources must be final.
- US1 installer (Phase 5) blocks public documentation because commands/paths must be real.
- US4 docs and convergence (Phases 6-7) are validated together before the final feature commit.

### User story order

```text
US3 (P1 compatibility) -> US2 (P2 native roles) -> US1 (P3 safe lifecycle) -> US4 (P4 release surface)
```

Each story is independently testable at its checkpoint. The ordering is also the commit order.

### Parallel opportunities

- T009 can update exports while T007/T008 stay in provider implementation files.
- T014 can prepare goldens after T011 defines the contract while T013 implements the renderer.
- T024 can draft the runbook from the committed installer contract while T020-T023 implement code.
- T027 and T028 can update separate language files after installer behavior is final.

## Parallel examples

### User Story 2

```text
Task T013: implement the Codex role renderer in adapters/codex/compiler.py
Task T014: add reviewable agent/policy goldens under adapters/codex/templates/
```

### User Story 4

```text
Task T027: update README.md
Task T028: update README.zh-TW.md
```

## Implementation strategy

1. Commit the complete, analyzed Spec Kit baseline.
2. Use red-green-refactor for one story at a time in dependency order.
3. Mark that story's tasks complete and commit only that independently green slice.
4. Keep Claude compatibility checks running with every installer/public-surface change.
5. Complete convergence, full validation, independent review, and the final docs commit.
6. Inspect the five feature commits, then push only `codex/codex-cli-native` to `origin`.

## Validation evidence

Populate this section as tasks complete; do not predict results.

- Spec baseline: `python3 -m unittest discover -s tests -v` passed 214 tests with 2
  Windows-only skips on 2026-07-16; all 25 requirements are mapped, all 36 tasks
  match the required format, and cross-artifact analysis found 0 CRITICAL/HIGH issues.
- US3 compatibility: pending
- US2 native roles: pending
- US1 installer: pending
- US4 docs and full verification: pending
