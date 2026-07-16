# Contract: Codex adapter artifacts

## Compiler input

- A schema-valid canonical role registry with exactly one virtual orchestrator and seven leaf roles.
- A bounded `CodexProbeResult` from live commands or recorded fixture output.
- Optional strict required-capability names drawn from the canonical capability vocabulary.

Invalid registries, unknown capability names, duplicate requirements, or an incompatible strict
probe fail before any artifact is returned.

## Required artifact inventory

The compiler returns artifacts in this stable order:

1. `agents/scout.toml`
2. `agents/Explore.toml`
3. `agents/mech-executor.toml`
4. `agents/executor.toml`
5. `agents/senior-executor.toml`
6. `agents/verifier.toml`
7. `agents/security-executor.toml`
8. `AGENTS.orchestration.md`
9. `invocation-policy.json`
10. `verifier-output.schema.json`

`capability-report.json` is appended by `emitted_files()` after those artifacts.

## Custom-agent TOML contract

Every role file MUST parse as TOML and contain:

- one unique `name` matching the canonical role;
- one non-empty `description`;
- one non-empty `developer_instructions` block;
- one target-specific `model` from the documented Codex map;
- one documented `model_reasoning_effort`;
- explicit `sandbox_mode = "read-only"` for recon/verifier roles and no sandbox override for
  write-capable roles, which inherit the parent's permission boundary;
- an explicit `[agents] max_depth = 1` recursion bound.

No file may contain secrets, auth paths, dangerous flags, an MCP credential, or a canonical Claude
model alias as an active Codex model.

## Policy contract

The policy MUST:

- carry the current downstream version marker;
- require callers that need deterministic selection to use the executable canonical router before
  invoking the selected named agent;
- preserve dispatch brake, authorization, escalation/no-downgrade, security lane, and verifier
  semantics;
- refer to roles rather than target model names;
- state that model, explicit read-only sandbox, and leaf depth fields are native controls;
- state that positive tool allowlists and runtime model observation remain limited;
- label AGENTS-based role selection, dispatch brake, no-downgrade, and verifier procedure as prompt
  guidance rather than native enforcement;
- never recommend dangerous approval/sandbox/hook-trust bypass.

## Capability report contract

The JSON report is byte-stable, sorted, timestamp-free, and contains hashes rather than raw command
output. It includes the detected version, minimum stable version, generated-config-load result,
command summaries, binary/target surfaces, capability statuses, evidence strings, strict
requirements, and deterministic warnings. Future project/managed overrides remain `unknown`.

Required native capabilities for install are:

- `per_role_model_binding`
- `child_spawn_control`
- `fresh_context_verifier`

`per_role_tool_policy` may be degraded only when the report explicitly says that sandbox is native
but positive tool allowlists are not independently enforced. `runtime_model_observation` remains
unsupported unless separately sourced structured evidence is supplied.
