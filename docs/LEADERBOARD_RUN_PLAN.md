# FWCollab Benchmark v2: Standard-52 leaderboard run plan

Status: **protocol frozen; model matrix pending; not executed**. No task, map, DAG, witness, evaluator, prompt, prior result, or model/API call is changed by this document.

This is the authority for future Standard-52 execution. It supersedes two Standard-profile details in `LEADERBOARD_PROTOCOL_V1.md`: Core Diagnostic-24 uses a fixed 200-round horizon, and replicate 1 is formal from its first episode. It does not alter Benchmark v2 records, historical Full-72 dynamic budgets, or the historical Phase II fixed-80 causal experiment.

## 1. Frozen population and order

Source: `eval_private/benchmark_v2/manifest.json`, SHA-256 `1173b4f4e574aa50bd31f82306da0e76f170130d5077347bfb00773f728e383c`. Machine-readable list: `eval_private/leaderboard_v1/standard_52_run_manifest.json`. Every model and replicate uses this order unchanged.

| Positions | Track | Ordered IDs |
|---:|---|---|
| 1--24 | Core Diagnostic-24 | `V4-L1-001`, `V4-L1-002`, `V4-L2-008`, `V4-L2-006`, `V4-L2-007`, `V4-L3-010`, `V4-L3-004`, `V4-L3-009`, `V4-L4-009`, `V4-L4-012`, `V4-L4-005`, `V4-L4-008`, `V4-L5-011`, `V4-L5-004`, `V4-L5-006`, `V4-L5-005`, `V4-L6-010`, `V4-L6-007`, `V4-L6-009`, `V4-L6-008`, `V4-L7-001`, `V4-L7-009`, `V4-L7-003`, `V4-L7-002` |
| 25--36 | Information-12 | `C5-001` through `C5-012`, numeric order |
| 37--44 | Sync-8 | `SYNC-01` through `SYNC-08`, numeric order |
| 45--52 | Join-8 | `JOIN-01` through `JOIN-08`, numeric order |

The non-numeric Core order is intentional. An episode key is `(exact_model_identifier, replicate, task_id)`; duplicate completed keys are invalid.

## 2. Unified leaderboard condition

- Homogeneous self-play: the same exact model/version controls F and W through separate policy instances.
- Contexts and histories remain independent. Private reasoning, raw output, and role-local history are never shared.
- Only each track's official public communication channel crosses roles.
- `planning_rounds=0`; no preliminary or hidden planning calls.
- No reference DAG, witness, task answer/label, dependency annotation, evaluator state, failure frontier, or evaluator feedback enters a model context.
- `coordination_mode=emergent`; context limits and task-to-task clearing follow the frozen protocol.

## 3. Track protocol and fixed horizons

All tracks use simultaneous unit actions and one-round-delayed structured messages (`INFO`, `READY`, `HOLDING`, `CROSSED`, `RELEASE`, `BLOCKED`, or `null`). They do not share an artificially identical observation boundary.

| Track | Observation | Public/private boundary | Budget | Communication | Evaluator |
|---|---|---|---:|---|---|
| Core Diagnostic-24 | `fwcollab.agent_observation.v1` | Shared public world; independent role histories | **200 fixed** | Official delayed public structured channel | `collaboration_evaluator_v1` |
| Information-12 | `fwcollab.c5_agent_observation.v1` | Complementary role-private DTO; authoritative state private to replay/evaluation | **30 fixed** | Same delayed channel; information transfer is visible only through it | `information_evaluator_v1` |
| Sync-8 | `fwcollab.agent_observation.v1` | Shared public world; temporal reference metadata private | **80 fixed** | Official delayed public structured channel | `extension_evaluator_v1` |
| Join-8 | `fwcollab.agent_observation.v1` | Shared public world; branch/join reference metadata private | **80 fixed** | Official delayed public structured channel | `extension_evaluator_v1` |

Core's fixed 200-round horizon covers all 24 existing successful certificates (maximum 164), prevents the observation clock from revealing task-specific witness length, and removes a task-varying horizon confound. It is a future Standard-52 run-time override only; source manifests, historical Full-72 budgets, and Phase II's fixed-80 protocol/results remain unchanged. Because the horizons differ, absolute Standard-52 and Phase II success rates must not be compared directly. Evaluation dispatch uses `evaluate_fwcollab_task` and retains the lower evaluator return under `raw_evaluation`.

## 4. Fairness and prompt contract

| Field | Frozen setting |
|---|---|
| Temperature | `1.0`; if unsupported, omit and record `unsupported` rather than emulate it |
| Reasoning/thinking | `NEEDS_AUTHOR_CONFIRMATION`; freeze before the model matrix under the minimum-common-compute rule |
| Maximum output | 800 output tokens |
| Model-behavior retry | At most one automatic retry per role decision |
| Infrastructure retry | One pre-registered retry; any provider-mandated backoff is logged |
| Request timeout | 120 seconds |
| Seeds | Replicates 1/2/3 request `2026091801`, `2026091802`, `2026091803`; unsupported seeds are disclosed |
| Prompt | Identical semantic content within track/role; no model-specific wording or tuning |
| Action/message schema | Same authoritative action and structured-message contract |
| Planning | Zero extra planning rounds |

Reasoning fairness uses the lowest common expressible test-time compute: prefer off if every selected model supports it; otherwise use each provider's documented minimal/low setting. High for one model while another is off is prohibited. All provider exceptions must be recorded before the model matrix is frozen. This document does not select the value.

The existing prompt is not modified. Its frozen contract is the SHA-256 of canonical JSON over ordered path/hash pairs for `agents.py`, `rules.py`, `c5.py`, and `runner.py`: `862349ef54d6991f1b20102ac6936edde93bb7a0be8d1f849e993dd1e67b9831`. Component hashes are in the run manifest and freeze.

## 5. Model behavior versus infrastructure failure

The formal harness classifies a failed response before deciding whether the episode is scored.

### A. Model/interface behavior: scored

| Event | Retry | Exhausted handling |
|---|---|---|
| Malformed output | At most once | Record `malformed_output`; substitute `WAIT`; keep episode scored |
| Schema-invalid output | At most once | Record `schema_invalid_output`; substitute `WAIT`; keep episode scored |
| Empty model response/no model text | At most once | Record `empty_model_response`; substitute `WAIT`; keep episode scored |
| Explicit model safety refusal | At most once under the same frozen rule | Record `safety_refusal`; substitute `WAIT`; keep episode scored |

These measure whether the model/interface produces a usable action. No human correction is allowed.

### B. Infrastructure/provider failure: not scored

HTTP 429, HTTP 5xx, network/transport errors, service-attributable request timeout, connection reset, unavailable gateway, and transient provider outage receive the one pre-registered retry. If still failing:

- mark the key `infrastructure_error`;
- do not synthesize a scored WAIT trajectory or include it in performance statistics;
- retain the same key as missing and rerun only that key after recovery, from the initial state;
- never replace an already completed, parseable, replay-valid episode.

Record infrastructure error count, provider error rate (infrastructure failures/provider requests), retries, rerun keys, and normalized rerun reasons. Infrastructure rerun is **integrity recovery**, not outcome-dependent rerunning.

Authentication failure, unknown model, unsupported API parameter, or invalid endpoint is systematic: stop that model, mark `interface_unavailable`, and do not convert remaining tasks into WAIT. The existing generic adapter does not expose every required distinction; the formal leaderboard harness must pass non-benchmark error-policy conformance before matrix freeze, without changing benchmark, evaluator, or prompt semantics.

## 6. Three-stage freeze and execution workflow

### Stage A: non-benchmark API preflight

Use only trivial dummy requests, synthetic schema tests, or non-benchmark toy observations. Verify exact immutable identifier, route, endpoint, API style, authentication, structured output, temperature, reasoning/thinking, maximum output, seed, timeout, retry, and error classification. Standard-52 tasks or derivatives are prohibited, and preflight produces no benchmark performance data.

### Stage B: freeze model matrix

After all selected models pass Stage A, create `eval_private/leaderboard_v1/model_matrix.json` and freeze its hash. It fixes identifiers, providers, endpoint identities without secrets, API styles, supported parameters/exceptions, reasoning policy, temperature, output budget, seed behavior, prompt-contract hash, concurrency, retry/error policy, and credentials-present status.

Current status is `NEEDS_AUTHOR_CONFIRMATION`; the file is intentionally absent. A fundamentally unavailable model may be replaced only before this hash exists. After freeze, replacement due to low score, latency, weak track performance, or unfavorable replicate 1 is prohibited.

### Stage C: formal replicate 1

Replicate 1 starts only after protocol, prompt, model matrix, error policy, and concurrency are frozen. It is formal from its first Standard-52 episode. The only permitted smoke is Stage A's non-benchmark preflight.

For each model, replicate 1 passes integrity only when:

- 52 scored episode keys are present exactly once after any registered infrastructure recovery;
- unrecoverable `runner_error` count is zero;
- all traces parse and replay deterministically;
- all tasks dispatch through the unified evaluator and validate against `fwcollab.unified_evaluation.v1`;
- requested/resolved model IDs, parameter exceptions, infrastructure errors, retries, and recovery keys are recorded.

Resume may fill only an uncompleted or registered `infrastructure_error` key. It cannot replace a completed valid episode. After integrity passes, replicate 1 remains in the paper result and replicates 2/3 run unchanged. A runner defect stops the matrix and must never trigger selective outcome-dependent reruns.

## 7. Publication metrics and presentation

Universal metrics are reported per track: Success Rate, mean DAG Completion, and mean Progress AUC. Track diagnostics are:

| Track | Diagnostics |
|---|---|
| Core | Clean Handoff; coordination/dependency violation count and rate with denominator |
| Information | Information dependency success; clean information handoff |
| Sync | Synchronization satisfaction; overlap/temporal violations |
| Join | Per-role/both-branch completion; join satisfaction |

The primary display order is Model; Core, Information, Sync, Join SR; Core, Information, Sync, Join DAG Completion; then track-specific diagnostics. MacroSR belongs only in the final secondary column or a secondary table. No arbitrary overall collaboration score is created.

## 8. Aggregation and MacroSR interpretation

First aggregate replicates within task. A track SR is the unweighted mean over its tasks: denominators are 24, 12, 8, and 8.

$$
\mathrm{MacroSR}_{m}=\frac{1}{4}\sum_{k\in\{\mathrm{Core,Info,Sync,Join}\}}\mathrm{SR}_{m,k}.
$$

Each track has 25% descriptive weight. MacroSR is a track-macro average, not a 52-task micro average, not "overall collaboration ability", and not the sole ranking criterion. It must not support a claim that one model is globally superior at collaboration. All four track SRs remain primary. N/A is excluded from eligible denominators and never set to zero.

## 9. Formal statistics

After all three replicates pass integrity:

1. aggregate replicates within each task;
2. use task, not replicate, as the sampling unit;
3. construct 95% task-level bootstrap CIs with 10,000 resamples;
4. resample tasks inside each track for track-specific CIs;
5. for the descriptive Standard MacroSR CI, independently resample tasks inside all four tracks, recompute track means, then macro-average;
6. use aligned task/replicate cells and common bootstrap indices for paired model comparisons.

Three replicates are repeated measurements, not independent tasks. Infrastructure-recovery keys must be complete before analysis; no missing data are imputed.

## 10. Cost and call envelope

Each model contributes 52 episodes in replicate 1 and 156 over three replicates. The fixed maximum environment rounds are

$$
24\times200+12\times30+8\times80+8\times80=6{,}440.
$$

Two role decisions per round give an absolute ceiling of 12,880 logical role-decision calls per model/replicate and 38,640 across three replicates. Retries can add provider requests but do not add logical role decisions and are reported separately. No calibrated token/call distribution exists for the new fixed-200 protocol, so only exact ceilings are reported; no dollar estimate is made.

| Models | Replicate-1 episodes | Three-replicate episodes | Environment-round ceiling, r1 / final | Logical-call ceiling, r1 / final | Suggested concurrency |
|---:|---:|---:|---:|---:|---|
| 6 | 312 | 936 | **38,640 / 115,920** | **77,280 / 231,840** | 2--4 episodes/provider; global cap 8 |
| 7 | 364 | 1,092 | **45,080 / 135,240** | **90,160 / 270,480** | 2--4 episodes/provider; global cap 8 |
| 8 | 416 | 1,248 | **51,520 / 154,560** | **103,040 / 309,120** | 2--4 episodes/provider; global cap 8 |

These are conservative ceilings, not expected usage or wall-time estimates. Freeze concurrency before replicate 1 and retain it.

## 11. Candidate evidence, not the formal matrix

No endpoint was contacted. A local OpenAI-compatible gateway is documented but was inactive during the last audit. Presence of credential material is reported only as yes/no.

| Requested identifier | Route | Open/closed | Class | Current endpoint | Credentials | Compatibility evidence | Status |
|---|---|---|---|---|---|---|---|
| `gpt-5.5` (prior resolved `gpt-5.5-2026-04-23`) | Local Copilot/OpenAI Responses | closed | frontier | inactive | yes | Prior frozen runs | Immutable ID `NEEDS_AUTHOR_CONFIRMATION` |
| `gemini-3.7-flash` | Local Copilot/Chat Completions | closed | frontier/fast | inactive | yes | Prior frozen runs | Thinking semantics `NEEDS_AUTHOR_CONFIRMATION` |
| `gpt-5-mini` | Local Copilot/OpenAI-compatible | closed | medium/compact | inactive | yes | Prior current-protocol run | Preflight required |
| `gpt-5.4-mini` | Local Copilot/OpenAI-compatible | closed | compact | inactive | yes | Historical earlier-protocol run | High risk; preflight required |
| Strong open-weight A/B, other medium, small baseline | Author-provided endpoints | pending | pending | absent | no | None locally | Exact IDs `NEEDS_AUTHOR_CONFIRMATION` |

This table has no selection authority and is not `model_matrix.json`. Historical traces do not prove current endpoint availability.

## 12. Author decisions before any benchmark run

- Select 6, 7, or 8 models and complete Stage A for each.
- Confirm every immutable model ID, provider route, endpoint, and API style, including open-weight candidates.
- Choose reasoning off or documented minimal/low under the common-compute rule.
- Confirm replicate 1 remains formal after integrity passes.
- Freeze concurrency, rate limits, retry/backoff settings, and API/token budget.
- Decide whether aliases are permitted or immutable resolved versions are mandatory.
- Approve and hash `model_matrix.json` only after all preceding decisions pass preflight.
