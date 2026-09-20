# FWCollab Standard-52 model matrix status

Status: **Closed-4 frozen and formal execution in progress; open-weight slots pending** (2026-09-20). This document records current selection and interface facts. It does not change the frozen benchmark, prompt, evaluator, horizons, task order, error policy, or concurrency.

## Frozen Closed-4

The author replaced unavailable `gemini-3.1-pro` with exact model `gemini-3.5-flash` before the valid Closed-4 matrix was frozen and before any valid Standard-52 episode began. The earlier fuzzy-routed attempt remains excluded under `artifacts/audits/invalid_closed4_attempt_20260920/`; historical files are not rewritten.

| Slot | Requested ID | Resolved ID | Provider / endpoint | API style | Reasoning | Temperature | Seed | Structured output | Preflight | Status |
|---:|---|---|---|---|---|---:|---|---|---|---|
| 1 | `gpt-5.5` | `gpt-5.5` | local Copilot gateway, `http://127.0.0.1:23333/api/openai` | Responses | `none` | 1.0 | accepted, not guaranteed | pass | pass | frozen, running |
| 2 | `gpt-5-mini` | `gpt-5-mini` | local Copilot gateway, `http://127.0.0.1:23333/api/openai` | Responses | `low` (minimum supported) | 1.0 | accepted, not guaranteed | pass | pass | frozen, running |
| 3 | `gemini-3.7-flash` | `gemini-3.7-flash` | local Copilot gateway, `http://127.0.0.1:23333/api/openai` | Chat Completions | `none` | 1.0 | accepted, not guaranteed | pass | pass | frozen, running |
| 4 | `gemini-3.5-flash` | `gemini-3.5-flash` | local Copilot gateway, `http://127.0.0.1:23333/api/openai` | Chat Completions | `none` | 1.0 | unsupported, omitted | pass | pass after the preregistered model-behavior retry | frozen, running |

Authoritative artifacts:

- Matrix: `eval_private/leaderboard_v1/closed4_model_matrix.json`
- Matrix SHA-256: `934b4eb25c436a661fe995e0b4177614f1ad799ea86b2debebc5adf7420e3cff`
- Passing non-benchmark preflight SHA-256: `dbffe90a8041d2a24d89962b5ab32bb6ff14291cd0a6b898de4428c8431ffd30`
- Standard-52 manifest SHA-256: `b3970fdd31ef2259d477a33801c7442be92df6ed3cd7bb3cb07399eaa21a0716`
- Prompt contract SHA-256: `862349ef54d6991f1b20102ac6936edde93bb7a0be8d1f849e993dd1e67b9831`

## Frozen Final-6 structure

| Slot | Model | Status |
|---:|---|---|
| 1 | `gpt-5.5` | `FROZEN_PREFLIGHT_PASS` |
| 2 | `gpt-5-mini` | `FROZEN_PREFLIGHT_PASS` |
| 3 | `gemini-3.7-flash` | `FROZEN_PREFLIGHT_PASS` |
| 4 | `gemini-3.5-flash` | `FROZEN_PREFLIGHT_PASS` |
| 5 | `OPEN_WEIGHT_A_PENDING` | pending under preregistered policy |
| 6 | `OPEN_WEIGHT_B_PENDING` | pending under preregistered policy |

Slots 5 and 6 must be selected only under `docs/OPEN_WEIGHT_MODEL_SELECTION_POLICY.md`. Closed-model performance cannot be used to select them, and model-specific prompt tuning remains prohibited.

## Superseded candidate

`gemini-3.1-pro` is unavailable from the configured endpoint as an exact catalog ID and is not part of the formal matrix. It must not be treated as an alias for `gemini-3.5-flash`. The replacement is an explicit author decision recorded before matrix freeze.
