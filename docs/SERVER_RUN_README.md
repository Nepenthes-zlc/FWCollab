# FWCollab Open-weight Standard-52 server runner

This bundle runs preregistered open-weight Slots 5/6 against the frozen Standard-52 protocol. Witnesses, DAGs, bindings, and evaluator metadata are private runtime assets and are never included in model prompts.

## 1. Extract and create the environment

```bash
tar -xzf fwcollab_openweight_runner_v1.tar.gz
cd fwcollab_openweight_runner_v1
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH="$PWD/src"
python scripts/selfcheck_openweight_bundle.py
```

## 2. Start the endpoint

Start the chosen vLLM/OpenAI-compatible server separately. The exact command depends on the preregistered model and server hardware. It must expose either Chat Completions or Responses and must not apply an additional hidden system prompt. Do not put a key in a command argument or repository file.

```bash
export FWCOLLAB_API_KEY='YOUR_LOCAL_ENDPOINT_KEY_IF_REQUIRED'
```

## 3. Run the non-benchmark preflight

This sends only a synthetic schema request and does not use any benchmark task.

```bash
python scripts/preflight_openweight_model.py \
  --model-id MODEL_ID \
  --base-url http://127.0.0.1:8000/v1 \
  --api-key-env FWCOLLAB_API_KEY \
  --api-style chat_completions
```

Record the resolved model ID, exact weights/revision, vLLM version, parameter support, endpoint identity, and selected reasoning setting. Freeze the selected entry after preflight (repeat the pair for Slot 6 when both are ready):

```bash
python scripts/freeze_openweight_matrix.py \
  --slot 5 \
  --preflight eval_private/leaderboard_v1/preflight/MODEL_ID.json \
  --concurrency 4
```

## 4. Run formal replicate 1

The CLI values must exactly match the frozen open-weight matrix. Replicate 1 is formal data from its first episode.

```bash
python scripts/run_standard52_openweight.py \
  --model-id MODEL_ID \
  --base-url http://127.0.0.1:8000/v1 \
  --api-key-env FWCOLLAB_API_KEY \
  --replicate 1 \
  --output-dir artifacts/runs/leaderboard_v1 \
  --concurrency 4 \
  --reasoning-setting off
```

The runner resumes by episode key. It preserves completed replay-valid traces, reruns only absent or `infrastructure_error` keys, writes deterministic directories, and safely keeps completed work after Ctrl-C. Never rerun an episode because of its score.

## 5. Check integrity

```bash
python scripts/check_standard52_integrity.py \
  --run-dir artifacts/runs/leaderboard_v1/MODEL_ID/rep1 \
  --output-dir artifacts/evaluations/leaderboard_v1/MODEL_ID/rep1
```

Proceed only if the report says 52/52, deterministic replay valid, unified evaluator valid, and zero unresolved runner/infrastructure errors.

## 6. Run replicates 2 and 3

Repeat the same run and integrity commands with `--replicate 2`/`rep2`, then `--replicate 3`/`rep3`. Keep model, endpoint, server configuration, reasoning, temperature, output budget, prompt, evaluator, and concurrency unchanged.

## 7. Package results for return

```bash
python scripts/package_openweight_results.py \
  --model-id MODEL_ID \
  --run-dir artifacts/runs/leaderboard_v1/MODEL_ID \
  --evaluation-dir artifacts/evaluations/leaderboard_v1/MODEL_ID \
  --output-dir dist/results
```

The archive contains traces, run/model metadata, evaluations, integrity and error accounting, logs, and hashes. It excludes model weights and credentials. Before transfer, inspect the generated manifest and ensure no `.env`, API key, Hugging Face token, or other secret was placed under the run/evaluation directories.
