# Research: Codex CLI-native orchestration

## Decision 1: Target stable Codex CLI 0.144.5

**Decision**: Use `@openai/codex` stable `0.144.5`, observed on 2026-07-16, as the exact minimum
baseline. Accept later stable versions only when bounded feature checks and a generated-agent
config-load check confirm the version-tested surface.

**Rationale**: The local installation was 0.144.3, while `npm view @openai/codex version dist-tags`
reported `latest: 0.144.5`; an isolated `npx @openai/codex@0.144.5 --version` returned the same
version and `features list` reported `multi_agent` as stable and enabled. The package was published
at `2026-07-16T02:57:51.857Z`. Pinning the floor and loading generated files against that binary
makes capability claims reproducible while allowing probe-compatible future releases.

**Alternatives considered**:

- Follow `0.145.0-alpha.*`: rejected because the feature explicitly targets the latest stable CLI
  and the constitution forbids experimental baseline dependencies.
- Require only "latest": rejected because it is not reproducible.
- Upgrade the user's installed CLI: out of scope; the repository must fail closed and explain the
  floor instead of mutating machine tooling.

## Decision 2: Use native custom agents and global AGENTS.md

**Decision**: Compile the seven canonical leaf roles to documented Codex custom-agent TOML files
under `agents/` inside an explicitly supplied, exact `CODEX_HOME`, and install one marker-owned
orchestration policy in `AGENTS.md` in that same root.

**Rationale**: The current official Codex manual documents personal custom agents in
`$CODEX_HOME/agents/`, global instructions in `$CODEX_HOME/AGENTS.md`, native per-agent model/
reasoning/sandbox fields, and stable multi-agent operation. The standalone file format is current
and documented but may evolve, so this release version-tests it. A non-empty global
`AGENTS.override.md` shadows `AGENTS.md`; installer preflight therefore blocks rather than claiming
that an inactive policy was installed.

**Alternatives considered**:

- Keep generating only prompt policy plus `codex exec` wrappers: rejected because it ignores the
  documented native agent surface and preserves stale capability classifications.
- Install project-local `.codex/agents/`: deferred; this release is the global successor to the
  existing global Claude install and must work across repositories.
- Package as a plugin: rejected for this slice because the product needs global agent definitions
  and instruction ownership first; plugin distribution can be evaluated separately.

**Official evidence**:

- `https://learn.chatgpt.com/docs/agent-configuration/subagents`
- `https://learn.chatgpt.com/docs/agent-configuration/agents-md`
- `https://learn.chatgpt.com/docs/config-file/config-basic`
- `https://github.com/openai/codex/blob/rust-v0.144.5/codex-rs/core/src/config/agent_roles.rs`

## Decision 3: Do not mutate config.toml

**Decision**: The baseline installer will not edit `config.toml`. Every emitted leaf agent sets
`[agents] max_depth = 1`; read-only roles set `sandbox_mode = "read-only"`, while write-capable
roles omit `sandbox_mode` and inherit the parent permission boundary.

**Rationale**: `multi_agent` is stable and enabled by default in 0.144.5. Relying only on the
documented `agents.max_depth` default would be unsafe because parent/project configuration can
raise it, so the leaf configuration layer carries the bound explicitly. Appending a second
`[agents]` or `[features]` table to an arbitrary user TOML file can make it invalid; structurally
rewriting user TOML would create an unnecessary ownership surface. Omitting a write role's sandbox
override also avoids broadening a read-only parent session.

**Alternatives considered**:

- Append a config fragment: rejected because duplicate table/key behavior is unsafe.
- Parse and rewrite the entire user TOML: rejected because stdlib preserves values but not comments
  or formatting, violating user-content preservation.
- Require users to copy a global config snippet: unnecessary because leaf files carry their own
  recursion bound; diagnostics report when target, project, or managed policy may disable use.

## Decision 4: Encode an explicit target-specific model map

**Decision**: Map `scout` and `Explore` to `gpt-5.6-terra`, `mech-executor` to `gpt-5.6-luna`,
`executor` to `gpt-5.6-terra`, and `senior-executor`, `verifier`, and `security-executor` to
`gpt-5.6-sol`. Preserve canonical low/medium/high reasoning intent. The orchestrator remains virtual
and inherits the user's main session. These are adapter defaults; account/runtime availability is
reported as unknown until separately observed.

**Rationale**: The official model page identifies Sol for complex coding/security, Terra for
balanced everyday and read-heavy work, and Luna for the fastest, lowest-cost clear mechanical work.
Keeping this map inside the adapter preserves provider neutrality and makes future model migration
one bounded change.

**Alternatives considered**:

- Inherit the same model for every role: rejected because it removes pilotfish's core cost/latency
  benefit.
- Put Codex model IDs in `routing.yaml`: rejected by the canonical-boundary principle.
- Use preview Codex-Spark: rejected because baseline functionality cannot rely on preview access.

**Official evidence**:

- `https://learn.chatgpt.com/docs/models#recommended-models`

## Decision 5: Reuse the hardened installer through target profiles

**Decision**: Add a target profile abstraction to `install/installer.py`; keep `claude` as the
default and add `codex`. For legacy Claude, `--target-home` retains its existing OS-home semantics.
For Codex, the same argument means the exact `CODEX_HOME` and no `.codex` suffix is appended. Both
profiles reuse descriptor-relative path checks, plan binding, atomic writes, backups, state
integrity, rollback, and uninstall machinery.

**Rationale**: The current engine already has extensive adversarial coverage. A second lightweight
installer would duplicate the most security-sensitive code and likely weaken behavior. A profile
limits provider differences to paths, version probes, source compiler, agent-name parser, allowed
files, and optional settings ownership.

**Alternatives considered**:

- Copy the installer and rename paths: rejected due security drift and maintenance duplication.
- Generalize every lifecycle type into a new framework: rejected as unnecessary scope; a small
  immutable profile plus existing generic mutation records is sufficient.
- Manual-only install: rejected because it loses dry-run, fingerprint approval, CAS, and rollback.

## Decision 6: Capability claims combine probe and versioned documentation

**Decision**: Extend the bounded probe with `codex features list`, stable version-floor
classification, and a no-auth config-load check over generated agents in an isolated temporary
`CODEX_HOME`. Distinguish binary capability, explicit-target configuration, and future
project/managed overrides. The last category remains unknown rather than globally enforced.

**Rationale**: CLI help alone cannot prove the standalone agent-file schema, while mutable
documentation alone cannot prove the installed binary. The version-tagged parser source, generated
config-load check, exact-target feature status, and help surfaces provide distinct evidence without
auth or a model call.

**Alternatives considered**:

- Infer custom-agent support only from the version string: insufficient evidence by itself.
- Run a real model/subagent task during install: rejected because it consumes quota, requires auth,
  can write session state, and is not deterministic.
- Treat an isolated binary probe as proof of every future project/managed override: rejected because
  configuration precedence can change effective behavior after installation.

## Decision 7: Keep routing enforcement claims honest

**Decision**: The executable canonical router remains authoritative for deterministic selection,
dispatch brake, escalation/no-downgrade, and security routing. Global `AGENTS.md`, agent
descriptions, and developer instructions carry those rules as prompt guidance. Native Codex fields
enforce only the selected role's model, explicit read-only sandbox, and per-leaf depth bound.

**Rationale**: Official discovery rules allow later project instructions to override global
guidance, and native custom-agent files do not implement the repository's canonical decision
function. Separating routing procedure from native enforcement prevents capability overclaims while
retaining useful Codex-native execution roles.

**Alternatives considered**:

- Claim that `AGENTS.md` enforces the canonical router: rejected because it is instruction input.
- Duplicate routing logic in prose: rejected because it can drift from the tested Python router.

## Decision 8: Version as a Codex-first downstream major release

**Decision**: Use downstream version `2.0.0-trionnemesis.1`.

**Rationale**: The primary install surface, user instructions, model family, and adapter guarantees
change from Claude-first to Codex-first. Existing Claude compatibility remains, but the public
product contract changes enough to warrant a major downstream bump.

**Alternatives considered**:

- `1.3.0`: rejected because it understates the primary-platform change.
- Reuse upstream `v1.2.0`: rejected because this is not an upstream release.
