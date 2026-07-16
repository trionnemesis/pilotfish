# Implementation Plan: Codex CLI-native orchestration

**Branch**: `codex/codex-cli-native` | **Date**: 2026-07-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-codex-cli-native/spec.md`

## Summary

Promote pilotfish from a Claude-first downstream fork to a Codex CLI-first release while
preserving the provider-neutral router and legacy Claude behavior. Extend the existing Codex
probe/compiler to the stable multi-agent and documented, version-tested custom-agent surface in
Codex CLI 0.144.5, emit seven custom
agent TOML definitions plus a global routing policy, and add a `codex` target profile to the
existing hardened installer so dry-run, fingerprint approval, ownership, rollback, and
uninstall guarantees are reused rather than reimplemented. Finish with bilingual Codex-first
documentation and a new downstream version.

## Technical Context

**Language/Version**: Python 3.11+; Markdown, JSON, and TOML artifacts

**Primary Dependencies**: Python standard library only (`tomllib` for validation); local Codex
CLI subprocess probes; no new runtime package

**Storage**: Local files directly under an explicitly supplied exact `CODEX_HOME`; hash-only
ownership state and private rollback manifests/backups

**Testing**: `python3 -m unittest` with recorded probe fixtures, deterministic compiler goldens,
installer lifecycle/integrity tests, legacy regression suite, `compileall`, and `git diff --check`

**Target Platform**: macOS, Linux, and Windows user profiles; stable Codex CLI 0.144.5 baseline

**Project Type**: Offline Python routing/compiler library plus local configuration installer

**Performance Goals**: Probe uses at most five bounded, no-auth subprocesses; deterministic
compile completes below one second on the seven-role registry; installer planning scales linearly
with the managed file inventory and completes below two seconds excluding CLI probe timeout

**Constraints**: No network requirement after checkout; no auth/session/plugin/MCP/skill access;
no target `config.toml` mutation; no mutation outside exact target; non-empty
`AGENTS.override.md` blocks active-policy install; read roles narrow sandbox while write roles inherit
the parent; no dangerous flags; byte-stable output; legacy Claude remains the default CLI behavior

**Scale/Scope**: Seven canonical leaf roles, one global policy block, two provider target profiles,
one active ownership state, and one manifest per approved operation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Canonical boundary — PASS**: `routing.yaml`, schemas, router, ledger, and escalation remain
  provider-neutral; Codex model IDs and TOML live only in `adapters/codex`.
- **Capability evidence — PASS**: Stable 0.144.5 was confirmed from the package dist-tag and an
  isolated `npx` execution; version-tagged parser source plus current official documentation define
  the custom-agent schema, while `codex features list` proves stable `multi_agent`. Generated config
  loading gates that documented surface; prompt-only routing/tool-policy gaps remain labelled.
- **Test-first — PASS**: Each behavioral phase begins in existing/new `unittest` modules before
  implementation; legacy tests remain unchanged.
- **Least privilege — PASS**: The exact Codex target reuses descriptor-relative mutation, CAS,
  fingerprint, private backup, manifest, rollback, and uninstall. It does not edit `config.toml`,
  blocks a shadowing override, binds leaf depth in each role file, fixes recon/verifier read-only,
  and lets write roles inherit rather than broaden the parent sandbox.
- **Traceability — PASS**: Specs use FR/CSR/SC IDs; contracts and tasks map them to files and
  validation. One commit is created per independently testable feature slice.

**Post-design re-check**: PASS. The contracts retain every gate; no complexity exception is
required.

## Project Structure

### Documentation (this feature)

```text
specs/001-codex-cli-native/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── codex-artifacts.md
│   └── installer-cli.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
adapters/
├── claude/                         # retained compatibility target
└── codex/
    ├── capability_probe.py         # version/help/features evidence
    ├── compiler.py                 # native agent/policy artifact compiler
    ├── attestor.py
    └── templates/
        ├── AGENTS.orchestration.md
        └── agents/*.toml           # reviewable compiler goldens

install/
├── installer.py                    # hardened engine + claude/codex profiles
├── AGENT-INSTALL.md                # legacy Claude runbook
└── CODEX-INSTALL.md                # Codex-first runbook

tests/
├── test_codex_adapter.py
├── test_codex_installer.py
├── test_codex_docs.py
└── test_installer.py               # unchanged Claude regression behavior

README.md
README.zh-TW.md
CHANGELOG.md
VERSION
```

**Structure Decision**: Extend the existing single Python project. Provider-specific sources and
goldens stay under the adapter; lifecycle mutations stay in the one hardened installer engine.
No new package or parallel installer implementation is introduced.

## Complexity Tracking

No constitution violations or extra project layers require justification.

## Delivery Slices and Commit Gates

1. **Spec Kit baseline**: constitution + spec + plan + contracts + tasks + analysis; commit before
   production code.
2. **Codex compatibility gate**: red fixtures, version/features/config-load evidence, deterministic
   capability report, and targeted validation; one feature commit.
3. **Native Codex role compiler**: red artifact contracts, Sol/Terra/Luna mapping, seven TOML roles,
   explicit leaf depth, policy/enforcement boundary, and goldens; one feature commit.
4. **Codex installer target**: red lifecycle/security tests, target-profile refactor, exact
   `CODEX_HOME` semantics, shadow-policy blocker, Codex
   runbook, full Claude+Codex installer validation; one feature commit.
5. **Codex-first release surface**: red documentation contracts, bilingual README/design/spec
   alignment, changelog/version update; one feature commit.
6. **Convergence and release verification**: no production scope expansion; update task evidence,
   run complete validation, inspect diff/history, then push the branch.
