# FWCollab Standard-52 preflight checklist

No Standard-52 model call may begin until every box is checked and the model matrix hash is frozen. API checks use only non-benchmark dummy/schema/toy inputs.

## Allowed preflight inputs

Only the following may be sent during future API/interface preflight:

- a trivial dummy text request with no FWCollab content;
- a synthetic request for the frozen action/message JSON schema;
- the fixed toy observation in `eval_private/leaderboard_v1/prompt_contract/`, whose map ID is `SYNTHETIC-PROMPT-CONTRACT-*` and whose content is not derived from any benchmark task.

The following are prohibited: Standard-52 tasks, any Full-72 map or text, C5/Sync/Join content, held-out layouts, witness/DAG/evaluator content, and any transformed or paraphrased benchmark instance. Preflight validates interface behavior only and must not produce or retain a performance score.

## Per-model non-benchmark API preflight

Complete and retain one machine-readable record per candidate before creating `model_matrix.json`:

- [ ] Authentication succeeds without logging a secret
- [ ] Endpoint identity and API style (`responses` or `chat_completions`) match the intended route
- [ ] Requested model ID resolves; immutable resolved model ID is captured when provided
- [ ] One trivial request returns non-empty model text and measured latency
- [ ] Synthetic action-schema request parses under the frozen action/message contract
- [ ] Maximum output parameter accepts 800 tokens without truncating the small synthetic response
- [ ] Reasoning OFF is tested; if unsupported, the documented minimum/low setting and provider behavior are recorded
- [ ] Temperature `1.0` is tested; unsupported parameters are omitted and recorded rather than emulated
- [ ] Requested seed behavior is tested and classified as accepted/rejected/ignored/undocumented; determinism is not assumed
- [ ] Timeout behavior is classified without using a benchmark episode
- [ ] Model-behavior retry classification is verified for malformed/schema-invalid/empty/refusal cases
- [ ] Infrastructure retry classification is verified for synthetic 429/5xx/transport/timeout cases where the harness can inject them locally
- [ ] Systematic failures (auth, unknown model, invalid endpoint, unsupported required API style) stop that model before matrix freeze
- [ ] No Standard-52 or other benchmark content was transmitted

Preflight outputs must record timestamp, endpoint identity without credentials, requested/resolved IDs, parameter support, latency, schema outcome, retry/error classification, and `benchmark_content_used=false`. They must not record credentials or secret-bearing headers.

## Matrix freeze gate

- [ ] Exact immutable model IDs confirmed
- [ ] Providers and endpoints confirmed
- [ ] Reasoning/thinking policy confirmed under the minimum-common-compute rule
- [ ] `eval_private/leaderboard_v1/model_matrix.json` created and its hash frozen
- [x] Prompt-contract source hash verified: `862349ef54d6991f1b20102ac6936edde93bb7a0be8d1f849e993dd1e67b9831`
- [x] Canonical prompt snapshots generated from non-benchmark synthetic observations
- [x] Canonical prompt snapshot hashes frozen and exact-reproduction check passed
- [x] Future leaderboard Core Diagnostic-24 budget = 200 (Phase II remains historical fixed-80)
- [x] Information-12 budget = 30
- [x] Sync-8 budget = 80
- [x] Join-8 budget = 80
- [x] Standard-52 horizon feasibility = 52/52 PASS under fixed 200/30/80/80
- [x] Error-policy local conformance PASS (16/16 synthetic cases)
- [ ] Concurrency frozen
- [x] Standard-52 hash verified against `run_plan_freeze.json`
- [x] Unified evaluator dispatch verified locally (100/100 Benchmark v2 tasks)
- [ ] Non-benchmark API/interface preflight passed for every model
- [ ] Each preflight record declares `benchmark_content_used=false` and has no benchmark task/map identifier
- [ ] The six selected slots and exact IDs match `docs/LEADERBOARD_MODEL_CANDIDATES.md` author decisions
- [ ] Reasoning policy is identical OFF where supported, otherwise documented minimum/low common compute
- [ ] `model_matrix_TEMPLATE.json` has been fully instantiated without placeholders before it is copied to the formal filename
- [x] No benchmark/API calls used in this offline preflight
- [x] Zero benchmark model calls before model-matrix freeze
