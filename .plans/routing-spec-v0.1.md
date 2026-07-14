# routing-spec v0.1 implementation

## Overview

Implement `SPEC.md` on top of pilotfish `v1.1.5` as six stacked, independently reviewable phases. The canonical router and L1 gate remain dependency-free and offline; runtime-facing adapters, ledger, attestation, and L2 execution are opt-in. Existing template installation remains the default path until the corresponding adapter/installer phase is merged, and unavailable model observation is reported as `UNKNOWN` rather than inferred.

## Design Principles

1. **Canonical state before adapters.** Schemas, role registry, and pure routing decisions are the source of truth; target artifacts consume them.
2. **Deterministic core, explicit stochastic edge.** L1 never calls an LLM or network; L2 owns classifier variance and reports every run.
3. **Monotonic safety.** Security routing, risk floors, failure escalation, and observed execution tier cannot downgrade.
4. **Evidence-bounded runtime claims.** Configured, observed, and unknown model identity remain separate; secrets and complete prompts never enter the ledger.

## Architecture

```text
Prompt -> preclassifier -> classifier contract -> Task Envelope
                                                |
                                                v
                                      pure router + history
                                                |
                    +---------------------------+------------------+
                    v                           v                  v
             Claude compiler             Codex compiler       L1/L2 evals
                    |                           |
                    +------ delegation -> ledger/attestation
```

The canonical Python package validates envelopes and registries, normalizes effective risk, and returns a side-effect-free routing decision. Adapter compilers emit target-specific artifacts and capability reports. Runtime components append records and evaluate only evidence actually available from the target.

### Option A: Python standard library with JSON-compatible YAML

- Reuses the repository's existing `unittest` surface and release command.
- Keeps L1 offline and avoids a package manager or install-time dependency.
- Stores canonical `.yaml` documents as JSON syntax, which is valid YAML 1.2 and parseable with `json`.
- Requires a focused in-repo validator for the schemas used by the reference implementation.

### Option B: Python plus PyYAML/jsonschema

- Accepts general YAML syntax and delegates JSON Schema semantics to mature libraries.
- Adds dependency management, supply-chain surface, and an installation prerequisite to a repository that currently ships configuration files only.
- Makes the blocking L1 gate depend on package installation and registry availability.

**Recommendation: Option A** — it can be verified by running the existing Python interpreter alone, preserves the repository's low-dependency product boundary, and still emits standard JSON Schema plus YAML-1.2-valid canonical documents. General YAML parsing can be introduced later behind an adapter boundary if a concrete consumer requires it.

## Configuration

`routing.yaml` is canonical YAML represented in JSON-compatible syntax:

```json
{
  "schema_version": "0.1",
  "roles": {
    "executor": {
      "model_alias": "sonnet",
      "effort": "high",
      "allowed_tools": [],
      "disallowed_tools": ["Agent", "Workflow"],
      "can_spawn": false
    }
  }
}
```

Configuration precedence:

1. The validated Task Envelope supplies task-local classification state.
2. `risk_tags` normalization raises the effective risk floor.
3. Append-only history raises the escalation/no-downgrade floor.
4. `routing.yaml` supplies role bindings and target-independent policy.
5. Adapter capability discovery may mark a binding degraded/unsupported, but may not silently replace canonical intent.
6. Strict adapter mode fails if a required capability is unavailable.

## Implementation Plan

### Phase 0: Verifiable fork baseline and implementation contract

**Files: `SPEC.md`, `.gitattributes`**

#### 0a. Import the supplied Draft v0.1 byte-for-byte (~934 lines)

Preserve the user's source text and hash it so later implementation changes cannot silently mutate requirements.
The source uses Markdown hard-break spaces on two lines; a path-scoped Git whitespace attribute preserves those bytes without weakening checks for other files.

**File: `docs/baseline-v1.1.5.md`**

#### 0b. Record live baseline and errata (~100 lines)

Record fork relationship, remotes, immutable main SHA, tags, baseline hashes/tests, stale commit/star/release assumptions, and upstream overlap.

**Files: `baseline/manifest.json`, `tests/test_baseline.py`**

#### 0c. Make the snapshot machine-verifiable (~115 lines)

Hash immutable baseline Git objects, record the original six-role inventory, and verify the imported spec hash without comparing later generated templates to stale working-tree bytes.

**File: `.plans/routing-spec-v0.1.md`**

#### 0d. Define options, phase boundaries, tests, and rollout (~260 lines)

Use a stacked PR topology so every review contains only one phase delta.

**Tests for Phase 0:** (~5 evidence checks)

- Source and committed `SPEC.md` have identical SHA-256 hashes.
- `python3 -m unittest tests.test_baseline -v` validates the immutable Git objects and manifest.
- `main`, `origin/main`, and `upstream/main` baseline evidence is recorded.
- `git tag -l` and `git show-ref --tags` include `v1.1.5` at the baseline SHA.
- Existing `python3 -m unittest discover -s tests -v` passes.
- `git diff --check` passes.

### Phase 1: Canonical state model and blocking L1 router

**Files: `routing.yaml`, `schemas/*.schema.json`, `router/*.py`, `evals/l1-routing.yaml`, `evals/runner.py`, `tests/test_{schemas,routing,escalation,registry,delegation}.py`, `.github/workflows/l1.yml`**

#### 1a. Add canonical schemas and dependency-free validation (~420 lines)

Define closed enums, required fields, nullable runtime evidence, role ownership, and fixture contracts. Keep schema documents standards-compatible while the reference validator enforces the subset used by the project.

#### 1b. Implement preclassifier contracts and pure routing (~420 lines)

Implement `preclassify()`, `validate_envelope()`, `effective_risk()`, `route(envelope, history)`, and task-specific escalation ladders with no side effects.

#### 1c. Define Delegation Spec and delegator contract (~180 lines)

Add a canonical Delegation Spec schema plus `validate_delegation_spec()` and `delegate(role, delegation_spec)` contract. Mechanical work must include objective, constraints, done criteria, and bounded allowed/forbidden scope; the run handle carries only a stable spec hash/reference and never persists the raw prompt, secrets, or full source content.

#### 1d. Add L1 fixtures, runner, and pinned CI gate (~360 lines)

Cover all task types/completeness/risk values, migration normalization, security routing, verifier isolation, every escalation boundary, invalid inputs, registry ownership, and no-downgrade.

**Tests for Phase 1:** (~850 lines)

- Positive/negative schema fixtures are accepted/rejected deterministically.
- Identical envelope/registry/history inputs produce byte-identical decisions.
- Security never routes to Sonnet roles.
- Migration forces effective high risk.
- Every escalation boundary and no-downgrade invariant is covered.
- Mechanical delegation rejects incomplete scope/done criteria; valid delegation produces a stable hash/reference without raw prompt persistence.
- L1 CLI runs offline and exits non-zero on a fixture mismatch.
- Full unittest discovery, JSON parsing, workflow syntax inspection, and `git diff --check` pass.

**Phase 1 actual:** 2,837 implementation/config/schema/fixture/CI lines + 1,757 test lines = 4,594 lines. The larger-than-estimated delta comes from publishing five self-contained Draft 2020-12 schemas, explicit escalation-boundary coverage, a dependency-free schema validator, and an offline L1 guard with a stable aggregate CI gate; no Phase 2 template or installer behavior was pulled forward.

### Phase 2: Claude compiler, seven roles, and safe installer lifecycle

**Files: `adapters/claude/*.py`, `adapters/claude/templates/*`, `templates/agents/*`, `templates/claude-md.orchestration.md`, `install/installer.py`, `install/AGENT-INSTALL.md`, `tests/test_claude_compiler.py`, `tests/test_installer.py`**

#### 2a. Compile byte-stable Claude artifacts from canonical registry (~450 lines)

Generate seven role definitions, role-only policy, and capability report. Change `executor` to `sonnet/high`, add `senior-executor` as `opus/high`, keep `security-executor` as `opus/high`, and never override a named role at invocation time.

#### 2b. Update orchestration policy around envelope and router decisions (~160 lines)

Require a valid Task Envelope before delegation, preserve security pre-route and verifier isolation, and represent refine/takeover/block without inventing a role.

#### 2c. Implement dry-run, approval, backup, rollback, update, and uninstall (~520 lines)

Operate on a supplied target home, merge settings keys, detect frontmatter name collisions and global model overrides, create a rollback manifest, and remove only owned/unchanged content. No network fetch or security bypass is part of installation.

**Tests for Phase 2:** (~700 lines)

- Compiler output is byte-stable and matches golden artifacts.
- All seven leaf roles have `can_spawn=false`; tool policies match the registry.
- Named-role model binding has exactly one source.
- Temp-HOME E2E proves dry-run makes no changes.
- Install requires explicit approval, creates backups/manifest, and is idempotent.
- Uninstall preserves user-modified or unowned content.
- Collision and `CLAUDE_CODE_SUBAGENT_MODEL` warnings are observable.

### Phase 3: Append-only ledger and best-effort attestation

**Files: `runtime/ledger.py`, `runtime/attestation.py`, `runtime/models.py`, `tests/test_ledger_append_only.py`, `tests/test_attestation.py`**

#### 3a. Implement append-only JSONL ledger (~300 lines)

Enforce monotonic sequence/failure state, reject record mutation, and represent corrections only through `supersedes_record_id`. Store spec hashes/references instead of raw prompts or code.

#### 3b. Implement configured/observed/unknown attestation (~260 lines)

Read canonical claim, invocation metadata, environment override presence, and optional transcript/provider metadata. Return `UNKNOWN` when observation is absent; mismatch invalidates the run.

#### 3c. Connect execution, verifier, cost, and attestation fields (~180 lines)

Preserve nullable token/latency fields, separate execution status from verifier verdict, and append failure-count updates as new records.

**Tests for Phase 3:** (~520 lines)

- Existing ledger bytes are never rewritten during append/correction.
- Sequence, task failure count, and superseding references are validated.
- Raw prompts/secrets are rejected from persisted record shapes.
- Missing observation returns `UNKNOWN`.
- Global override/mismatched observation produces `MISMATCHED` and `INVALIDATED`.
- Verifier `REFUTED` increments the parent task without becoming verifier failure.

### Phase 4: Non-blocking stochastic classifier evaluation

**Files: `evals/l2-classification.yaml`, `evals/l2_runner.py`, `evals/report.py`, `tests/test_l2_runner.py`, `docs/l2-eval.md`**

#### 4a. Add natural-language fixtures and accepted ranges (~180 lines)

Include high-confidence security/migration cases, ambiguous cases, and ordinary recon/mechanical/judgment/verification prompts.

#### 4b. Run configurable headless classifier commands repeatedly (~340 lines)

Accept a command adapter, validate each emitted envelope, route it through the deterministic core, and persist per-run evidence. Credits and run count remain explicit configuration; the runner has no embedded credential handling.

#### 4c. Aggregate quality and variance without a release gate (~260 lines)

Report schema-valid rate, field agreement, route agreement, security recall, per-fixture variance, latency/tokens, and CLI/model metadata when available.

**Tests for Phase 4:** (~420 lines)

- Fake headless command E2E produces deterministic report structure.
- Invalid classifier output is retained as evidence and counted, not guessed.
- Multiple outcomes produce non-zero per-fixture variance.
- Security recall and route agreement use documented denominators.
- Default CI does not spend credits or invoke external models.

### Phase 5: Codex capability probe and strict adapter

**Files: `adapters/codex/capability_probe.py`, `adapters/codex/compiler.py`, `adapters/codex/attestor.py`, `tests/test_codex_adapter.py`, `docs/adapter-capabilities.md`**

#### 5a. Probe actual Codex CLI surfaces without assuming Claude parity (~260 lines)

Collect version/help/config evidence through bounded subprocess calls and classify each canonical capability as supported, degraded, or unsupported.

#### 5b. Compile target policy and capability report deterministically (~300 lines)

Emit only verified target controls, attach warnings for degraded behavior, and keep runtime model observation `UNKNOWN` when metadata is unavailable.

#### 5c. Enforce required capabilities in strict mode (~140 lines)

Fail compilation when a requested required capability is not supported; non-strict mode must preserve explicit warnings rather than silently succeeding.

**Tests for Phase 5:** (~380 lines)

- Recorded probe fixtures cover supported/degraded/unsupported states.
- Compiler output is byte-stable for identical probe evidence.
- Strict mode fails on every missing required capability.
- Non-strict output lists all degradations.
- Missing runtime model observation remains `UNKNOWN`.
- Live probe smoke test records the local Codex version without writing user config.

## Integration Issues & Edge Cases

1. **Upstream PR #10 may merge mid-series.** Re-fetch before each Phase; rebase only at a phase boundary and reconcile compatible controls instead of cherry-picking its conflicting role taxonomy.
2. **Upstream PR #11 is a plugin replatform.** Keep it outside this series; adopting it would materially change scope and requires a separate decision.
3. **JSON-compatible YAML surprises editors.** Document the convention and validate every canonical document as both JSON and YAML-1.2-compatible syntax.
4. **Role template drift.** Golden compiler tests compare generated bytes with checked-in artifacts; manual edits fail CI.
5. **Installer targets user-global files.** All E2E tests use a temp HOME and explicit target path; no test writes the real user configuration.
6. **Attestation evidence is incomplete.** Use `UNKNOWN`, never configured-as-observed; mismatch invalidates but absence does not imply mismatch.
7. **Ledger concurrency.** Serialize append with a lock and verify the file tail before writing; never repair historical bytes in place.
8. **L2 cost/network usage.** No default CI invocation; headless command and run budget are explicit opt-in parameters.
9. **Codex CLI changes over time.** Probe output contains version/source evidence and compilation consumes the report rather than hard-coded parity assumptions.
10. **Stacked PR base movement.** Merge strictly in order; after each merge, retarget/rebase the next PR and rerun its complete verification bundle.

## Files Changed Summary

| File | Phase | Changes |
|---|---:|---|
| `SPEC.md` | 0 | NEW — supplied canonical draft |
| `.gitattributes` | 0 | NEW — preserve source hard breaks while keeping repository whitespace checks strict |
| `docs/baseline-v1.1.5.md` | 0 | NEW — fork/tag/overlap evidence and errata |
| `.plans/routing-spec-v0.1.md` | 0 | NEW — implementation and rollout contract |
| `baseline/manifest.json`, `tests/test_baseline.py` | 0 | NEW — machine-verifiable immutable snapshot |
| `routing.yaml`, `schemas/*` | 1 | NEW — canonical config, envelope/registry/ledger/eval/delegation schemas |
| `router/*`, `evals/runner.py`, `evals/l1-routing.yaml` | 1 | NEW — deterministic core, delegator contract, and L1 harness |
| `.github/workflows/l1.yml` | 1 | NEW — blocking offline L1 gate |
| `adapters/claude/*`, `templates/*`, `install/*` | 2 | Claude compiler/artifacts and installer lifecycle |
| `runtime/*` | 3 | NEW — ledger and attestation runtime |
| `evals/l2*`, `evals/report.py`, `docs/l2-eval.md` | 4 | NEW — stochastic evaluation and reporting |
| `adapters/codex/*`, `docs/adapter-capabilities.md` | 5 | NEW — live capability probe and compiler |
| `tests/*` | 1–5 | Phase-scoped contract, unit, golden, and temp-HOME E2E tests |

**Revised implementation forecast after Phase 1**: ~6,190 lines / **Revised test forecast**: ~3,780 lines / **Imported spec and Phase 0 planning/baseline docs**: ~1,470 lines

## Rollout Plan

1. Merge Phase 0 into fork `main` to establish immutable spec/baseline/plan evidence.
2. Merge Phase 1 after the offline L1 workflow passes; it adds no installer/runtime behavior.
3. Merge Phase 2 after golden and temp-HOME E2E tests; generated Claude artifacts become the checked-in templates, while installation still requires explicit approval.
4. Merge Phase 3 after append-only and attestation adversarial tests; ledger use remains opt-in by an explicit output path.
5. Merge Phase 4 as a non-blocking evaluation surface; no model credits are consumed by default CI.
6. Merge Phase 5 after recorded-fixture tests and a read-only live Codex probe; strict mode is opt-in and fails closed on required capabilities.

Each phase is independently mergeable and testable.
