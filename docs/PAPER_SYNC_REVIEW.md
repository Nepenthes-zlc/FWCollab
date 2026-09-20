# Paper synchronization review

Date: 2026-09-17

Fact source: `docs/CURRENT_PAPER_GROUND_TRUTH.md`

Active manuscript: `C:\paper\ICLR 2027 - It-Takestwo It Really Takes Two Agents for Embodied Tasks`

No benchmark content, evaluator semantics, experiment result, or model output was changed or generated during this synchronization.

## A. Fixed inconsistencies

| Section | Old wording | New wording | Repository evidence |
|---|---|---|---|
| Introduction | C1/C2/C3/C6 patterns were presented as central coverage and C5 was said to be uncovered. | Full-72 is described through 216 ENABLE and 151 MAINTAIN dependencies; INFORMATION is confined to separate C5-12; bounded SYNCHRONIZE and Parallel Join remain absent. | `artifacts/audits/dependency_edges_v1/dependency_edges_summary.md`; `docs/C5_INFORMATION_MINISUITE_SPEC.md` |
| Introduction | The construction hierarchy and mechanism/dependency boundary were implicit. | Components -> Mechanisms -> Role Grounding -> Cross-Agent Dependencies -> Executable DAG -> Spatial Scene -> Deterministic Evaluation is explicit, with joint generation and acceptance noted. | `src/fwcollab/symbolic/constructive.py`; `src/fwcollab/symbolic/spatial.py`; `src/fwcollab/symbolic/full_spatial.py` |
| Introduction | Empirical contribution omitted the separate information suite and dependency-complexity audit. | Both are reported with bounded claims; complexity is explicitly exploratory and collinear. | `artifacts/evaluations/c5_information_v1/REPORT.md`; `artifacts/evaluations/dag_complexity_v1/REPORT.md` |
| Experimental Setup | Diagnostic selection referred to “collaboration primitives.” | Selection now refers to audited task structure, avoiding an unsupported finalized ontology. | `artifacts/audits/task_coverage_v1/coverage_summary.md`; Phase II protocol/freeze artifacts |
| Discussion / Limitations | “C4, C5, and C7 remain uncovered” incorrectly omitted the separate information suite. | Full-72/C5-12 scope is separated and exact C5 results are stated. | `artifacts/evaluations/c5_information_v1/REPORT.md`; `eval_private/c5_information_12/manifest.json` |
| Discussion / Limitations | Taxonomy, DAG uniqueness, witness optimality, held-out analysis, and model-family boundaries were incomplete. | These are now explicit limitations; collected held-out traces are not treated as generalization evidence. | `artifacts/audits/dependency_edges_v1/dependency_edges_summary.md`; `artifacts/runs/heldout_layout_12_model_20260915/run_state.json`; `artifacts/evaluations/dag_complexity_v1/REPORT.md` |
| Appendix | C1–C7 appeared as seven same-level taxonomy categories. | C1–C7 are labeled derived legacy diagnostic tags; candidate primitive semantics and composition motifs are presented as a proposed interpretation. | `src/fwcollab/symbolic/analysis.py` (`collaboration_taxonomy`); dependency-edge audit |
| Appendix | C5 was described as absent without a main-suite qualifier. | Full-72 C5=0 and separate C5-12 coverage/results are both stated. | C5 manifest and evaluation report |
| Appendix | Witness wording did not explicitly exclude optimality. | Witness is a deterministic, non-unique, non-optimal solvability certificate; all 72 witnesses and fresh replays pass. | `artifacts/audits/task_coverage_v1/coverage_summary.md` |
| Benchmark statistics | C columns appeared without a diagnostic/legacy boundary, and the note said C5 was absent globally. | Frozen counts remain, but labels are explicitly derived legacy tags and C5 absence is limited to Full-72/Diagnostic-24. | `artifacts/audits/benchmark_v1/REPORT.md`; C5 audit artifacts |
| Conclusion | Summary did not expose contracted semantic counts or the separate information result. | It reports the 367 ENABLE/MAINTAIN dependencies and bounded C5 result, while rejecting unique-DAG/exhaustive-taxonomy interpretations. | Dependency-edge audit; C5 evaluation report |
| Evaluator conformance | Appendix contained an uncompleted two-annotator protocol and pending table. | Replaced by deterministic positive-witness, mutation, metamorphic, and counterfactual conformance with explicit denominators and claim boundaries. | `artifacts/evaluations/evaluator_conformance_v1/REPORT.md`; `cases.csv`; `src/fwcollab/analysis/evaluator_conformance.py` |

## B. Remaining gaps (not modified)

- Bounded temporal SYNCHRONIZE coverage is absent.
- Role-owned Parallel Join coverage is absent.
- Automatic evaluator conformance is now complete; human construct-validity agreement remains unavailable and should not be claimed.
- Formal causal evaluation covers only two model families.
- Held-out Layout-12 lacks replay-authenticated evaluation and statistical closure.
- An anonymized public release URL/package is not yet finalized.

## C. Strongest currently supported claims

- Full-72 contains 72 executable tasks, 367 audited cross-agent dependencies (216 ENABLE, 151 MAINTAIN), 65 normalized collaboration templates, and 48 dependency topologies.
- Every Full-72 task has a successful deterministic witness and fresh deterministic replay; the witness certifies existence, not uniqueness or optimality.
- The executable DAG deterministically evaluates arbitrary traces through state predicates, readiness constraints, live-state checks, progress, violations, stage handoffs, and first-incomplete-node localization.
- In replay-authenticated Phase II, explicit messaging changes process quality differently for GPT-5.5 and Gemini-3.7-Flash; similar terminal SR can conceal different collaboration dynamics.
- In the separate frozen C5-12 suite, communication has strong task value under complementary private information.
- The evaluator passes all 555 applicable executable-oracle conformance cases: 267/267 invalid mutations, 0/216 valid-trace false positives, 1,260/1,260 required node completions, 72/72 failure stages, and 130/130 MAINTAIN checks.

## D. Claims not supported

- FWCollab provides a complete or exhaustive ontology of multi-agent collaboration.
- Full-72 covers INFORMATION, bounded temporal SYNCHRONIZE, or Parallel Join.
- Raw DAG edge labels directly equal the 216 ENABLE / 151 MAINTAIN semantic audit counts.
- The benchmark DAG is the unique correct task decomposition, or its witness is unique/optimal.
- Current evidence establishes general communication benefits, broad partner/generalization claims, causal effects of individual DAG features, or held-out spatial generalization.

## Evaluator conformance status

- Automatic implementation conformance is complete: **555/555 applicable cases passed**.
- Human annotation is not required to test implementation conformance to benchmark-defined executable obligations.
- Human construct validity and subjective collaboration-quality agreement remain outside the paper's claim scope.
- This evidence does not validate DAG uniqueness, causal failure attribution, taxonomy completeness, or INFORMATION/bounded SYNCHRONIZE semantics outside Full-72.
