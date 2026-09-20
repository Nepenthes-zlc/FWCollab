# Paper update changelog

Date: 2026-09-17

## Scope

No benchmark map, DAG, manifest, witness, audit, experiment result, or task definition was changed. No task generation, experiment, or model call was run. This synchronization added a repository fact source and changed only four Method/Benchmark TeX sections in the current FWCollab manuscript.

## Added

- docs/CURRENT_PAPER_GROUND_TRUTH.md: source-linked account of implemented components/mechanisms, V4 construction order, dependency-edge audit, executable evaluator semantics, task validity, all located experiments/run collections (including unanalysed Held-out Layout-12), coverage gaps, and section-by-section paper freshness.
- docs/PAPER_UPDATE_CHANGELOG.md: this record.

## Updated manuscript files

- sections/03_benchmark_design.tex
  - Added the precise benchmark question and the Components-to-Evaluation chain.
  - Distinguished the conceptual method chain from the actual shared-specification joint-generation pipeline.
  - Added explicit Full-72/C5/synchronization scope and non-uniqueness boundaries.
- sections/04_task_construction.tex
  - Added 19-component and M01–M30 mechanism organization.
  - Defined when local mechanism wiring becomes a verified role-grounded dependency.
  - Added the 367-edge audit (216 ENABLE, 151 MAINTAIN; 322 stage and 45 capability edges).
  - Reframed Handoff, Mutual Enable, Role Alternation, and Parallel Join as proposed composition interpretations rather than implemented same-level primitives.
  - Clarified witness status as a non-optimal, non-unique solvability certificate.
- sections/05_executable_dag.tex
  - Distinguished native DAG labels from contracted paper dependency semantics.
  - Recorded actual Full-72 label counts: 500 requires, 822 enables, 72 terminal synchronizes, and no literal maintains/handoff.
  - Clarified node/predicate execution, alternatives, persistence/regression, and DAG non-uniqueness.
- sections/06_process_evaluation.tex
  - Clarified Completion/AUC, live-predicate regression, violations, stage handoff, and clean handoff.
  - Stated that the implementation returns a first incomplete required node plus direct blockers, not a separate exhaustive failure-frontier set.

## Deferred inconsistencies

The first synchronization pass excluded Introduction, Results, Discussion/Limitations, and Appendix. The second pass below resolves the stale wording identified there.

## Remaining-section synchronization

The active manuscript directory is now:

`C:\paper\ICLR 2027 - It-Takestwo It Really Takes Two Agents for Embodied Tasks`

No benchmark map, DAG, manifest, witness, task definition, evaluator semantic, frozen result, or model output was changed. No benchmark generation, experiment, held-out analysis, or model call was run.

- `sections/01_introduction.tex`
  - Reorganized the motivation around required cross-agent dependencies and the Components-to-Evaluation chain.
  - Replaced the old C1–C7-centered coverage claim with Full-72 ENABLE/MAINTAIN, separate C5-12 INFORMATION, and explicit SYNCHRONIZE/Parallel Join gaps.
  - Added the 19-component, 367-edge, 216/151 semantic counts and bounded empirical claims.
- `sections/07_experimental_setup.tex`
  - Replaced “collaboration primitives” with the implementation-neutral “audited task structure.”
- `sections/09_discussion_limitations.tex`
  - Corrected the stale claim that C5 remained uncovered by separating Full-72 from C5-12.
  - Added taxonomy, DAG, witness, evaluator-validity, complexity-collinearity, two-model-family, and held-out-analysis boundaries.
- `sections/10_conclusion.tex`
  - Added the contracted dependency counts and separate C5 evidence while explicitly bounding the DAG and taxonomy claims.
- `sections/appendix.tex`
  - Reframed C1–C7 as legacy/derived diagnostic tags.
  - Distinguished native `synchronizes` labels from bounded temporal SYNCHRONIZE.
  - Made witness non-uniqueness/non-optimality explicit and recorded C5-12 and Held-out Layout-12 status.
- `tables/benchmark_statistics.tex`
  - Retained frozen C1/C2/C3/C6 counts but labeled them derived legacy diagnostic tags.
  - Corrected the C5 note to apply only to Full-72/Diagnostic-24 and pointed to the separate suite.
- `docs/PAPER_SYNC_REVIEW.md`
  - Added the final consistency audit, remaining gaps, and supported/unsupported claim boundaries.

Checked without textual changes: Abstract, Related Work, Results, and the already synchronized Method/Benchmark sections 03–06.

## Evaluator automatic conformance suite

Implemented after paper synchronization, without modifying the manuscript in this pass:

- Added `src/fwcollab/analysis/evaluator_conformance.py` and `scripts/validate_evaluator_conformance.py`.
- Added positive witness, mutation, metamorphic, and counterfactual conformance cases over frozen Full-72.
- Added `tests/unit/test_evaluator_conformance.py`.
- Generated `artifacts/evaluations/evaluator_conformance_v1/` with machine-readable case rows and a paper-ready report.
- Full deterministic run: 576 generated cases, 555 applicable, 555/555 passed; 267/267 invalid mutations detected; 0/216 false positives on valid traces; 1260/1260 required node completions; 72/72 failure stages; 130/130 MAINTAIN mutations.
- The report explicitly limits this evidence to executable implementation conformance; it does not claim human construct validity or DAG uniqueness.

No benchmark map, DAG, manifest, witness, task, evaluator semantic, frozen model result, Sync/Join extension, or model call was added or changed.

## Evaluator conformance paper synchronization

- Replaced the uncompleted Appendix human-annotation protocol and pending table with `Automatic Evaluator Conformance`.
- Added the fixed 576/555 case accounting and the successful-witness, mutation, metamorphic, truncation, and MAINTAIN results to the Appendix.
- Added `tables/evaluator_conformance.tex`; removed the obsolete `tables/evaluator_validity.tex`.
- Added bounded conformance statements to Abstract, Introduction, Process-aware Evaluation, Discussion/Limitations, and Conclusion.
- Defined the validation target as implementation conformance to explicit environment-state-grounded executable oracles, not subjective human agreement.
- Retained limitations on DAG uniqueness, causal failure attribution, taxonomy completeness, and INFORMATION/SYNCHRONIZE semantics outside Full-72.
- Corrected Introduction's “The release contains” to “The benchmark contains.”

This was paper-only synchronization from the frozen `evaluator_conformance_v1` artifacts. No conformance case or result was regenerated or altered.
