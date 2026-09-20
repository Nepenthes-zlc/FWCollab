# 实现状态

更新时间：2026-09-14

当前路线：symbol-based。原连续物理路线已归档，不计入当前进度。

## 已完成

- `S00-01`：记录用户路线切换；新增 `SYMBOL_SPEC.md` 和 `TASKS_SYMBOLIC.json`，保留旧成果但取消其主入口身份。
- `S01-01`：实现严格 `.fwmap` 解析与校验，包括等宽边界、唯一角色/出口、符号白名单及机关引用检查。
- `S01-02`：实现严格单格同步动作、遇阻反馈、箱子/机关球统一目标冲突仲裁、持续压板、持久拨杆、动态门、角色危险区差异、出口和状态哈希。
- `S01-03`：实现无外部依赖的独立 HTML Canvas；提供 `window.renderFWCollabState` 接口显示后续状态，不在浏览器复制规则。
- `S03-01`：完成 S01–S12 共 12 张核心地图、公开机制清单、私有通关见证、单图 HTML 和一页式地图总览。
- `S03-02`：发布 `.fwmap v2`，加入成对传送门、切换器、单向门、热相地块、光路/镜面、离散移动桥和滚动机关球；完成 S13–S24 增强地图。
- `S02-01`：实现 F/W 两个独立策略历史、同轮冻结观察、并行提交以及下一轮才送达的显式消息。
- `S02-02`：实现逐轮完整轨迹、动作理由、模型原始输出、前后状态哈希、确定性回放和交互式 HTML 播放器。
- `S04-01`：实现带私有通关证书的确定性种子生成器，以及跨文件严格解析、ID 与结构指纹去重。
- `S04-02`：实现双方移动/等待/受阻/无效输出/消息/并行动作/支援轮次/动作平衡等过程指标。
- `S04-03`：清理旧S01–S24课程/关内DAG，基于30种一级元机关生成L1–L6共72个状态谓词DAG；提供统一JSON Schema、读写验证、双层同构比较、独立SVG和总览。
- `S05-01`：实现离线脚本策略与通用 OpenAI Responses API 双 LLM 适配器；模型错误会记录并替换为 WAIT，不中断 episode。
- `S04-06`：冻结 `fwcollab.symbol_rules.v3.1`；完整公开规则覆盖同步结算、全部元组件、门/桥占据和延迟消息。新模型使用结构化 `INFO/READY/HOLDING/CROSSED/RELEASE/BLOCKED` 消息；旧字符串消息继续兼容。
- `S04-07`：新增 R01–R12 二维房间核心图。12/12 空间拓扑唯一、含实际纵向动作和分支，12/12 私有见证成功，DAG 节点与边全部绑定；生成逐轮 JSON/HTML 和 DAG 进度。
- `S04-08`：完成72张L1–L7全机制二维课程；升级v3使实例控制网络可与传送、单向、热相和光路共存。M01–M30全部落地，72/72语义与拓扑唯一，72/72私有单步见证成功并可重放。
- `S04-09`：发布规则 v3.2 和紧凑 `agent_observation.v1`；模型输入新增上一轮公开事件、双方反馈、轮数预算、共享已送达消息、精确坐标与镜面接线，删除墙坐标和重复地图/状态视图；支持 `message=null`、`emergent/protocol_assisted` 分离及可选两轮候选 DAG 协商，并保持旧 V4 轨迹哈希可回放。
- `S04-10`：实现私有DAG协作过程评估器；逐轮输出节点完成、OR分支豁免、Progress AUC、依赖违规、状态回归、handoff、clean handoff、提前释放、有效保持/等待和结构化失败阶段；从72图按协作结构确定性选择24图diagnostic set，选择过程不读取模型结果。
- timeout 后置诊断现在区分规划循环、互等、受阻循环、可证明的压力板自锁和纯预算耗尽；旧 L5/L7 已分别回归为 `planning_cycle` 与 `irreversible_deadlock`。

## 运行证据

| 命令 | 退出码 | 结果 |
|---|---:|---|
| `python -m pytest tests/symbolic -q` | 0 | 59 passed；包括紧凑双agent观察、公开状态增量、两种协作提示条件、DAG协商、v3增强机制共存和72张M01–M30地图验收 |
| `symbol-validate`、`symbol-run --provider witness`、`symbol-replay`、`symbol-render-trace`（S01 v3.2 smoke） | 0 | 14轮 `team_success`，0模型错误，逐轮哈希重放与HTML生成成功 |
| `symbol-replay`（旧 V4-L1-001 v3.1真实模型轨迹） | 0 | 41轮 `team_success`，升级后最终状态哈希一致，证明新便利字段未破坏旧回放 |
| `symbol-run --provider witness` + `symbol-replay`（V4-L1-001 v3.2） | 0 | 72轮 `team_success`；首轮模型观察比完整审计观察少32.5%，仍保留双层地图、全部接线、规则、状态、预算与事件接口 |
| `python -m fwcollab.cli dag-generate` | 0 | 生成72个独立JSON、72个SVG和一页式总览；L1–L6为4/8/12/16/18/14，12个诊断、60个计分 |
| `python -m fwcollab.cli dag-validate --input eval_private/dag_curriculum/catalog.json` | 0 | 72/72类型唯一，68个纯拓扑签名组，单一拓扑最多复用3次，M01–M30全部覆盖 |
| `python -m fwcollab.cli dag-compare` 自比较 | 0 | 拓扑与受控类型均等价，两个分数均为1.0；自然语言和表示层字段不参与比较 |
| 对 S01–S24 循环执行 `python -m fwcollab.cli symbol-validate` | 0 | 24/24 解析成功并输出初始状态 hash |
| 对 S01–S24 循环执行 `python -m fwcollab.cli symbol-render` | 0 | 生成 24 个无外部依赖的单图 HTML |
| `python -m fwcollab.cli symbol-render-suite --map-dir maps/symbolic --output artifacts/symbolic/core_gallery.html` | 0 | 生成 24 图总览 |
| Edge headless 打开总览并截图 | 0 | `artifacts/symbolic/core_gallery.png`，24 张地图及 v2 新符号均可辨认 |
| 对 S01–S24 执行 `symbol-run --provider witness` 后执行 `symbol-replay` | 0 | 24/24 达到 `team_success` 且逐轮哈希一致；4–33 轮，平均 19.62 轮 |
| `symbol-run`，F/W 均为独立 `gpt-5.4-mini`，S02，40 轮上限 | 0 | 9 轮成功；18 次真实模型调用，0 次模型错误；墙钟时间 21.237 秒 |
| `symbol-render-trace` 渲染 S02 真实轨迹 | 0 | `artifacts/runs/S02_dual_llm_unit_steps.html` 可逐轮播放并查看动作、理由与消息 |
| S01–S24 真实双 LLM 完整套件，单图 80 轮上限 | 0 | 21/24 成功，S04/S12/S15 timeout；1132 次模型调用，0 模型格式错误，24/24 回放与 HTML 通过 |
| `python -m fwcollab.cli spatial-generate` | 0 | R01–R12 共12图，空间拓扑12/12唯一，生成地图、DAG、见证、轨迹与HTML |
| 对 R01–R12 执行 `symbol-validate` 和 `symbol-replay` | 0 | 12/12 严格解析；12/12 单格见证轨迹达到 `team_success` 且哈希一致 |
| Edge headless 打开当前72图总览并截图 | 0 | `artifacts/symbolic/spatial_curriculum_full_v4_gallery.png` 可辨认当前全机制地图 |
| `spatial-generate --full-curriculum` | 0 | 生成72张V4地图、DAG、私有见证、总览与逐轮HTML；M01–M30覆盖，72/72语义和拓扑唯一 |
| V4全机制72图真实双LLM运行 | 0 | `gpt-5.4-mini`×2；14成功、57超时、1危险失败；34,670次调用、980次可恢复错误、无runner error，耗时11,394.533秒 |
| V4真实轨迹回放与渲染 | 0 | 72/72状态哈希一致；生成72个交互HTML、汇总索引与按难度/主机制阶段分析 |
| `python -m pytest tests/symbolic -q`（DAG协作评估器） | 0 | 64 passed；含成功handoff、提前释放、关门穿越、OR合法替代分支和taxonomy不伪造测试 |
| `scripts/evaluate_collaboration_runs.py` | 0 | 离线评估GPT-5.5、Gemini 3.7 Flash和GPT-5-mini各72条现有轨迹；生成逐图timeline、指标汇总和失败节点报告 |
| `scripts/select_collaboration_diagnostics.py` | 0 | 选出24张结构诊断图；24/24 ID唯一，L1–L7配额为2/3/3/4/4/4/4，明确C4/C5/C7覆盖为空 |

## 数据与实验

当前公开开发地图为24张（12张 v1 基础图、12张 v2 增强图），正式生成数据为72张V4全机制空间图。V4已实际覆盖M01–M30，72/72有成功见证和可验证轨迹；v3.1 输入下真实双模型结果为14/72成功。v3.2 输入协议已经实现但尚未重新执行72图真实模型实验，因此两版成绩不能混合。抽象72-DAG仍作为私有结构研究集保留，V4没有声称逐节点复刻每个抽象DAG。外部游戏素材：0；HTML使用自行绘制的色块和文字。

## 下一项

`S05-02`：先在24图 Collaboration Diagnostic Set 上运行 Normal、No-Communication 和 GPT/Gemini 双向 Cross-play，再补齐held-out切分、重复seed、外部人工审核和正式发布报告。
