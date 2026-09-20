# FWCollab Symbol Benchmark

森林冰火人风格的双智能体合作规划 benchmark。项目只采用符号地图、离散规则更新和独立 HTML Canvas 渲染，不实现连续物理。

## 当前数据

- `eval_private/spatial_curriculum_full_v4/`：当前 L1–L7 共72张全机制空间任务，覆盖 M01–M30。
- `eval_private/dag_curriculum/`：72个私有参考状态 DAG，用于比较 agent 提交的候选计划。
- `maps/symbolic/`：S01–S24 公开开发与回归地图。
- `artifacts/runs/spatial_curriculum_full_v4_model_20260913/`：保留的72任务真实双模型实验。
- `artifacts/symbolic/spatial_curriculum_full_v4_gallery.html`：72张地图初始状态总览。

旧连续物理实现、旧 V3 数据集、12图中间课程、部分落图原型和旧实验回放已经移除。`src/fwcollab/symbolic/constructive.py` 与 `spatial.py` 仍是当前 V4 生成器的内部依赖，不代表另行保留的旧 benchmark。

## 安装与验证

需要 Python 3.11+：

```bash
python -m pip install -e '.[dev]'
python -m fwcollab.cli doctor
python -m pytest -q
python -m fwcollab.cli symbol-validate --map maps/symbolic/S01.fwmap
python -m fwcollab.cli symbol-render-suite --map-dir maps/symbolic --output artifacts/symbolic/core_gallery.html
```

重新生成当前72任务及其确定性见证：

```bash
python -m fwcollab.cli spatial-generate
```

运行真实双模型评测：

```bash
python scripts/run_dag_map_model_suite.py \
  --endpoint http://127.0.0.1:4141 \
  --model gpt-5.4-mini \
  --max-rounds 80
```

真实模型调用必须显式指定可用端点。火与冰由两个独立策略上下文控制；每轮基于同一份冻结观察同时提交动作，下一轮才能收到对方消息。每名 agent 每轮只能执行一次相邻单格移动或等待。

## 主要命令

```bash
python -m fwcollab.cli symbol-validate --map MAP.fwmap
python -m fwcollab.cli symbol-render --map MAP.fwmap --output MAP.html
python -m fwcollab.cli symbol-run --map MAP.fwmap --provider witness --output TRACE.json
python -m fwcollab.cli symbol-replay --map MAP.fwmap --trace TRACE.json
python -m fwcollab.cli symbol-render-trace --trace TRACE.json --output TRACE.html
python -m fwcollab.cli dag-generate
python -m fwcollab.cli dag-validate --input DAG.json
python -m fwcollab.cli dag-compare --reference REFERENCE.json --candidate CANDIDATE.json
python -m fwcollab.cli dag-render --input DAG.json --output DAG.svg
python -m fwcollab.cli dag-render-suite
python scripts/evaluate_collaboration_runs.py --run NAME=RUN_DIR
python scripts/select_collaboration_diagnostics.py
```

## 权威文件

| 文件 | 用途 |
|---|---|
| `SYMBOL_SPEC.md` | 符号地图、动作与状态更新契约 |
| `docs/PUBLIC_RULEBOOK.md` | 可提供给 agent 的完整公开规则 |
| `docs/PRIMITIVE_COMPONENTS.md` | 元组件、符号与组合关系 |
| `docs/STATE_DAG_CURRICULUM.md` | DAG格式、难度、去重和比较协议 |
| `docs/FULL_SPATIAL_CURRICULUM_72.md` | 当前72任务的设计与验收结果 |
| `docs/MULTI_AGENT_EVALUATION_RESEARCH.md` | 外部多智能体 benchmark 调研、指标设计与现有 run 可报告性审计 |
| `docs/COLLABORATION_EVALUATION_PROTOCOL.md` | 可执行DAG协作过程指标、失败诊断和24图诊断集协议 |
| `TASKS_SYMBOLIC.json` | 当前符号版开发记录 |
| `src/fwcollab/symbolic/` | 解析、规则、双 agent runner 与渲染实现 |
| `schemas/state_dag.schema.json` | 候选 DAG 的机器可读 schema |
| `schemas/collaboration_evaluation.schema.json` | 私有离线协作评测输出 schema |

发布边界和尚未完成的外部验证见 `docs/BENCHMARK_READINESS.md`。
