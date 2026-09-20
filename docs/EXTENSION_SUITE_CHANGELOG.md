# FWCollab extension suite changelog

## 2026-09-17 — Sync-8 and Join-8 v1

- Added eight separate bounded temporal synchronization tasks and eight separate role-owned Parallel Join tasks. They are not part of Full-72, Diagnostic-24, or C5-12.
- Added an opt-in extension evaluator. `bounded_temporal_synchronize` is enabled only for records declaring `suite=Sync-8`; `parallel_join` is enabled only for records declaring `suite=Join-8`.
- Added the `temporal_overlap` binding with an explicit positive `minimum_overlap_rounds`. Existing Full-72 `synchronizes` edge labels retain provenance-only meaning.
- Implemented Parallel Join as two role-owned branches feeding an existing `join=all` node. No pairwise Parallel Join edge type was added.
- Added deterministic witnesses, replay checks, permanent-WAIT interventions, corridor-cut/no-bypass checks, conformance mutations, audit tables, and per-suite freeze manifests.
- Did not modify `collaboration_eval.py`, the symbolic world transition rules, Full-72 assets, C5-12 assets, Diagnostic-24, Phase II artifacts, or existing evaluator-conformance results.
