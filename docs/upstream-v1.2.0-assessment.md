# Upstream v1.2.0 compatibility assessment

Delivery branch: `v.1.2`

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

## Phase-to-v1.2 mapping matrix

The Phase 0-5 work merged on 2026-07-14 remains the fork's implementation baseline. The v1.2 compatibility port changes only the policy and documentation surfaces that can preserve that baseline.

| Existing phase | Preserved implementation | Upstream v1.2 control mapped onto it | v1.2 delivery change | Verification evidence |
|---|---|---|---|---|
| Phase 0: routing specification | `SPEC.md`, `.plans/routing-spec-v0.1.md`, and the pinned baseline manifest | Compatibility boundary for all imported controls | No specification or baseline replacement; this document records the selective-port decision | Baseline and specification tests remain in the full suite |
| Phase 1: canonical router | `router/`, `schemas/`, `routing.yaml`, and the seven-role registry | Dispatch brake and positive net-benefit test | Policy requires every eligible delegation to remain subordinate to the canonical `route(envelope, history)` decision | Policy tests assert the brake, positive delegation paths, and unchanged role set |
| Phase 2: Claude adapter and installer | `adapters/claude/`, `install/installer.py`, and the canonical orchestration template | Discovery -> Plan -> Approval -> Execution -> Verification lifecycle; analysis-only pre-approval security boundary | Both canonical policy copies receive the same lifecycle, brake, and approval text; compiler and installer contracts remain unchanged | Golden-template equality and installer regression coverage remain in the full suite |
| Phase 3: ledger and attestation | `runtime/ledger.py`, `runtime/attestation.py`, and ledger schemas | Verification gate semantics | No Baton trace format is imported; verification continues to emit fork-native append-only evidence and uncertainty remains `UNKNOWN` | Ledger append-only and attestation tests remain unchanged |
| Phase 4: non-blocking L2 evaluation | `evals/l2_runner.py`, classifier fixtures, and reports | Delegation eligibility guidance | No upstream six-role benchmark or classifier becomes runtime authority; the L2 lane stays advisory and non-blocking | L2 regression tests remain unchanged |
| Phase 5: Codex adapter | `adapters/codex/` capability probe, compiler, and attestor | Cross-harness compatibility boundary | No Claude-specific upstream role or Baton fixture is added to the Codex contract | Codex probe, compiler, and attestation tests remain unchanged |

This matrix is intentionally asymmetric: compatible policy controls are adapted onto the fork, while upstream runtime structure, role taxonomy, fixtures, release metadata, and replay packaging are excluded.

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
