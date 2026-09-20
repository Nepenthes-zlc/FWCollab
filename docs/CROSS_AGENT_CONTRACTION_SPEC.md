# FWCollab cross-agent contraction specification

Status: **frozen-audit interpretation, version 1**. This document formalizes the existing transformation implemented by `scripts/audit_dependency_edges.py`; it does not alter the native DAG, evaluator, task membership, or the audited totals of **367** cross-agent dependencies (**216 ENABLE**, **151 MAINTAIN**).

## 1. Objects and admissible paths

Let the native executable dependency graph be $G=(V,E)$. Each node $v\in V$ has an owner in `{agent_a, agent_b, environment, team}`, an executable predicate $p_v$ bound to an observation path, and a typed operation. Native edge relations (`requires`, `enables`, `maintains`, `handoff`, `synchronizes`) describe evaluator ordering or persistence constraints; they are not themselves the contracted semantic taxonomy.

A controller stage declares supporter $A$, traveler $B$, controller nodes $C=\{c_1,\ldots,c_m\}$, environment actuator node $e$, and traveler progress node $t$, with $A\ne B$. It is contractible only when all of the following hold:

1. `owner(c_i)` is the supporter role and `owner(t)` is the traveler role; `owner(e)=environment`.
2. Every path $c_i\rightarrow e\rightarrow t$ exists in the native DAG and its node bindings are verified.
3. Controller-to-actuator evidence establishes the declared `all(...)` or `any(...)` Boolean control semantics. For `all`, each input has a truth-table necessity certificate; for `any`, each retained input is an explicitly sufficient alternative.
4. Actuator-to-progress evidence is a required interaction cut: in the current Full-72 controller stages this is `single_lane_corridor_cut`, so the traveler cannot reach the post-actuator region while the actuator is unavailable.
5. Source and target agents differ. Same-agent mechanism paths are not emitted as cross-agent dependencies.

The verified path family is contracted to one semantic edge

$$
(A,\;p_C) \xrightarrow{\tau} (B,\;p_t),
$$

while retaining the source node IDs, environment node ID, target node ID, predicates, mechanism IDs, and evidence methods in `dependency_edges.csv`. A multi-controller stage remains one edge because the source is the declared Boolean controller expression, not $m$ unrelated collaboration obligations.

Collaborative capability capsules use the same test with an environment-state source and a verified `mandatory_capsule_cut`. Their path can contain only source and target nodes because the established capability predicate already names the relevant environment state.

## 2. Semantic label

`MAINTAIN` applies exactly when the source expression contains a volatile role-held pressure plate. Its truth must overlap the traveler's required crossing: releasing it before target completion disables the actuator. Thus `requires_persistence=1` and `requires_temporal_overlap=1`.

`ENABLE` applies when the supporter establishes a state that persists without continuous supporter occupation—currently a latched lever/toggle or a persistent capability state. The traveler still requires the resulting state, but the supporter need not remain at the source after establishment. Thus both persistence flags are 0.

These labels are mutually exclusive in the current audit. A stage containing both a persistent controller and a pressure plate is `MAINTAIN`, because the volatile conjunct must remain true. `Handoff`, Mutual Enable, and Role Alternation are compositions over successive contracted edges, not additional primitive labels. Native `synchronizes` edges into team success are not automatically contracted as cross-agent `SYNCHRONIZE`; Full-72 contains no verified bounded same-window synchronization primitive.

## 3. Worked example: MAINTAIN

Source: `V4-L1-003`, stage `S1`, audited row `E01`.

| Item | Native evidence |
|---|---|
| Supporter / traveler | F / W (`agent_a` / `agent_b`) |
| Native nodes | `n001`: F occupies `c1_plate`; `n002`: environment activates `a1_platform`; `n003`: W traverses the platform |
| Native path | `n001 --enables--> n002 --enables--> n003` |
| Source predicate binding | `state.controllers.c1_plate == true`; actuator check `state.platforms.a1_platform == true` |
| Target predicate | W alive and column $>9$; witness binding `state.actors.W == [7,10]` |
| Control evidence | `n001->n002`: `boolean_truth_table_necessary` |
| No-bypass evidence | `n002->n003`: `single_lane_corridor_cut` |
| Contracted edge | `F:c1_plate_on --MAINTAIN--> W:a1_platform_crossed` |

The pressure plate is recomputed from occupancy each round. F must keep `c1_plate` true through W's crossing, so this path contracts to MAINTAIN, with temporal overlap required.

## 4. Worked example: ENABLE

Source: `V4-L2-002`, stage `S2`, audited row `E02`.

| Item | Native evidence |
|---|---|
| Supporter / traveler | F / W (`agent_a` / `agent_b`) |
| Native nodes | `n004`: F latches `c2_lever`; `n005`: environment activates `a2_door`; `n006`: W traverses the door |
| Native path | `n004 --enables--> n005 --enables--> n006` |
| Source predicate binding | `state.controllers.c2_lever == true`; actuator check `state.doors.a2_door == true` |
| Target predicate | W alive and column $>18$; witness binding `state.actors.W == [7,19]` |
| Control evidence | `n004->n005`: `boolean_truth_table_necessary` |
| No-bypass evidence | `n005->n006`: `single_lane_corridor_cut` |
| Contracted edge | `F:c2_lever_on --ENABLE--> W:a2_door_crossed` |

The lever is latched. Once F establishes the source state, it persists while F leaves; W's progress therefore depends on establishment but not continued occupation.

## 5. Reproducible algorithm

```text
algorithm contract_cross_agent_dependencies(G, manifest):
    output = []
    for task in manifest.records ordered by task.id:
        validate G_task and exact node-binding coverage
        index nodes by predicate.state

        for stage in task.stages ordered by stage number:
            C = nodes named controller_id + "_on"
            e = node named actuator_id + "_on"
            t = node named actuator_id + "_crossed"

            require stage.supporter != stage.traveler
            require owners(C) = stage.supporter
            require owner(e) = environment
            require owner(t) = stage.traveler
            require verified bindings for C, e, t
            require verified Boolean evidence for every c -> e
            require verified no-bypass evidence for e -> t

            source = declared all(C) or any(C), plus actuator predicate e
            target = executable traveler boundary-crossing predicate t
            label = MAINTAIN if any controller kind is plate else ENABLE
            emit one edge with C, e, t, predicates, evidence, and label

        if task has a collaborative capability capsule:
            s, t = bound capability engage and crossing nodes
            require different responsible roles and verified mandatory cut
            emit s --ENABLE--> t with retained evidence

    require every emitted edge crosses roles and is fully verified
    return output
```

The executable implementation is `scripts/audit_dependency_edges.py`; the frozen row-level output is `artifacts/audits/dependency_edges_v1/dependency_edges.csv`. Its internal guards require 72 tasks, exactly 367 emitted edges, cross-role endpoints, and verified source binding, target binding, and dependency evidence for every row. The summary independently records 322 controller-stage plus 45 capability edges, yielding 216 ENABLE and 151 MAINTAIN.
