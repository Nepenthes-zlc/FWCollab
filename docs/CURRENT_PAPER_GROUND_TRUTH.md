# FWCollab current paper ground truth

Status: repository-backed fact source for the current paper update. Snapshot reviewed on 2026-09-17. New task generation, model calls, and experiment execution were out of scope. When older prose conflicts with a frozen artifact or the current evaluator, this document follows the latter and records the conflict.

## 1. Benchmark core claim

The current benchmark asks:

> When one agent's progress depends on another agent establishing, maintaining, communicating, or synchronizing a condition, can current models correctly satisfy those cross-agent dependencies?

The implemented evidence is narrower than the question. Full-72 directly instantiates ENABLE and MAINTAIN; the separate C5-12 suite instantiates INFORMATION; bounded-window SYNCHRONIZE is not implemented in Full-72. No broader novelty or exhaustive-taxonomy claim follows from the repository alone.

Sources: SYMBOL_SPEC.md; docs/PUBLIC_RULEBOOK.md; artifacts/audits/dependency_edges_v1/dependency_edges_summary.md; docs/C5_INFORMATION_MINISUITE_SPEC.md.

## 2. Current task-construction hierarchy

### 2.1 Conceptual chain and actual build order

The useful paper abstraction is:

Components -> Mechanisms -> Role grounding -> Cross-agent dependency -> Executable dependency DAG -> Spatial scene/map -> Deterministic evaluation.

This is not a literal sequence of independent compiler passes. The V4 implementation starts from shared ModuleSpec/StageSpec plus a capability motif and a spatial layout variant. It builds the spatial controller rooms and capability capsule, generates a deterministic unit-step witness, derives a DAG from the same stage metadata, appends capability nodes, binds nodes to replay facts, verifies each graph edge, and writes the map/DAG/witness/manifest together. The correct description is **shared-specification joint generation and joint acceptance**, not arbitrary-DAG-first map synthesis.

Sources: src/fwcollab/symbolic/constructive.py (_build_map, _derive_dag, _binding_and_edge_evidence); src/fwcollab/symbolic/spatial.py (build_spatial_map, spatial_witness); src/fwcollab/symbolic/full_spatial.py (build_full_spatial_map, full_spatial_witness, _append_capability_dag, write_full_spatial_curriculum).

### 2.2 Primitive components

A primitive component is the smallest implemented transition-rule element, such as a pressure plate, door, box, hazard, mirror, or portal. Dynamic glyphs such as open/closed doors count as states of one component. The rule catalog reports **19 implemented components** across triggers, light source/router elements, traversal actuators/constraints, movable resources, terrain/state fields, state changers, and paired portals. The catalog's conceptual “physical converter” category is empty and is not counted.

Sources: docs/PRIMITIVE_COMPONENTS.md, Sections 1–14 and 16.1; SYMBOL_SPEC.md.

### 2.3 Normalized first-order mechanisms M01–M30

A mechanism is the smallest normalized gameplay unit with an independently understandable input–state–effect rule. It may be one self-contained component or a connected local component system. The current count convention yields 30 first-order mechanisms:

| ID | Implemented mechanism | Local semantics |
|---|---|---|
| M01 | ordinary floor | enterable base terrain |
| M02 | wall | blocks actors/objects and light |
| M03 | water terrain | safe for W, lethal for F |
| M04 | lava terrain | safe for F, lethal for W |
| M05 | universal hazard | lethal to either role |
| M06 | pushable crate | pushed one cell; can hold plates/block light |
| M07 | rolling orb | rolls to an obstacle; can hold plates/block light |
| M08 | one-way passage | entry allowed only in the declared direction |
| M09 | paired portal | actor/object transfers between paired endpoints |
| M10 | two role-specific exits | team succeeds only when F/W reach f/w |
| M11 | role-held plate -> door | door open only while the accepted role occupies the plate |
| M12 | crate-held plate -> door | crate occupation opens the door |
| M13 | orb-held plate -> door | stopped orb occupation opens the door |
| M14 | role-held plate -> bridge | bridge deployed only while role occupies plate |
| M15 | crate-held plate -> bridge | crate occupation deploys bridge |
| M16 | orb-held plate -> bridge | stopped orb occupation deploys bridge |
| M17 | persistent lever -> door | one activation permanently opens door |
| M18 | persistent lever -> bridge | one activation permanently deploys bridge |
| M19 | toggle -> door | each re-entry flips door state |
| M20 | toggle -> bridge | each re-entry flips bridge state |
| M21 | direct light sensor -> door | uninterrupted direct beam opens door |
| M22 | direct light + crate occlusion -> door | crate placement changes direct beam |
| M23 | direct light + orb occlusion -> door | orb stop point changes direct beam |
| M24 | fixed-mirror light -> door | fixed reflection establishes beam |
| M25 | fixed mirror + crate occlusion -> door | reflection and crate placement jointly determine beam |
| M26 | fixed mirror + orb occlusion -> door | reflection and orb stop point determine beam |
| M27 | rotatable-mirror light -> door | toggle-controlled mirror orientation establishes beam |
| M28 | rotatable mirror + crate occlusion -> door | mirror and crate states jointly determine beam |
| M29 | rotatable mirror + orb occlusion -> door | mirror and orb states jointly determine beam |
| M30 | heater/freezer -> thermal tile | H makes liquid; K freezes; state persists |

M01–M10 are atomic or self-contained mechanisms under the catalog's counting convention. M11–M30 are explicit connected local systems with input/state/effect semantics. The statement “has local dependency semantics” must not be confused with “is already cross-agent”: only a role-bound, required supporter-to-traveler cut is a cross-agent dependency.

Sources: docs/PRIMITIVE_COMPONENTS.md, Sections 16.2–16.8; docs/PUBLIC_RULEBOOK.md; src/fwcollab/symbolic/full_spatial.py (_add_capability); src/fwcollab/symbolic/constructive.py (_motif).

### 2.4 From mechanism to cross-agent dependency and DAG

A controller stage records supporter, traveler, controller instances, actuator, join mode, and gate geometry. It becomes a real cross-agent dependency only when source and target roles differ and the controlled cut is required. The no-bypass artifact verifies that the traveler cannot reach the teammate's controllers even with gates optimistically open, the exact controller set is wired to the actuator, role-specific plate acceptance holds, and the actuator is a corridor cut. Capability dependencies are included only for heavy-plate, thermal, or object/rotatable-light capsules for which one role establishes the state and the other crosses.

The raw DAG represents each controller, environment actuator, crossing, and team goal as nodes. Ordered stage crossings become prerequisites for later controllers; multiple controllers connect to an actuator with all/any; the final capability is inserted before the goal. The dependency-edge audit then contracts supporter -> environment state -> traveler progress into 367 agent-to-agent edges.

Sources: eval_private/spatial_curriculum_full_v4/manifest.json; src/fwcollab/symbolic/constructive.py (_derive_dag); src/fwcollab/symbolic/full_spatial.py (_append_capability_dag); src/fwcollab/symbolic/collaboration_eval.py (_stage_handoffs); artifacts/evaluations/collaboration_necessity_v1/summary.json; scripts/audit_dependency_edges.py.

### 2.5 Predicate binding, witness, and verification

Every DAG node has a symbolic predicate with op, motif, state, and phase. The manifest adds an executable node binding: equality predicates point to an authoritative observation path and expected value; traversal/engagement predicates are normalized at evaluation time to equality or alive-and-beyond-boundary tests. Bindings are derived from witness replay but execute on arbitrary traces.

The witness is generated by deterministic path finding and scripted one-cell joint actions. It activates controllers in a safe order, crosses each required cut, resolves the capability capsule, and reaches both exits. Generation rejects a task unless the witness reaches team_success, all nodes are bound, and all graph edges have evidence. The coverage audit independently runs each stored witness and freshly replays the resulting trace. The witness is a **solvability certificate only**: it is neither optimal nor unique and evaluated models need not copy its coordinates or action sequence.

Sources: src/fwcollab/symbolic/full_spatial.py (full_spatial_witness, write_full_witness_traces); src/fwcollab/symbolic/constructive.py (_binding_and_edge_evidence); src/fwcollab/symbolic/collaboration_eval.py (_normalized_predicate, _predicate_true); scripts/audit_task_coverage.py (_verify_witness); artifacts/audits/task_coverage_v1/coverage_summary.md.

## 3. Latest cross-agent dependency audit

### 3.1 Frozen counts

| Fact | Count |
|---|---:|
| Formal V4 tasks | 72 |
| Dependency topology classes | 48 |
| Normalized collaboration templates | 65 |
| Typed executable templates | 72 |
| Contracted cross-agent edges | 367 |
| ENABLE edges | 216 |
| MAINTAIN edges | 151 |
| Controller-stage edges | 322 |
| Collaborative capability edges | 45 |
| Adjacent responsibility-transfer transitions | 270 |
| Tasks with both directions / Mutual Enable motif | 68 |
| Full-72 SYNCHRONIZE semantic edges | 0 |
| Full-72 INFORMATION semantic edges | 0 |
| Full-72 Parallel Join compositions | 0 |
| Separate C5 information tasks | 12 |

Sources: artifacts/audits/task_coverage_v1/coverage_summary.md; artifacts/audits/dependency_edges_v1/dependency_edges_summary.md; artifacts/audits/benchmark_v1/REPORT.md; docs/C5_INFORMATION_MINISUITE_SPEC.md.

### 3.2 Taxonomy status

The following is the cleanest reading of the latest audits, but its status must remain explicit:

| Layer | Term | Repository status |
|---|---|---|
| Candidate primitive semantic | ENABLE | implemented and audited in Full-72 |
| Candidate primitive semantic | MAINTAIN | implemented and audited in Full-72 |
| Candidate primitive semantic | INFORMATION | implemented only in separate C5-12 |
| Candidate primitive semantic | SYNCHRONIZE | proposed; no bounded-window instance in Full-72 |
| Composition/motif | Handoff | **proposed interpretation**, supported by 270 target-to-next-source transitions; current evaluator also calls each supporter-to-traveler stage a handoff opportunity |
| Composition/motif | Mutual Enable | **proposed interpretation**, supported by both dependency directions in 68 tasks |
| Composition/motif | Role Alternation | **proposed interpretation**, directly countable from supporter sequence |
| Composition/motif | Fork / Parallel Join | **proposed interpretation**; graph schema supports joins, but no verified independent role-owned Parallel Join occurs in Full-72 |

This is not a fully formalized ontology implemented as a shared runtime enum. Existing C1–C7 tags in collaboration_taxonomy() are derived diagnostic tags, not seven proven same-level primitives. In particular, C2_sequential_handoff is currently triggered by any persistent lever/toggle stage, whereas the edge audit defines role transfer by adjacency. They should not be treated as identical definitions.

Sources: artifacts/audits/dependency_edges_v1/dependency_edges_summary.md; src/fwcollab/symbolic/collaboration_eval.py (collaboration_taxonomy, _stage_handoffs); scripts/audit_dependency_edges.py.

## 4. Executable DAG semantics

### 4.1 Nodes and predicates

A node contains id, kind (condition, state, event, or goal), owner (agent_a, agent_b, team, or environment), join (all or any), a predicate, and a human-readable label. The schema-level predicate has exactly op, motif, state, and nonnegative phase. The evaluator compiles it with the manifest binding into equals, actor_column_at_least, or actor_column_greater_than over authoritative snapshots.

Sources: src/fwcollab/symbolic/dag.py (validate_state_dag); schemas/state_dag.schema.json; src/fwcollab/symbolic/collaboration_eval.py (_normalized_predicate, _predicate_true).

### 4.2 Edge labels versus paper semantics

The schema accepts requires, enables, maintains, handoff, and synchronizes. Across the actual Full-72 DAGs there are **500 requires, 822 enables, and 72 synchronizes**, with zero literal maintains or handoff edges. The evaluator uses edge adjacency plus the target node's join mode for readiness; it does not branch on the edge relation. The 72 synchronizes labels connect final path progress to the team goal and impose no same-round or bounded-window synchronization.

The paper semantic counts, 216 ENABLE and 151 MAINTAIN, come from contracting supporter/environment/traveler paths and inspecting persistence, not from counting literal DAG labels. These two vocabularies must remain separate.

Sources: src/fwcollab/symbolic/dag.py (EDGE_RELATIONS, validate_state_dag); src/fwcollab/symbolic/constructive.py (_derive_dag); src/fwcollab/symbolic/full_spatial.py (_append_capability_dag); the 72 files under eval_private/spatial_curriculum_full_v4/dags/; artifacts/audits/dependency_edges_v1/dependency_edges_summary.md.

### 4.3 Completion rule, alternatives, and persistence

At every initial/post-transition snapshot, nodes are scanned in topological order. A node becomes completed when its predicate is true and either it has no predecessor, every predecessor is already completed for join=all, or at least one is completed for join=any. Completion is monotone and a predecessor and dependent may complete in the same snapshot. After an any target completes, unused incoming alternatives are waived and removed from the observed-path denominator.

Persistent completion memory does not mean a live condition stays true. Raw predicates are re-evaluated each snapshot. A true-to-false transition of occupy or activate is a regression; it is harmful if a direct dependent is pending. Role-held plates also produce premature-release violations if released before crossing. Lever/toggle/object/light/thermal states categorized as ENABLE persist without continuous supporter occupation under the audited witness semantics.

Sources: src/fwcollab/symbolic/collaboration_eval.py (evaluate_collaboration_trace); artifacts/audits/dependency_edges_v1/dependency_edges_summary.md.

### 4.4 Violations, failure stage, completion, and AUC

- predicate_before_dependencies: an agent-owned occupy, toggle, latch, or traverse predicate rises before predecessors are ready.
- closed_actuator_traverse_attempt: traveler attempts to enter a closed stage actuator.
- Coordination violations additionally include premature_release and capability_regression_before_cross.
- DAG Completion is completed required non-start nodes divided by all required non-start nodes after any waivers.
- Progress AUC is the normalized trapezoidal integral of per-snapshot completion over the episode horizon.
- For a failed episode, the evaluator returns the first topologically ordered required incomplete node, incomplete direct predecessors, role, related stage/violations, last progress round, and final completion. The Phase II table calls this failure_stage. The current evaluator does **not** emit a separate complete failure-frontier set; using “failure frontier” for this singleton-plus-direct-blockers record would overstate the implementation.

Sources: src/fwcollab/symbolic/collaboration_eval.py (evaluate_collaboration_trace); src/fwcollab/analysis/phase2.py; docs/COLLABORATION_EVALUATION_PROTOCOL.md.

### 4.5 Handoff and clean handoff

The evaluator constructs a handoff opportunity for every controller stage and selected collaborative capability capsule. It records supporter/target, actuator/cross nodes, start round, completion round, whether maintenance is required, premature releases, and cleanliness. A handoff succeeds when the crossing node completes. It is clean when it succeeds and no related coordination violation exists. This is an operational stage metric, not evidence that HANDOFF is a primitive DAG edge type.

Sources: src/fwcollab/symbolic/collaboration_eval.py (_stage_handoffs, evaluate_collaboration_trace); artifacts/audits/task_coverage_v1/coverage_summary.md.

## 5. Task validity

| Check | Current result | Interpretation boundary |
|---|---:|---|
| Successful deterministic witnesses | 72/72 | proves at least one legal solution, not uniqueness or optimality |
| Fresh deterministic replay | 72/72 | action/state trajectory reproduces exactly |
| All DAG nodes bound | 72/72 | every node is executable on state |
| All DAG edges evidenced | 72/72 | construction evidence exists for every raw edge |
| All controller stages cross-agent/no-bypass | 322 stages in 72/72 tasks | stronger collaboration evidence than the exit rule alone |
| F always WAIT -> no team success | 72/72 | partly follows from F's required exit |
| W always WAIT -> no team success | 72/72 | partly follows from W's required exit |
| F always WAIT directly blocks teammate | 70/72 | verified cross-agent cut |
| W always WAIT directly blocks teammate | 70/72 | verified cross-agent cut |
| Bidirectional-assistance tasks | 68/72 | both roles control at least one required teammate gate |
| Unique semantic signatures | 72/72 | hash of stage roles/controller kinds/actuator/mode plus capability motif |
| Unique spatial signatures | 72/72 | hash of wall/non-wall spatial mask; called topology_signature in the generator but not a DAG topology class |

Sources: artifacts/audits/task_coverage_v1/coverage_summary.md; artifacts/evaluations/collaboration_necessity_v1/REPORT.md; artifacts/evaluations/collaboration_necessity_v1/summary.json; src/fwcollab/symbolic/full_spatial.py (_semantic_signature); src/fwcollab/symbolic/spatial.py (_topology_signature); scripts/audit_task_coverage.py.

## 6. Current experiments

### A. Historical and exploratory experiments

| Run | Tasks / episodes | Models and protocol | Metrics / result | Claim boundary |
|---|---|---|---|---|
| Early S02 smoke | 1 task, 1 episode | two independent GPT-5.4-mini sessions; 40-round cap | team success in 9 rounds; 18 calls; 0 model errors | implementation smoke only, not a benchmark result |
| Early public S01–S24 development-suite run | 24 tasks, one episode/task | GPT-5.4-mini self-play; fixed 80-round cap | 21/24 success; 1,132 calls; 0 format errors; 24/24 replayed | historical development set, not held out and not the formal V4 set; surviving repository evidence is a status record rather than a complete run report |
| Historical Full-72 pilot | 72 tasks, one episode/task | two independent GPT-5.4-mini sessions; old symbol_observation.v3; dynamic budget max(80, ceil(1.5*witness rounds)); delayed messages | 14 success, 57 timeout, 1 team failure; 34,670 calls; 980 recoverable model errors; deterministic replay of all traces | historical system result only; cannot isolate model from prompt/observation changes |
| Current-protocol Full-72 single runs | 72 tasks/model, one episode/task | Gemini-3.7-Flash, GPT-5.5, GPT-5-mini self-play; agent_observation.v1; emergent coordination; no planning rounds; same dynamic witness-based budget | Gemini 72/72; GPT-5.5 71/72; GPT-5-mini 6/72. Post-run DAG evaluation: completion 1.000/0.998/0.293 respectively | descriptive single-run observations; no repeats or confidence intervals; not official Phase II causal contrasts |

Sources: TASKS_SYMBOLIC.json (S05-01); docs/IMPLEMENTATION_STATUS.md; artifacts/runs/spatial_curriculum_full_v4_model_20260913/REPORT.md; artifacts/runs/THREE_MODEL_COMPARISON_20260914.md; artifacts/runs/FOUR_MODEL_COMPARISON_20260914.md; artifacts/evaluations/collaboration_v1_20260914/REPORT.md.

### B. Official Phase II experiments

- Tasks: structurally selected Diagnostic-24.
- Conditions: GPT self-play, Gemini self-play, GPT No-Comm, Gemini No-Comm, GPT(F)+Gemini(W), Gemini(F)+GPT(W).
- Episodes: 24 * 6 * 3 = 432; three aligned replicates; all 432 replay-authenticated and valid.
- Protocol: fixed 80 rounds, temperature 1.0, zero planning rounds, emergent coordination, one-round message delay, task-first analysis and 10,000-draw task bootstrap.
- Primary metrics: Success Rate, DAG Completion, Progress AUC, Clean Handoff Rate, violations per executed round, and conditional Rounds-to-Success.
- Outcomes: GPT self-play 38.9%, Gemini self-play 31.9%, GPT No-Comm 40.3%, Gemini No-Comm 23.6%, and both cross-play directions 34.7%; total 147 successes and 285 timeouts.
- Formal claims supported: GPT messaging improves clean-handoff rate and reduces coordination violations but does not improve SR; its DAG completion slightly favors No-Comm. Gemini messaging improves completion, AUC, and clean handoff; its +8.33 pp SR interval touches zero. The pooled self-play-minus-cross-play SR point gap is +0.69 pp with an interval spanning zero.
- Unsupported claims: communication universally improves success; cross-play is equivalent to self-play; role assignment has a proved effect; conditional Rounds-to-Success is unconditional efficiency.

Sources: artifacts/evaluations/phase2_v1/REPORT.md; artifacts/evaluations/phase2_v1/STATISTICAL_CLOSURE.md; artifacts/evaluations/phase2_v1/run_integrity.json; eval_private/phase2_v1/experiment_spec.json.

### Why Full-72 single runs and Phase II percentages differ

The artifacts establish protocol differences, not a causal explanation: Full-72 uses all 72 tasks, one episode per model/task, and a dynamic budget often above 80; Phase II uses Diagnostic-24, fixed 80-round horizons, six conditions, and three task-aligned replicates. The single-run headline also mixes a different experimental purpose from Phase II's communication/cross-play contrasts. No repository artifact isolates which difference causes the gap, so no further explanation is claimed.

Sources: artifacts/runs/FOUR_MODEL_COMPARISON_20260914.md; artifacts/evaluations/phase2_v1/REPORT.md; eval_private/phase2_v1/experiment_spec.json.

### C. Separate C5 information suite

- Tasks: 12, separate from Full-72; four wiring permutations crossed with three required routes.
- Information protocol: complementary role-private DTOs; F knows private controller wiring, W knows the required route/actuator, and delayed public messaging is the only channel joining both facts.
- Conditions/episodes: GPT Comm/No-Comm and Gemini Comm/No-Comm, three task-aligned replicates; 144 episodes.
- Metrics: SR, DAG Completion, AUC, clean information handoff, dependency violation, and conditional Rounds-to-Success.
- Results: both Comm conditions 100% SR; GPT No-Comm 33.3%; Gemini No-Comm 30.6%; frozen hashes, projections, channel isolation, outputs, and all replays passed.
- Supported claim: in this finite frozen suite, explicit communication strongly improves performance under complementary private information.
- Boundary: C5-12 is not part of the main 72 and should not be used to claim main-set information coverage or broad communication universality.

Sources: docs/C5_INFORMATION_MINISUITE_SPEC.md; eval_private/c5_information_12/manifest.json; artifacts/evaluations/c5_information_v1/REPORT.md; artifacts/evaluations/c5_information_v1/run_integrity.json.

### D. DAG complexity analysis

- Scope: Diagnostic-24 self-play only; three replicates averaged within task.
- Models: GPT-5.5 and Gemini-3.7-Flash.
- Analysis: exploratory Spearman associations, bootstrap intervals/permutation tests, plus within-difficulty centered sensitivity analysis.
- Main descriptive result: depth versus completion is strongly negative (Gemini rho -0.954; GPT rho -0.924).
- Claim boundary: depth, handoff count, role alternation, and node count are highly collinear. These are univariate associations, not causal or independent feature attributions.

Source: artifacts/evaluations/dag_complexity_v1/REPORT.md.

### E. Held-out Layout-12 data collection

- Tasks: 12 new spatial layouts paired to 12 Diagnostic-24 sources while keeping executable DAG, mechanisms, and roles fixed.
- Models/protocol: GPT-5.5 self-play and Gemini-3.7-Flash self-play; three aligned replicates; fixed 80 rounds, temperature 1.0, zero planning rounds, emergent coordination.
- Episodes: 72/72 traces are present in run_state (36/model). GPT has 15 successes and 21 timeouts; Gemini has 10 successes and 26 timeouts; total 25 successes and 47 timeouts, 12,411 calls, and 527 recorded model errors.
- Intended metrics: paired seen-minus-held-out SR, DAG Completion, AUC, Clean Handoff, violations, and conditional Rounds-to-Success.
- Current evidence state: model data collection is complete, but no colocated held-out evaluation report or replay-authenticated statistical closure exists under artifacts/evaluations. Raw run_state counts are historical facts; **no spatial-generalization effect may yet be claimed**.

Sources: docs/HELDOUT_LAYOUT_12_SPEC.md; eval_private/heldout_layout_12/experiment_spec.json; eval_private/heldout_layout_12/manifest.json; artifacts/runs/heldout_layout_v1/run_state.json and traces/.

### F. Benchmark audits

These are deterministic structure/validity analyses, not model experiments and have zero model calls:

- Benchmark-v1 diversity audit: 72 typed templates, 65 normalized templates, 48 dependency topologies.
- Task-coverage audit: task-level mechanism/dependency/graph/validity matrix, fresh 72/72 witness execution and replay.
- Dependency-edge audit: 367 contracted real cross-agent edges and the proposed primitive/composition interpretation.
- Collaboration-necessity audit: no-bypass/controller cuts, permanent-WAIT checks, and 68/72 bidirectional assistance.

Sources: artifacts/audits/benchmark_v1/REPORT.md; artifacts/audits/task_coverage_v1/coverage_summary.md; artifacts/audits/dependency_edges_v1/dependency_edges_summary.md; artifacts/evaluations/collaboration_necessity_v1/REPORT.md.

## 7. Current coverage gaps

| Dependency / composition | Status |
|---|---|
| ENABLE | covered in Full-72: 216 edges across 70 tasks |
| MAINTAIN | covered in Full-72: 151 edges across 65 tasks |
| INFORMATION | covered only in separate C5-12; 0 in Full-72 |
| bounded SYNCHRONIZE | not covered; literal final synchronizes labels are not temporal synchronization |
| Parallel Join | no verified independent role-owned branches with required AND merge |
| primitive-only isolation | absent in current task-level seven-flag audit; dependencies are largely composed |
| one-way rather than bidirectional assistance | four tasks; 68/72 are bidirectional |
| taxonomy implementation | primitive/composition hierarchy remains a proposed interpretation, not one shared formal runtime taxonomy |

Sources: artifacts/audits/task_coverage_v1/coverage_summary.md; artifacts/audits/dependency_edges_v1/dependency_edges_summary.md; artifacts/evaluations/collaboration_necessity_v1/REPORT.md.

## 8. Paper freshness audit

Status is measured against the repository evidence above. “Recommended update” may point outside the four Method/Benchmark files allowed in this synchronization pass; such entries are TODOs, not changes made here.

| Paper section | Current claim before/at audit | Latest repository evidence | Status | Recommended update |
|---|---|---|---|---|
| Abstract | 72 instances, 65 templates, 48 topologies; Phase II results | benchmark audit and Phase II final report agree | correct | retain |
| Introduction: core question | execute cross-agent dependencies specified in advance | supported by private DAG/evaluator, but establish/preserve/transfer omits information/synchronization scope | partially correct | align phrasing with the narrower four-semantic research question and actual coverage |
| Introduction: novelty/contributions | executable dependencies plus deterministic evaluator | implementation supports conjunction; repository cannot prove universal priority | partially correct | retain bounded contribution wording; avoid first or exhaustive ontology |
| Introduction: taxonomy | hold/pass, sequential handoff, mutual unlock, alternating; C4/C5/C7 missing | newer edge audit separates candidate primitives from compositions; C5 now exists separately | stale | state Full-72 C5=0 while naming separate C5-12; do not treat C1–C7 as same-level primitives |
| Related Work | bounded conjunction claim and no universal priority | consistent with source boundary in prose | correct | retain |
| Benchmark Design | instance tuple, symbolic environment, privacy | code agrees; old text lacked component-to-evaluation chain and joint-generation nuance | partially correct | updated in this pass |
| Task Construction | joint emission; 72 maps; M01–M30; 48/65/72 | manifest/audits agree; old C1–C7 paragraph treated tags as same-level coverage | partially correct | updated with 19 components, M01–M30 groups, 367-edge audit, and proposed taxonomy |
| Executable DAG | node/predicate/readiness/waivers | evaluator agrees; old text did not distinguish native labels from semantic contracted edges | partially correct | updated; explicitly note 500/822/72 native labels and no temporal synchronization |
| Process Evaluation | AUC, handoff, violations, failure localization | evaluator agrees; handoff is operational stage composition and failure frontier is not a separate set | partially correct | updated definitions and boundaries |
| Experimental Setup | Diagnostic-24, six conditions, three repeats, fixed 80, inference | Phase II frozen spec/integrity agree | correct | retain |
| Results | final 432-episode estimates and bounded interpretations | final report/statistical closure agree | correct | retain |
| Discussion: C5 | says C5 remains uncovered | Full-72 C5=0, but separate frozen C5-12 is implemented/evaluated | stale | clarify main-set versus separate suite before submission |
| Discussion: limitations | C4/C5/C7 uncovered, symbolic scope, validity pending | C4 and C7 main-set gaps true; C5 statement needs suite qualification; human validity still pending | partially correct | update C5 qualification; retain evaluator-validity limitation |
| Conclusion | 72/65/48 and Phase II summary | current artifacts agree | correct | retain, subject to taxonomy phrasing consistency |
| Appendix: diversity caption | C1/C2/C3/C6 evidenced; C4/C5/C7 uncovered | accurate only for Full-72; C5-12 now exists separately | partially correct | label explicitly Full-72 and mention separate information suite elsewhere |
| Appendix: taxonomy definitions | C1–C7 diagnostic rules | code implements these as derived tags, but latest audit proposes primitives/compositions | stale | reframe as legacy/diagnostic tags or replace with primitive/composition hierarchy |
| Appendix: generation/witness | witness certifies generation and boundary predicates permit alternatives | code/audits agree, but optimality/uniqueness boundary is implicit | partially correct | explicitly call witness a non-unique, non-optimal solvability certificate |
| Appendix: evaluator validity | human labels pending | no completed human-agreement artifact found | correct limitation / unsupported result | must complete or remove any intended validity claim before submission |
| Appendix: data availability | release planned, no public URL claimed | no public URL established in reviewed artifacts | correct | retain until release exists |
| Author/COI/funding | pending placeholders | no repository evidence resolves them | unsupported/pending | author must supply before submission |

Sources for the table: all sections/*.tex; artifacts/audits/benchmark_v1/; artifacts/audits/task_coverage_v1/; artifacts/audits/dependency_edges_v1/; artifacts/evaluations/phase2_v1/; artifacts/evaluations/c5_information_v1/; current generator/evaluator sources cited above.

## Frozen discrepancy log

1. docs/IMPLEMENTATION_STATUS.md predates Phase II and C5 and still says those experiments are next; it is historical status, not the current result source.
2. docs/STATE_DAG_CURRICULUM.md describes an older abstract 72-DAG set as pre-spatial. The formal V4 paper set is eval_private/spatial_curriculum_full_v4/ and its audits; the two sets must not be conflated.
3. Generator topology_signature is a wall-mask spatial signature, while paper “dependency topology” is graph isomorphism. Latest audit names the former spatial_signature to avoid collision.
4. Full-72 raw DAG labels contain 72 synchronizes goal edges, yet audited bounded synchronization is 0. Raw edge names do not establish dependency semantics.
5. Full-72 raw DAG labels contain no maintains or handoff edges, although contracted audit semantics find 151 MAINTAIN dependencies and the evaluator creates 367 handoff opportunities.
6. Existing C2_sequential_handoff tags infer handoff-like structure from persistent lever/toggle stages. The newest role-transfer audit instead checks whether one edge's target agent becomes the next edge's source. These are related but non-identical definitions.
7. Main-paper limitations saying “C5 uncovered” are correct only for Full-72; the separate C5-12 suite now covers information and must remain explicitly separate.
8. Held-out Layout-12 has a complete 72-episode run_state and raw traces but no formal evaluation report/statistical closure in artifacts/evaluations. It is completed data collection, not yet a paper-ready generalization result.
