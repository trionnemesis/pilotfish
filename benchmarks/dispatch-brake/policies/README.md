# Policy sources used by the dispatch-brake experiment

| Run family | Exact policy source |
|---|---|
| pilotfish baseline | Tag `v1.1.5`, commit `e5b45dd2330b1ba781d9da0f80211dd657d854cf`, `templates/claude-md.orchestration.md` |
| remora baseline | Tag `v0.1.6`, commit `d2ad6e553c48de2b9a6feda199fc6f595882b5dc`, `agents/orchestration.md` |
| pilotfish candidate | [`pilotfish-candidate.md`](./pilotfish-candidate.md) |
| remora candidate | [`remora-candidate.md`](./remora-candidate.md) |
| pilotfish first postpatch | Commit `633336b`, `templates/claude-md.orchestration.md` |
| remora first postpatch | Commit `17b9b90`, `agents/orchestration.md` |
| pilotfish balanced/final | Repository `templates/claude-md.orchestration.md` in the commit that publishes this benchmark |
| remora balanced/final | remora repository `agents/orchestration.md` in the corresponding dispatch-brake release commit |

The baseline and balanced final policies use canonical repository files rather than duplicated snapshots. The development candidates are stored here because they were never canonical product policies. Later positive-control iterations, including the rejected direct-speed veto, broad net-benefit rule, subjective size wording, and unshipped skill-precedence probe, are documented with their observable behavior in [`../positive-controls/`](../positive-controls/). The raw stream hashes preserve the experiment identity; no rejected wording is presented as a shipped product policy.
