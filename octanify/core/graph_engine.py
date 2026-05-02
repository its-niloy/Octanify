"""Octanify — Graph engine.

Recursive traversal engine that determines the correct conversion order
(dependency-first / leaves-first) by walking the node graph from the
output node backward through all inputs.

Also handles reroute flattening at the link level and provides
the node_map (Cycles node name → Octane node reference) used by the
conversion engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .node_registry import (
    NODE_TYPE_MAP,
    PASSTHROUGH_TYPES,
    SKIP_TYPES,
    create_octane_node,
)
from ..utils.logger import get_logger

if TYPE_CHECKING:
    import bpy
    from .shader_detection import TreeAnalysis

log = get_logger()


class GraphEngine:
    """Walks a TreeAnalysis and produces an ordered conversion schedule."""

    def __init__(self, analysis: "TreeAnalysis", group_converter_cb=None) -> None:
        self.analysis = analysis
        self.group_converter_cb = group_converter_cb
        # Ordered list of node names to convert (dependencies first)
        self._schedule: list[str] = []
        self._visited: set[str] = set()
        # Adjacency built from links: node_name → set of upstream node_names
        self._deps: dict[str, set[str]] = {}
        self._build_dependency_graph()

    # -----------------------------------------------------------------
    # Dependency graph construction
    # -----------------------------------------------------------------

    def _build_dependency_graph(self) -> None:
        """Build upstream dependency sets from the link list."""
        for node_name in self.analysis.nodes:
            self._deps.setdefault(node_name, set())

        for link in self.analysis.links:
            self._deps.setdefault(link.to_node, set()).add(link.from_node)
            self._deps.setdefault(link.from_node, set())

    # -----------------------------------------------------------------
    # Traversal
    # -----------------------------------------------------------------

    def compute_schedule(self) -> list[str]:
        """Return an ordered list of node names (leaves first, output last)."""
        self._schedule.clear()
        self._visited.clear()

        # Find the output node(s), start traversal from there
        output_nodes = [
            name for name, info in self.analysis.nodes.items()
            if info.bl_idname == "ShaderNodeOutputMaterial"
        ]

        if not output_nodes:
            # Fallback: traverse all nodes
            for name in self.analysis.nodes:
                self._visit(name)
        else:
            for out_name in output_nodes:
                self._visit(out_name)

        return list(self._schedule)

    def _visit(self, node_name: str) -> None:
        """Recursive depth-first visit."""
        if node_name in self._visited:
            return
        self._visited.add(node_name)

        # Visit all upstream dependencies first
        for dep in self._deps.get(node_name, set()):
            self._visit(dep)

        self._schedule.append(node_name)

    # -----------------------------------------------------------------
    # Node creation following the schedule
    # -----------------------------------------------------------------

    def create_nodes(
        self, target_tree: "bpy.types.NodeTree"
    ) -> dict[str, "bpy.types.Node"]:
        """
        Create Octane nodes in *target_tree* following the computed schedule.
        Returns a mapping of original node name → new Octane node.
        """
        schedule = self.compute_schedule()
        node_map: dict[str, "bpy.types.Node"] = {}

        for node_name in schedule:
            info = self.analysis.nodes.get(node_name)
            if info is None:
                continue

            bl_id = info.bl_idname

            # Skip nodes that are handled separately or are passthrough
            if bl_id in SKIP_TYPES:
                continue

            if bl_id in PASSTHROUGH_TYPES:
                # For output material, reuse or create
                if bl_id == "ShaderNodeOutputMaterial":
                    existing = None
                    for n in target_tree.nodes:
                        if n.bl_idname == "ShaderNodeOutputMaterial":
                            existing = n
                            break
                    if existing is None:
                        existing = target_tree.nodes.new("ShaderNodeOutputMaterial")
                    # Set target to Octane
                    try:
                        existing.target = "octane"
                    except (AttributeError, TypeError):
                        pass
                    existing.location = info.location
                    node_map[node_name] = existing
                elif bl_id == "ShaderNodeGroup":
                    new_node = target_tree.nodes.new("ShaderNodeGroup")
                    new_node.location = info.location
                    if self.group_converter_cb and "node_tree_name" in getattr(info, "properties", {}):
                        orig_tree_name = info.properties["node_tree_name"]
                        import bpy
                        orig_tree = bpy.data.node_groups.get(orig_tree_name)
                        if orig_tree:
                            new_tree = self.group_converter_cb(orig_tree)
                            if new_tree:
                                new_node.node_tree = new_tree
                    node_map[node_name] = new_node
                continue

            # Standard creation through registry
            new_node = create_octane_node(target_tree, bl_id, label=info.label)
            if new_node is not None:
                new_node.location = info.location
                node_map[node_name] = new_node
            else:
                log.warning(
                    "Skipping unsupported node '%s' (%s) — creating fallback",
                    node_name, bl_id,
                )
                # Create a fallback RGB node so links don't break completely
                try:
                    fallback = target_tree.nodes.new("ShaderNodeOctRGBColorTex")
                    fallback.label = f"[UNSUPPORTED] {info.label}"
                    fallback.location = info.location
                    fallback.use_custom_color = True
                    fallback.color = (0.8, 0.2, 0.2)
                    node_map[node_name] = fallback
                except Exception:
                    try:
                        fallback = target_tree.nodes.new("OctaneRGBColor")
                        fallback.label = f"[UNSUPPORTED] {info.label}"
                        fallback.location = info.location
                        node_map[node_name] = fallback
                    except Exception:
                        log.error(
                            "Cannot create fallback node for '%s'", node_name
                        )

        return node_map
