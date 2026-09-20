"""State-predicate DAG validation, comparison, and dependency-free rendering."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from html import escape
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

DAG_FORMAT = "fwcollab.symbolic.state_dag.v1"
CATALOG_FORMAT = "fwcollab.symbolic.state_dag_catalog.v1"
DIFFICULTY_COUNTS = {"L1": 4, "L2": 8, "L3": 12, "L4": 16, "L5": 18, "L6": 14}
DIFFICULTY_VALUES = {*DIFFICULTY_COUNTS, "L7"}
OWNER_VALUES = {"agent_a", "agent_b", "team", "environment"}
NODE_KINDS = {"condition", "state", "event", "goal"}
JOIN_VALUES = {"all", "any"}
EDGE_RELATIONS = {"requires", "enables", "maintains", "handoff", "synchronizes"}
MOTIF_RE = re.compile(r"M(?:0[1-9]|[12][0-9]|30)$")

ComparisonMode = Literal["topology", "typed"]


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path: str | Path, data: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _require_nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _graph_parts(graph: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(item, dict) for item in nodes):
        raise ValueError("DAG nodes must be a non-empty list of objects")
    if not isinstance(edges, list) or not all(isinstance(item, dict) for item in edges):
        raise ValueError("DAG edges must be a list of objects")
    return nodes, edges


def _adjacency(
    graph: Mapping[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[tuple[str, str], str]]:
    nodes, edges = _graph_parts(graph)
    outgoing = {node["id"]: set() for node in nodes}
    incoming = {node["id"]: set() for node in nodes}
    edge_labels: dict[tuple[str, str], str] = {}
    for edge in edges:
        source, target = edge["from"], edge["to"]
        outgoing[source].add(target)
        incoming[target].add(source)
        edge_labels[(source, target)] = edge["relation"]
    return outgoing, incoming, edge_labels


def topological_order(graph: Mapping[str, Any]) -> list[str]:
    outgoing, incoming, _ = _adjacency(graph)
    indegree = {node_id: len(sources) for node_id, sources in incoming.items()}
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(ordered) != len(outgoing):
        raise ValueError("state dependency graph contains a cycle")
    return ordered


def event_layers(graph: Mapping[str, Any]) -> list[list[str]]:
    outgoing, incoming, _ = _adjacency(graph)
    indegree = {node_id: len(sources) for node_id, sources in incoming.items()}
    remaining = set(outgoing)
    layers: list[list[str]] = []
    while remaining:
        layer = sorted(node_id for node_id in remaining if indegree[node_id] == 0)
        if not layer:
            raise ValueError("state dependency graph contains a cycle")
        layers.append(layer)
        for source in layer:
            remaining.remove(source)
            for target in outgoing[source]:
                indegree[target] -= 1
    return layers


def graph_metrics(graph: Mapping[str, Any]) -> dict[str, int]:
    nodes, edges = _graph_parts(graph)
    outgoing, incoming, _ = _adjacency(graph)
    ordered = topological_order(graph)
    depth = {node_id: 0 for node_id in ordered}
    for node_id in ordered:
        for target in outgoing[node_id]:
            depth[target] = max(depth[target], depth[node_id] + 1)
    layers = event_layers(graph)
    relations = Counter(edge["relation"] for edge in edges)
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "longest_path": max(depth.values(), default=0),
        "max_parallel_width": max(map(len, layers), default=0),
        "branch_nodes": sum(len(outgoing[node_id]) > 1 for node_id in outgoing),
        "join_nodes": sum(len(incoming[node_id]) > 1 for node_id in incoming),
        "any_joins": sum(node.get("join") == "any" for node in nodes),
        "handoffs": relations["handoff"],
        "maintains": relations["maintains"],
    }


def validate_state_dag(graph: dict[str, Any]) -> None:
    if graph.get("format") != DAG_FORMAT:
        raise ValueError("unsupported state DAG format")
    _require_nonempty_string(graph.get("id"), "DAG id")
    if graph.get("difficulty") not in DIFFICULTY_VALUES:
        raise ValueError("difficulty must be one of L1 through L7")
    _require_nonempty_string(graph.get("title"), "DAG title")
    _require_nonempty_string(graph.get("summary"), "DAG summary")
    _require_nonempty_string(graph.get("topology_family"), "topology_family")
    mechanisms = graph.get("mechanisms")
    if (
        not isinstance(mechanisms, list)
        or not mechanisms
        or not all(isinstance(item, str) and MOTIF_RE.fullmatch(item) for item in mechanisms)
        or len(mechanisms) != len(set(mechanisms))
    ):
        raise ValueError("mechanisms must be a unique non-empty list containing M01 through M30")

    nodes, edges = _graph_parts(graph)
    node_by_id: dict[str, dict[str, Any]] = {}
    used_mechanisms: set[str] = set()
    start_ids: list[str] = []
    success_ids: list[str] = []
    for node in nodes:
        required = {"id", "kind", "owner", "join", "predicate", "label"}
        if not required <= set(node):
            raise ValueError(f"node is missing fields {sorted(required - set(node))}: {node!r}")
        node_id = _require_nonempty_string(node["id"], "node id")
        if node_id in node_by_id:
            raise ValueError(f"duplicate node id: {node_id}")
        if node["kind"] not in NODE_KINDS:
            raise ValueError(f"invalid node kind: {node['kind']!r}")
        if node["owner"] not in OWNER_VALUES:
            raise ValueError(f"invalid node owner: {node['owner']!r}")
        if node["join"] not in JOIN_VALUES:
            raise ValueError(f"invalid join operator: {node['join']!r}")
        _require_nonempty_string(node["label"], f"node {node_id} label")
        predicate = node["predicate"]
        if not isinstance(predicate, dict) or set(predicate) != {"op", "motif", "state", "phase"}:
            raise ValueError(f"node {node_id} predicate requires op, motif, state and phase")
        op = _require_nonempty_string(predicate["op"], f"node {node_id} predicate.op")
        motif = predicate["motif"]
        if motif != "SYSTEM" and (not isinstance(motif, str) or not MOTIF_RE.fullmatch(motif)):
            raise ValueError(f"node {node_id} has invalid predicate motif")
        _require_nonempty_string(predicate["state"], f"node {node_id} predicate.state")
        if not isinstance(predicate["phase"], int) or predicate["phase"] < 0:
            raise ValueError(f"node {node_id} predicate.phase must be a non-negative integer")
        if motif != "SYSTEM":
            used_mechanisms.add(motif)
        if op == "start":
            start_ids.append(node_id)
        if op == "team_success":
            success_ids.append(node_id)
        node_by_id[node_id] = node

    if len(start_ids) != 1:
        raise ValueError("DAG requires exactly one start predicate")
    if len(success_ids) != 1:
        raise ValueError("DAG requires exactly one team_success predicate")
    if used_mechanisms != set(mechanisms):
        raise ValueError(
            f"mechanism list mismatch: missing={sorted(used_mechanisms - set(mechanisms))}, "
            f"unused={sorted(set(mechanisms) - used_mechanisms)}"
        )

    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if set(edge) != {"from", "to", "relation"}:
            raise ValueError(f"edge requires exactly from, to and relation: {edge!r}")
        source, target = edge["from"], edge["to"]
        if source not in node_by_id or target not in node_by_id:
            raise ValueError(f"edge references an unknown node: {edge!r}")
        if source == target or (source, target) in seen_edges:
            raise ValueError(f"duplicate or self edge: {edge!r}")
        if edge["relation"] not in EDGE_RELATIONS:
            raise ValueError(f"invalid edge relation: {edge['relation']!r}")
        seen_edges.add((source, target))

    ordered = topological_order(graph)
    outgoing, incoming, _ = _adjacency(graph)
    start_id, success_id = start_ids[0], success_ids[0]
    if incoming[start_id] or outgoing[success_id]:
        raise ValueError("start must be a source and team_success must be a sink")
    reachable = {start_id}
    for node_id in ordered:
        if node_id in reachable:
            reachable.update(outgoing[node_id])
    if reachable != set(node_by_id):
        raise ValueError(f"all nodes must be reachable from start: {sorted(set(node_by_id) - reachable)}")
    contributes = {success_id}
    for node_id in reversed(ordered):
        if any(target in contributes for target in outgoing[node_id]):
            contributes.add(node_id)
    if contributes != set(node_by_id):
        raise ValueError(f"all nodes must contribute to team_success: {sorted(set(node_by_id) - contributes)}")
    for node_id, node in node_by_id.items():
        if node["join"] == "any" and len(incoming[node_id]) < 2:
            raise ValueError(f"node {node_id} uses any join but has fewer than two prerequisites")


def _owner_token(owner: str, swap_agents: bool) -> str:
    if not swap_agents:
        return owner
    if owner == "agent_a":
        return "agent_b"
    if owner == "agent_b":
        return "agent_a"
    return owner


def _node_token(node: Mapping[str, Any], mode: ComparisonMode, swap_agents: bool = False) -> str:
    if mode == "topology":
        return str(node["join"])
    predicate = node["predicate"]
    return "|".join(
        (
            str(node["kind"]),
            _owner_token(str(node["owner"]), swap_agents),
            str(node["join"]),
            str(predicate["op"]),
            str(predicate["motif"]),
            str(predicate["state"]),
        )
    )


def _edge_token(relation: str, mode: ComparisonMode) -> str:
    return "edge" if mode == "topology" else relation


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wl_signature(graph: Mapping[str, Any], mode: ComparisonMode, swap_agents: bool = False) -> str:
    nodes, edges = _graph_parts(graph)
    node_by_id = {node["id"]: node for node in nodes}
    outgoing, incoming, edge_labels = _adjacency(graph)
    colors = {node_id: _sha(_node_token(node, mode, swap_agents)) for node_id, node in node_by_id.items()}
    for _ in range(len(nodes)):
        next_colors: dict[str, str] = {}
        for node_id in node_by_id:
            predecessor = sorted(
                f"{_edge_token(edge_labels[(source, node_id)], mode)}:{colors[source]}" for source in incoming[node_id]
            )
            successor = sorted(
                f"{_edge_token(edge_labels[(node_id, target)], mode)}:{colors[target]}" for target in outgoing[node_id]
            )
            next_colors[node_id] = _sha(
                json.dumps([colors[node_id], predecessor, successor], separators=(",", ":"), ensure_ascii=True)
            )
        if next_colors == colors:
            break
        colors = next_colors
    colored_edges = sorted(
        (colors[edge["from"]], _edge_token(edge["relation"], mode), colors[edge["to"]]) for edge in edges
    )
    payload = [len(nodes), len(edges), sorted(colors.values()), colored_edges]
    return _sha(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


def graph_signature(graph: Mapping[str, Any], mode: ComparisonMode) -> str:
    """Return an ID/order/text-invariant signature; typed mode also ignores A/B agent renaming."""

    if mode == "topology":
        return _wl_signature(graph, mode)
    return min(_wl_signature(graph, mode, False), _wl_signature(graph, mode, True))


def _isomorphic_once(
    left: Mapping[str, Any], right: Mapping[str, Any], mode: ComparisonMode, swap_right_agents: bool
) -> bool:
    left_nodes, left_edges = _graph_parts(left)
    right_nodes, right_edges = _graph_parts(right)
    if len(left_nodes) != len(right_nodes) or len(left_edges) != len(right_edges):
        return False
    left_by_id = {node["id"]: node for node in left_nodes}
    right_by_id = {node["id"]: node for node in right_nodes}
    left_out, left_in, left_labels = _adjacency(left)
    right_out, right_in, right_labels = _adjacency(right)

    def left_key(node_id: str) -> tuple[str, int, int]:
        return (_node_token(left_by_id[node_id], mode), len(left_in[node_id]), len(left_out[node_id]))

    def right_key(node_id: str) -> tuple[str, int, int]:
        return (
            _node_token(right_by_id[node_id], mode, swap_right_agents),
            len(right_in[node_id]),
            len(right_out[node_id]),
        )

    candidates = {
        left_id: [right_id for right_id in right_by_id if left_key(left_id) == right_key(right_id)]
        for left_id in left_by_id
    }
    if any(not values for values in candidates.values()):
        return False
    order = sorted(
        left_by_id,
        key=lambda node_id: (len(candidates[node_id]), -(len(left_in[node_id]) + len(left_out[node_id])), node_id),
    )
    mapping: dict[str, str] = {}
    used: set[str] = set()

    def compatible(left_id: str, right_id: str) -> bool:
        for mapped_left, mapped_right in mapping.items():
            left_forward = (left_id, mapped_left) in left_labels
            right_forward = (right_id, mapped_right) in right_labels
            if left_forward != right_forward:
                return False
            if left_forward and _edge_token(left_labels[(left_id, mapped_left)], mode) != _edge_token(
                right_labels[(right_id, mapped_right)], mode
            ):
                return False
            left_backward = (mapped_left, left_id) in left_labels
            right_backward = (mapped_right, right_id) in right_labels
            if left_backward != right_backward:
                return False
            if left_backward and _edge_token(left_labels[(mapped_left, left_id)], mode) != _edge_token(
                right_labels[(mapped_right, right_id)], mode
            ):
                return False
        return True

    def search(index: int) -> bool:
        if index == len(order):
            return True
        left_id = order[index]
        for right_id in candidates[left_id]:
            if right_id in used or not compatible(left_id, right_id):
                continue
            mapping[left_id] = right_id
            used.add(right_id)
            if search(index + 1):
                return True
            used.remove(right_id)
            del mapping[left_id]
        return False

    return search(0)


def graphs_isomorphic(left: Mapping[str, Any], right: Mapping[str, Any], mode: ComparisonMode) -> bool:
    if graph_signature(left, mode) != graph_signature(right, mode):
        return False
    if mode == "topology":
        return _isomorphic_once(left, right, mode, False)
    return _isomorphic_once(left, right, mode, False) or _isomorphic_once(left, right, mode, True)


def _counter_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    left_counter, right_counter = Counter(left), Counter(right)
    keys = set(left_counter) | set(right_counter)
    if not keys:
        return 1.0
    intersection = sum(min(left_counter[key], right_counter[key]) for key in keys)
    union = sum(max(left_counter[key], right_counter[key]) for key in keys)
    return intersection / union


def _number_similarity(left: int, right: int) -> float:
    return 1.0 if left == right == 0 else 1.0 - abs(left - right) / max(left, right, 1)


def _topology_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    if graphs_isomorphic(left, right, "topology"):
        return 1.0
    left_metrics, right_metrics = graph_metrics(left), graph_metrics(right)
    left_out, left_in, _ = _adjacency(left)
    right_out, right_in, _ = _adjacency(right)
    numeric_fields = ("nodes", "edges", "longest_path", "max_parallel_width", "branch_nodes", "join_nodes", "any_joins")
    numeric = [_number_similarity(left_metrics[field], right_metrics[field]) for field in numeric_fields]
    degree = [
        _counter_similarity((str(len(values)) for values in left_in.values()), (str(len(values)) for values in right_in.values())),
        _counter_similarity((str(len(values)) for values in left_out.values()), (str(len(values)) for values in right_out.values())),
    ]
    return sum(numeric + degree) / (len(numeric) + len(degree))


def _typed_similarity(left: Mapping[str, Any], right: Mapping[str, Any], topology_score: float) -> float:
    if graphs_isomorphic(left, right, "typed"):
        return 1.0
    left_nodes, left_edges = _graph_parts(left)
    right_nodes, right_edges = _graph_parts(right)

    def node_values(nodes: list[dict[str, Any]], field: str) -> list[str]:
        if field in {"kind", "owner", "join"}:
            return [str(node[field]) for node in nodes]
        return [str(node["predicate"][field]) for node in nodes]

    owner_direct = _counter_similarity(node_values(left_nodes, "owner"), node_values(right_nodes, "owner"))
    swapped_right_owners = [_owner_token(str(node["owner"]), True) for node in right_nodes]
    owner_swapped = _counter_similarity(node_values(left_nodes, "owner"), swapped_right_owners)
    semantic_parts = [
        _counter_similarity(node_values(left_nodes, "kind"), node_values(right_nodes, "kind")),
        max(owner_direct, owner_swapped),
        _counter_similarity(node_values(left_nodes, "op"), node_values(right_nodes, "op")),
        _counter_similarity(node_values(left_nodes, "motif"), node_values(right_nodes, "motif")),
        _counter_similarity(node_values(left_nodes, "state"), node_values(right_nodes, "state")),
        _counter_similarity((edge["relation"] for edge in left_edges), (edge["relation"] for edge in right_edges)),
    ]
    return 0.65 * topology_score + 0.35 * (sum(semantic_parts) / len(semantic_parts))


def compare_state_dags(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare DAGs without using titles, labels, node IDs, JSON order, coordinates, or agent A/B naming."""

    validate_state_dag(reference)
    validate_state_dag(candidate)
    topology_score = _topology_similarity(reference, candidate)
    typed_score = _typed_similarity(reference, candidate, topology_score)
    return {
        "format": "fwcollab.symbolic.state_dag_comparison.v1",
        "reference_id": reference["id"],
        "candidate_id": candidate["id"],
        "topology_equivalent": graphs_isomorphic(reference, candidate, "topology"),
        "typed_equivalent": graphs_isomorphic(reference, candidate, "typed"),
        "topology_score": round(topology_score, 6),
        "typed_score": round(typed_score, 6),
        "reference_metrics": graph_metrics(reference),
        "candidate_metrics": graph_metrics(candidate),
        "ignored_fields": ["id", "title", "summary", "label", "node_id", "phase_number", "json_order", "coordinates"],
        "agent_role_swap_invariant": True,
    }


def validate_curriculum_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("format") != CATALOG_FORMAT:
        raise ValueError("unsupported state DAG catalog format")
    if catalog.get("visibility") != "private_reference_catalog":
        raise ValueError("reference DAG catalog must be marked private_reference_catalog")
    dags = catalog.get("dags")
    if not isinstance(dags, list):
        raise ValueError("catalog dags must be a list")
    if len(dags) != sum(DIFFICULTY_COUNTS.values()):
        raise ValueError(f"catalog must contain {sum(DIFFICULTY_COUNTS.values())} DAGs")
    distribution = Counter(graph.get("difficulty") for graph in dags)
    if dict(sorted(distribution.items())) != DIFFICULTY_COUNTS:
        raise ValueError(f"difficulty distribution mismatch: {dict(distribution)}")
    ids: set[str] = set()
    typed_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topology_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for graph in dags:
        validate_state_dag(graph)
        graph_id = graph["id"]
        if graph_id in ids:
            raise ValueError(f"duplicate DAG id: {graph_id}")
        ids.add(graph_id)
        typed_buckets[graph_signature(graph, "typed")].append(graph)
        topology_buckets[graph_signature(graph, "topology")].append(graph)
    for bucket in typed_buckets.values():
        for index, left in enumerate(bucket):
            for right in bucket[index + 1 :]:
                if graphs_isomorphic(left, right, "typed"):
                    raise ValueError(f"typed duplicate DAGs: {left['id']} and {right['id']}")
    max_reuse = catalog.get("comparison_contract", {}).get("max_topology_reuse", 3)
    if not isinstance(max_reuse, int) or max_reuse < 1:
        raise ValueError("comparison_contract.max_topology_reuse must be a positive integer")
    for bucket in topology_buckets.values():
        exact_groups: list[list[dict[str, Any]]] = []
        for graph in bucket:
            for group in exact_groups:
                if graphs_isomorphic(group[0], graph, "topology"):
                    group.append(graph)
                    break
            else:
                exact_groups.append([graph])
        for group in exact_groups:
            if len(group) > max_reuse:
                raise ValueError(f"topology reused {len(group)} times: {[graph['id'] for graph in group]}")


def _node_lines(label: str, limit: int = 16) -> tuple[str, str]:
    return label[:limit], label[limit : limit * 2]


def render_state_dag_svg(graph: dict[str, Any]) -> str:
    validate_state_dag(graph)
    nodes = {node["id"]: node for node in graph["nodes"]}
    layers = event_layers(graph)
    node_width, node_height = 220, 70
    x_gap, y_gap = 246, 104
    max_rows = max(map(len, layers))
    width = max(760, 48 + max_rows * x_gap)
    height = max(310, 115 + len(layers) * y_gap)
    positions: dict[str, tuple[int, int]] = {}
    for layer_index, layer in enumerate(layers):
        row_width = len(layer) * x_gap
        start_x = (width - row_width) // 2 + 13
        for row_index, node_id in enumerate(layer):
            positions[node_id] = (start_x + row_index * x_gap, 98 + layer_index * y_gap)

    colors = {
        "agent_a": ("#4c1d1d", "#fb7185"),
        "agent_b": ("#0c3559", "#38bdf8"),
        "environment": ("#3f3212", "#facc15"),
        "team": ("#31205f", "#c4b5fd"),
    }
    relation_colors = {
        "requires": "#94a3b8",
        "enables": "#60a5fa",
        "maintains": "#f59e0b",
        "handoff": "#f472b6",
        "synchronizes": "#a78bfa",
    }
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#0b1220"/>',
        f'<text x="24" y="32" fill="#f8fafc" font-family="system-ui,sans-serif" font-size="21" font-weight="700">{escape(graph["id"])} · {escape(graph["title"])}</text>',
        f'<text x="24" y="57" fill="#93c5fd" font-family="ui-monospace,monospace" font-size="12">{escape(graph["difficulty"])} · {escape(graph["topology_family"])} · {escape(", ".join(graph["mechanisms"]))}</text>',
        f'<text x="24" y="80" fill="#94a3b8" font-family="system-ui,sans-serif" font-size="12">{escape(graph["summary"][:100])}</text>',
    ]
    for edge in graph["edges"]:
        sx, sy = positions[edge["from"]]
        tx, ty = positions[edge["to"]]
        start_x, start_y = sx + node_width / 2, sy + node_height
        end_x, end_y = tx + node_width / 2, ty
        bend = (start_y + end_y) / 2
        color = relation_colors[edge["relation"]]
        dash = ' stroke-dasharray="6 4"' if edge["relation"] == "maintains" else ""
        lines.append(
            f'<path d="M{start_x} {start_y} C{start_x} {bend},{end_x} {bend},{end_x} {end_y}" '
            f'stroke="{color}" stroke-width="1.6" fill="none" marker-end="url(#arrow)"{dash}>'
            f'<title>{escape(edge["relation"])}</title></path>'
        )
    for node_id, (x, y) in positions.items():
        node = nodes[node_id]
        fill, stroke = colors[node["owner"]]
        first, second = _node_lines(str(node["label"]))
        predicate = node["predicate"]
        lines.extend(
            [
                f'<g class="state-node" data-node-id="{escape(node_id)}">',
                f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
                f'<text x="{x + 10}" y="{y + 17}" fill="{stroke}" font-family="ui-monospace,monospace" font-size="10" font-weight="700">{escape(node["owner"])} · {escape(predicate["motif"])}</text>',
                f'<text x="{x + 10}" y="{y + 39}" fill="#f8fafc" font-family="system-ui,sans-serif" font-size="12">{escape(first)}</text>',
                f'<text x="{x + 10}" y="{y + 56}" fill="#cbd5e1" font-family="system-ui,sans-serif" font-size="12">{escape(second)}</text>',
                f'<text x="{x + node_width - 10}" y="{y + 17}" text-anchor="end" fill="#94a3b8" font-family="ui-monospace,monospace" font-size="9">{escape(node["join"].upper())}</text>',
                "</g>",
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines)


def save_state_dag_svg(output: str | Path, graph: dict[str, Any]) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_state_dag_svg(graph), encoding="utf-8")


def save_catalog_artifacts(output_dir: str | Path, catalog: dict[str, Any]) -> list[Path]:
    validate_curriculum_catalog(catalog)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    cards: list[str] = []
    for graph in catalog["dags"]:
        level_dir = destination / graph["difficulty"]
        level_dir.mkdir(parents=True, exist_ok=True)
        rendered = render_state_dag_svg(graph)
        path = level_dir / f"{graph['id']}.svg"
        path.write_text(rendered, encoding="utf-8")
        generated.append(path)
        encoded = base64.b64encode(rendered.encode("utf-8")).decode("ascii")
        cards.append(
            f'<section data-level="{escape(graph["difficulty"])}"><h2>{escape(graph["id"])} · {escape(graph["title"])}</h2>'
            f'<p>{escape(graph["summary"])}</p><img alt="{escape(graph["id"])}" src="data:image/svg+xml;base64,{encoded}"></section>'
        )
    gallery = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FWCollab 72 DAG 课程</title><style>body{margin:0;padding:20px;background:#0b1220;color:#e2e8f0;font-family:system-ui,sans-serif}header,main{max-width:1800px;margin:auto}p{color:#94a3b8}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:16px}section{background:#111c31;border:1px solid #334155;border-radius:12px;padding:12px;overflow:auto}h2{margin:0 0 6px;font-size:16px;color:#93c5fd}section p{font-size:12px;margin:0 0 8px}img{display:block;width:100%;height:auto}</style></head><body><header><h1>FWCollab · 72 个状态依赖 DAG</h1><p>L1/L2 为诊断课程；L3–L6 为正式规划难度。图中的中文仅用于阅读，不参与机器比较。</p></header><main>""" + "".join(cards) + "</main></body></html>"
    gallery_path = destination / "index.html"
    gallery_path.write_text(gallery, encoding="utf-8")
    generated.append(gallery_path)
    return generated


def save_manifest_dag_artifacts(manifest_path: str | Path, output_dir: str | Path) -> list[Path]:
    """Render every per-task DAG referenced by a spatial curriculum manifest."""

    manifest_file = Path(manifest_path)
    manifest = load_json(manifest_file)
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("manifest.records must be a non-empty list")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    cards: list[str] = []
    counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for record in sorted(records, key=lambda item: (int(str(item["difficulty"])[1:]), str(item["id"]))):
        dag_reference = Path(str(record["dag"]))
        dag_path = dag_reference if dag_reference.is_file() else manifest_file.parent / dag_reference
        graph = load_json(dag_path)
        validate_state_dag(graph)
        graph_id = str(graph["id"])
        level = str(record["difficulty"])
        if graph_id in seen_ids:
            raise ValueError(f"duplicate DAG id in manifest: {graph_id}")
        if graph["difficulty"] != level:
            raise ValueError(f"difficulty mismatch for {graph_id}")
        seen_ids.add(graph_id)
        counts[level] += 1

        level_dir = destination / level
        svg_path = level_dir / f"{graph_id}.svg"
        save_state_dag_svg(svg_path, graph)
        generated.append(svg_path)
        relative_svg = svg_path.relative_to(destination).as_posix()
        mechanisms = ", ".join(str(value) for value in graph["mechanisms"])
        cards.append(
            f'<section class="card" data-level="{escape(level)}">'
            f'<h2>{escape(str(record["id"]))}</h2>'
            f'<p>{escape(level)} · {len(graph["nodes"])} nodes · {len(graph["edges"])} edges · {escape(mechanisms)}</p>'
            f'<a href="{escape(relative_svg)}" target="_blank"><img loading="lazy" alt="{escape(graph_id)}" src="{escape(relative_svg)}"></a>'
            "</section>"
        )

    levels = sorted(counts, key=lambda value: int(value[1:]))
    buttons = ['<button class="active" data-level="all">全部</button>'] + [
        f'<button data-level="{escape(level)}">{escape(level)} · {counts[level]}</button>' for level in levels
    ]
    gallery = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>FWCollab V4 · 72任务依赖图</title><style>'
        'body{margin:0;padding:20px;background:#07101f;color:#e2e8f0;font-family:system-ui,sans-serif}'
        'header,main{max-width:1900px;margin:auto}header{position:sticky;top:0;z-index:2;background:#07101fee;padding:8px 0 14px;backdrop-filter:blur(8px)}'
        'h1{margin:0 0 6px}.note{margin:0 0 12px;color:#94a3b8}.filters{display:flex;gap:8px;flex-wrap:wrap}'
        'button{border:1px solid #334155;border-radius:999px;padding:7px 12px;background:#111c31;color:#cbd5e1;cursor:pointer}'
        'button.active{background:#2563eb;border-color:#60a5fa;color:white}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(580px,1fr));gap:16px}'
        '.card{background:#111c31;border:1px solid #334155;border-radius:12px;padding:12px;overflow:auto}.card.hidden{display:none}'
        'h2{margin:0 0 5px;font-size:17px;color:#93c5fd}.card p{margin:0 0 8px;color:#94a3b8;font:12px ui-monospace,monospace}'
        'img{display:block;width:100%;height:auto;background:#0b1220;border-radius:8px}'
        '</style></head><body><header><h1>FWCollab V4 · 72任务依赖图</h1>'
        '<p class="note">节点颜色表示执行者；箭头颜色表示 requires / enables / maintains / handoff / synchronizes。点击图片可单独打开 SVG。</p>'
        f'<nav class="filters">{"".join(buttons)}</nav></header><main>{"".join(cards)}</main>'
        '<script>document.querySelectorAll("button[data-level]").forEach(b=>b.onclick=()=>{'
        'document.querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));'
        'document.querySelectorAll(".card").forEach(c=>c.classList.toggle("hidden",b.dataset.level!=="all"&&c.dataset.level!==b.dataset.level));'
        '});</script></body></html>'
    )
    gallery_path = destination / "index.html"
    gallery_path.write_text(gallery, encoding="utf-8")
    generated.append(gallery_path)
    return generated
