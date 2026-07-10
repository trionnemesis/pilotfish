# Changelog

All notable changes to pilotfish. The installed version is stamped inside the policy block in `~/.claude/CLAUDE.md` (`<!-- pilotfish vX.Y.Z -->`); installs older than v1.1.0 carry no stamp.

## v1.1.2 — 2026-07-10

Hardening patch. Re-run the install prompt to upgrade.

| Change | Credit |
|---|---|
| **The six roles are now hard leaf agents.** The four executor roles get `disallowedTools: Agent, Workflow`; `verifier` extends its existing read-only exclusions with the same; `scout`/`Explore` were already leaves via their `tools` allowlist. Each also carries an explicit "you are a leaf agent" line so a genuinely mis-routed task is reported back instead of re-delegated. | [@dromsak](https://github.com/dromsak) (#6) |

This replaces v1.1.1's prompt-only guard with capability removal. The prompt guard put the routing table into every subagent's context, so a `mech-executor` could pattern-match its own task and re-delegate — observed cascading four levels deep in a real incident. Verified before merge: with the prompt guard a nested role still spawned (a haiku scout ran real work); with `disallowedTools` the spawn is blocked and the role does the work itself.

## v1.1.1 — 2026-07-10

Community-driven patch. Re-run the install prompt to upgrade.

| Change | Credit |
|---|---|
| Policy block now forbids subagent roles from spawning further subagents — delegation is a main-session-only concern. The recursive-spawn risk was verified empirically (a sonnet role successfully dispatched a haiku role) before merging | [@nicofirst1](https://github.com/nicofirst1) (#3, #5) |
| `executor` / `mech-executor` no longer babysit long-running processes: launch detached (nohup + log), one sanity check, then yield with PID + log path for the orchestrator to monitor | [@nicofirst1](https://github.com/nicofirst1) (#2, #4) |
| Follow-up to the above: a detached launch must be reported as a handoff, not a completed verification, when done-criteria depend on the process outcome | maintainer |
| Installer Step 4 verification updated for Claude Code 2.1.198+ (the `/agents` wizard was removed); verify via `/model` and by asking Claude which subagent types are available | [@zxcj04](https://github.com/zxcj04) (#1) |

## v1.1.0 — 2026-07-09

Security, accuracy, and update-flow release. Re-running the install prompt upgrades in place.

### Security & trust

| Change | Why |
|---|---|
| New **Trust & security** README section, with a tag/SHA-pinned install variant | `main` can change between review and install (TOCTOU); pinning makes what-you-reviewed = what-installs |
| Runbook: templates must be fetched from the same pinned ref as the runbook | Pinning now covers the actual installed bytes, not just the instructions |
| `scout` / `Explore` switched from a `disallowedTools` denylist to a positive `tools: Read, Glob, Grep` allowlist | They previously retained Bash, so "read-only" was prompted, not enforced |
| Runbook detects agent collisions by frontmatter `name:` (not filename) and flags plugin shadowing | Claude Code loads only one definition per name; `executor`/`scout` are common names |

### Behavior & quality

| Change | Why |
|---|---|
| Policy block self-disables for subagent roles | A custom `Explore` loads user memory (the built-in skips it); the policy is main-session-only |
| New policy rule: scout findings are unverified inputs | The verifier gate covers executor output, not reconnaissance |
| `verifier` runs maximum-thoroughness on security-sensitive work | medium-effort verification of high-effort security work was inconsistent |
| Versioning + "Updating an existing install" flow (this release) | Early installs had no way to learn about updates |

### Docs & claim accuracy

| Change | Why |
|---|---|
| Split Anthropic's endorsement (delegation + fresh-context verification) from pilotfish's own cheap-model routing thesis | Attribution honesty |
| 12-worker numbers reframed as an upper-bound, API-dollar experiment, with inline sources | One community experiment ≠ a guarantee; subscription quota ≠ API dollars |
| Explore warning corrected: inherited model is Opus-capped on the Claude API | Precision |
| `best`-alias fallback at the 7/12 boundary restated honestly (documented rule + June outage precedent; boundary UX unpublished; `fallbackModel` never triggers on billing errors) | The boundary hasn't been observed by anyone yet |
| Windows portability note; subscription-vs-API/Bedrock scope note; FAQ rows for spawn overhead, fast off-switch, managed environments, project-CLAUDE.md stacking | Compatibility coverage |

## v1.0.0 — 2026-07-08

Initial public release: three-layer global architecture (settings `best` + `fallbackModel`, six role agents with tiered model/effort bindings, role-based delegation policy), one-prompt agent-guided installer with approval gate and idempotent upgrades, bilingual README, sourced research report and design rationale.
