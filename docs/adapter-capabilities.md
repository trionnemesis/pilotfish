# Adapter capability discovery

Codex compilation is probe-driven. It does not assume that Codex supports Claude agent frontmatter, named-role model binding, per-role tool lists, child-agent controls, or model attestation. The compiler accepts a recorded `CodexProbeResult` for reproducible builds or runs the same bounded local probe when none is supplied.

## Probe boundary

The probe runs only three commands with `shell=False` and a per-command timeout:

- `codex --version`
- `codex --help`
- `codex exec --help`

`HOME`, `USERPROFILE`, `CODEX_HOME`, and `XDG_CONFIG_HOME` point to a temporary directory, and the working directory is temporary. The probe does not run an agent, contact a model, inspect authentication, list feature state, or write user configuration. Reports store command return codes and SHA-256 digests rather than full help output, environment values, or command credentials.

## Observed local surface

The 2026-07-14 live smoke test observed `codex-cli 0.144.3`. Capability decisions are based on discovered flags, not this version string. The local help exposed `exec`, `--model`, `--sandbox`, root-level `--ask-for-approval`, `--json`, `--output-schema`, `--ephemeral`, `--ignore-user-config`, `--cd`, and `--add-dir`. The compiler places the approval flag before the `exec` subcommand.

| Canonical capability | Status with the observed surface | Evidence boundary |
|---|---|---|
| `per_role_model_binding` | degraded | `--model` is invocation-wide; canonical aliases are not translated into Codex model IDs |
| `per_role_tool_policy` | degraded | sandbox and approval flags are invocation-wide, not per role |
| `child_spawn_control` | unsupported | no verified CLI control was found |
| `fresh_context_verifier` | supported | `exec` + ephemeral session + read-only sandbox + output schema |
| `runtime_model_observation` | unsupported | JSONL output exists, but help does not promise an observed model field |
| `isolated_parallel_writes` | degraded | `--cd` can target a caller-managed worktree; the CLI probe does not create isolation |

## Compiled layout

- `codex-policy.md` carries canonical role intent and labels named-role constraints as prompt-level.
- `invocation-policy.json` includes only flags observed by the probe and forbids the observed dangerous sandbox bypass.
- `verifier-output.schema.json` closes verifier output to `CONFIRMED` or `REFUTED`.
- `capability-report.json` records CLI/version evidence hashes, status, required capabilities, and warnings.

Strict mode fails when any requested capability is not `supported`. Non-strict mode still emits all degradations and unsupported controls. Missing runtime model evidence is `UNKNOWN`; `--json` availability alone never upgrades it to `MATCHED`.
