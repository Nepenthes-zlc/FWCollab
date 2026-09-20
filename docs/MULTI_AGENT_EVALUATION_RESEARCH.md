# 多智能体协作 Benchmark 评估调研与 FWCollab 指标方案

更新日期：2026-09-14

## 1. 结论

成熟的多智能体 benchmark 通常不会用一个“成功率”概括协作能力，而是至少区分以下四层：

1. **团队结果**：是否成功、奖励、完成时间或动作数。
2. **协作过程**：谁推进了哪些必要步骤，是否正确发起、响应和维持协作。
3. **搭档泛化**：与未见过的模型、策略或人类搭档合作时是否仍然有效。
4. **因果与鲁棒性**：去掉通信、历史、反馈或集中规划后，表现如何变化。

对 FWCollab 而言，当前72张地图和完整逐轮轨迹已经足以报告团队结果、运行成本、动作反馈、失败类型和一部分过程进展；但不能仅凭这一次同模型运行声称具有通用协作、通信增益或零样本搭档适应能力。

## 2. 相关 Benchmark 如何评估

### 2.1 Overcooked-AI

[Overcooked-AI 原始论文](https://proceedings.neurips.cc/paper/8760-on-the-utility-of-learning-about-humans-for-human-ai-coordination.pdf)使用完全合作的双人做菜任务。主要结果指标是在400个时间步内获得的累计奖励，并对学习策略运行多次 rollout。论文不仅测试 self-play，还让智能体分别与保留的代理人类模型及真实人类合作。

关键启示是：self-play 得分高不等于能与行为习惯不同的搭档合作。评估中必须区分同策略搭档表现与陌生搭档表现。

### 2.2 Collab-Overcooked

[Collab-Overcooked](https://aclanthology.org/2025.emnlp-main.249.pdf)是与 FWCollab 最接近的 LLM 多智能体任务。它通过资源隔离和知识不对称，使单个智能体不能独立完成名义上的合作任务，并使用人工标注的参考动作轨迹评估执行过程。

其主要过程指标包括：

- `TES`：当前轨迹相对参考轨迹的效率与完整性；
- `ITES`：单个动作给轨迹进度带来的边际变化；
- `PC`：整体任务进度完整度；
- `IC`：需要协作时正确发起请求的比例；
- `RC`：收到协作请求后作出有效响应的比例。

FWCollab 不宜要求动作序列严格匹配单条参考轨迹。现有私有 DAG 可以作为更宽容的参考：按状态节点和偏序依赖计分，接受满足同一目标的合法替代路径。

### 2.3 Hanabi

[Hanabi Learning Environment](https://arxiv.org/pdf/1902.00506)区分 self-play 和 ad-hoc team play。后者将独立训练出来的策略两两配对，形成 cross-play 矩阵。论文显示，策略与自己的复制品配合良好时，换成采用不同约定的搭档，成绩可能明显下降。

对 FWCollab 的直接含义是：两个独立上下文虽然可以避免泄露私有历史，但如果两边仍使用同一个模型和同一套提示，它仍然主要是 self-play 类型实验。

### 2.4 Melting Pot

[Melting Pot](https://proceedings.mlr.press/v139/leibo21a/leibo21a.pdf)把待评估智能体放入未见过的背景智能体群体中，主要统计待评估群体的人均回报，同时关注它给背景智能体带来的收益、损害和不平等。

这提示 FWCollab 不应只问“团队是否通关”，还应衡量某个角色是否通过错误动作让队友增加等待、绕路、重新开门或补救。

### 2.5 SMAC 与 SMACv2

[SMACv2](https://arxiv.org/abs/2212.07489)延续了胜率、回报和去中心化执行评估，同时通过程序生成场景和更强的部分可观测性减少固定策略投机。其分析表明，旧任务上仅依赖时间步、不充分读取状态的开环策略也可能获得非平凡成绩。

因此 FWCollab 应加入固定脚本或开环基线。如果一张地图不读取更新状态也能稳定通过，它就不能有力证明闭环规划或协作能力。

### 2.6 RoCoBench

[RoCoBench](https://arxiv.org/abs/2307.04738)评估多机器人语言协作，报告成功率、成功 episode 的环境步数和动作前重新规划次数，并设置集中式规划、无历史、无环境反馈等消融条件。

这些对照可直接迁移到 FWCollab，用于区分集中规划能力、去中心化协调能力和根据环境反馈修正计划的能力。

### 2.7 MultiAgentBench

[MultiAgentBench](https://aclanthology.org/anthology-files/anthology-files/pdf/acl/2025.acl-long.421.pdf)把复杂任务拆成里程碑，记录每个智能体对里程碑的贡献，并额外用 LLM 评判通信和规划质量。

里程碑贡献适合借鉴，但 FWCollab 是确定性符号环境，主要分数应由状态、事件和 DAG 验证器计算。LLM 或人工评审可用于定性分析，不应替代可复现的环境裁判。

## 3. 可迁移到 FWCollab 的指标体系

### 3.1 团队结果与成本

- `Success Rate`：双方到达各自出口的 episode 比例；
- `Outcome Breakdown`：成功、死亡或团队失败、超时、运行错误；
- `Rounds to Success`：仅在成功 episode 上报告，并同时给中位数和分布；
- `Model Calls`、`Provider Errors`、`Wall Time`、`Tokens`；
- `Witness-relative Efficiency`：实际轮数相对确定性成功见证轮数的比例。当前见证只证明可达，不应称为最优轨迹。

### 3.2 DAG 规划与任务进度

- `DAG Final Completion`：episode 结束时完成的节点权重占比；
- `DAG Progress AUC`：每轮 DAG 完成比例的时间积分；
- `Dependency Violations`：前置节点未满足时尝试后续操作的次数；
- `Capability Reached / Engaged / Crossed`：是否到达、使用并通过目标机制；
- `Replan Count`：显式计划版本发生变化的次数；
- `Redundant Action Rate`：撞墙、重复尝试不满足条件的机关、无效移动等比例。

`DAG Progress AUC` 可区分“接近完成后超时”和“全程没有推进”两种失败。节点完成必须由环境状态或事件判定，不能只根据 agent 的自然语言声明。

### 3.3 协作过程

- `Initiation Capability (IC)`：出现跨角色依赖时，责任方是否及时提出可执行请求；
- `Response Capability (RC)`：收到请求后，队友是否在允许窗口内完成相应 DAG 节点；
- `Handoff Success`：压力板—通行、开门—穿门、箱子或机关球转交等跨角色依赖是否成功；
- `Coordination Violations`：过早离开持续机关、关闭队友路径、阻塞关键格等次数；
- `Partner Burden`：由本角色错误导致队友额外等待、绕路或补救的动作数；
- `Useful Wait Ratio`：为了保持机关或避免冲突的等待，占全部等待的比例。

贡献不必强制为 50:50。非对称地图应检查双方是否完成各自必需的 DAG 节点，而不是追求表面动作均衡。

### 3.4 协作的因果价值

同一地图和模型组合应增加以下实验条件：

| 条件 | 要回答的问题 |
|---|---|
| 正常双智能体并允许通信 | 完整系统能达到什么水平 |
| 禁止通信 | 语言通信是否真的产生收益 |
| 消息打乱或额外延迟 | 协作是否依赖正确时序和语义 |
| 不提供候选 DAG | 计划表示是否有帮助 |
| 提供私有参考 DAG 的 oracle 版本 | 已知计划时，执行协调能力如何 |
| 单一中央规划器控制双方 | 去中心化带来的性能差距 |
| 开环脚本或盲策略 | 任务是否真正要求读取状态 |
| 单智能体控制双方 | 任务需要的是合作还是单纯联合规划 |

建议单独报告：

```text
Communication Gain = Score(normal communication) - Score(no communication)
Decentralization Gap = Score(central planner) - Score(two agents)
Closed-loop Gain = Score(full observation) - Score(open-loop baseline)
```

### 3.5 搭档泛化

应构造 `F 模型 × W 模型` 的 cross-play 矩阵，并区分：

- 同模型、同提示的 self-play；
- 不同模型的 cross-play；
- 同模型、不同协作提示或沟通风格；
- F/W 角色互换；
- 未见布局和机制组合；
- 地图布局扰动但 DAG 结构保持不变。

主报告应分别展示 self-play 和 cross-play，不应把两者合并成一个平均分。

## 4. 当前72任务真实 Run 的可报告性审计

审计对象：`artifacts/runs/spatial_curriculum_full_v4_model_20260913/`。

该 run 使用 `gpt-5.4-mini` 控制 F/W 两个独立会话，每张地图一次试验。轨迹逐轮保存冻结观察、完整状态、双方动作、结构化消息、简短理由、反馈、公开事件、模型错误、调用次数、延迟和状态哈希。72条轨迹均已通过确定性精确回放。

### 4.1 已经可以直接报告

| 指标 | 当前状态 | 说明 |
|---|---|---|
| 总体及 L1–L7 成功率 | 可直接报告 | 汇总文件已有 outcome 与难度分组 |
| 成功、团队失败、超时数量 | 可直接报告 | 环境权威终态 |
| 每图实际轮数 | 可直接报告 | 成功效率应只在成功样本上统计 |
| 模型调用数和 provider/解析错误数 | 可直接报告 | 有总量和逐角色记录 |
| 消息数量 | 可直接报告 | 只能表示发送量，不能表示消息有效 |
| 墙钟时间和逐轮调用延迟 | 可直接报告 | 当前未拆分纯模型时间与环境处理时间 |
| 每角色移动、等待、blocked、invalid | 可直接报告 | `blocked` 是现有 runner 的粗分类，不等价于冗余动作 |
| 同轮双方移动数、support rounds、action balance | 可直接报告 | 属于描述统计，不单独证明协作质量 |
| 失败诊断 | 可直接报告 | 包括 budget exhausted、mutual wait、blocked action loop 等 |
| 到达/使用/通过主要能力 | 可直接报告 | 当前私有分析已有 reached、engaged、crossed |
| 基础阶段完成数 | 可直接报告 | 当前是空间阶段检查，不是完整 DAG 节点评分 |
| 轨迹可复现性 | 可直接报告 | 72/72 已精确回放 |

当前可发布的事实性摘要为：

- 72张地图，成功14张，成功率 `19.44%`；
- 超时57张，团队失败1张；
- 分级成功率：L1 `75%`、L2 `37.5%`、L3 `30%`、L4 `25%`、L5 `14.29%`、L6 `0%`、L7 `0%`；
- 成功 episode 平均使用 `106.5` 轮；
- 共 `34,670` 次模型调用、`980` 次模型错误、`29,818` 条已记录发送消息；
- 整套运行墙钟时间 `11,394.533` 秒，约 `3小时09分55秒`；
- 主要能力 reached `30/72`、engaged `21/72`、crossed `18/72`；
- 72条轨迹均可由权威符号规则精确回放。

注意：套件命令中的 `max_rounds=80` 是预算下限；实际每图预算会依据成功见证长度和 multiplier 提高。因此报告超时和效率时，应读取每条 trace 中的实际 `protocol.max_rounds`，不能把所有地图都描述为固定80轮。

### 4.2 能从现有轨迹离线补算，但目前尚未正式实现

| 指标 | 可恢复程度 | 需要补充的评估器 |
|---|---|---|
| 精确反馈类别与动作冗余率 | 高 | 按 `feedback` 和状态差分重新分类，修正现有粗粒度 blocked |
| 最终 DAG 完成率 | 中到高 | 为每类 DAG predicate 建立环境状态/事件绑定 |
| DAG Progress AUC | 中到高 | 在每轮状态上执行 DAG 节点判定并保留首次完成轮次 |
| 依赖违规次数 | 中 | 定义“尝试后续节点”的可观察动作或事件 |
| Witness-relative Efficiency | 高 | 读取每图确定性见证长度；只能称见证相对效率，不能称最优性 |
| Useful Wait Ratio | 中 | 将等待与持续机关、避让和消息请求窗口对齐 |
| IC/RC 与 handoff success | 中 | 把结构化 request、消息投递和跨角色 DAG 节点进行因果链接 |
| Partner Burden | 中 | 定义由某角色错误引起的补救和额外等待规则 |
| 启发式 replan count | 低到中 | 可比较 subgoal/commitment 文本，但可靠版本需要未来显式 `plan_id/revision` |

现有私有 DAG 的谓词是结构化的，但还没有一个覆盖 M01–M30、逐轮执行所有节点谓词的统一轨迹评分器。现有 `private_evaluation` 只计算基础空间阶段和主要能力的 reached/engaged/crossed。因此在该评分器完成前，不应把这些数值称为完整的 `DAG Completion` 或 `DAG Progress AUC`。

### 4.3 无法从当前单次 Run 推导，必须重新实验

| 指标 | 无法回算的原因 |
|---|---|
| Communication Gain | 没有同配置的 no-message 对照 |
| 消息延迟或打乱鲁棒性 | 没有对应干预轨迹 |
| Central-planner gap | 没有中央控制器基线 |
| Oracle-DAG gain | 当前 run 没有提供私有参考 DAG 的 oracle 条件 |
| 候选 DAG 规划质量 | 当前保留 run 未执行新版双 agent DAG 协商流程 |
| Cross-play / zero-shot partner score | F/W 均为同一模型，没有异模型配对矩阵 |
| 角色互换鲁棒性 | 没有交换 F/W 策略配置重跑 |
| 开环与盲策略差距 | 没有固定脚本或不读取更新状态的基线 |
| 统计置信区间和方差 | 每个地图只有一次真实模型试验 |
| Token cost | provider 的 token usage 未写入旧轨迹，只能报告调用数和时间 |
| 不完全信息协作 | 当前双方都收到相同完整公开状态 |
| 人机协作与主观信任 | 没有人类参与者实验 |

## 5. 当前结果应如何表述

当前结果适合表述为：

> 在72张开发期全观察符号地图上，对同一个模型的两个独立会话进行一次闭环 self-pair 运行，测得团队成功率、执行成本、失败类型和粗粒度任务进展。

当前结果不适合表述为：

> 模型已经被证明具有通用多智能体协作、有效语言通信、陌生搭档适应或接近最优规划能力。

## 6. 推荐实施顺序

1. 先实现离线轨迹评估器：反馈分类、完整 DAG 节点完成时间、Progress AUC、依赖违规和有效等待。
2. 在不调用模型的情况下，对现有72条轨迹生成扩展评估报告，保留原始 trace 不变。
3. 选取一组诊断地图运行正常通信、无通信、oracle DAG、中央控制器和开环脚本消融。
4. 再运行至少一个异模型 cross-play 矩阵及 F/W 角色互换。
5. 正式结果对每个配置进行多次重复，并报告 bootstrap 置信区间或地图级配对置信区间。
6. 另设少量不完全信息地图，单独命名为 `Asymmetric-Information Collaboration` 赛道；现有72图继续作为 `Full-Observation Decentralized Coordination` 赛道。

最终 leaderboard 不建议压缩为一个总分，至少并列报告：成功率、成功效率、DAG Progress AUC、无效动作率、IC、RC、Communication Gain、Cross-play 成功率和运行成本。
