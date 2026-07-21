"""Deterministic layout and visual styling for converted shader graphs.

The layered ranking and barycentric crossing-reduction stages are adapted to
Octanify's conversion workflow from the GPL-3.0 Node Arrange add-on by
Leonardo Pike-Excell: https://github.com/Leonardo-Pike-Excell/node-arrange.
This implementation remains dependency-free and never inserts or removes
shader nodes, links, frames, or reroutes.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from statistics import median
from typing import Iterable

import bpy


# Low-saturation graphite keeps the authored graph readable without competing
# with the converted result. Deep teal gives the Octane graph a clean visual
# identity while avoiding the previous heavy orange/brown cast.
CYCLES_NODE_COLOR = (0.16, 0.20, 0.27)
OCTANE_NODE_COLOR = (0.07, 0.30, 0.25)
GRAPH_GAP = 1000.0
HORIZONTAL_GAP = 180.0
VERTICAL_GAP = 80.0
COMPONENT_GAP = 220.0
MAX_CROSSING_SWEEPS = 12


@dataclass
class _LayoutItem:
    """A movable top-level node or frame cluster."""

    key: int
    anchor: bpy.types.Node
    members: tuple[bpy.types.Node, ...]
    order: int
    left: float
    bottom: float
    right: float
    top: float
    rank: int = 0

    @property
    def width(self) -> float:
        return max(self.right - self.left, 160.0)

    @property
    def height(self) -> float:
        return max(self.top - self.bottom, 140.0)


@dataclass(frozen=True)
class _LayoutEdge:
    source: int
    target: int
    source_socket: int
    target_socket: int
    order: int


def _rna_identity(value) -> int:
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _location_xy(node: bpy.types.Node) -> tuple[float, float]:
    location = getattr(node, "location", (0.0, 0.0))
    try:
        return float(location.x), float(location.y)
    except (AttributeError, TypeError):
        try:
            return float(location[0]), float(location[1])
        except (IndexError, KeyError, TypeError):
            return 0.0, 0.0


def _set_location(node: bpy.types.Node, x: float, y: float) -> None:
    try:
        node.location = (float(x), float(y))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        location = getattr(node, "location", None)
        if location is not None:
            try:
                location.x = float(x)
                location.y = float(y)
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass


def _absolute_location(node: bpy.types.Node) -> tuple[float, float]:
    """Return a node position in tree space, including frame parents."""
    x, y = _location_xy(node)
    parent = getattr(node, "parent", None)
    visited: set[int] = set()
    while parent is not None and _rna_identity(parent) not in visited:
        visited.add(_rna_identity(parent))
        parent_x, parent_y = _location_xy(parent)
        x += parent_x
        y += parent_y
        parent = getattr(parent, "parent", None)
    return x, y


def _node_size(node: bpy.types.Node) -> tuple[float, float]:
    width = max(float(getattr(node, "width", 0.0) or 0.0), 160.0)
    dimensions = getattr(node, "dimensions", None)
    height = float(getattr(dimensions, "y", 0.0) or 0.0)
    return width, max(height, 140.0)


def graph_bounds(nodes: Iterable[bpy.types.Node]) -> tuple[float, float, float, float]:
    """Return left, bottom, right, top bounds for a node collection."""
    materialized = list(nodes)
    if not materialized:
        return 0.0, 0.0, 0.0, 0.0

    left = float("inf")
    bottom = float("inf")
    right = float("-inf")
    top = float("-inf")
    for node in materialized:
        x, y = _absolute_location(node)
        width, height = _node_size(node)
        left = min(left, x)
        bottom = min(bottom, y - height)
        right = max(right, x + width)
        top = max(top, y)
    return left, bottom, right, top


def color_nodes(
    nodes: Iterable[bpy.types.Node],
    color: tuple[float, float, float],
    graph_kind: str,
    enabled: bool = True,
) -> None:
    """Tag graph ownership and optionally apply its editor color."""
    for node in nodes:
        try:
            node.use_custom_color = enabled
            if enabled:
                node.color = color
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            node["octanify_graph"] = graph_kind
        except (AttributeError, RuntimeError, TypeError):
            pass


def _socket_index(node: bpy.types.Node, socket, collection_name: str) -> int:
    sockets = getattr(node, collection_name, ())
    socket_id = _rna_identity(socket) if socket is not None else None
    for index, candidate in enumerate(sockets):
        if socket_id is not None and _rna_identity(candidate) == socket_id:
            return index
    return 0


def _build_layout_items(
    nodes: Iterable[bpy.types.Node],
) -> tuple[dict[int, _LayoutItem], dict[int, int]]:
    """Collapse each selected frame and its descendants into one layout item."""
    selected: list[bpy.types.Node] = []
    selected_ids: set[int] = set()
    for node in nodes:
        node_id = _rna_identity(node)
        if node_id in selected_ids:
            continue
        selected.append(node)
        selected_ids.add(node_id)

    source_order = {
        _rna_identity(node): index for index, node in enumerate(selected)
    }
    clusters: dict[int, list[bpy.types.Node]] = defaultdict(list)
    anchors: dict[int, bpy.types.Node] = {}

    for node in selected:
        parent = getattr(node, "parent", None)

        anchor = node
        visited: set[int] = set()
        while parent is not None:
            parent_id = _rna_identity(parent)
            if parent_id in visited or parent_id not in selected_ids:
                break
            visited.add(parent_id)
            anchor = parent
            parent = getattr(parent, "parent", None)

        anchor_id = _rna_identity(anchor)
        anchors[anchor_id] = anchor
        clusters[anchor_id].append(node)

    items: dict[int, _LayoutItem] = {}
    node_to_item: dict[int, int] = {}
    ordered_clusters = sorted(
        clusters.items(),
        key=lambda pair: min(source_order[_rna_identity(n)] for n in pair[1]),
    )
    for key, (anchor_id, members) in enumerate(ordered_clusters):
        left, bottom, right, top = graph_bounds(members)
        order = min(source_order[_rna_identity(node)] for node in members)
        item = _LayoutItem(
            key=key,
            anchor=anchors[anchor_id],
            members=tuple(members),
            order=order,
            left=left,
            bottom=bottom,
            right=right,
            top=top,
        )
        items[key] = item
        for node in members:
            node_to_item[_rna_identity(node)] = key
    return items, node_to_item


def _frame_depth(node: bpy.types.Node) -> int:
    depth = 0
    parent = getattr(node, "parent", None)
    visited: set[int] = set()
    while parent is not None and _rna_identity(parent) not in visited:
        visited.add(_rna_identity(parent))
        depth += 1
        parent = getattr(parent, "parent", None)
    return depth


def _is_descendant_of(node: bpy.types.Node, frame: bpy.types.Node) -> bool:
    frame_id = _rna_identity(frame)
    parent = getattr(node, "parent", None)
    visited: set[int] = set()
    while parent is not None and _rna_identity(parent) not in visited:
        parent_id = _rna_identity(parent)
        if parent_id == frame_id:
            return True
        visited.add(parent_id)
        parent = getattr(parent, "parent", None)
    return False


def _build_edges(
    node_tree: bpy.types.NodeTree,
    node_to_item: dict[int, int],
) -> list[_LayoutEdge]:
    edges: list[_LayoutEdge] = []
    seen: set[tuple[int, int, int, int]] = set()
    for link_order, link in enumerate(getattr(node_tree, "links", ())):
        source_node = getattr(link, "from_node", None)
        target_node = getattr(link, "to_node", None)
        source = node_to_item.get(_rna_identity(source_node)) if source_node else None
        target = node_to_item.get(_rna_identity(target_node)) if target_node else None
        if source is None or target is None or source == target:
            continue
        source_socket = _socket_index(
            source_node, getattr(link, "from_socket", None), "outputs"
        )
        target_socket = _socket_index(
            target_node, getattr(link, "to_socket", None), "inputs"
        )
        signature = (source, target, source_socket, target_socket)
        if signature in seen:
            continue
        seen.add(signature)
        edges.append(
            _LayoutEdge(
                source=source,
                target=target,
                source_socket=source_socket,
                target_socket=target_socket,
                order=link_order,
            )
        )
    return edges


def _weak_components(
    items: dict[int, _LayoutItem],
    edges: list[_LayoutEdge],
) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {key: set() for key in items}
    for edge in edges:
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)

    components: list[list[int]] = []
    visited: set[int] = set()
    for root in sorted(items, key=lambda key: items[key].order):
        if root in visited:
            continue
        component: list[int] = []
        queue = deque([root])
        visited.add(root)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(
                adjacency[current], key=lambda key: items[key].order
            ):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        components.append(component)
    return components


def _strong_components(
    keys: list[int],
    successors: dict[int, set[int]],
) -> list[list[int]]:
    """Return strongly connected components without recursive Python calls."""
    visited: set[int] = set()
    finish_order: list[int] = []
    for root in keys:
        if root in visited:
            continue
        stack: list[tuple[int, bool]] = [(root, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                finish_order.append(current)
                continue
            if current in visited:
                continue
            visited.add(current)
            stack.append((current, True))
            for target in sorted(successors[current], reverse=True):
                if target not in visited:
                    stack.append((target, False))

    predecessors: dict[int, set[int]] = {key: set() for key in keys}
    for source in keys:
        for target in successors[source]:
            predecessors[target].add(source)

    assigned: set[int] = set()
    result: list[list[int]] = []
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component: list[int] = []
        stack = [(root, False)]
        assigned.add(root)
        while stack:
            current, _ = stack.pop()
            component.append(current)
            for source in sorted(predecessors[current], reverse=True):
                if source in assigned:
                    continue
                assigned.add(source)
                stack.append((source, False))
        result.append(component)
    return result


def _assign_ranks(
    component: list[int],
    items: dict[int, _LayoutItem],
    edges: list[_LayoutEdge],
) -> None:
    """Assign dependency columns while collapsing cycles into one rank."""
    component_set = set(component)
    successors: dict[int, set[int]] = {key: set() for key in component}
    for edge in edges:
        if edge.source in component_set and edge.target in component_set:
            successors[edge.source].add(edge.target)

    ordered = sorted(component, key=lambda key: items[key].order)
    strong = _strong_components(ordered, successors)
    component_of = {
        key: index
        for index, members in enumerate(strong)
        for key in members
    }
    dag_successors: dict[int, set[int]] = {
        index: set() for index in range(len(strong))
    }
    indegree = {index: 0 for index in range(len(strong))}
    for source in component:
        source_component = component_of[source]
        for target in successors[source]:
            target_component = component_of[target]
            if source_component == target_component:
                continue
            if target_component in dag_successors[source_component]:
                continue
            dag_successors[source_component].add(target_component)
            indegree[target_component] += 1

    strong_order = {
        index: min(items[key].order for key in members)
        for index, members in enumerate(strong)
    }
    ranks = {index: 0 for index in range(len(strong))}
    ready = deque(sorted(
        (index for index, degree in indegree.items() if degree == 0),
        key=strong_order.get,
    ))
    while ready:
        source = ready.popleft()
        for target in sorted(
            dag_successors[source], key=strong_order.get
        ):
            ranks[target] = max(ranks[target], ranks[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)

    for key in component:
        items[key].rank = ranks[component_of[key]]


def _crossing_reduced_layers(
    component: list[int],
    items: dict[int, _LayoutItem],
    edges: list[_LayoutEdge],
) -> dict[int, list[int]]:
    """Order each rank with dummy vertices and deterministic barycenters."""
    component_set = set(component)
    relevant = [
        edge for edge in edges
        if edge.source in component_set and edge.target in component_set
        and items[edge.target].rank > items[edge.source].rank
    ]
    max_rank = max((items[key].rank for key in component), default=0)
    real_vertex = {key: ("r", key, 0) for key in component}
    vertex_item = {vertex: key for key, vertex in real_vertex.items()}
    layers: dict[int, list[tuple[str, int, int]]] = {
        rank: [] for rank in range(max_rank + 1)
    }
    vertex_rank: dict[tuple[str, int, int], int] = {}

    for key in component:
        vertex = real_vertex[key]
        rank = items[key].rank
        layers[rank].append(vertex)
        vertex_rank[vertex] = rank
    for rank in layers:
        layers[rank].sort(
            key=lambda vertex: (-items[vertex_item[vertex]].top,
                                items[vertex_item[vertex]].order)
        )

    predecessors: dict[
        tuple[str, int, int], list[tuple[tuple[str, int, int], float]]
    ] = defaultdict(list)
    successors: dict[
        tuple[str, int, int], list[tuple[tuple[str, int, int], float]]
    ] = defaultdict(list)

    for edge in relevant:
        source_rank = items[edge.source].rank
        target_rank = items[edge.target].rank
        chain = [real_vertex[edge.source]]
        for rank in range(source_rank + 1, target_rank):
            dummy = ("d", edge.order, rank)
            layers[rank].append(dummy)
            vertex_rank[dummy] = rank
            chain.append(dummy)
        chain.append(real_vertex[edge.target])

        source_bias = min(edge.source_socket, 32) * 0.02
        target_bias = min(edge.target_socket, 32) * 0.04
        for source, target in zip(chain, chain[1:]):
            successors[source].append((target, target_bias))
            predecessors[target].append((source, source_bias))

    def _positions() -> dict[tuple[str, int, int], int]:
        return {
            vertex: index
            for rank in layers
            for index, vertex in enumerate(layers[rank])
        }

    def _crossings() -> int:
        positions = _positions()
        total = 0
        for rank in range(max_rank):
            segments: list[tuple[int, int]] = []
            for source in layers[rank]:
                for target, _bias in successors[source]:
                    if vertex_rank.get(target) == rank + 1:
                        segments.append((positions[source], positions[target]))
            for first_index, first in enumerate(segments):
                for second in segments[first_index + 1:]:
                    if first[0] == second[0] or first[1] == second[1]:
                        continue
                    if (first[0] - second[0]) * (first[1] - second[1]) < 0:
                        total += 1
        return total

    def _score() -> tuple[int, int]:
        positions = _positions()
        socket_inversions = 0
        by_target: dict[int, list[_LayoutEdge]] = defaultdict(list)
        for edge in relevant:
            by_target[edge.target].append(edge)
        for target_edges in by_target.values():
            for first_index, first in enumerate(target_edges):
                for second in target_edges[first_index + 1:]:
                    if items[first.source].rank != items[second.source].rank:
                        continue
                    first_position = positions[real_vertex[first.source]]
                    second_position = positions[real_vertex[second.source]]
                    socket_delta = first.target_socket - second.target_socket
                    position_delta = first_position - second_position
                    if socket_delta * position_delta < 0:
                        socket_inversions += 1
        return _crossings(), socket_inversions

    best = {rank: list(vertices) for rank, vertices in layers.items()}
    best_score = _score()
    stagnant = 0
    for _sweep in range(MAX_CROSSING_SWEEPS):
        positions = _positions()
        for rank in range(1, max_rank + 1):
            current = {vertex: index for index, vertex in enumerate(layers[rank])}

            def _forward_key(vertex):
                neighbors = predecessors.get(vertex, ())
                if not neighbors:
                    return float(current[vertex]), current[vertex], vertex
                value = sum(positions[n] + bias for n, bias in neighbors) / len(neighbors)
                return value, current[vertex], vertex

            layers[rank].sort(key=_forward_key)
            positions.update(
                (vertex, index) for index, vertex in enumerate(layers[rank])
            )

        positions = _positions()
        for rank in range(max_rank - 1, -1, -1):
            current = {vertex: index for index, vertex in enumerate(layers[rank])}

            def _backward_key(vertex):
                neighbors = successors.get(vertex, ())
                if not neighbors:
                    return float(current[vertex]), current[vertex], vertex
                value = sum(positions[n] + bias for n, bias in neighbors) / len(neighbors)
                return value, current[vertex], vertex

            layers[rank].sort(key=_backward_key)
            positions.update(
                (vertex, index) for index, vertex in enumerate(layers[rank])
            )

        score = _score()
        if score < best_score:
            best_score = score
            best = {rank: list(vertices) for rank, vertices in layers.items()}
            stagnant = 0
        else:
            stagnant += 1
        if best_score == (0, 0) or stagnant >= 3:
            break

    return {
        rank: [vertex_item[vertex] for vertex in best[rank] if vertex in vertex_item]
        for rank in best
    }


def _layout_component(
    component: list[int],
    items: dict[int, _LayoutItem],
    edges: list[_LayoutEdge],
    origin_x: float,
    origin_y: float,
) -> float:
    """Position one weak component and return its bottom edge."""
    _assign_ranks(component, items, edges)
    layers = _crossing_reduced_layers(component, items, edges)
    positions: dict[int, tuple[float, float]] = {}

    x = origin_x
    for rank in sorted(layers):
        layer = layers[rank]
        if not layer:
            continue
        width = max(items[key].width for key in layer)
        y = origin_y
        for key in layer:
            item = items[key]
            positions[key] = (x + (width - item.width) * 0.5, y)
            y -= item.height + VERTICAL_GAP
        x += width + HORIZONTAL_GAP

    # Shift downstream columns as whole units toward the median of their
    # incoming neighbors. This improves link horizontality without creating
    # overlaps or changing the crossing-reduced order.
    for rank in sorted(layers):
        if rank == 0 or not layers[rank]:
            continue
        deltas: list[float] = []
        layer_set = set(layers[rank])
        for edge in edges:
            if edge.target not in layer_set:
                continue
            if edge.source not in positions or edge.target not in positions:
                continue
            source_item = items[edge.source]
            target_item = items[edge.target]
            source_center = positions[edge.source][1] - source_item.height * 0.5
            target_center = positions[edge.target][1] - target_item.height * 0.5
            deltas.append(source_center - target_center)
        if deltas:
            shift = max(-300.0, min(300.0, float(median(deltas))))
            for key in layers[rank]:
                left, top = positions[key]
                positions[key] = left, top + shift

    component_top = max(top for _left, top in positions.values())
    normalize_y = origin_y - component_top
    bottom = float("inf")
    for key, (left, top) in positions.items():
        item = items[key]
        desired_top = top + normalize_y
        anchor_x, anchor_y = _location_xy(item.anchor)
        _set_location(
            item.anchor,
            anchor_x + left - item.left,
            anchor_y + desired_top - item.top,
        )
        bottom = min(bottom, desired_top - item.height)
    return bottom


def _arrange_node_collection(
    node_tree: bpy.types.NodeTree,
    nodes: Iterable[bpy.types.Node],
    origin: tuple[float, float],
) -> None:
    items, node_to_item = _build_layout_items(nodes)
    if not items:
        return
    edges = _build_edges(node_tree, node_to_item)
    components = _weak_components(items, edges)

    next_top = float(origin[1])
    for component in components:
        component_edges = [
            edge for edge in edges
            if edge.source in component and edge.target in component
        ]
        bottom = _layout_component(
            component,
            items,
            component_edges,
            float(origin[0]),
            next_top,
        )
        next_top = bottom - COMPONENT_GAP


def arrange_nodes(
    node_tree: bpy.types.NodeTree,
    nodes: Iterable[bpy.types.Node],
    origin: tuple[float, float],
) -> None:
    """Arrange a node graph, including the contents of nested frames.

    Frame contents are laid out from the deepest frame outward, after which
    each top-level frame moves as one cluster. Cycles share a column,
    disconnected graphs are packed vertically, and repeated barycentric
    sweeps reduce link crossings.
    """
    selected: list[bpy.types.Node] = []
    selected_ids: set[int] = set()
    for node in nodes:
        identity = _rna_identity(node)
        if identity in selected_ids:
            continue
        selected.append(node)
        selected_ids.add(identity)
    frames = sorted(
        (
            node for node in selected
            if getattr(node, "bl_idname", "") == "NodeFrame"
        ),
        key=_frame_depth,
        reverse=True,
    )
    for frame in frames:
        descendants = [
            node for node in selected
            if node is not frame and _is_descendant_of(node, frame)
        ]
        if not descendants:
            continue
        left, _bottom, _right, top = graph_bounds(descendants)
        _arrange_node_collection(node_tree, descendants, (left, top))

    _arrange_node_collection(node_tree, selected, origin)
    try:
        node_tree.update_tag()
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        pass


def style_smart_graphs(
    node_tree: bpy.types.NodeTree,
    original_nodes: Iterable[bpy.types.Node],
    converted_nodes: Iterable[bpy.types.Node],
    auto_arrange: bool,
    colorize: bool = True,
) -> None:
    """Style and separate Cycles and Octane graphs in one material tree."""
    original = list(original_nodes)
    converted = list(converted_nodes)
    color_nodes(original, CYCLES_NODE_COLOR, "cycles", enabled=colorize)
    color_nodes(converted, OCTANE_NODE_COLOR, "octane", enabled=colorize)
    if not converted:
        return

    original_left, _, original_right, original_top = graph_bounds(original)
    if auto_arrange:
        arrange_nodes(node_tree, original, (original_left, original_top))
        original_left, _, original_right, original_top = graph_bounds(original)
        arrange_nodes(
            node_tree,
            converted,
            (original_right + GRAPH_GAP, original_top),
        )
        return

    converted_left, _, _, _ = graph_bounds(converted)
    shift_x = original_right + GRAPH_GAP - converted_left
    for node in converted:
        if getattr(node, "parent", None) is None:
            node_x, node_y = _location_xy(node)
            _set_location(node, node_x + shift_x, node_y)


def style_converted_graph(
    node_tree: bpy.types.NodeTree,
    converted_nodes: Iterable[bpy.types.Node],
    auto_arrange: bool,
    colorize: bool = True,
) -> None:
    """Style a standalone converted material or node-group graph."""
    converted = list(converted_nodes)
    color_nodes(
        converted,
        OCTANE_NODE_COLOR,
        "octane",
        enabled=colorize,
    )
    if auto_arrange and converted:
        left, _, _, top = graph_bounds(converted)
        arrange_nodes(node_tree, converted, (left, top))
