# Held-out Layout-12 generalization protocol

Status: pre-registered design. Source-task selection, generation rules and hashes are frozen before any held-out model episode is run.

## Question

Does performance transfer when the collaboration dependency DAG and mechanism instantiation are held fixed, but the spatial layout is unseen?

This is a paired intervention:

`same executable DAG + same mechanisms + same roles + new obstacle/branch layout`.

It does not claim generalization to new collaboration primitives or new mechanisms.

## Task selection

Twelve source tasks are selected from Diagnostic-24 using task structure only, without reading model outcomes. Quotas are `L1:1, L2:1, L3:2, L4:2, L5:2, L6:2, L7:2`. The fixed source IDs are:

`V4-L1-001, V4-L2-008, V4-L3-010, V4-L3-004, V4-L4-009, V4-L4-012, V4-L5-011, V4-L5-004, V4-L6-010, V4-L6-007, V4-L7-009, V4-L7-002`.

They cover 12 source instances, seven difficulty levels, stage depths 1–7 and eleven primary capability labels. This list is fixed before held-out inference.

## Generation and acceptance

For each source task, reconstruct its semantic stage specification and primary capability. Search deterministically for a layout-variant code satisfying all of the following:

1. the generated topology differs from its paired source;
2. its topology is absent from all 72 benchmark maps;
3. its executable DAG nodes and edges are exactly identical to the source after removing graph identity/title metadata;
4. its mechanism and controller-stage signatures are unchanged;
5. the existing deterministic symbolic witness reaches team success;
6. every DAG node binding and edge certificate is verified.

Only obstacle placement and controller branch position may change. Map dimensions, action space, prompt, budget and mechanism rules remain unchanged.

## Experiment

Run only normal communication self-play:

- GPT-5.5 + GPT-5.5;
- Gemini-3.7-flash + Gemini-3.7-flash;
- 12 paired tasks × 3 aligned seeds;
- 72 held-out episodes total.

The seen-layout reference is the already frozen Phase-II self-play trace for the paired source task and seed. No source episode is rerun.

For each model and metric, first average three seeds within each task, then compute:

\[
\Delta_{generalization}=Perf_{seen}-Perf_{heldout}.
\]

Report task-level paired bootstrap 95% confidence intervals for SR, DAG Completion, Progress AUC, Clean Handoff, Violation and Rounds-to-Success. A confidence interval containing zero is inconclusive and is not evidence of equivalence.

## Claim boundary

This experiment supports only controlled spatial-layout generalization under a fixed collaboration DAG. It must not be described as generalization to new DAGs, mechanisms, partners or information structures.
