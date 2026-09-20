# FWCollab 72个状态依赖DAG课程

状态：抽象DAG已生成，尚未落地为对应符号地图。

## 1. 目标

这套课程把 `docs/PRIMITIVE_COMPONENTS.md` 中的30种一级元机关组合成72个状态依赖DAG。DAG用于表达“哪些可观测状态必须先成立，哪些后续状态才可能成立”，并为后续符号地图生成提供空间落地蓝图。

参考DAG接近关卡解题结构，因此保存在 `eval_private/dag_curriculum/`，不能直接加入游戏agent的观测。agent可以先基于公开地图和机制规则合作生成候选DAG，再由评测端使用结构化比较器与私有参考DAG比较。

## 2. 数量与难度

| 难度 | 数量 | 评测用途 | 主要结构 |
|---|---:|---|---|
| L1 | 4 | diagnostic | 单一规则与短链 |
| L2 | 8 | diagnostic | 单机关或两个简单元机关 |
| L3 | 12 | scored | 资源运输与基础组合 |
| L4 | 16 | scored | 保持、交接、分叉与汇合 |
| L5 | 18 | scored | 多阶段状态、OR路线和复杂资源关系 |
| L6 | 14 | scored | 多系统嵌套、并行波次和组合恢复 |
| 合计 | 72 | 12个诊断 + 60个计分 |  |

所有 `M01–M30` 至少出现在一个DAG中。72个DAG的受控类型签名全部唯一；纯拓扑允许最多复用3次，以便比较同一种协作结构在不同机关语义中的迁移能力。

## 3. 文件布局

| 路径 | 内容 |
|---|---|
| `schemas/state_dag.schema.json` | agent候选和参考DAG共用的公开JSON Schema |
| `examples/dag_candidate.json` | 可以修改、验证和比较的公开候选示例 |
| `eval_private/dag_curriculum/catalog.json` | 72个私有参考DAG的完整机器可读目录 |
| `eval_private/dag_curriculum/graphs/L*/` | 每个参考DAG的独立可读写JSON |
| `artifacts/eval_private/dag_curriculum/index.html` | 72图可视化总览 |
| `artifacts/eval_private/dag_curriculum/L*/` | 每个DAG的独立SVG |

目录数据由 `fwcollab.symbolic.dag_curriculum` 确定性生成。修改生成规则后必须重新生成并运行测试，不能只手改汇总文件而留下不一致的独立图。

## 4. 节点格式

一个节点由机器字段和阅读字段组成：

```json
{
  "id": "hold",
  "kind": "condition",
  "owner": "agent_a",
  "join": "all",
  "predicate": {
    "op": "occupy",
    "motif": "M11",
    "state": "plate_held",
    "phase": 1
  },
  "label": "角色A持续占据压力板"
}
```

- `id`：只用于同一JSON文件内引用，比较时忽略具体拼写。
- `kind`：`condition`、`state`、`event` 或 `goal`。
- `owner`：`agent_a`、`agent_b`、`team` 或 `environment`。
- `join`：多个前置是否全部需要（`all`）或任选其一（`any`）。
- `predicate.op`：受控操作类型，例如 `occupy`、`activate`、`traverse`。
- `predicate.motif`：`M01–M30`；框架起点、同步点和成功点使用 `SYSTEM`。
- `predicate.state`：受控状态词，不使用完整自然语言句子。
- `predicate.phase`：帮助人类区分多阶段状态；比较器忽略具体数字，依赖顺序由边表达。
- `label`：人类可读说明，可以是中文或其他语言，完全不参与比较。

每张图必须恰好有一个 `start` 节点和一个 `team_success` 节点。所有节点必须从起点可达、能够汇入成功节点，并且整张图无环。

## 5. 边格式

```json
{
  "from": "hold",
  "to": "cross",
  "relation": "maintains"
}
```

| relation | 含义 |
|---|---|
| `requires` | 目标以前置状态为必要条件 |
| `enables` | 前置状态直接使目标状态可达 |
| `maintains` | 前置状态必须持续到目标事件发生 |
| `handoff` | 依赖跨越agent A/B，表示接力或帮助 |
| `synchronizes` | 多个分支在目标节点汇合 |

切换器需要“先开后关”时，使用两个不同的阶段状态节点，而不是让一条边回到旧节点。这样既能表达重复状态，又能保持DAG无环。

## 6. 比较协议

比较器输出两个等价判断和两个分数：

- `topology_equivalent`：只比较有向连接结构和 `all/any` 汇合，不看机关和自然语言。
- `typed_equivalent`：在拓扑上继续比较受控谓词、元机关编号、状态类型、owner和边关系。
- `topology_score`：不完全同构时，根据节点数、边数、最长路径、并行宽度、分叉/汇合和度分布给出近似分。
- `typed_score`：在拓扑分的基础上加入受控类型匹配。

以下差异不影响等价结果：

- DAG标题、摘要和节点中文说明；
- 图ID、节点ID和JSON数组顺序；
- 地图坐标、门字母、压力板编号等空间实例信息；
- `phase` 使用0、1还是其他编号；
- 全局交换 `agent_a` 与 `agent_b` 的命名。

比较器不会完全忽略规则语义。把“门打开”写成错误状态时，纯拓扑仍可能相同，但 `typed_equivalent` 会失败。这使结构表达不受语言措辞影响，同时避免一个形状正确但机制错误的DAG获得满分。

## 7. agent协作生成方式

给agent的输入可以包含：

1. 当前完整公开符号地图和结构化状态；
2. `docs/PRIMITIVE_COMPONENTS.md` 的公开机制规则；
3. `schemas/state_dag.schema.json`；
4. 要求双方讨论后只输出一个候选DAG JSON。

不应提供：

- 私有参考DAG；
- 参考通关轨迹；
- 隐藏依赖标签；
- 比较器针对该任务的中间匹配结果。

推荐让两个agent先分别提出局部依赖，再通过公开消息合并。最终候选图使用抽象 `agent_a/agent_b`，不要把自然语言角色名、坐标或门编号写进受控比较字段；这些内容可以写在 `label` 中供人阅读。

运行器已经提供可选的两轮实现。`--planning-rounds 2` 先让 F/W 独立输出完整 `agent_candidate`，再让双方读取对方候选并完整修订；两份最终候选使用本章比较器检查拓扑与类型共识，达成类型共识的计划会进入后续逐步执行上下文。规划失败单独记录，不会中止 episode；默认值 0 保留纯在线控制设置。

```bash
python -m fwcollab.cli symbol-run \
  --map MAP.fwmap \
  --provider openai --allow-network --endpoint ENDPOINT \
  --planning-rounds 2 \
  --output TRACE.json
```

## 8. 命令

重新生成72个DAG、独立JSON和SVG总览：

```bash
python -m fwcollab.cli dag-generate
```

验证整个目录或单个候选：

```bash
python -m fwcollab.cli dag-validate --input eval_private/dag_curriculum/catalog.json
python -m fwcollab.cli dag-validate --input examples/dag_candidate.json
```

比较候选和某个参考DAG：

```bash
python -m fwcollab.cli dag-compare \
  --reference eval_private/dag_curriculum/graphs/L2/DAG-L2-001.json \
  --candidate examples/dag_candidate.json \
  --output artifacts/dag_comparison.json
```

渲染单个候选DAG：

```bash
python -m fwcollab.cli dag-render \
  --input examples/dag_candidate.json \
  --output artifacts/dag_candidate.svg
```

## 9. 与符号地图的关系

当前72个DAG是**空间落地前的抽象参考**，还不是72张可运行地图。下一阶段需要为每个节点绑定可由权威状态机检查的具体谓词，并把抽象实体放置为 `.fwmap` 中的角色、地形、机关和通路。

空间生成至少要验证：

- 所有DAG前置在地图中确实必要，不能绕路跳过；
- 存在至少一条合法通关轨迹；
- 压板的保持区间、AND/OR汇合和角色交接可以在回放中判定；
- 同一DAG的不同空间布局共享DAG结构，但不共享坐标答案；
- L1/L2只作规则诊断，正式总分集中在L3–L6。
