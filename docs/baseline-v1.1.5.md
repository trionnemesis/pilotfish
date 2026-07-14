# routing-spec implementation baseline

Snapshot time: `2026-07-14T13:09:26+08:00`

This document records the live repository state used to implement `SPEC.md`. It does not rewrite the supplied draft; where the draft's input assumptions differ from live evidence, this snapshot is authoritative for implementation and rebasing decisions.

## Repository identity

| Item | Verified value |
|---|---|
| Upstream | `Nanako0129/pilotfish` |
| Fork | `trionnemesis/pilotfish` |
| Fork relationship | GitHub `fork=true`, parent/source=`Nanako0129/pilotfish` |
| Default branch | `main` |
| Baseline commit | `e5b45dd2330b1ba781d9da0f80211dd657d854cf` |
| Baseline tag | `v1.1.5` |
| `VERSION` | `1.1.5` |
| Commit count at snapshot | `27` |

The local `main`, `origin/main`, and `upstream/main` all resolved to the same baseline commit before Phase 0 work began.

## Tag evidence

The required local checks were run after fetching both remotes:

```text
git fetch origin
git fetch --tags --force upstream
git tag -l
git show-ref --tags
```

Observed release tags were `v1.0.0`, `v1.1.0`, `v1.1.1`, `v1.1.2`, `v1.1.3`, `v1.1.4`, and `v1.1.5`. Tag `v1.1.5` resolves to the baseline commit above.

## Supplied draft errata

The following §1 statements in the supplied Draft v0.1 were historical assumptions, not current facts at fork time:

| Draft statement | Live snapshot | Implementation treatment |
|---|---|---|
| `main` has 2 commits | `main` has 27 commits | Use immutable baseline SHA, not the count in the draft |
| Repository has 9 stars | GitHub API returned 431 | Informational only; never use as an acceptance gate |
| No published GitHub Releases | GitHub API returned 7 published releases | Tags remain the release identity source for this work |
| `v1.1.5` still needs confirmation | `v1.1.5` exists and points to baseline SHA | Preserve the tag; do not overwrite it |

The three-layer design and six checked-in role templates were still current at baseline. The target adds `senior-executor`, producing seven checked-in subagent roles plus the virtual `orchestrator` control-plane role.

## Baseline inventory

| Surface | Baseline state |
|---|---|
| Machine | `templates/settings.snippet.json` selects the main model and fallback chain |
| Roles | Six files under `templates/agents/` own role-level model/tool bindings |
| Policy | `templates/claude-md.orchestration.md` routes by prose and role name |
| Installer | `install/AGENT-INSTALL.md` is an agent-guided, approval-gated runbook |
| Tests | Two dependency-free `unittest` policy contracts |
| Runtime code | None |
| CI | No workflow on baseline `main` |

Baseline tests:

```text
python3 -m unittest discover -s tests -v
Ran 2 tests ... OK
```

## Baseline content hashes

SHA-256 hashes make the pre-change settings, policy, roles, and installer auditable without duplicating their contents:

The same data is available to tests and tooling in `baseline/manifest.json`; validation reads file bytes from the immutable baseline Git commit, so intentional changes in later phases do not invalidate the snapshot.

| Path | SHA-256 |
|---|---|
| `templates/settings.snippet.json` | `2bb3f3391af7c35683a8393fa769dce4b78a88b89cf209fd546adaa0c015f246` |
| `templates/claude-md.orchestration.md` | `9e8b3618cef583f5a402b782ba79139441e5a81c653a9d170182bf4338fdfec7` |
| `templates/agents/Explore.md` | `4e29c43c3fc7979e91e2b3b18eb29c9068e99646f64633f655ca4d4c362cf20e` |
| `templates/agents/executor.md` | `8afaba22f568c2e646ebfe9d4421c9b78d5a578f50481a5759f5f7beaab3d0be` |
| `templates/agents/mech-executor.md` | `4fbcb1361353c98f4191e0c1c139b31357254a30111a0becd227aee171cf504d` |
| `templates/agents/scout.md` | `0b86d44b5fe4bb40ec97560d6f2acf3d1c17617b271f31a81ee77d3c5c4b5de0` |
| `templates/agents/security-executor.md` | `a841f32720347d69de34000baceda0e9ca6da206ec7f9ea2d145c0979cd9658f` |
| `templates/agents/verifier.md` | `d85f219eac0f1dbce011ebf72431c0994ef0a0c2f39f7cbad9e4922e6b396c23` |
| `install/AGENT-INSTALL.md` | `75fb673307ceee5d68f7fd769d8271c0c2057c0562f359589b056ddb520ca784` |

## Upstream overlap at snapshot

- PR #10, `feat(orchestration): phase-aware Baton composition (v1.2.0)`, overlaps Claude policy, roles, installer, and benchmark work but does not implement the canonical router, ledger/attestation, or Codex adapter. Its eight-role taxonomy and `executor=opus/medium` conflict with this spec.
- PR #11, `Repackage pilotfish as a Claude Code plugin`, is a product replatforming alternative and is outside this implementation series.
- Issue #8 documents installer friction and reinforces pinned-local installation, explicit approval, and no security-bypass behavior. Backup and collision-detection requirements come from `SPEC.md` and the existing installer contract, not from that issue.

Before starting each implementation branch, fetch `upstream` and recheck whether overlapping PRs have merged. Do not cherry-pick an overlapping branch wholesale; reconcile only compatible controls and preserve this spec's role taxonomy and model ownership.
