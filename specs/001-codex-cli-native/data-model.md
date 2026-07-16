# Data Model: Codex CLI-native orchestration

This feature stores local configuration and lifecycle evidence, not application/database data.

## Install Target Profile

Represents provider-specific facts consumed by the shared lifecycle engine.

| Field | Rules |
|---|---|
| `name` | Closed value: `claude` or `codex` |
| `root_semantics` | Claude: OS home containing `.claude`; Codex: exact `CODEX_HOME` |
| `root_directory` | Claude: `.claude`; Codex: `.` inside the exact supplied root |
| `policy_path` | One UTF-8 global instruction file inside the target root |
| `shadow_policy_path` | Codex-only read-only preflight path `AGENTS.override.md`; never owned |
| `agent_directory` | One directory inside the target root |
| `state_path` | Private `pilotfish/state.json` under the target root |
| `minimum_version` | Three non-negative integers; stable release only for Codex |
| `allowed_agents` | Exact filename-to-role-name map |
| `settings_path` | Claude JSON settings path or absent for Codex |

The profile is immutable after installer construction. All derived manifest and backup paths use
its root; no operation may mix records from two targets.

## Codex Capability Snapshot

| Field | Rules |
|---|---|
| `cli.available` | Boolean derived from successful bounded version/help evidence |
| `cli.version` | Parsed stable semantic version or null |
| `cli.minimum_supported` | Constant `0.144.5` for this release |
| `cli.schema_load` | Whether the generated seven-agent set loads under strict isolated config |
| `commands` | Ordered summaries with return code and stdout hash; never raw help/config in report |
| `surfaces` | Ordered booleans for version, documented agents, multi-agent, exec, sandbox, approvals, structured output, and verifier isolation |
| `capabilities` | Closed status per canonical capability: `supported`, `degraded`, `unsupported` |
| `evidence` | Human-readable source and limitation for each classification |
| `target_configuration` | Exact-target multi-agent status or `unknown`; content is never copied into the report |
| `future_project_overrides` | Always `unknown` until evaluated in the actual project session |
| `warnings` | Deterministic ordered messages; no timestamps or machine secrets |

### State transitions

Exactly five incompatible fixture classes stop strict work: missing binary, below-floor version,
prerelease version, unparsable version, and missing/disabled required surface. Timeout/partial output
is normalized into unavailable/incomplete. A supported stable binary still requires successful
generated-agent config loading before native mappings are supported.

## Codex Role Mapping

| Field | Rules |
|---|---|
| `canonical_name` | Exact leaf role from the validated registry |
| `codex_name` | Unique custom-agent name; no unrequested control-plane role |
| `model` | `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna` in this release |
| `reasoning_effort` | Derived from canonical effort; closed documented value |
| `sandbox_mode` | Explicit `read-only` for recon/verifier; omitted/inherited for write-capable roles |
| `agents.max_depth` | Explicit `1` in every leaf file |
| `developer_instructions` | Role-specific bounded duties plus no-spawn and verification rules |
| `source_evidence` | Reference to the target mapping decision, not persisted user state |

Exactly seven mappings exist. The virtual orchestrator is documented in policy but is not emitted
as a custom-agent file.

## Codex Artifact Set

Ordered, byte-stable artifacts:

1. Seven `agents/*.toml` definitions.
2. One marker-delimited orchestration policy.
3. One invocation-policy JSON document.
4. One verifier result JSON Schema.
5. One capability report JSON document.

Only the first two are active global Codex configuration. Relative active paths are resolved
directly inside the exact supplied `CODEX_HOME`; `.codex` is never appended. Audit/runtime artifacts
may be stored under the installer-owned private directory; they never change user
config/auth/session/plugin state.

## Installation Plan

Existing `InstallPlan` contract is retained:

- operation and explicit resolved target;
- ordered changes with action/detail and before/after hashes/modes;
- warnings and blockers;
- deterministic fingerprint over the complete plan.

`will_write` is true only when the ordered change list is non-empty. A blocked plan is reportable
but cannot be applied.

## Ownership State

| Field | Rules |
|---|---|
| `schema_version` | Existing supported state version |
| `target` | Codex state records `codex`; legacy Claude state remains byte-compatible |
| `agents` | Owned relative paths with declared name and SHA-256 only |
| `policy` | Owned marker-block hash, creation flag, and separator length |
| `settings` | Claude-owned values or empty for Codex |
| `integrity_sha256` | Hash over canonical state bytes excluding the integrity field itself |

No raw pre-install user content is stored in active state.

## Rollback Manifest

One immutable, private manifest per successful mutating operation. Records are limited to the
selected profile's policy, allowed agent files, optional settings file, and state file. Each record
contains before/after hash and mode, existence, optional private backup path, and kind-specific
metadata. Manifest integrity and exact target identity must validate before any rollback plan is
shown; manifests cannot cross Claude/Codex profiles or two different Codex homes.
