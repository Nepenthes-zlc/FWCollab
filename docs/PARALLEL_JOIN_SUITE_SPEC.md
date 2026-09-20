# ParallelJoin-8 suite specification

Status: extension composition v1. This suite is separate from frozen Full-72 and C5-12.

## Formal semantic

Parallel Join is a graph composition, not a pairwise dependency primitive. A role-owned F branch and a distinct role-owned W branch both feed a downstream node with `join=all`:

```text
branch_F --\
              join(all) -> downstream progress
branch_W --/
```

Both branches may advance independently. The join completes only when both predecessors have completed and its authoritative state predicate is true. F-first and W-first completion are both legal; no temporal overlap is required.

## Scope and variants

- Two primitive symmetric fork/join cases.
- Three asymmetric cases with different controller mechanisms and/or path lengths.
- Three compositions in which the join is followed by a role-held MAINTAIN dependency and downstream crossing.

Each task stores a map, executable DAG, bindings, deterministic primary/F-first/W-first witnesses, edge evidence, and branch-necessity evidence. Conformance includes both branches, F-only, W-only, both branch orders, leading irrelevant wait, and safe reversible detour.

| Task | F branch | W branch | Downstream |
|---|---|---|---|
| JOIN-01 | lever | lever | symmetric door join |
| JOIN-02 | toggle | toggle | symmetric bridge join |
| JOIN-03 | lever, shorter | toggle, longer | asymmetric door join |
| JOIN-04 | toggle, longer | lever, shorter | asymmetric bridge join |
| JOIN-05 | toggle, shorter | lever, longer | reversed asymmetric door join |
| JOIN-06 | lever | toggle | join then F-maintains-for-W |
| JOIN-07 | toggle | lever | join then W-maintains-for-F |
| JOIN-08 | lever, longer | toggle, shorter | asymmetric join then F-maintains-for-W |

## Distinction from synchronization

Parallel Join requires eventual completion of both role-owned branches and is invariant to their completion order. Synchronization instead requires live state predicates to overlap for an explicit duration. ParallelJoin-8 adds no new DAG edge relation and uses the existing `join=all` readiness rule.
