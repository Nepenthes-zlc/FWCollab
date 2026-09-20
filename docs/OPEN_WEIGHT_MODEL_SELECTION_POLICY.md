# FWCollab open-weight model selection preregistration

Status: **preregistered before any Closed-4 Standard-52 benchmark episode**. The policy fixes how the two pending open-weight slots will be selected; it does not select their exact model identifiers.

## Frozen slots

- Slot 5: **Strong open-weight A** (`OPEN_WEIGHT_A_PENDING`).
- Slot 6: **Strong open-weight B** (`OPEN_WEIGHT_B_PENDING`).

The exact models, immutable weight revisions, inference endpoints, and serving configurations remain `NEEDS_AUTHOR_CONFIRMATION`. The first four Closed-4 slots and the frozen Standard-52 protocol cannot be changed when these two slots are filled.

## Admissible selection criteria

Both future selections must satisfy all of the following without consulting Closed-4 benchmark outcomes:

- weights are open-weight and openly downloadable under a documented license;
- contemporary, strong instruction-following capability;
- deployable through an OpenAI-compatible or vLLM inference interface;
- stable compatibility with the frozen FWCollab action/message schema under a non-benchmark preflight;
- context length sufficient for the frozen Standard-52 observation and interaction protocol;
- model size and inference cost fit the author's available server resources;
- the two selections should come from different model families when feasible.

Operational compatibility, context limits, license, hardware fit, and non-benchmark schema conformance may be used for selection. Standard-52, Full-72, Information-12, Sync-8, Join-8, or any benchmark-derived performance must not be used.

## Prohibited selection behavior

- Selecting models after inspecting Closed-4 results in order to manufacture or alter a ranking.
- Selecting a model for an observed advantage on a particular FWCollab track.
- Running candidate open-weight models on benchmark content as a selection screen.
- Model-specific prompt tuning, schema relaxation, extra planning rounds, privileged state, or unequal test-time compute.
- Replacing any of the four frozen closed-model slots when adding the two open-weight slots.

## Reasoning and protocol constraint

The preferred common policy is reasoning/thinking **OFF**. If a selected endpoint cannot disable reasoning, it must use the documented minimum/low setting and record the exception before the final six-model matrix is frozen. All other prompt, action schema, retry/error, task-order, horizon, evaluator, and replicate rules remain those of the frozen Standard-52 run plan.

## Freeze record

The SHA-256 of this file is recorded in `eval_private/leaderboard_v1/final6_matrix_plan.json`. Any content change requires a new policy version and must occur without reference to Closed-4 benchmark performance.
