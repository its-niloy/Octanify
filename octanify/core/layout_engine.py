"""Deterministic layout and visual styling for converted shader graphs."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

import bpy


# Low-saturation graphite keeps the authored graph readable without competing
# with the converted result.  Deep teal gives the Octane graph a clean visual
# identity while avoiding the previous heavy orange/brown cast.
CYCLES_NODE_COLOR = (0.16, 0.20, 0.27)
OCTANE_NODE_COLOR = (0.07, 0.30, 0.25)
GRAPH_GAP = 1000.0
HORIZONTAL_GAP = 180.0
VERTICAL_GAP = 80.0


def _rna_identity(value) -> int:
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _absolute_location(node: bpy.types.Node) -> tuple[float, float]:
    """Return a node position in tree space, including frame parents."""
    x = float(node.location.x)
    y = float(node.location.y)
    parent = getattr(node, "parent", None)
    visited: set[int] = set()
    while parent is not None and _rna_identity(parent) not in visited:
        visited.add(_rna_identity(parent))
        x += float(parent.location.x)
        y += float(parent.location.y)
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
) -> None:
    """Apply a consistent editor color without changing node semantics."""
    for node in nodes:
        try:
            node.use_custom_color = True
            node.color = color
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            node["octanify_graph"] = graph_kind
        except (AttributeError, RuntimeError, TypeError):
            pass


def arrange_nodes(
    node_tree: bpy.types.NodeTree,
    nodes: Iterable[bpy.types.Node],
    origin: tuple[float, float],
) -> None:
    """Arrange an unframed graph in dependency columns.

    Existing frame contents are intentionally left in place. Moving a node
    across frame coordinate spaces can corrupt carefully authored grouping;
    the surrounding graph is still arranged and separated safely.
    """
    candidates = [
        node for node in nodes
        if getattr(node, "bl_idname", "") != "NodeFrame"
        and getattr(node, "parent", None) is None
    ]
    if not candidates:
        return

    by_name = {node.name: node for node in candidates}
    order = {node.name: index for index, node in enumerate(candidates)}
    successors: dict[str, list[str]] = defaultdict(list)
    indegree = {name: 0 for name in by_name}
    depth = {name: 0 for name in by_name}

    for link in node_tree.links:
        source = getattr(getattr(link, "from_node", None), "name", "")
        target = getattr(getattr(link, "to_node", None), "name", "")
        if source not in by_name or target not in by_name or source == target:
            continue
        if target in successors[source]:
            continue
        successors[source].append(target)
        indegree[target] += 1

    ready = deque(sorted(
        (name for name, degree in indegree.items() if degree == 0),
        key=order.get,
    ))
    visited: set[str] = set()
    while ready:
        source = ready.popleft()
        visited.add(source)
        for target in successors[source]:
            depth[target] = max(depth[target], depth[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)

    # Cyclic components cannot be topologically sorted. Keep them together
    # in a deterministic extra column instead of looping or overlapping the
    # acyclic portion of the graph.
    if len(visited) != len(candidates):
        cycle_depth = max(depth.values(), default=0) + 1
        for name in by_name:
            if name not in visited:
                depth[name] = cycle_depth

    columns: dict[int, list[bpy.types.Node]] = defaultdict(list)
    for name, node in by_name.items():
        columns[depth[name]].append(node)

    x = float(origin[0])
    for column_index in sorted(columns):
        column = sorted(columns[column_index], key=lambda node: order[node.name])
        y = float(origin[1])
        column_width = max(_node_size(node)[0] for node in column)
        for node in column:
            node.location = (x, y)
            _, height = _node_size(node)
            y -= height + VERTICAL_GAP
        x += column_width + HORIZONTAL_GAP


def style_smart_graphs(
    node_tree: bpy.types.NodeTree,
    original_nodes: Iterable[bpy.types.Node],
    converted_nodes: Iterable[bpy.types.Node],
    auto_arrange: bool,
) -> None:
    """Style and separate Cycles and Octane graphs in one material tree."""
    original = list(original_nodes)
    converted = list(converted_nodes)
    color_nodes(original, CYCLES_NODE_COLOR, "cycles")
    color_nodes(converted, OCTANE_NODE_COLOR, "octane")
    if not converted:
        return

    original_left, _, original_right, original_top = graph_bounds(original)
    if auto_arrange:
        # Frame children use parent-relative coordinates. Rearranging only the
        # unframed subset can place it on top of an authored frame, while
        # moving frame contents would destroy deliberate grouping. Preserve
        # the complete original layout whenever frames are present.
        has_framed_layout = any(
            getattr(node, "bl_idname", "") == "NodeFrame"
            or getattr(node, "parent", None) is not None
            for node in original
        )
        if not has_framed_layout:
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
            node.location.x += shift_x


def style_converted_graph(
    node_tree: bpy.types.NodeTree,
    converted_nodes: Iterable[bpy.types.Node],
    auto_arrange: bool,
) -> None:
    """Style a standalone converted material graph."""
    converted = list(converted_nodes)
    color_nodes(converted, OCTANE_NODE_COLOR, "octane")
    if auto_arrange and converted:
        left, _, _, top = graph_bounds(converted)
        arrange_nodes(node_tree, converted, (left, top))
