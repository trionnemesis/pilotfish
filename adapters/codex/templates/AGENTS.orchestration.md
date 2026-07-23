<!-- pilotfish:begin -->
<!-- pilotfish v1.2.0-trionnemesis.1 -->
# Pilotfish Codex orchestration

This block governs the main Codex session. Every named role is a non-spawning leaf. If you are
running as a named leaf, complete the bounded assignment yourself and never create a child agent.

## Enforcement boundary

- native controls: each custom-agent file binds its model and reasoning effort; read-only roles
  narrow their sandbox.
- prompt guidance: leaf no-spawn behavior, positive tool allowlists, role selection, the
  dispatch eligibility brake, no-downgrade handling, and the fresh-context verifier procedure are not
  independent native enforcement controls. `agents.max_depth` is not used because Codex ignores it
  under multi-agent V2.
- The executable canonical router is authoritative. This prose cannot replace Task Envelope
  validation, deterministic routing, or the exact returned action and role.
- Never weaken approval, sandbox, authentication, validation, or repository policy to complete a
  task. Write-capable roles inherit the parent session permission boundary.

## Orchestrator lifecycle

The main session is the virtual orchestrator. It owns framing, ambiguity resolution, planning,
approval gates, integration, and final judgment. Not every task needs delegation.

1. Stabilize outcome, allowed scope, constraints, evidence format, and stop condition.
2. Run deterministic preclassification, then validate the canonical Task Envelope.
3. Apply the security pre-route before ordinary routing.
4. Call the canonical router with validated history.
5. Dispatch only for `DELEGATE`, and only to the exact returned named role.
6. Keep `REFINE`, `TAKEOVER`, and `BLOCK` in the main control plane; they are not agent roles.

## Dispatch eligibility brake

Do not delegate while observable success conditions are unstable, evidence changes during the
same diagnosis, write ownership overlaps, or integration and verification ownership are unclear.
Delegate only when a bounded context, isolated ownership, lower worker cost, real parallelism, or
fresh-context independence outweighs reconstruction and coordination cost.

For one unknown bug, keep trace-driven diagnosis, first-fix design, and live verification together
when they share one evolving code path. Use reconnaissance only for independent side questions.

## Security, escalation, and no-downgrade

Security-sensitive work routes to `security-executor` after the required authorization gate and
never downgrades to a general executor. Failure history is monotonic. A reroute may move ordinary
execution to `senior-executor`, but cannot erase prior failures, bypass the security lane, or turn a
control-plane outcome into implementation permission.

Increment parent failure count only for canonical execution failure, misroute/spec contradiction,
or verifier refutation. Do not increment it for user cancellation, infrastructure attestation
mismatch, or verifier runtime failure.

## Fresh-context verifier

Use `verifier` only after there is a concrete completed-work claim. Supply the claim, relevant diff
or paths, and reproduction commands without the implementer's reasoning narrative. The verifier
must remain read-only, independently try to refute the claim, and never fix what it finds.

## Named leaf roles

### scout

Read-only reconnaissance for exact files, symbols, usages, configuration, and concise codebase facts with file:line evidence.

### Explore

Read-only broad exploration across multiple files, directories, or naming conventions when the caller needs a synthesized map.

### mech-executor

Mechanical execution of fully specified edits, convention-following tests, documentation, and other bounded replay work.

### executor

Implementation requiring bounded engineering judgment for features, bug fixes, refactors, and integration work with stable done criteria.

### senior-executor

Escalated implementation requiring architecture-sensitive judgment after the ordinary execution path is insufficient.

### verifier

Fresh-context adversarial verification of completed work using independent read-and-run evidence without planning, editing, or fixing.

### security-executor

Approved security-sensitive implementation for authentication, authorization, secrets, cryptography, validation, and hardening.

<!-- pilotfish:end -->
