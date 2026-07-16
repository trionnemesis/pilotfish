# Feature Specification: Codex CLI-native orchestration

**Feature Branch**: `codex/codex-cli-native`

**Created**: 2026-07-16

**Status**: Draft

**Input**: User description: "將目前針對 Claude Code 的 fork，重新設計為針對最新版 Codex CLI；先建立分支，再以 Spec Kit 撰寫並 commit，逐 feature 實作且每完成一項即 commit，最後 push GitHub。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safely install native Codex roles (Priority: P3)

As a Codex CLI user, I can preview and install pilotfish's role-based orchestration into an
explicit, exact `CODEX_HOME` without replacing unrelated configuration or weakening Codex
safety controls.

**Why this priority**: A native, reversible install is required for adoption, but it must consume
the already verified compatibility gate and native role artifact set rather than define either one
inside the mutation boundary.

**Independent Test**: Start from a temporary home with existing user instructions and an
unrelated custom agent, preview the plan, approve its exact fingerprint, install, and uninstall.
The unrelated content remains byte-identical throughout.

**Acceptance Scenarios**:

1. **Given** a supported Codex CLI and an explicit `CODEX_HOME`, **When** the user requests a
   dry run, **Then** the system reports every proposed write, warning, blocker, and one stable
   approval fingerprint without changing the target.
2. **Given** the same unchanged target and approved fingerprint, **When** the user applies the
   plan, **Then** Codex-native role definitions and one owned policy block are installed while
   unrelated files and content are preserved.
3. **Given** an installed version whose owned content is unchanged, **When** the user requests
   rollback or uninstall through the same preview-and-approve flow, **Then** only owned content
   is reverted or removed.
4. **Given** user-modified owned content or a conflicting role name, **When** an update or
   uninstall is planned, **Then** the system preserves the user content and reports a blocker or
   skip instead of overwriting it.
5. **Given** a non-empty global `AGENTS.override.md`, **When** install or update is planned,
   **Then** the plan blocks before writes because the owned `AGENTS.md` policy would otherwise be
   shadowed.

---

### User Story 2 - Route work through current Codex multi-agent controls (Priority: P2)

As a Codex CLI user, I can use pilotfish's canonical roles through the stable Codex subagent
surface, with fast models for bounded read/mechanical work and stronger models for judgment,
security, and independent verification.

**Why this priority**: This is the core product value after installation: reduce unnecessary
frontier-model work while keeping high-risk and verification lanes strong.

**Independent Test**: Compile the canonical role registry for the supported Codex target and
inspect every emitted role and policy artifact. Each canonical leaf role has one documented
Codex mapping, bounded responsibility, reasoning level, and safety posture.

**Acceptance Scenarios**:

1. **Given** a schema-valid canonical registry, **When** it is compiled for Codex, **Then** all
   canonical leaf roles are represented by documented Codex custom-agent fields and no
   provider-specific model identifier enters the canonical registry.
2. **Given** exploration or fully specified mechanical work, **When** the canonical router selects
   a low-cost role, **Then** the Codex mapping uses the documented Terra or Luna worker model and
   bounded reasoning posture.
3. **Given** judgment-heavy, high-risk, security, or verifier work, **When** the canonical router
   selects the corresponding role, **Then** the Codex mapping uses the documented Sol model;
   read-only roles narrow the sandbox and write-capable roles inherit the parent's permission
   boundary instead of broadening it.
4. **Given** a leaf agent, **When** its role definition is inspected, **Then** it cannot expand
   the workflow beyond the documented recursion boundary.

---

### User Story 3 - Detect compatibility before generation or install (Priority: P1)

As an operator, I can see whether the active Codex CLI supports the exact stable controls this
version relies on, and the system fails closed when a required control or version cannot be
verified.

**Why this priority**: This fail-closed gate must exist before native artifacts or installer writes;
Codex CLI evolves quickly and stale assumptions must not become enforcement claims.

**Independent Test**: Run recorded probes for exactly five incompatible cases (missing binary,
below-floor version, prerelease version, unparsable version, and missing/disabled required
surface), plus the supported stable CLI. The resulting reports are deterministic and every
incompatible case stops strict generation before artifacts or target writes.

**Acceptance Scenarios**:

1. **Given** Codex CLI `0.144.5`, or a newer stable release that passes the same feature and
   generated-agent config-load checks, **When** the capability probe runs, **Then** it reports the
   native multi-agent and documented custom-agent controls as supported with evidence.
2. **Given** a missing, older, unparsable, or incomplete CLI, **When** strict compilation or
   installation is requested, **Then** the operation stops before writes and names the unmet
   capability or version floor.
3. **Given** a control that is only prompt guidance or invocation-wide, **When** the report is
   generated, **Then** it is labelled degraded or unsupported rather than enforced.
4. **Given** a dangerous bypass flag is present in the observed CLI, **When** invocation policy
   is generated, **Then** that flag is recorded only as forbidden and is never recommended.

---

### User Story 4 - Adopt the Codex-first release without losing legacy behavior (Priority: P4)

As a maintainer, I can publish a clearly versioned Codex-first release whose bilingual
documentation, examples, and validation evidence match the repository while existing Claude
users retain their current adapter and installer behavior.

**Why this priority**: Public positioning must match the new primary target without turning a
platform migration into an unrelated compatibility break.

**Independent Test**: Follow the English and Traditional Chinese quickstarts from a pinned local
checkout, compare the documented install surface with emitted artifacts, and run the legacy
Claude regression suite alongside the Codex suite.

**Acceptance Scenarios**:

1. **Given** a reader opening either README, **When** they review installation and architecture,
   **Then** Codex CLI is the primary supported surface and Claude Code is described as retained
   compatibility rather than the main product.
2. **Given** an existing Claude installation workflow, **When** the new version's regression
   checks run, **Then** its previously supported compiler, installer, rollback, and attestation
   behavior remains unchanged.
3. **Given** a pinned checkout of the new version, **When** a user follows the Codex runbook,
   **Then** all commands, paths, version requirements, and expected outputs correspond to files
   present in that checkout.

### Edge Cases

- Codex is not on `PATH`, returns a non-standard version string, times out, or emits partial help.
- The installed Codex version is below `0.144.5`, is an alpha/pre-release, or exposes only part
  of the required stable surface.
- Multi-agent support exists but is disabled in the explicit target; later project or managed
  overrides remain an explicit runtime unknown rather than a global enforcement claim.
- The exact `CODEX_HOME` is missing, a symlink, changes identity during the operation, or points
  outside the current Windows operator profile.
- Existing global `AGENTS.md` is empty, lacks pilotfish markers, contains one valid owned block,
  contains malformed/duplicate markers, or has user edits inside the owned block.
- A non-empty `AGENTS.override.md` shadows global `AGENTS.md` and therefore blocks install/update.
- A target agent file is absent, already installer-owned, user-modified after install, a symlink,
  or declares a conflicting custom-agent name in another file.
- An operation is re-run after no changes, after a partial user edit, or with a stale approval
  fingerprint.
- The user sets a custom `CODEX_HOME`; the Codex target treats the explicitly supplied directory as
  that exact root and never appends `.codex` or infers a home from ambient environment variables.
- A model recommended today becomes unavailable; capability reporting remains truthful and the
  target-specific mapping can change without editing the canonical role registry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The product MUST compile the existing canonical role registry into native Codex
  custom-agent definitions for every canonical leaf role.
- **FR-002**: The Codex adapter MUST keep target model names and reasoning levels outside the
  canonical routing schema and MUST expose one reviewable mapping for each role.
- **FR-003**: The generated policy MUST carry deterministic-routing, dispatch-brake,
  escalation/no-downgrade, security-routing, and verifier instructions while clearly stating that
  the executable canonical router is authoritative and instruction-driven role selection is not a
  native enforcement control.
- **FR-004**: The capability probe MUST collect bounded, non-auth evidence for CLI version,
  stable multi-agent availability, generated custom-agent config loading, non-interactive
  execution, sandbox/approval controls, structured output, and verifier isolation; target checks
  MUST not write outside an isolated temporary directory.
- **FR-005**: Capability results MUST classify each required behavior as supported, degraded, or
  unsupported and MUST identify the evidence used for that classification.
- **FR-006**: Strict compilation and installation MUST stop before writes when the stable Codex
  version floor or any required capability cannot be verified.
- **FR-007**: The Codex installer MUST require an explicit directory interpreted as the exact
  `CODEX_HOME` and support dry-run, install, idempotent update, single-operation rollback, and
  uninstall without consulting ambient home variables.
- **FR-008**: Every mutating operation MUST require the exact fingerprint of the current reviewed
  plan and MUST reject stale approval after target state changes.
- **FR-009**: The installer MUST preserve unrelated user instructions and custom agents, reject
  unsafe paths and name collisions, block when non-empty `AGENTS.override.md` would shadow the
  owned policy, and use compare-and-swap behavior for owned content.
- **FR-010**: The installer MUST record hash-based ownership and rollback metadata without storing
  user secrets or unrelated content in active state.
- **FR-011**: English and Taiwan Traditional Chinese documentation MUST describe the Codex-first
  architecture, safe installation, update, rollback, uninstall, compatibility boundary, and
  current version floor with commands that exist in the checkout.
- **FR-012**: Existing Claude adapter and installer behavior MUST remain supported and all legacy
  regression tests MUST continue to pass.
- **FR-013**: The downstream version and changelog MUST distinguish this Codex-first release from
  both upstream pilotfish and the earlier Claude-first downstream line.

### Compatibility & Safety Requirements

- **CSR-001**: The supported baseline is stable `@openai/codex 0.144.5`, observed on 2026-07-16;
  the custom-agent format is a documented, version-tested surface rather than a permanently stable
  schema, and newer releases are accepted only when feature and config-load probes pass.
- **CSR-002**: Runtime enforcement claims MUST cite official Codex documentation or captured probe
  evidence; prompt-only guidance and invocation-wide controls MUST be labelled accurately.
- **CSR-003**: User-owned state mutations MUST expose a no-write preview, fingerprint-bound
  approval, preservation behavior, and bounded rollback.
- **CSR-004**: Generated output MUST never enable or recommend approval, sandbox, hook-trust, or
  authentication bypasses.
- **CSR-005**: `config.toml`, auth files, tokens, session history, logs, plugins, MCP configuration,
  unrelated skills, and managed policy are outside the mutation boundary; bounded target
  capability inspection MUST not expose their contents.

### Key Entities

- **Codex Capability Snapshot**: Versioned, ordered evidence and classification for the observed
  CLI surface, including warnings and unmet required capabilities.
- **Codex Role Mapping**: Target-specific model, reasoning, sandbox, and instruction choices for
  one canonical role without changing the canonical registry.
- **Codex Artifact Set**: Deterministically generated custom-agent definitions, routing policy,
  invocation guidance, verifier contract, and capability report.
- **Installation Plan**: The complete proposed change set, warnings, blockers, hashes, modes, and
  fingerprint for one explicit target.
- **Ownership State**: Hash-only record of currently owned content used for safe update/uninstall.
- **Rollback Manifest**: Bounded record for restoring one approved operation while preserving
  later unrelated user changes.

## Out of Scope *(mandatory)*

- Updating or installing the user's Codex CLI package.
- Reading, changing, migrating, or backing up Codex authentication, tokens, sessions, logs,
  plugins, MCP servers, or unrelated skills.
- Removing the Claude adapter/installer or migrating an existing `~/.claude` installation.
- Relying on alpha, experimental, or undocumented Codex features for baseline functionality.
- Changing canonical task-envelope, role-registry, ledger, escalation, or attestation schemas
  except where an additive target capability vocabulary is required.
- Publishing a release tag, merging to a protected branch, creating a deployment, or changing
  GitHub branch protection.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can preview, approve, install, inspect, and uninstall the Codex-native role
  set from a pinned checkout in under 5 minutes using only the documented commands.
- **SC-002**: 100% of canonical leaf roles have exactly one generated Codex definition and one
  documented target mapping, with no target model identifiers in the canonical registry.
- **SC-003**: 100% of required capability classifications include deterministic evidence and the
  five defined incompatible CLI cases stop strict operations before any write.
- **SC-004**: Adversarial path, collision, modified-content, and stale-approval scenarios produce
  zero writes outside the exact approved ownership boundary.
- **SC-005**: Every functional requirement and buildable success criterion maps to at least one
  task and one validation step before implementation begins.
- **SC-006**: All pre-existing regression checks and all new Codex-native checks pass together,
  with no skipped test introduced to hide a failure.
- **SC-007**: English and Traditional Chinese quickstarts produce the same installed artifact set
  and contain zero stale Claude-first primary-install commands.

## Assumptions

- Stable `@openai/codex 0.144.5`, the version-tagged parser source, and the official Codex manual
  fetched on 2026-07-16 define the compatibility baseline for this feature.
- Stable multi-agent operation plus the documented, version-tested custom-agent and global
  `AGENTS.md` surfaces are the native extension points; project-local installation is not required
  for this release.
- The canonical role registry and deterministic routing behavior are already validated and are
  reused rather than redesigned.
- Python 3.11+ remains available for the repository's offline compiler, tests, and safe local
  installer.
- The user wants the branch pushed after completion, but not merged, tagged, released, or used to
  mutate the live global Codex configuration.
