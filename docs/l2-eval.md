# L2 stochastic classification evaluation

L2 measures the stochastic `classify()` boundary without changing the deterministic router or the blocking L1 gate. It is opt-in and non-blocking in v0.1: the repository workflow does not invoke a headless model, spend credits, or enforce a quality threshold.

## Command contract

The runner starts the configured command directly with `shell=False`. On each invocation it sends one JSON object to standard input:

```json
{"fixture_id":"security-refresh-token-replay","run_index":1,"schema_version":"0.1","task_description":"..."}
```

The command may emit either a Task Envelope or a wrapper:

```json
{
  "classifier_source": "codex-headless",
  "envelope": {"schema_version":"0.1","task_id":"..."},
  "metadata": {
    "cli_version": "optional",
    "model": "optional",
    "token_usage": {"input": null, "output": null, "total": null}
  }
}
```

The runner has no credential flags and does not persist the command arguments or environment. Supply provider credentials through the command's normal runtime configuration. Do not place credentials in `--command-json`.

## Explicit execution

Every paid or network-capable run requires all four controls: command, repetitions, maximum invocations, and a new output directory.

```sh
python -m evals.l2_runner \
  --command-json '["your-classifier","--json"]' \
  --runs 3 \
  --max-invocations 27 \
  --output-dir .artifacts/l2-run-001
```

The budget is an invocation ceiling, not a currency estimate. The runner rejects a request when `fixture_count × runs` exceeds `max_invocations`. Existing output directories are never overwritten. `runs.jsonl` is flushed after every invocation; `report.json` is written after aggregation. Non-JSON output is capped and retained in `raw_output`; parsed but invalid envelopes are retained in `emitted_envelope`. Neither case is repaired or guessed.

## Metrics and denominators

- **Envelope schema-valid rate:** valid Task Envelopes divided by every attempted run.
- **Accepted-envelope rate:** valid envelopes whose declared accepted fields all fall within the fixture ranges, divided by every attempted run.
- **Field-level agreement:** accepted field matches divided by every accepted field declared for every attempted run. Invalid output contributes zero matches.
- **Final route agreement:** exact `action` and `role` matches divided by every attempted run. Invalid output has no route and counts as a miss.
- **Security recall:** valid envelopes classified as `task_type=security` divided by every run of fixtures marked `security_expected=true`.
- **Per-fixture variance:** `1 - dominant semantic outcome runs / attempted runs`. A semantic outcome is the tuple of task type, completeness, risk, and risk tags; all invalid outputs share the `INVALID` outcome.

Latency is measured by the runner. Token counts, model, and CLI version remain nullable and are reported only when the command emits them. The CLI exits successfully after a completed evaluation regardless of quality metrics; configuration and persistence failures remain operational errors.
