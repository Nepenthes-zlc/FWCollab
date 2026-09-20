"""Deterministic construction of the 72-DAG FWCollab curriculum."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from fwcollab.symbolic.dag import (
    CATALOG_FORMAT,
    DAG_FORMAT,
    DIFFICULTY_COUNTS,
    graph_metrics,
    graph_signature,
    graphs_isomorphic,
    save_catalog_artifacts,
    save_json,
    validate_curriculum_catalog,
    validate_state_dag,
)


@dataclass(frozen=True, slots=True)
class Stage:
    op: str
    state: str
    label: str
    owner: str
    kind: str = "state"


@dataclass(frozen=True, slots=True)
class Motif:
    name: str
    stages: tuple[Stage, ...]
    hold_until_last: bool = False


def _s(op: str, state: str, label: str, owner: str, kind: str = "state") -> Stage:
    return Stage(op, state, label, owner, kind)


MOTIFS: dict[str, Motif] = {
    "M01": Motif("普通地面", (_s("traverse", "ground_crossed", "通过普通地面", "actor", "event"),)),
    "M02": Motif("墙体阻挡", (_s("plan", "detour_selected", "确定绕墙路线", "actor", "condition"), _s("traverse", "detour_crossed", "完成绕墙通行", "actor", "event"))),
    "M03": Motif("水池差异地形", (_s("assign", "water_safe_role", "分配水池安全角色", "team", "condition"), _s("traverse", "water_crossed", "安全角色通过水池", "actor", "event"))),
    "M04": Motif("熔岩差异地形", (_s("assign", "lava_safe_role", "分配熔岩安全角色", "team", "condition"), _s("traverse", "lava_crossed", "安全角色通过熔岩", "actor", "event"))),
    "M05": Motif("通用危险地形", (_s("plan", "hazard_route_safe", "确认危险区绕行路线", "team", "condition"), _s("traverse", "hazard_avoided", "双方避开危险区", "team", "event"))),
    "M06": Motif("可推箱子", (_s("reach", "crate_reachable", "角色到达箱子推动位", "actor", "condition"), _s("position", "crate_positioned", "箱子到达目标位置", "actor", "event"))),
    "M07": Motif("滚动机关球", (_s("align", "orb_direction_selected", "确定机关球推动方向", "actor", "condition"), _s("position", "orb_stopped", "机关球停在目标位置", "actor", "event"))),
    "M08": Motif("单向通行", (_s("commit", "return_resources_ready", "确认穿门前资源已就绪", "team", "condition"), _s("traverse", "oneway_crossed", "按允许方向通过单向门", "actor", "event"))),
    "M09": Motif("配对传送", (_s("reach", "portal_entry_reached", "到达传送门入口", "actor", "condition"), _s("transport", "portal_exit_reached", "从配对出口出现", "actor", "event"))),
    "M10": Motif("双角色专属出口", (_s("goal", "both_agents_at_exits", "双方到达各自专属出口", "team", "goal"),)),
    "M11": Motif("角色压板门", (_s("occupy", "plate_held", "角色持续占据压力板", "actor", "condition"), _s("activate", "door_open", "压力板门保持打开", "environment"), _s("traverse", "partner_crossed_door", "搭档通过压力板门", "partner", "event")), True),
    "M12": Motif("箱子压板门", (_s("position", "crate_on_plate", "箱子到达压力板", "actor", "event"), _s("activate", "door_open", "箱子压力板门打开", "environment"), _s("traverse", "team_crossed_door", "所需角色通过箱子门", "team", "event"))),
    "M13": Motif("机关球压板门", (_s("position", "orb_on_plate", "机关球停在压力板", "actor", "event"), _s("activate", "door_open", "机关球压力板门打开", "environment"), _s("traverse", "team_crossed_door", "所需角色通过机关球门", "team", "event"))),
    "M14": Motif("角色压板桥", (_s("occupy", "plate_held", "角色持续占据桥压力板", "actor", "condition"), _s("activate", "bridge_deployed", "压力板桥保持展开", "environment"), _s("traverse", "partner_crossed_bridge", "搭档通过压力板桥", "partner", "event")), True),
    "M15": Motif("箱子压板桥", (_s("position", "crate_on_plate", "箱子到达桥压力板", "actor", "event"), _s("activate", "bridge_deployed", "箱子压力板桥展开", "environment"), _s("traverse", "team_crossed_bridge", "所需角色通过箱子桥", "team", "event"))),
    "M16": Motif("机关球压板桥", (_s("position", "orb_on_plate", "机关球停在桥压力板", "actor", "event"), _s("activate", "bridge_deployed", "机关球压力板桥展开", "environment"), _s("traverse", "team_crossed_bridge", "所需角色通过机关球桥", "team", "event"))),
    "M17": Motif("持久拨杆门", (_s("reach", "lever_reached", "角色到达持久拨杆", "actor", "event"), _s("latch", "door_latched_open", "拨杆门永久打开", "environment"), _s("traverse", "partner_crossed_door", "搭档通过拨杆门", "partner", "event"))),
    "M18": Motif("持久拨杆桥", (_s("reach", "lever_reached", "角色到达桥拨杆", "actor", "event"), _s("latch", "bridge_latched_open", "拨杆桥永久展开", "environment"), _s("traverse", "partner_crossed_bridge", "搭档通过拨杆桥", "partner", "event"))),
    "M19": Motif("二态切换门", (_s("toggle", "toggle_desired", "切换器进入目标状态", "actor", "event"), _s("activate", "door_desired_state", "切换门状态正确", "environment"), _s("traverse", "partner_crossed_door", "搭档通过切换门", "partner", "event"))),
    "M20": Motif("二态切换桥", (_s("toggle", "toggle_desired", "桥切换器进入目标状态", "actor", "event"), _s("activate", "bridge_desired_state", "切换桥状态正确", "environment"), _s("traverse", "partner_crossed_bridge", "搭档通过切换桥", "partner", "event"))),
    "M21": Motif("直射光感门", (_s("align", "direct_beam_clear", "直射光路保持连通", "environment", "condition"), _s("sense", "sensor_lit", "光传感器点亮", "environment"), _s("activate", "door_open", "光感门打开", "environment"), _s("traverse", "agent_crossed_door", "角色通过光感门", "actor", "event"))),
    "M22": Motif("箱子遮光门", (_s("position", "crate_beam_state", "箱子到达目标遮光位置", "actor", "event"), _s("sense", "sensor_desired", "传感器达到目标状态", "environment"), _s("activate", "door_desired_state", "遮光门状态正确", "environment"), _s("traverse", "partner_crossed_door", "搭档通过遮光门", "partner", "event"))),
    "M23": Motif("机关球遮光门", (_s("position", "orb_beam_state", "机关球停在目标遮光位置", "actor", "event"), _s("sense", "sensor_desired", "传感器达到目标状态", "environment"), _s("activate", "door_desired_state", "遮光门状态正确", "environment"), _s("traverse", "partner_crossed_door", "搭档通过遮光门", "partner", "event"))),
    "M24": Motif("固定反射光感门", (_s("route", "fixed_reflection_aligned", "固定镜面光路成立", "environment", "condition"), _s("sense", "sensor_lit", "反射光点亮传感器", "environment"), _s("activate", "door_open", "反射光感门打开", "environment"), _s("traverse", "agent_crossed_door", "角色通过反射光感门", "actor", "event"))),
    "M25": Motif("固定反射箱子遮光门", (_s("position", "crate_reflection_state", "箱子对齐反射光路", "actor", "event"), _s("route", "fixed_reflection_desired", "固定反射光路达到目标状态", "environment"), _s("sense", "sensor_desired", "反射传感器状态正确", "environment"), _s("activate", "door_desired_state", "反射遮光门状态正确", "environment"), _s("traverse", "partner_crossed_door", "搭档通过反射遮光门", "partner", "event"))),
    "M26": Motif("固定反射机关球遮光门", (_s("position", "orb_reflection_state", "机关球对齐反射光路", "actor", "event"), _s("route", "fixed_reflection_desired", "固定反射光路达到目标状态", "environment"), _s("sense", "sensor_desired", "反射传感器状态正确", "environment"), _s("activate", "door_desired_state", "反射遮光门状态正确", "environment"), _s("traverse", "partner_crossed_door", "搭档通过反射遮光门", "partner", "event"))),
    "M27": Motif("可旋转反射光感门", (_s("toggle", "mirror_toggle_desired", "镜面切换器状态正确", "actor", "event"), _s("orient", "mirror_oriented", "可旋转镜面对准", "environment"), _s("sense", "sensor_lit", "旋转光路点亮传感器", "environment"), _s("activate", "door_open", "旋镜光感门打开", "environment"), _s("traverse", "partner_crossed_door", "搭档通过旋镜光感门", "partner", "event"))),
    "M28": Motif("可旋转反射箱子遮光门", (_s("position", "crate_beam_state", "箱子设置目标遮光状态", "actor", "event"), _s("toggle", "mirror_toggle_desired", "镜面切换器状态正确", "partner", "event"), _s("orient", "mirror_oriented", "可旋转镜面对准", "environment"), _s("sense", "sensor_desired", "旋转传感器状态正确", "environment"), _s("activate", "door_desired_state", "旋转遮光门状态正确", "environment"), _s("traverse", "team_crossed_door", "所需角色通过组合光门", "team", "event"))),
    "M29": Motif("可旋转反射机关球遮光门", (_s("position", "orb_beam_state", "机关球设置目标遮光状态", "actor", "event"), _s("toggle", "mirror_toggle_desired", "镜面切换器状态正确", "partner", "event"), _s("orient", "mirror_oriented", "可旋转镜面对准", "environment"), _s("sense", "sensor_desired", "旋转传感器状态正确", "environment"), _s("activate", "door_desired_state", "旋转遮光门状态正确", "environment"), _s("traverse", "team_crossed_door", "所需角色通过组合光门", "team", "event"))),
    "M30": Motif("冻结—融化热相系统", (_s("control", "thermal_control_reached", "角色到达热相控制器", "actor", "event"), _s("phase", "thermal_phase_safe", "热相地块处于所需状态", "environment"), _s("traverse", "partner_crossed_thermal", "所需角色通过热相区域", "partner", "event"))),
}

LEVEL_MOTIF_COUNTS = {
    "L1": (1,),
    "L2": (1, 2),
    "L3": (2, 3),
    "L4": (3, 4, 5),
    "L5": (4, 5, 6),
    "L6": (6, 7, 8),
}

LEVEL_POOLS = {
    "L1": tuple(f"M{index:02d}" for index in range(1, 11)),
    "L2": tuple(f"M{index:02d}" for index in range(2, 22)),
    "L3": tuple(f"M{index:02d}" for index in range(6, 31)),
    "L4": tuple(f"M{index:02d}" for index in range(3, 31)),
    "L5": tuple(f"M{index:02d}" for index in range(6, 31)),
    "L6": tuple(f"M{index:02d}" for index in range(3, 31)),
}

PATTERNS = (
    "serial",
    "parallel_all",
    "fan_out",
    "fan_in",
    "fork_join_tail",
    "two_wave",
    "nested",
    "or_tail",
    "ladder",
    "diamond_chain",
    "hold_handoff",
)


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, str]] = []
        self._edge_pairs: set[tuple[str, str]] = set()
        self._counter = 0

    def node(
        self,
        *,
        kind: str,
        owner: str,
        op: str,
        motif: str,
        state: str,
        phase: int,
        label: str,
        join: str = "all",
    ) -> str:
        node_id = f"n{self._counter:03d}"
        self._counter += 1
        self.nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "owner": owner,
                "join": join,
                "predicate": {"op": op, "motif": motif, "state": state, "phase": phase},
                "label": label,
            }
        )
        return node_id

    def edge(self, source: str, target: str, relation: str = "enables") -> None:
        if source == target or (source, target) in self._edge_pairs:
            return
        self._edge_pairs.add((source, target))
        self.edges.append({"from": source, "to": target, "relation": relation})

    def set_join(self, node_id: str, join: str) -> None:
        next(node for node in self.nodes if node["id"] == node_id)["join"] = join

    def owner(self, node_id: str) -> str:
        return str(next(node for node in self.nodes if node["id"] == node_id)["owner"])


def _resolve_owner(owner: str, primary: str) -> str:
    if owner == "actor":
        return primary
    if owner == "partner":
        return "agent_b" if primary == "agent_a" else "agent_a"
    return owner


def _cross_relation(builder: _GraphBuilder, source: str, target: str) -> str:
    owners = {builder.owner(source), builder.owner(target)}
    return "handoff" if owners == {"agent_a", "agent_b"} else "requires"


def _select_mechanisms(level: str, attempt: int) -> list[str]:
    if level == "L1":
        return [["M01"], ["M02"], ["M03"], ["M10"]][attempt % 4]
    pool = LEVEL_POOLS[level]
    counts = LEVEL_MOTIF_COUNTS[level]
    count = counts[attempt % len(counts)]
    start = (attempt + int(level[1:]) * 3) % len(pool)
    step = (attempt * 2 + 3) % len(pool)
    if step == 0:
        step = 1
    selected: list[str] = []
    cursor = start
    while len(selected) < count:
        motif = pool[cursor]
        if motif not in selected:
            selected.append(motif)
        cursor = (cursor + step) % len(pool)
        if len(selected) < count and cursor == start:
            cursor = (cursor + 1) % len(pool)
    return selected


def _connect_pattern(
    builder: _GraphBuilder,
    chains: list[list[str]],
    pattern: str,
    phase: int,
) -> list[str]:
    """Add cross-motif dependencies and return explicit synchronization nodes."""

    sync_nodes: list[str] = []
    count = len(chains)
    if count == 1:
        return sync_nodes
    if pattern in {"serial", "hold_handoff"}:
        for left, right in zip(chains, chains[1:]):
            builder.edge(left[-1], right[0], _cross_relation(builder, left[-1], right[0]))
        if pattern == "hold_handoff" and count >= 2:
            builder.edge(chains[0][0], chains[-1][-1], "maintains")
        return sync_nodes
    if pattern == "parallel_all":
        return sync_nodes
    if pattern == "fan_out":
        for chain in chains[1:]:
            builder.edge(chains[0][-1], chain[0], _cross_relation(builder, chains[0][-1], chain[0]))
        return sync_nodes
    if pattern == "fan_in":
        for chain in chains[:-1]:
            builder.edge(chain[-1], chains[-1][0], "synchronizes")
        builder.set_join(chains[-1][0], "all")
        return sync_nodes
    if pattern == "fork_join_tail" and count >= 3:
        prefix, branches, tail = chains[0], chains[1:-1], chains[-1]
        for branch in branches:
            builder.edge(prefix[-1], branch[0], _cross_relation(builder, prefix[-1], branch[0]))
        sync = builder.node(kind="state", owner="team", op="synchronize", motif="SYSTEM", state="fork_join", phase=phase, label="并行分支全部完成")
        for branch in branches:
            builder.edge(branch[-1], sync, "synchronizes")
        builder.edge(sync, tail[0], "enables")
        sync_nodes.append(sync)
        return sync_nodes
    if pattern == "two_wave" and count >= 4:
        split = max(2, count // 2)
        first, second = chains[:split], chains[split:]
        sync1 = builder.node(kind="state", owner="team", op="synchronize", motif="SYSTEM", state="wave_one", phase=phase, label="第一批并行子目标完成")
        for chain in first:
            builder.edge(chain[-1], sync1, "synchronizes")
        for chain in second:
            builder.edge(sync1, chain[0], "enables")
        sync_nodes.append(sync1)
        return sync_nodes
    if pattern == "nested" and count >= 4:
        builder.edge(chains[0][-1], chains[1][0], "requires")
        builder.edge(chains[0][-1], chains[2][0], "requires")
        builder.edge(chains[2][-1], chains[3][0], _cross_relation(builder, chains[2][-1], chains[3][0]))
        sync = builder.node(kind="state", owner="team", op="synchronize", motif="SYSTEM", state="nested_join", phase=phase, label="嵌套分支汇合")
        builder.edge(chains[1][-1], sync, "synchronizes")
        builder.edge(chains[3][-1], sync, "synchronizes")
        previous = sync
        for chain in chains[4:]:
            builder.edge(previous, chain[0], "enables")
            previous = chain[-1]
        sync_nodes.append(sync)
        return sync_nodes
    if pattern == "or_tail" and count >= 3:
        alternatives, tail = chains[:-1], chains[-1]
        sync = builder.node(kind="state", owner="team", op="alternative", motif="SYSTEM", state="one_route_complete", phase=phase, label="任一合法分支完成", join="any")
        for chain in alternatives:
            builder.edge(chain[-1], sync, "synchronizes")
        builder.edge(sync, tail[0], "enables")
        sync_nodes.append(sync)
        return sync_nodes
    if pattern == "ladder" and count >= 4:
        lanes = (chains[::2], chains[1::2])
        for lane in lanes:
            for left, right in zip(lane, lane[1:]):
                builder.edge(left[-1], right[0], _cross_relation(builder, left[-1], right[0]))
        for index in range(min(len(lanes[0]), len(lanes[1]) - 1)):
            builder.edge(lanes[0][index][-1], lanes[1][index + 1][0], "handoff")
        return sync_nodes
    if pattern == "diamond_chain" and count >= 4:
        previous: str | None = None
        for group_index in range(0, count, 2):
            group = chains[group_index : group_index + 2]
            if previous is not None:
                for chain in group:
                    builder.edge(previous, chain[0], "enables")
            if len(group) == 2:
                sync = builder.node(kind="state", owner="team", op="synchronize", motif="SYSTEM", state=f"diamond_{group_index // 2}", phase=phase, label=f"第{group_index // 2 + 1}组分支汇合")
                for chain in group:
                    builder.edge(chain[-1], sync, "synchronizes")
                previous = sync
                sync_nodes.append(sync)
            else:
                previous = group[0][-1]
        return sync_nodes
    for left, right in zip(chains, chains[1:]):
        builder.edge(left[-1], right[0], _cross_relation(builder, left[-1], right[0]))
    return sync_nodes


def build_state_dag(
    graph_id: str,
    difficulty: str,
    mechanisms: list[str],
    pattern: str,
    *,
    title: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    builder = _GraphBuilder()
    start = builder.node(kind="condition", owner="team", op="start", motif="SYSTEM", state="task_ready", phase=0, label="任务初始状态成立")
    chains: list[list[str]] = []
    for motif_index, motif_id in enumerate(mechanisms):
        motif = MOTIFS[motif_id]
        primary = "agent_a" if motif_index % 2 == 0 else "agent_b"
        chain: list[str] = []
        for stage_index, stage in enumerate(motif.stages):
            node_id = builder.node(
                kind=stage.kind,
                owner=_resolve_owner(stage.owner, primary),
                op=stage.op,
                motif=motif_id,
                state=stage.state,
                phase=motif_index,
                label=stage.label,
            )
            if chain:
                builder.edge(chain[-1], node_id, "enables")
            chain.append(node_id)
        chain_owners = {builder.owner(node_id) for node_id in chain}
        if motif.hold_until_last and len(chain) > 2:
            builder.edge(chain[0], chain[-1], "maintains")
        elif {"agent_a", "agent_b"} <= chain_owners and len(chain) > 2:
            builder.edge(chain[0], chain[-1], "handoff")
        chains.append(chain)

    _connect_pattern(builder, chains, pattern, len(mechanisms))
    node_ids = [node["id"] for node in builder.nodes]
    incoming = {node_id: 0 for node_id in node_ids}
    outgoing = {node_id: 0 for node_id in node_ids}
    for edge in builder.edges:
        incoming[edge["to"]] += 1
        outgoing[edge["from"]] += 1
    for chain in chains:
        if incoming[chain[0]] == 0:
            builder.edge(start, chain[0], "requires")

    success = builder.node(kind="goal", owner="team", op="team_success", motif="SYSTEM", state="all_required_goals", phase=len(mechanisms) + 1, label="双方满足团队成功条件")
    outgoing = {node["id"]: 0 for node in builder.nodes}
    for edge in builder.edges:
        outgoing[edge["from"]] += 1
    for node_id, degree in outgoing.items():
        if degree == 0 and node_id != success:
            builder.edge(node_id, success, "synchronizes")
    incoming_success = sum(edge["to"] == success for edge in builder.edges)
    if incoming_success > 1:
        builder.set_join(success, "all")

    names = " × ".join(MOTIFS[motif_id].name for motif_id in mechanisms)
    graph = {
        "format": DAG_FORMAT,
        "id": graph_id,
        "difficulty": difficulty,
        "evaluation_role": "diagnostic" if difficulty in {"L1", "L2"} else "scored",
        "visibility": "private_reference",
        "title": title or names,
        "summary": summary or f"以{pattern}拓扑组合{len(mechanisms)}种元机关：{names}。",
        "topology_family": pattern,
        "mechanisms": mechanisms,
        "nodes": builder.nodes,
        "edges": builder.edges,
    }
    validate_state_dag(graph)
    return graph


def _has_typed_duplicate(graph: dict[str, Any], existing: Iterable[dict[str, Any]]) -> bool:
    signature = graph_signature(graph, "typed")
    return any(graph_signature(other, "typed") == signature and graphs_isomorphic(graph, other, "typed") for other in existing)


def _topology_reuse(graph: dict[str, Any], existing: Iterable[dict[str, Any]]) -> int:
    signature = graph_signature(graph, "topology")
    return sum(
        graph_signature(other, "topology") == signature and graphs_isomorphic(graph, other, "topology")
        for other in existing
    )


def build_curriculum_catalog() -> dict[str, Any]:
    dags: list[dict[str, Any]] = []
    level_serials = Counter()

    # Preserve the user's three-door relay as an explicit L4 reference topology.
    relay = build_state_dag(
        "DAG-L4-001",
        "L4",
        ["M11", "M17", "M12", "M03", "M04", "M10"],
        "multi_stage_relay",
        title="三门接力：压板支援、拨杆回援与箱子占板",
        summary="火方保持角色压板帮助水方触发拨杆，水方永久开门回援火方，随后箱子占板打开共同终门并分流到专属出口。",
    )
    dags.append(relay)
    level_serials["L4"] = 1

    for level, target in DIFFICULTY_COUNTS.items():
        attempt = 0
        while level_serials[level] < target:
            mechanisms = _select_mechanisms(level, attempt)
            pattern = PATTERNS[(attempt + int(level[1:]) * 2) % len(PATTERNS)]
            serial = level_serials[level] + 1
            graph_id = f"DAG-{level}-{serial:03d}"
            graph = build_state_dag(graph_id, level, mechanisms, pattern)
            attempt += 1
            if _has_typed_duplicate(graph, dags) or _topology_reuse(graph, dags) >= 3:
                if attempt > 10000:
                    raise RuntimeError(f"unable to generate enough unique {level} DAGs")
                continue
            dags.append(graph)
            level_serials[level] += 1

    dags.sort(key=lambda graph: (int(graph["difficulty"][1:]), graph["id"]))
    mechanism_coverage = Counter(motif for graph in dags for motif in graph["mechanisms"])
    catalog = {
        "format": CATALOG_FORMAT,
        "visibility": "private_reference_catalog",
        "status": "abstract_dag_before_spatial_grounding",
        "count": len(dags),
        "difficulty_distribution": DIFFICULTY_COUNTS,
        "comparison_contract": {
            "max_topology_reuse": 3,
            "ignored_human_fields": ["id", "title", "summary", "label"],
            "ignored_representation_details": ["node_id", "phase_number", "json_order", "coordinates", "agent_a_b_global_swap"],
            "topology_mode": "directed structure plus all/any joins",
            "typed_mode": "topology plus controlled predicate, motif, state, owner and edge-relation tokens",
        },
        "mechanism_coverage": dict(sorted(mechanism_coverage.items())),
        "dags": dags,
    }
    validate_curriculum_catalog(catalog)
    return catalog


def write_curriculum(
    catalog_path: str | Path,
    graph_dir: str | Path,
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    catalog = build_curriculum_catalog()
    save_json(catalog_path, catalog)
    destination = Path(graph_dir)
    for graph in catalog["dags"]:
        save_json(destination / graph["difficulty"] / f"{graph['id']}.json", graph)
    if artifact_dir is not None:
        save_catalog_artifacts(artifact_dir, catalog)
    return catalog


def curriculum_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    validate_curriculum_catalog(catalog)
    topology_groups: dict[str, int] = defaultdict(int)
    for graph in catalog["dags"]:
        topology_groups[graph_signature(graph, "topology")] += 1
    return {
        "count": len(catalog["dags"]),
        "difficulty_distribution": dict(Counter(graph["difficulty"] for graph in catalog["dags"])),
        "diagnostic": sum(graph["evaluation_role"] == "diagnostic" for graph in catalog["dags"]),
        "scored": sum(graph["evaluation_role"] == "scored" for graph in catalog["dags"]),
        "typed_unique": len({graph_signature(graph, "typed") for graph in catalog["dags"]}),
        "topology_signature_groups": len(topology_groups),
        "max_topology_reuse": max(topology_groups.values()),
        "mechanism_coverage": catalog["mechanism_coverage"],
        "metric_ranges": {
            level: {
                key: [min(values), max(values)]
                for key in ("nodes", "edges", "longest_path", "max_parallel_width", "branch_nodes", "join_nodes")
                if (values := [graph_metrics(graph)[key] for graph in catalog["dags"] if graph["difficulty"] == level])
            }
            for level in DIFFICULTY_COUNTS
        },
    }
