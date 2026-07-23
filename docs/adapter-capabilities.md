# Adapter capability discovery

Codex compilation is probe-driven. It does not assume that Codex supports Claude agent frontmatter, named-role model binding, per-role tool lists, child-agent controls, or model attestation. The compiler accepts a recorded `CodexProbeResult` for reproducible builds or runs the same bounded local probe when none is supplied.

## Probe boundary

The probe runs only five bounded checks with `shell=False` and a per-command
timeout:

- `codex --version`
- `codex --help`
- `codex exec --help`
- `codex features list`
- isolated `codex app-server --stdio` configuration load

`HOME`, `USERPROFILE`, `CODEX_HOME`, and `XDG_CONFIG_HOME` point to a temporary
directory, and the working directory is temporary. The probe does not run an
agent, contact a model, inspect authentication, or write user configuration.
Reports store command return codes and SHA-256 digests rather than full help
output, environment values, or command credentials.

## Observed local surface

The 2026-07-23 live smoke test observed `codex-cli 0.144.3`, which is below the
adapter's minimum compatible version `0.144.5`. The isolated configuration load
accepted the generated custom-agent fields, and the local feature surface
reported both `multi_agent = true` and `multi_agent_v2 = true`; strict
compilation still fails closed on the version gate. Capability decisions are
based on all probe evidence, not the version string alone.

| Canonical capability | Status with the observed surface | Evidence boundary |
|---|---|---|
| `per_role_model_binding` | supported on a compatible target | standalone custom-agent `model` and `model_reasoning_effort` fields |
| `per_role_tool_policy` | degraded | custom-agent read-only sandbox is native; positive tool allowlists remain prompt guidance |
| `child_spawn_control` | degraded | leaf instructions forbid delegation; `agents.max_depth` is ignored by multi-agent V2 |
| `fresh_context_verifier` | supported on a compatible target | custom verifier agent + fresh session + read-only sandbox + closed verifier output contract |
| `runtime_model_observation` | unsupported | JSONL output exists, but help does not promise an observed model field |
| `isolated_parallel_writes` | degraded | `--cd` can target a caller-managed worktree; the CLI probe does not create isolation |

## Compiled layout

- `agents/*.toml` binds seven canonical leaf roles to native Codex model,
  reasoning-effort, and applicable read-only sandbox settings.
- `AGENTS.orchestration.md` carries the marker-delimited main-session policy and
  labels non-native constraints as prompt guidance.
- `invocation-policy.json` includes only flags observed by the probe and forbids the observed dangerous sandbox bypass.
- `verifier-output.schema.json` closes verifier output to `CONFIRMED` or `REFUTED`.
- `capability-report.json` records CLI/version evidence hashes, status, required capabilities, and warnings.

Strict mode fails when any requested capability is not `supported`. Non-strict mode still emits all degradations and unsupported controls. Missing runtime model evidence is `UNKNOWN`; `--json` availability alone never upgrades it to `MATCHED`.
