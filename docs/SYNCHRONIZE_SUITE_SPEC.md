# Synchronize-8 suite specification

Status: extension semantic v1. This suite is separate from frozen Full-72 and C5-12.

## Formal semantic

For role-owned live predicates `c_F(t)` and `c_W(t)` and an explicit positive integer `K`, a bounded temporal synchronization obligation completes at the first snapshot `t_e` for which there is an interval `[t_s,t_e]` of length at least `K` such that both predicates are true at every snapshot in the interval:

```text
exists [t_s,t_e], t_e - t_s + 1 >= K:
  forall t in [t_s,t_e]: c_F(t) and c_W(t)
```

The binding stores both authoritative observation paths, both source agents, and `minimum_overlap_rounds=K`. A downstream node is ready only after this temporal predicate completes. Predicate completion is monotone, while the two source conditions remain live and may later regress.

This semantic is not inferred from the raw DAG relation name. Full-72 terminal `synchronizes` labels retain their old provenance-only meaning. Temporal interpretation is enabled only by `extension_semantics.type = bounded_temporal_synchronize` and a `temporal_overlap` binding in Synchronize-8.

## Scope and variants

- Two primitive cases: symmetric door and symmetric bridge, `K=1`.
- Three medium cases: asymmetric arrival and `K` values 1--3.
- Three compositions: cross-agent ENABLE or MAINTAIN before synchronization and crossing.

Each task stores a map, executable DAG, bindings, synchronization metadata, deterministic witness, edge evidence, and corridor-cut necessity evidence. Conformance includes valid overlap, no overlap, insufficient overlap, early release when applicable, and extra legal wait.

| Task | Variant | Overlap | Composition |
|---|---|---:|---|
| SYNC-01 | symmetric dual plates opening a door | 1 | primitive |
| SYNC-02 | symmetric dual plates deploying a bridge | 1 | primitive |
| SYNC-03 | asymmetric F/W arrival at a door | 1 | asymmetric arrival |
| SYNC-04 | opposite arrival asymmetry at a bridge | 2 | asymmetric arrival |
| SYNC-05 | longer bounded door overlap | 3 | asymmetric arrival |
| SYNC-06 | W enables F before synchronization | 2 | ENABLE then SYNCHRONIZE |
| SYNC-07 | W maintains F's prefix gate before synchronization | 2 | MAINTAIN then SYNCHRONIZE |
| SYNC-08 | F enables W before a longer synchronization | 3 | ENABLE then SYNCHRONIZE |

## Evaluation boundary

The extension evaluator checks executable formal semantics from authoritative state. It does not change the legacy Full-72 evaluator, claim information dependence, or interpret Full-72 raw edge labels as temporal synchronization.
