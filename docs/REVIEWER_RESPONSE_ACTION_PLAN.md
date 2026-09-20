# FWCollab reviewer-response action plan

Status: updated after the frozen Phase II reviewer-response analyses and the Standard-52 privileged planning reference. No manuscript text, benchmark task, evaluator, prompt, Phase II trace, or frozen result was changed. All work is offline; model/API calls: **0**.

| Concern | Analysis performed | Result | Status | Paper sections to update later |
|---|---|---|---|---|
| W2: communication necessity | Frozen Phase II paired Self-play/NoComm effects, Information-12, feasibility sensitivity, direct interaction test, and the frozen Standard-52 protocol | Communication effects differ by metric and family; Information-12 isolates explicit information transfer, while other tracks include distinct dependency semantics. This supports bounded communication/dependency conclusions, not a universal communication benefit. | **PARTIALLY RESOLVED** | Abstract; Introduction; Phase II Results; Standard-52 design; Discussion |
| W3: empirical breadth | Existing GPT/Gemini families and completed Information/Sync/Join benchmark coverage reviewed; no new model run authorized | Structural breadth is stronger, but the formal Phase II causal experiment still covers only two model families. | **UNRESOLVED** | Limitations; Experimental Setup; future leaderboard table |
| W4: direct Model x Communication interaction | `artifacts/evaluations/model_communication_interaction_v1/` | P0 interaction CIs exclude zero for SR, Completion, AUC, and violations; Clean Handoff does not. P1 retains the same four; P2 retains SR, Completion, and violations but not AUC or Clean Handoff. | **RESOLVED / STRONGLY ADDRESSED** | Phase II statistical methods and interaction-results paragraph/table |
| W5: horizon / upper bound | Frozen P0/P1/P2 sensitivity plus `artifacts/evaluations/privileged_oracle_standard52_v1/` | Historical Phase II fixed-80 includes nine provably infeasible tasks and is caveated. Under the future frozen 200/30/80/80 horizons, the privileged deterministic joint planner reaches 52/52 success, 52/52 replay validity, and 52/52 unified evaluator validity. This establishes attainability, not optimality or fair-agent performance. | **SUBSTANTIALLY ADDRESSED** | Experimental Setup; absolute SR Results; oracle-reference paragraph/table; sensitivity appendix; Limitations |
| W6: DAG construct robustness | Original/Coarse/Refined measurement decompositions on all 432 traces | Six-condition and GPT-vs-Gemini rankings are stable; all Comm/NoComm effect signs are stable; correlations are high and declared-stage failure localization agrees 100%. Absolute values remain granularity-dependent. | **PARTIALLY RESOLVED** | Executable DAG evaluation; robustness appendix; Limitations |
| W7: contraction reproducibility | `docs/CROSS_AGENT_CONTRACTION_SPEC.md` plus two native-ID worked examples and executable pseudocode | The supporter→environment→traveler contraction, predicates, no-bypass evidence, and ENABLE/MAINTAIN rule are now explicit without changing 367/216/151. | **RESOLVED** | Benchmark Method; dependency taxonomy; appendix worked examples |
| W9: message mechanism | Structured-message-only analysis on 144 Self-play episodes | GPT sends far more messages and repeats confirmations much more often, with longer READY/HOLDING→partner movement latency; it does not have a higher post-message WAIT probability. The requested mechanism-level description is now available, with an explicit noncausal boundary. | **RESOLVED / STRONGLY ADDRESSED** | Process Results; communication analysis; Limitations |
| W10: readability | No manuscript edit authorized this turn | New reports and contraction examples provide source material, but exposition/figures still require an editorial pass. | **UNRESOLVED** | Abstract; Method overview; figure captions; appendix organization |
| Diagnostic selection bias | Full-72 vs Diagnostic-24 structural/length audit | No systematic difficulty enrichment was detected: depth, size, path lower bound, certificates, and witness length are close to Full-72. It remains an intentionally enriched non-random diagnostic set. | **RESOLVED** | Diagnostic-24 selection paragraph; Limitations |
| Held-out Layout-12 | Freeze verification, 72/72 replay, evaluator recomputation, exact 72-key pairing to seen Phase II references | A formal paired fixed-DAG spatial-layout result is available. Most CIs include zero; Gemini AUC shows a small seen-layout advantage. Claim scope remains controlled layout transfer only. | **RESOLVED** | Generalization Results; Experimental Setup; Limitations |

## Currently resolved

- W4 direct interaction inference.
- W5 horizon disclosure, sensitivity, and 52/52 privileged attainable reference (substantially addressed).
- W7 contraction reproducibility.
- W9 structured message mechanism (strongly addressed, descriptive rather than causal).
- Diagnostic-24 structural difficulty audit.
- Held-out Layout-12 formal replay-authenticated analysis.

## Currently partial

- W2: communication is now characterized by paired, interaction, LOTO, and message analyses, but necessity is bounded to the tested protocols/families.
- W6: decomposition robustness is strong for rankings/signs/localization, but it is not human construct validation.

## Still requires new model runs

- W3 broader model-family leaderboard evidence. No such run was performed in this analysis cycle.

## Still requires writing/release work

- W10 editorial/readability pass.
- Integrate the frozen horizon caveat, interaction table, LOTO counts, selection-bias result, contraction definition, decomposition sensitivity, message behavior, and held-out results into the manuscript only after author approval.
- Publish machine-readable artifacts and exact frozen hashes with the submission package.
