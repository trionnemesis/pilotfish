# Upstream v1.2.0 compatibility assessment

Assessment branch: `upstream-v1.2.0-assessment`

## Boundary

- Fork baseline: `12e0e5b` (`trionnemesis/pilotfish` `main`).
- Upstream release tag: `v1.2.0` at `cf9f854` (`Nanako0129/pilotfish`).
- Common ancestor: `e5b45dd` (`v1.1.5` baseline).
- This branch does not merge upstream `main` and does not change `VERSION` to `1.2.0`.

The fork's compatibility boundary is the canonical Task Envelope, deterministic router, seven-leaf role registry, append-only ledger/attestation model, and Claude/Codex adapter contracts. Any upstream change that adds a role, bypasses `route(envelope, history)`, or replaces those contracts is not a drop-in update.

## Commit-level decisions

| Upstream change | Decision | Reason |
|---|---|---|
| `633336b` dispatch brake | Ported in adapted form | The eligibility brake is policy-level and preserves the existing seven roles. The port keeps the canonical router as the dispatch authority and adds tests for tightly coupled diagnosis plus positive delegation paths. |
| `5f027b8` dispatch net-benefit balance | Ported in adapted form | The net-benefit rule complements, rather than replaces, deterministic routing. Raw upstream benchmark policy files are not imported because they use the upstream six-role surface and do not produce canonical ledger evidence. |
| `863b117` Baton attribution | Not ported | Documentation-only attribution is not required for the fork's compatibility change. |
| `ff9f118`, `16b9650`, `9b7e4d6` Baton lifecycle and gate fixtures | Partially ported | The Discovery → Plan → Approval → Execution → Verification boundary is compatible and is represented in the policy. Baton-specific harnesses and snapshots are excluded. |
| `40b9b7f` read-only approval roles | Not ported wholesale | `plan-verifier` and `security-reviewer` would expand and change the canonical role taxonomy. The fork keeps explicit approval, analysis-only pre-approval, the existing security lane, and the existing outcome verifier. |
| `1f1e76f` supported runtime gate | Already present; no change | The fork already requires Claude Code `2.1.207+` before planning writes, with installer regression coverage. |
| `32e89b8`, `1ac02e7` release/benchmark packaging | Not ported | Release metadata and upstream replay inputs do not alter the fork's runtime contracts. |

## Preserved invariants

- Exactly seven leaf roles remain in `routing.yaml` and `templates/agents/`.
- `REFINE`, `TAKEOVER`, and `BLOCK` remain control-plane outcomes, not roles.
- Security work remains on `security-executor`; pre-approval analysis is explicitly non-writing.
- Named-role model binding remains owned by the role registry.
- Ledger corrections remain append-only, and attestation uncertainty remains `UNKNOWN` rather than an inferred match.
- Installer approval remains fingerprint-bound with backup, rollback, collision, and runtime gates.

## Verification target

Run the full dependency-free suite after the policy and documentation changes:

```sh
python3 -m unittest discover -s tests -v
```
