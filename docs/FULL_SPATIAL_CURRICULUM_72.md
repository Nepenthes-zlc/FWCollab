# 72-map M01-M30 full spatial curriculum

Updated: 2026-09-13

## Scope

`eval_private/spatial_curriculum_full_v4/` is the first runnable 72-map set in
this repository that actively covers every catalog motif from M01 through M30.
It keeps the verified L1-L7 controller-room chains and appends one mandatory
capability capsule to each map.  The capsules are part of the route to a
role-specific exit; they are not decorative glyphs.

The suite remains a discrete symbolic planning benchmark.  It does not add
gravity, jumping, continuous collision, velocity or pixel-level control.

## Dataset acceptance

- 72 maps across L1-L7: 4, 8, 10, 12, 14, 14, 10.
- 72 unique semantic signatures and 72 unique wall-topology signatures.
- M01-M30 all occur in the verified manifest.
- The 21 capabilities missing from the previous controller-only suite each
  occur in 2-4 maps.
- 72/72 deterministic unit-step witnesses reach `team_success`.
- 72/72 witness and model traces replay against the authoritative state
  hashes.
- Full test suite: 59 passed in 267.94 seconds.

Primary capability occurrence counts are stored in
`eval_private/spatial_curriculum_full_v4/manifest.json`.  M01, M02 and M10 are
present in all maps; the controller motifs M11/M14/M17/M18/M19/M20 remain
distributed through the base room chains.

Generate the complete set with:

```powershell
.venv\Scripts\python.exe -m fwcollab.cli spatial-generate `
  --output-root eval_private\spatial_curriculum_full_v4 `
  --gallery artifacts\symbolic\spatial_curriculum_full_v4_gallery.html `
  --trace-root artifacts\runs\spatial_curriculum_full_v4_witness
```

## Real dual-model run

The 72 maps were run with two independent `gpt-5.4-mini` sessions.  Each role
received the complete updated observation, chose one adjacent-cell move or
`WAIT`, submitted from the same frozen round state, and received messages one
round later.  Provider/schema errors became `WAIT` and did not abort a map.

Per-map budget was `max(80, ceil(witness_rounds * 1.5))`; concurrency was 8 and
the request timeout was 120 seconds.

| Level | Success | Team failure | Timeout | Success rate |
|---|---:|---:|---:|---:|
| L1 | 3/4 | 0 | 1 | 75.0% |
| L2 | 3/8 | 1 | 4 | 37.5% |
| L3 | 3/10 | 0 | 7 | 30.0% |
| L4 | 3/12 | 0 | 9 | 25.0% |
| L5 | 2/14 | 0 | 12 | 14.3% |
| L6 | 0/14 | 0 | 14 | 0.0% |
| L7 | 0/10 | 0 | 10 | 0.0% |
| Total | 14/72 | 1 | 57 | 19.4% |

The run used 34,670 provider calls, recorded 980 recoverable model errors, and
took 11,394.533 seconds (3 h 9 min 54.533 s).  There were no runner errors.
All 72 recorded traces replayed exactly and have interactive HTML renderings.

Failure diagnoses for the 58 unsuccessful maps were 50 budget exhaustion, 5
blocked-action loops, 2 mutual-wait loops, and one direct environment failure
caused by entering unsafe thermal terrain.

## Capability-stage interpretation

Only 30/72 model trajectories reached the appended capability area, 21/72
activated or otherwise engaged the primary capability, and 18/72 crossed its
mandatory cut.  Four trajectories crossed the capability but still failed to
finish both exits.  In L6 no trajectory reached the capability capsule; in L7
only one did.  Therefore raw success rates grouped by M03-M30 are confounded by
the preceding controller-chain difficulty and must not be interpreted as pure
isolated-mechanism scores.

For isolated mechanism proficiency, use additional short diagnostic maps or
score `capability_reached -> capability_engaged -> capability_crossed`
conditionally.  The current full suite primarily measures compound long-horizon
planning.

## Artifacts

- manifest: `eval_private/spatial_curriculum_full_v4/manifest.json`;
- maps and private DAGs: `eval_private/spatial_curriculum_full_v4/maps/` and `dags/`;
- private witnesses: `eval_private/spatial_curriculum_full_v4/witnesses.json`;
- initial gallery: `artifacts/symbolic/spatial_curriculum_full_v4_gallery.html`;
- deterministic witnesses are stored compactly in `eval_private/spatial_curriculum_full_v4/witnesses.json`; bulky rendered witness traces are generated on demand and are not retained;
- real-model summary: `artifacts/runs/spatial_curriculum_full_v4_model_20260913/summary.json`;
- stage/mechanism analysis: `artifacts/runs/spatial_curriculum_full_v4_model_20260913/analysis.json`;
- real-model replay index: `artifacts/runs/spatial_curriculum_full_v4_model_20260913/index.html`.

## Remaining release boundary

This completes runnable M01-M30 coverage for the V4 spatial curriculum.  It
does not prove that the older 72 abstract DAGs in `eval_private/dag_curriculum/`
are each reproduced node-for-node, and it does not provide a held-out split,
counterfactual variants or external human review.  Those remain release tasks.
