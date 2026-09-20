# DAG Diversity Audit

审计日期：2026-09-15。该审计只读取冻结任务和 DAG，不修改 Benchmark v1。

## 结论

| 集合 | Concrete DAG instances | Unique dependency topologies | Normalized collaboration templates | Unique typed executable templates |
|---|---:|---:|---:|---:|
| Full Benchmark | 72 | 48 | 65 | 72 |
| Diagnostic Collaboration Set | 24 | 21 | 23 | 24 |

因此“当前有多少 DAG”需要按口径回答：完整集有 72 个逐题 DAG 实例；忽略文本、节点 ID、具体谓词、责任角色和边类型，只保留有向依赖结构与 `all/any` join 后，有 48 种图同构类；按协作阶段归一化后有 65 种 collaboration template；保留完整 executable collaboration semantics 后有 72 种 typed template。

## 去重定义

`dependency topology` 使用 `graphs_isomorphic(..., mode="topology")`：

- 忽略节点 ID、label 和顺序；
- 忽略 predicate、motif、owner 和 edge relation；
- 保留有向边结构以及节点的 `all/any` join。

`typed executable template` 使用 `graphs_isomorphic(..., mode="typed")`：

- 在上述结构上保留 node kind、owner、join；
- 保留 predicate 的 op、motif、state；
- 保留 edge relation；
- 允许整体交换 agent A/B，因此不会仅因角色改名而视为新模板。

`normalized collaboration template` 是论文叙事更直观的中间口径：忽略坐标、机关 ID、具体 motif 和空间布局；保留归一化后的支持者角色顺序、每阶段是 `hold` 还是 `persistent unlock`、`all/any` 合取方式以及 controller 数量。按此口径 Full-72 有 65 种，Diagnostic-24 有 23 种；诊断集中 `V4-L3-004/010` 属于同一归一化模板。若进一步只保留角色交替与 `hold/unlock`，Full-72 会压缩到 51 种，因此论文必须随数字同时声明归一化规则。

## Full-72 纯拓扑复用情况

48 个 dependency topology class 的大小分布为：36 个只出现一次、5 个出现两次、5 个出现三次、1 个出现五次、1 个出现六次。重复组如下：

- 6 次：`V4-L3-001/002/004/007/008/010`
- 5 次：`V4-L2-002/003/004/005/008`
- 3 次：`V4-L2-001/006/007`
- 3 次：`V4-L4-001/002/008`
- 3 次：`V4-L4-004/010/011`
- 3 次：`V4-L5-005/011/014`
- 3 次：`V4-L5-006/012/013`
- 2 次：`V4-L1-003/004`
- 2 次：`V4-L3-003/009`
- 2 次：`V4-L4-003/009`
- 2 次：`V4-L5-004/010`
- 2 次：`V4-L6-012/013`

这些纯拓扑重复组在 typed mode 下全部分开，因此没有 typed duplicate。

## Diagnostic-24

Diagnostic-24 有 21 种 dependency topology，只有三组各复用两次：

- `V4-L2-006/007`
- `V4-L3-004/010`
- `V4-L5-005/011`

24 个 typed executable template 全部唯一。

## 与 V4 manifest 的区别

`eval_private/spatial_curriculum_full_v4/manifest.json` 中原有的 `topology_unique=72` 是任务/空间构造签名，不应引用为 DAG graph topology 数。论文建议分别报告：

- 72 task/map instances；
- 48 unique DAG dependency topologies；
- 65 normalized collaboration templates；
- 72 unique typed executable DAG templates；
- Diagnostic-24 中为 21/23/24。

更粗的 C1–C7 taxonomy 只能说明协作 primitive 覆盖，不能替代 DAG template 多样性统计。
