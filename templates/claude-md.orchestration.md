<!-- pilotfish:begin -->
<!-- pilotfish v1.1.3 -->
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

- **Every writing agent in a parallel batch gets its own worktree** (`isolation: "worktree"`; assumes a git checkout) and is told not to touch the main checkout; read-only roles (`scout` / `Explore`) can share safely. Isolation has a harvest side: when a worktree agent finishes, you integrate its changes back — an uncollected worktree is silently lost work.
- **A yielded agent is not a finished agent.** When an agent reports a detached launch (PID + log path), arm a background wait on that PID and resume the agent when it exits — a detached launch is a handoff, not a result.
- **Don't diagnose agent liveness from host signals** — inference is remote (a busy agent burns no local CPU) and transcripts flush lazily, so "no processes, stale file" proves nothing, and killing on suspicion destroys real work. Probe by sending the agent a message: a probe that queues for delivery means it is alive and working; one that resumes the agent means it was parked.
<!-- pilotfish:end -->
