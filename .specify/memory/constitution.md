<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Modified principles:
  - placeholder 1 -> I. Canonical Core Before Provider Adapters
  - placeholder 2 -> II. Probe Before Promise
  - placeholder 3 -> III. Test-First Behavioral Changes
  - placeholder 4 -> IV. Least Privilege and Reversible Installation
  - placeholder 5 -> V. Independent Verification and Traceability
- Added sections:
  - Codex CLI Compatibility and Safety
  - Spec-Driven Delivery Workflow
- Removed sections: none
- Template synchronization:
  - .specify/templates/plan-template.md: updated
  - .specify/templates/spec-template.md: updated
  - .specify/templates/tasks-template.md: updated
  - .agents/skills/speckit-*/SKILL.md: reviewed; generic Codex skills remain compatible
  - README.md and README.zh-TW.md: pending feature implementation
- Deferred items: none
-->
# pilotfish Constitution

## Core Principles

### I. Canonical Core Before Provider Adapters
Routing rules, task envelopes, role definitions, escalation semantics, ledgers, and
attestation MUST remain provider-neutral. Claude Code and Codex CLI behavior MUST be
implemented in adapters or installers that consume the canonical contracts. A provider
adapter MUST NOT silently redefine a canonical role, weaken a no-downgrade rule, or make
provider-specific model identifiers part of the canonical schema. This separation keeps
the routing model testable and prevents a platform migration from erasing proven behavior.

### II. Probe Before Promise
Every generated runtime control MUST be supported by current, reproducible evidence:
official product documentation, a bounded local capability probe, or both. Unsupported
controls MUST be reported as degraded or unsupported; prompt guidance MUST NOT be described
as runtime enforcement. Version checks MUST fail closed when a required stable capability
cannot be verified. This prevents documentation and installers from promising guarantees
that the target CLI cannot enforce.

### III. Test-First Behavioral Changes (NON-NEGOTIABLE)
Every change to routing, compilation, capability classification, installation, rollback,
or attestation MUST begin with a failing or newly relevant automated test before production
code changes. Tests MUST cover the success path and the material failure boundary. Existing
tests MUST remain intact. Documentation-only and generated-spec changes may omit a red test,
but their structural and link/command checks MUST still be recorded. This preserves the
deterministic behavior on which safe orchestration depends.

### IV. Least Privilege and Reversible Installation
Installers MUST require an explicit target directory, perform a read-only dry run, bind
writes to a reviewed fingerprint, preserve unrelated user content, and provide bounded
rollback/uninstall behavior. Installers MUST NOT weaken sandbox, approval, hook trust,
authentication, or secret-handling controls. Dangerous bypass flags MUST never be generated.
Owned files and policy blocks MUST use compare-and-swap semantics so user modifications are
reported rather than overwritten.

### V. Independent Verification and Traceability
Non-trivial features MUST have an independent verification step distinct from the authoring
pass. Each functional requirement and success criterion MUST map to implementation tasks and
validation evidence. Spec Kit artifacts, tests, and atomic feature commits MUST make that
mapping reviewable. A feature is complete only when its tasks are checked off, targeted and
related validation pass, and the implementation converges with the approved spec and plan.

## Codex CLI Compatibility and Safety

- The supported baseline MUST name an exact stable Codex CLI version and the evidence date.
- Capability discovery MUST distinguish CLI flags, stable feature flags, configuration
  schema, custom-agent behavior, and prompt-only policy.
- User-level Codex files MAY be installed only under an explicitly supplied Codex home.
- Agent definitions MUST use documented Codex configuration fields and MUST keep leaf-agent
  recursion bounded.
- Model mappings MUST live in the Codex adapter and MUST be documented as target-specific
  defaults, never canonical aliases.
- Secrets, authentication files, session history, logs, and unrelated user skills/plugins
  are always out of installer scope.

## Spec-Driven Delivery Workflow

1. Create or select the feature branch before repository writes.
2. Complete constitution, specification, plan, contracts, tasks, and consistency analysis.
3. Commit the approved Spec Kit artifacts before production implementation.
4. Implement one independently testable user story or feature slice at a time using
   red-green-refactor; mark its tasks complete and commit that slice before continuing.
5. Run targeted tests, the related suite, static/compile checks, and repository-wide tests
   when relevant.
6. Run convergence and independent verification, then push only the verified branch.

Any skipped gate MUST be documented with a concrete reason. Convenience or time pressure is
not sufficient justification for weakening a safety, test, or rollback requirement.

## Governance

This constitution governs all feature specs and plans in this repository. Amendments require
an updated Sync Impact Report, semantic version bump, propagation to dependent templates, and
a dedicated reviewable commit. MAJOR changes remove or redefine a principle, MINOR changes add
or materially expand governance, and PATCH changes clarify wording without changing duties.
Every feature plan MUST evaluate all five principles before research and again after design;
every final handoff MUST report actual validation evidence and any unresolved exception.

**Version**: 1.0.0 | **Ratified**: 2026-07-16 | **Last Amended**: 2026-07-16
