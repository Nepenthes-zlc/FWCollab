# Phase II 因果协作实验协议

## 冻结声明

Phase II 使用 Benchmark v1：72 题完整集、结构选取的 Diagnostic-24、私有 DAG predicates、协作 evaluator、80 轮预算、完整共享观察、单格同步动作和基础 prompt 均被冻结。

> The diagnostic subset was selected solely based on task structure and collaboration primitives, without access to model performance.

机器可核验清单位于 `eval_private/phase2_v1/freeze_manifest.json`。正式运行器会检查其中 61 个文件的 SHA-256；任一输入发生变化时拒绝继续运行。

## 实验矩阵

| Condition | F | W | Communication |
|---|---|---|---|
| GPT Self-play | GPT-5.5 | GPT-5.5 | enabled |
| Gemini Self-play | Gemini-3.7-Flash | Gemini-3.7-Flash | enabled |
| GPT No-Comm | GPT-5.5 | GPT-5.5 | disabled |
| Gemini No-Comm | Gemini-3.7-Flash | Gemini-3.7-Flash | disabled |
| Cross-play A | GPT-5.5 | Gemini-3.7-Flash | enabled |
| Cross-play B | Gemini-3.7-Flash | GPT-5.5 | enabled |

每个条件运行 24 tasks × 3 aligned replicates，共 432 episodes。三个 replicate 向 provider 请求不同 seed；由于跨 provider 的 seed 语义无法保证一致，结果中将其同时称为 replicate，并明确记录“不保证完全可复现”。温度固定为 1.0。

No-Comm 条件向模型明确声明通信不可用，传入空 inbox，并在适配层强制清除模型输出的 message。共享公开环境状态保持不变，因此该消融测量显式通信相对于环境隐式协调的增益。

## 主指标

1. Success Rate
2. DAG Completion
3. DAG Progress AUC
4. Clean Handoff Rate
5. Coordination Violation Rate：客观协作违规数 / 执行轮数
6. Rounds-to-Success：仅在成功 episode 上定义

Useful Wait、Useful Hold、failure stage 和 violation type 作为诊断指标。

Communication Gain 分别报告：

- `ΔSR = SR_normal - SR_no_comm`
- `ΔAUC = AUC_normal - AUC_no_comm`
- `ΔViol = Viol_no_comm - Viol_normal`

Cross-play Gap 的 SR 定义为：

`((SR_GG + SR_MM) / 2) - ((SR_GM + SR_MG) / 2)`

两个 cross-play 方向必须单独报告。Partner Sensitivity 按模型及其角色分别比较 self-play 与对应 cross-play，避免将 F/W 角色不对称性平均掉。

## 统计方法

同一任务的三个 replicate 不作为三个独立任务。先在 task 内求 replicate 均值，再以 24 个 task 为 bootstrap unit，进行 10,000 次 paired bootstrap，报告 95% confidence interval。条件比较始终保持 task 对齐。

当前任务没有 C5 信息不对称，因此 No-Comm 不显著下降不构成负面结果；它表示 Benchmark v1 主要评估完整共享观察下的 decentralized behavioral coordination，而不是必要的信息交换。
