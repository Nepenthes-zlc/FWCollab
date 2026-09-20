# DAG-based Collaboration Evaluation Protocol v1

状态：已实现的私有离线评测协议；不进入游戏 agent 观察，不使用 LLM judge。

## 1. 目标与边界

该协议把 V4 每张地图的私有状态 DAG、`node_bindings`、机关阶段元数据和权威逐轮轨迹组合为可执行 evaluator。它回答“任务推进到了哪个依赖节点、跨角色交接是否完成、是否发生可客观确认的提前释放或越级尝试”，而不是只报告最终成功率。

v1 只评估环境状态和动作能够确定判定的行为。消息语义、IC/RC、Message Relevance 和 Communication Gain 不在 v1 中，避免用自然语言启发式或 LLM judge 污染主分数。

## 2. 输入和隐私

输入包括：

- `eval_private/spatial_curriculum_full_v4/manifest.json` 中的逐图DAG路径、阶段、能力和节点绑定；
- 对应私有参考DAG；
- 真实运行保存的权威 `symbol_trace.v1`。

输出只在episode结束后生成，格式为 `fwcollab.symbolic.collaboration_evaluation.v1`。私有DAG、见证首次完成轮和评测结果均不得注入模型prompt。

## 3. 节点执行语义

节点在某轮完成必须同时满足：

1. 绑定的状态谓词在该轮权威观察中为真；
2. `join=all` 时全部前置已经完成；
3. `join=any` 时至少一个前置已经完成。

穿越节点使用“角色列坐标越过受控区域边界”，不要求复刻见证轨迹中的单个精确坐标，因此允许合法替代路线。静态危险/单向证据会规范化为角色实际到达或越过对应区域的动态谓词。

`join=any` 的未选择分支标为 `waived_by_any_join`，不计入该条合法轨迹的必需节点分母。旧见证中的 `first_true_round` 只用于地图生成验收，不参与模型轨迹评分。

## 4. 核心指标

### DAG Completion

```text
已完成的观测路径必需节点 / 观测路径全部必需节点
```

永真start节点不计分；被合法OR分支替代的节点不计入分母。

### DAG Progress AUC

每轮DAG Completion的归一化梯形积分。相同最终完成度下，更早完成依赖节点的轨迹得分更高。AUC仍受episode预算影响，应与完成轮数同时报告。

### Dependency Violation

v1记录两类可客观确认的越级行为：

- agent拥有的状态/事件谓词在前置未完成时提前成立；
- 角色在门或桥关闭时尝试进入执行器格。

比率以双方agent动作总数 `2 × rounds` 为分母。

### Handoff Success

V4每个 `supporter → actuator → traveler` 阶段构成一次handoff opportunity。traveler越过受控边界时记为成功。需要移动重物、调整光路或冻结热相后由另一角色穿越的能力胶囊也计为handoff。

`clean handoff` 还要求该handoff期间没有提前释放、能力状态提前回归或关门穿越尝试。

### Coordination Violation

v1包括：

- `premature_release`：traveler尚未穿越时，保持型控制器被释放并使执行器关闭；
- `closed_actuator_traverse_attempt`：执行器关闭时仍尝试穿越；
- `capability_regression_before_cross`：协作能力状态建立后，在traveler穿越前被破坏。

### Useful Hold / Useful Wait

- `useful_hold_rounds`：supporter实际站在已激活压力板上、且traveler尚未完成穿越的角色—轮数；
- `useful_waits`：上述状态中supporter选择WAIT的次数；
- `useful_wait_ratio`：`useful_waits / total_waits`。

箱子替代角色占板时，不把远处角色的WAIT误算为有效等待。

### Node Regression

只对 `occupy` 和 `activate` 这类持续状态计算真→假回归。穿越和进入能力区是瞬时事件，即使旧DAG把它标作state，也不计算回归。同轮中“角色合法穿过、机关随后关闭”不算有害回归。

## 5. 自动失败诊断

失败输出包含：

- 首个未完成的观测路径必需节点；
- 对应受控谓词和责任角色；
- 尚未完成的直接前置；
- 所属handoff；
- 相关协调违规；
- 最后推进轮与最终DAG进度。

示例形式：

```text
Task stopped before n4 (gate_2_crossed).
Handoff: stage_2, supporter=F, traveler=W.
Observed: F released plate before W crossed; W later attempted the closed gate.
```

该说明由结构化事件模板生成，不读取或判断模型的私有reason文本。

## 6. 当前三组轨迹的v1结果

| Run | Success | DAG Completion | Progress AUC | Handoff | Clean Handoff | Coordination Violations | Useful Wait Ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemini 3.7 Flash | 72/72 | 1.000 | 0.561 | 367/367 | 366/367 | 1 | 0.386 |
| GPT-5.5 | 71/72 | 0.998 | 0.563 | 366/367 | 364/367 | 3 | 0.420 |
| GPT-5-mini | 6/72 | 0.293 | 0.215 | 81/367 | 42/367 | 1,136 | 0.016 |

说明：表中数值由当前一次运行得到，不是置信区间；最终机器结果以 `artifacts/evaluations/collaboration_v1_20260914/summary.json` 为准。

## 7. Collaboration Diagnostic Set

结构选择器从72图中确定性选择24图，不读取任何模型outcome。目标是覆盖保持—通行、顺序交接、互相解锁、多阶段轮换、OR/AND汇合和不同控制器序列。输出为：

`eval_private/collaboration_diagnostic_24/manifest.json`

当前72图的结构覆盖为：C1 65图、C2 70图、C3 68图、C6 60图。当前没有可严格证明的C4有界同步动作、C5信息依赖或C7双agent并行分支汇合；诊断集明确记录这些空白，不伪造标签。

## 8. 命令

```bash
python scripts/evaluate_collaboration_runs.py \
  --run gpt-5.5=artifacts/runs/RUN_A \
  --run gemini=artifacts/runs/RUN_B \
  --output-dir artifacts/evaluations/collaboration_v1

python scripts/select_collaboration_diagnostics.py \
  --output eval_private/collaboration_diagnostic_24/manifest.json
```

## 9. v1限制与下一实验

- 当前双方看到相同完整公开状态，本赛道只能称为 Full-Observation Decentralized Coordination；
- 消息是否为有效请求尚未客观标注，不报告IC/RC；
- Communication Gain必须通过Normal与No-Communication配对重跑；
- Cross-play必须分别运行GPT(F)+Gemini(W)和Gemini(F)+GPT(W)；
- 每张地图当前只有一次模型轨迹，正式论文仍需至少3个seed和地图级配对置信区间。
