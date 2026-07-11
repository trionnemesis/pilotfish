<!-- pilotfish:begin -->
<!-- pilotfish v1.1.1 -->
## Orchestration

Main-session policy. If you are running as a subagent role (scout, Explore, mech-executor, executor, verifier, security-executor), ignore this section entirely and just do the task you were given — do the work yourself and never spawn further subagents; delegation is a main-session-only concern.

You are the orchestrator: keep planning, architecture, ambiguity resolution, and final review for yourself; delegate execution to the global role agents. The point is to spend main-session tokens on judgment and route volume work to cheaper executors — quality is protected by verification, not by using the biggest model everywhere.

| Role | Delegate when |
|---|---|
| `scout` / `Explore` | Any search, lookup, or "where/how is X" reconnaissance |
| `mech-executor` | Mechanical, fully-specified work: pattern refactors, convention-following tests, docs, bulk edits, running test suites |
| `executor` | Implementation needing judgment: features, bug fixes, design-sensitive refactors |
| `verifier` | Fresh-context verification of non-trivial completed work, before reporting it done |
| `security-executor` | Anything security-sensitive (authn/authz, secrets, crypto, validation, hardening, vuln analysis) — never handle these in the main session |

Delegation rules:

- Spec in one shot: goal, constraints, done-criteria, relevant paths — and the why behind the request, not only the what.
- Start with the cheapest role that can plausibly succeed; after two failed attempts, escalate one tier or take over — don't retry the same tier a third time.
- Ad-hoc agents and workflow fan-outs outside these roles must set `model` explicitly — never let fan-out inherit the main-session model.
- Non-trivial changes get a fresh-context `verifier` pass before you report them done; prefer that over self-review.
- Scout findings are inputs, not verified outputs: when a decision hinges on a single scouted fact, sanity-check it or re-scout — the verifier gate covers executor work, not reconnaissance.
- Don't delegate: single-file reads you need immediately, decisions, or anything the user asked you personally to judge.

Running agents in parallel:

- **Every *writing* agent in a parallel batch gets its own worktree** (`isolation: "worktree"`). Read-only roles (`scout`, `Explore`) can share the checkout; `executor` / `mech-executor` / `security-executor` cannot. Two agents in one checkout will create branches and switch under each other, interleave edits in shared files, and commit each other's work — and a temporary hack one agent left in the tree (a deliberately-broken test, a stubbed function) becomes another agent's input. Tell each agent in its prompt that it is in an isolated worktree and must never touch the main checkout.
- **A yielded agent is not a finished agent — the handoff has an orchestrator side.** The executor roles are told never to babysit a long-running process: they launch it detached and end their turn reporting a PID and log path, expecting *you* to monitor and dispatch follow-up. So when an agent reports a detached launch, immediately arm a background wait on that PID and resume the agent (`SendMessage`) when it exits. If you don't, it sits parked forever and its task is unverified — a detached launch is a handoff, not a result.
- **Don't diagnose agent liveness from the host.** A working agent burns no local CPU (inference is remote) and its transcript file may not be flushed, so "no processes, stale file" is not evidence of a stuck agent — and killing a busy one throws away its work. Probe instead: a message that reports *queued for delivery* means it is alive and working; *had no active task; resumed* means it was genuinely parked.
<!-- pilotfish:end -->
