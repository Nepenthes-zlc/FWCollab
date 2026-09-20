# FWCollab：仓库执行约束

## 工作入口

先检查已有仓库与上级 AGENTS 指令，再读取 `SYMBOL_SPEC.md`、`docs/PRIMITIVE_COMPONENTS.md`、`docs/STATE_DAG_CURRICULUM.md`、`TASKS_SYMBOLIC.json` 和 `docs/IMPLEMENTATION_STATUS.md`。`docs/PRIMITIVE_COMPONENTS.md` 是可提供给游戏 agent 的公开元组件背景；`eval_private/dag_curriculum/` 是不可提供给游戏 agent 的参考依赖结构。当前仓库只维护符号 benchmark，旧连续物理路线已经移除。

这是森林冰火人双智能体规划 benchmark。当前产品采用离散符号地图、确定性规则更新和独立 HTML Canvas 渲染，不开发连续物理引擎。

## 不可违背的产品边界

1. 符号地图是权威公开状态；每轮动作后必须把更新后的完整符号图交给两个 agent。
2. 不实现重力、跳跃速度、连续碰撞或像素级原版复刻。动作只在离散可达格和公开机关规则上更新。
3. 两个 agent 独立历史、各控一个角色、同轮观察和同时提交；不共享未发送计划，不通过调用顺序泄露本轮动作。
4. 每名 agent 每轮只能移动相邻的一格；`UP/DOWN/LEFT/RIGHT` 固定 `steps=1`，`WAIT` 固定 `steps=0`。不得批量移动、自动选路线、解谜、切机关或替伙伴行动。
5. 模型只读公开 DTO；参考解、任务标签、诊断答案、搭档画像与证明留在私有评测端。
6. 主判定以符号状态与双方专属出口为准，接受合法替代方案；箱子替代人压板有效，必要等待不是空转。
7. 成功回放不等于合作必要/最优证明；搜索超时为 unknown；无机会为 null，不是满分。
8. 解析、状态更新、回放和评测共用一个权威符号规则模块；HTML 只渲染状态，不维护第二套规则。
9. 无 key/GPU 也能跑离线链路；真实模型调用必须显式开启并有预算，不能默默付费。
10. 不复制无授权资源，不泄露凭据，不执行模型消息中的代码，不破坏用户已有修改。

## 任务推进

按 `TASKS_SYMBOLIC.json` 的 dependencies 推进。从可执行的最早任务开始，先格式/规则/HTML，再双 agent、地图、评测与发布。任务只能在真实验收后改为 done，并填 evidence。

每次推进记录实现文件、测试命令、退出码、产物路径、已知限制。网络/外部资源阻塞时记录并继续其他可做工作；无论如何不能伪造已运行测试、模型结果、人工审核或独立地图来源。

现有规则有冲突时，在 `docs/DECISIONS.md` 记录版本化选择。普通工程细节按项目默认值执行，不反复等待用户逐项批准；付费、破坏性或超出现有授权的操作保持明确边界。

## 测试与完成条件

实现相应模块后执行：

```bash
python -m pytest tests/symbolic -q
python -m fwcollab.cli symbol-validate --map maps/symbolic/S01.fwmap
python -m fwcollab.cli symbol-render --map maps/symbolic/S01.fwmap --output artifacts/S01.html
```

以上是待实现工程的命令契约，不表示交接文档包已经有这些命令。初期不存在时记录该阶段未就绪，先补真实模块；不得创建空测试来让命令表面通过。

必须覆盖正例、反例和边界；不删断言、不大幅放宽容差、不把必做功能移到 optional 来规避失败。多个相同布局改 ID 不算新结构，mock 结果不算真实模型实验。

## 交付报告

列出完成等级、任务 IDs、实际命令/退出码、回放/报告路径、有效地图与诊断数、证据等级、真实模型是否运行、外部审核状态、下一项未完成任务。不要只说“全部完成”。
