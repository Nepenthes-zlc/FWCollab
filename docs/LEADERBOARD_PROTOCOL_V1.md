# FWCollab official leaderboard protocol v1

Status: frozen design for FWCollab Benchmark v2. No model result is part of this document. Benchmark v2 contains four separately reported tracks and is not called Full-100.

## Evaluation profiles

| Profile | Core | Information | Sync | Join | Tasks/replicate | Intended use |
|---|---:|---:|---:|---:|---:|---|
| Standard Track | Diagnostic-24 | 12 | 8 | 8 | 52 | recommended main-paper leaderboard |
| Full Track | Full-72 | 12 | 8 | 8 | 100 | higher-cost confirmatory evaluation |

Standard is the recommended primary leaderboard: it preserves the structure-only Diagnostic-24 selection and includes every extension task while using 48 fewer Core episodes per replicate. Full is more representative of the complete Core difficulty distribution but costs about 1.92 times as many episodes. Standard results must never be labeled Full results.

## Common execution protocol

- Pairing: homogeneous self-play. The same model/version controls F and W, but the two roles have independent contexts and histories. No hidden state or current-round response is shared.
- Action space: one `UP`, `DOWN`, `LEFT`, or `RIGHT` unit move (`steps=1`), or `WAIT` (`steps=0`) per agent per round. Actions are submitted against the same frozen observation and settled simultaneously.
- Communication: enabled. A public structured `INFO/READY/HOLDING/CROSSED/RELEASE/BLOCKED` message is delivered one round later and does not consume movement. The leaderboard uses `coordination_mode=emergent`, so no fixed handshake is prompted.
- Context: each role receives its own most recent eight history entries. Public delivered-message history contains at most eight messages. F and W contexts must not be concatenated or reused across tasks. No reference DAG, witness, task label, answer, or evaluator feedback enters either context.
- Sampling: temperature `1.0`. Replicate request seeds are `2026091801`, `2026091802`, and `2026091803`; providers may ignore seeds, so replicates are not claimed bit-reproducible.
- Requests: 120-second timeout, 800 output tokens, low reasoning effort where the adapter/provider supports it, and at most one retry after an invalid/provider response. A failed response after retries becomes a recorded `WAIT`; provider and formatting errors remain separately counted.
- Planning: `planning_rounds=0`. There is no hidden planning call before movement.
- Termination: stop immediately at `team_success` or `team_failure`; otherwise terminate at the track budget as `timeout`. An orchestration failure outside the episode becomes `runner_error`, never success.
- Accounting: record provider requests (including retries), logical model calls, output/available input token counts when returned by the provider, model errors, wall time, rounds, and messages for every episode. Missing provider token accounting is `null`, never zero.

## Track-specific observation and budget

| Track | Observation supplied to each role | Public/private boundary | Round budget | Evaluator |
|---|---|---|---:|---|
| Core-72 | `fwcollab.agent_observation.v1` | shared public world; independent histories | `max(80, ceil(1.5 × frozen witness rounds))` per task | frozen collaboration evaluator v1 |
| Information-12 | `fwcollab.c5_agent_observation.v1` | complementary role-private DTO; authoritative world retained only for replay/evaluation | 30 | frozen C5 information evaluator v1 |
| Sync-8 | `fwcollab.agent_observation.v1` | shared public world; temporal reference metadata private | 80 | opt-in extension evaluator v1 |
| Join-8 | `fwcollab.agent_observation.v1` | shared public world; reference join metadata private | 80 | opt-in extension evaluator v1 |

The Core budget is intentionally not forced to 80: frozen Full-72 witnesses range beyond 80 rounds. Information retains its pre-registered 30-round identification budget. Sync/Join maps have 30–36-round witnesses; 80 rounds provides the same minimum interaction horizon as Core without altering their task semantics.

## Evaluator dispatch and output

`evaluate_fwcollab_task(task_record, trace)` dispatches exclusively from frozen suite metadata:

- Core → `evaluate_collaboration_trace`;
- Information → `evaluate_c5_trace`;
- Sync/Join → `evaluate_extension_trace`.

The return is `fwcollab.unified_evaluation.v1`. The exact bottom-level return is retained under `raw_evaluation`; dispatch never mutates it. Track-inapplicable objects are JSON `null`, not fabricated zeros.

## Leaderboard metrics

Universal metrics are reported per track: Success Rate, mean DAG Completion, mean Progress AUC, and mean/median Rounds-to-Success over successful episodes only. Macro SR and macro DAG Completion may average the four track-level means with equal track weight. There is no single overall collaboration score.

Track-specific metrics are not averaged across tracks:

- Core: dependency violations, handoff success/clean rate, maintain opportunities, harmful regressions.
- Information: information dependency success, clean information handoff, wrong-controller-before-answer, information failure stage.
- Sync: synchronization success, overlap satisfaction, required/maximum overlap, synchronization violations.
- Join: F/W branch completion, join satisfaction, join violations. Order invariance is a frozen task-level conformance property, not inferred from one model episode.

For three replicates, average replicates within each task first; tasks are the statistical unit. Report task-level bootstrap confidence intervals when comparing models. N/A observations remain excluded with their denominators shown.

## Replicate workflow and cost envelope

1. Run one Standard replicate as a smoke test only. Validate trace schema, replay, dispatch, model identity, and accounting before any expansion.
2. After author approval, run three total aligned replicates for the final Standard leaderboard. The smoke replicate may count as replicate 1 only if it used the exact frozen protocol and passed integrity checks.
3. Run the Full profile with three replicates only for selected models if budget permits; never mix Standard and Full denominators.

| Profile | 1 replicate/model | 3 replicates/model | 6 models at 3 reps | 8 models at 3 reps |
|---|---:|---:|---:|---:|
| Standard | 52 episodes | 156 episodes | 936 episodes | 1,248 episodes |
| Full | 100 episodes | 300 episodes | 1,800 episodes | 2,400 episodes |

The frozen budgets imply worst-case logical calls of 14,044 per Standard replicate/model and 36,028 per Full replicate/model because each round calls two role agents. With one retry, provider requests can be at most twice those figures. Actual cost depends on early termination, provider pricing, prompt caching, token usage, and retry rate; therefore this protocol assigns relative cost categories rather than unsupported currency estimates.

## Candidate model matrix requiring author confirmation

Specific versions must be pinned immediately before execution. This table defines coverage roles, not final model IDs.

| Candidate slot | Provider | Open/closed | Size class | Reason for inclusion | Cost category | Availability |
|---|---|---|---|---|---|---|
| Frontier closed A | OpenAI-compatible provider | closed | frontier | continuity with the strongest prior GPT-family condition | high | endpoint/version confirmation required |
| Frontier closed B | Google-compatible provider | closed | frontier | independent frontier family and prior Gemini-family continuity | high | endpoint/version confirmation required |
| Strong open-weight A | author-selected hosted/local provider | open-weight | large | leading reasoning-capable open-weight comparison | medium/high | weights and inference endpoint required |
| Strong open-weight B | author-selected hosted/local provider | open-weight | large | second architecture/license family | medium/high | weights and inference endpoint required |
| Medium A | author-selected provider | open or closed | medium | cost-capability operating point | medium | candidate/version required |
| Medium B | author-selected provider | open or closed | medium | second mid-tier family; optional for a six-model run | medium | candidate/version required |
| Small baseline | author-selected provider/local | preferably open-weight | small | weak/cost-efficient lower anchor | low | candidate/version required |
| Optional compact baseline | author-selected provider/local | open or closed | compact | eighth slot for scaling trend | low | optional |

Before model execution the author must confirm: six versus eight slots; exact immutable model versions and endpoints/API styles; Standard-only versus any Full runs; whether the exact smoke replicate is retained; budget/currency ceiling and concurrency; provider token-accounting availability; and any provider that cannot honor temperature or requested seed.
