# Horizon finding: paper impact assessment

Status: analysis memo only. The manuscript Results, generated tables, figures, frozen Phase II artifacts, benchmark tasks, and evaluators were not modified. Evidence: `artifacts/evaluations/phase2_horizon_sensitivity_v1/` and `artifacts/audits/leaderboard_horizon_feasibility_v1/`.

## Finding and scope

Phase II remains a valid historical fixed-80, matched-condition causal experiment, but its Diagnostic-24 population contains 9 tasks (37.5%) whose relaxed movement lower bound already exceeds 80 rounds. Those tasks cannot attain terminal success under the Phase II horizon, and all six conditions have observed SR 0 on them. Five further tasks are unresolved at 80; ten have successful certificates within 80. The original 24-task estimates are retained as P0, while P1 excludes only the nine proven-infeasible tasks and P2 includes only the ten certified-feasible tasks. P0 exactly reproduces the frozen Phase II estimates and confidence intervals.

This finding does not invalidate paired communication or cross-play comparisons automatically: the same fixed task/horizon cells occur in both sides of every paired contrast. It does constrain absolute SR interpretation and shows that several process-effect intervals are less stable after feasibility filtering.

## Existing statements

| Paper location | Existing statement | Latest evidence | Status | Required treatment after author approval |
|---|---|---|---|---|
| Abstract | Reports Phase II paired communication and cross-play effects | P0 is reproduced exactly; GPT SR, pooled SR, and direct A-vs-B SR conclusions are stable; process gains retain direction but filtered intervals change | **partially correct** | Keep original P0 numbers, add a short fixed-80 feasibility caveat and avoid presenting process-effect significance as uniformly robust |
| Introduction | Similar terminal SR can coexist with different process profiles | P0/P1/P2 retain this descriptive distinction; several filtered process CIs include zero | **correct with caveat** | Retain the high-level claim but state that feasibility filtering attenuates inferential strength |
| Experimental setup | Diagnostic-24, six conditions, three replicates, fixed 80, structurally selected | All protocol facts remain true; selection did not use outcomes, but 9/24 tasks are horizon-infeasible | **correct but incomplete** | Disclose the feasibility partition and distinguish historical Phase II fixed-80 from future leaderboard fixed-200 |
| Results: absolute condition SR | Treats six SRs as performance on Diagnostic-24 | Nine denominator tasks are forced zeros under 80; P2 Feasible-Task SR ranges from 56.7% to 96.7% | **requires caveat** | Keep original SR as the registered P0 estimand; add P1/P2 sensitivity and do not interpret it as success over a uniformly feasible task population |
| Results: GPT communication | No SR/AUC gain; better Clean Handoff and fewer violations | GPT SR is STABLE; Clean Handoff and violation effects are DIRECTIONALLY_STABLE, but P2 intervals touch zero | **partially correct** | Retain direction; qualify the process evidence as attenuated under certified-feasible filtering |
| Results: Gemini communication | Positive completion/AUC/Clean Handoff intervals; SR interval touches zero | All four directions persist, but P2 intervals include zero and magnitudes change | **sensitivity caveat required** | Describe completion/AUC/handoff conclusions as directional in feasibility-aware sensitivity, not uniformly interval-robust |
| Results: pooled cross-play | Small SR point gap, no equivalence claim | P0/P1/P2 SR effects remain small, same direction, and inconclusive | **correct / stable** | Retain with a sentence that feasibility-aware populations do not materially change the interpretation |
| Results: direct A-vs-B | SR effect zero; process intervals inconclusive | Direct SR remains exactly zero in P0/P1/P2 | **correct / stable for SR** | Retain; do not broaden beyond the analyzed SR stability without displaying process sensitivity |
| Discussion: DAG depth | Strong marginal negative association, explicitly noncausal due collinearity | Depth-completion rho is roughly -0.90 to -0.95 on 23 tasks, but partial rank rho controlling movement lower bound ranges -0.27 to +0.11; depth correlates 0.994 with lower bound | **materially confounded** | Rewrite the discussion: most marginal depth association is compatible with execution-length confounding; retain only exploratory, noncausal language |
| Diagnostic-24 description | Structurally selected subset retaining audited coverage | Membership and selection facts remain unchanged | **correct but incomplete** | Add that its historical 80-round evaluation horizon is not feasible for every selected task; do not imply outcome-based reselection |
| Conclusion | Summarizes model-dependent communication and process effects | Directions persist, but several feasible-only intervals touch zero | **partially correct** | Keep bounded conclusion, add sensitivity qualifier, and avoid unqualified positive-interval language |

## Stability summary

Under the analysis rule frozen in `scripts/analyze_phase2_horizon_sensitivity.py` before filtered estimates were inspected:

- **STABLE:** GPT communication effect on SR; pooled self-play-minus-cross-play SR; direct Cross-play A-minus-B SR.
- **DIRECTIONALLY_STABLE:** GPT communication effects on Clean Handoff and violations; Gemini communication effects on SR, DAG Completion, Progress AUC, and Clean Handoff.
- **SENSITIVE:** none of the nine prespecified checks.
- **NOT_ESTIMABLE:** none of the nine checks, although P2 has only ten task clusters and must be read cautiously.

The distinction matters: “directionally stable” does not preserve the original interval interpretation. In P2, the GPT Clean Handoff and violation intervals touch zero; Gemini Completion, AUC, and Clean Handoff intervals include zero.

## Required disclosure in the formal paper

The formal draft should disclose both facts below once the author approves paper edits:

1. Phase II used the preregistered fixed-80 horizon, under which 9/24 Diagnostic tasks are provably horizon-infeasible; original P0 estimates remain the registered results.
2. A feasibility-aware sensitivity analysis over P1 (15 tasks) and P2 (10 tasks) preserves all nine checked effect directions, but attenuates several process-metric intervals.

The paper should report P2 as descriptive **Feasible-Task SR**, never as a replacement for P0. The future Standard-52 leaderboard uses a fixed 200-round Core horizon and is not directly comparable to Phase II in absolute SR.

## DAG-complexity discussion

The existing depth result needs material revision. Across conditions, marginal depth-versus-completion Spearman rho over the 23 tasks with finite relaxed lower bounds ranges from -0.896 to -0.952. Within all ten certified-feasible tasks it attenuates to -0.415 to -0.810. More decisively, partial rank association after controlling the optimistic movement lower bound ranges from -0.270 to +0.106. DAG depth itself correlates 0.994 with the movement lower bound and 0.905 with best certified rounds.

Therefore the current rho near -0.95 cannot be presented as an independent dependency-complexity effect. It is consistent with a strong execution-length confound. The sample is too small for a causal decomposition.

## Minimal future paper edits

1. Add the fixed-80 feasibility caveat and P0/P1/P2 sensitivity summary beside the Phase II absolute SR results.
2. Qualify GPT/Gemini process-effect claims: directions persist, but several P2 intervals include zero.
3. Replace the current DAG-depth interpretation with the lower-bound-controlled result and explicit execution-length confounding.

No manuscript edit is made by this memo.
