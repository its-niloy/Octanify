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
    APPROXIMATION_NOTES,
    PASSTHROUGH_TYPES,
    SKIP_TYPES,
    create_node_from_candidates,
    create_octane_node,
    get_contextual_node_candidates,
)
from ..utils.logger import get_logger

if TYPE_CHECKING:
    import bpy
    from .shader_detection import TreeAnalysis

log = get_logger()


class GraphEngine:
    """Walks a TreeAnalysis and produces an ordered conversion schedule."""

    def __init__(
        self,
        analysis: "TreeAnalysis",
        group_converter_cb=None,
        context_name: str = "",
    ) -> None:
        self.analysis = analysis
        self.group_converter_cb = group_converter_cb
        self.context_name = context_name
        # Ordered list of node names to convert (dependencies first)
        self._schedule: list[str] = []
        self._visited: set[str] = set()
        # Adjacency built from links: node_name → upstream node_names.
        # Lists preserve node-tree/link order, which makes conversion output
        # deterministic and avoids hash-order differences between sessions.
        self._deps: dict[str, list[str]] = {}
        self._incoming: dict[str, list] = {}
        self._outgoing: dict[str, list] = {}
        # Some Cycles nodes require multiple Octane nodes.  Each original
        # input fans out to all variants while outgoing sockets select the
        # appropriate variant.
        self._created_variants: dict[str, list["bpy.types.Node"]] = {}
        self._output_variants: dict[tuple[str, str], "bpy.types.Node"] = {}
        self._build_dependency_graph()

    # -----------------------------------------------------------------
    # Dependency graph construction
    # -----------------------------------------------------------------

    def _build_dependency_graph(self) -> None:
        """Build upstream dependency sets from the link list."""
        for node_name in self.analysis.nodes:
            self._deps.setdefault(node_name, [])
            self._incoming.setdefault(node_name, [])
            self._outgoing.setdefault(node_name, [])

        dep_seen: dict[str, set[str]] = {
            node_name: set() for node_name in self._deps
        }
        for link in self.analysis.links:
            deps = self._deps.setdefault(link.to_node, [])
            seen = dep_seen.setdefault(link.to_node, set())
            if link.from_node not in seen:
                deps.append(link.from_node)
                seen.add(link.from_node)
            self._deps.setdefault(link.from_node, [])
            self._incoming.setdefault(link.to_node, []).append(link)
            self._outgoing.setdefault(link.from_node, []).append(link)

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
            output_nodes = list(self.analysis.nodes)
        else:
            for out_name in output_nodes:
                self._visit(out_name)

        # Preserve disconnected nodes and secondary branches as well.  They
        # may be intentionally staged for later use even though they do not
        # currently contribute to a Material Output.
        for name in self.analysis.nodes:
            self._visit(name)

        return list(self._schedule)

    def _visit(self, node_name: str) -> None:
        """Iterative depth-first visit.

        Blender node trees can legitimately contain thousands of nodes.  An
        iterative traversal avoids Python's recursion limit while retaining
        dependency-first ordering and terminating safely on cycles.
        """
        if node_name in self._visited:
            return

        active: set[str] = set()
        stack: list[tuple[str, bool]] = [(node_name, False)]
        while stack:
            current, expanded = stack.pop()
            if current in self._visited:
                continue

            if expanded:
                active.discard(current)
                self._visited.add(current)
                self._schedule.append(current)
                continue

            if current in active:
                # A back-edge indicates a cyclic graph.  Node creation does
                # not require a strict topological order, so leave the active
                # node to its existing stack frame and continue.
                continue

            active.add(current)
            stack.append((current, True))
            for dependency in reversed(self._deps.get(current, [])):
                if dependency not in self._visited:
                    stack.append((dependency, False))

    # -----------------------------------------------------------------
    # Node creation following the schedule
    # -----------------------------------------------------------------

    @staticmethod
    def _apply_common_state(node, info) -> None:
        """Restore generic Blender node state supported by Octane nodes."""
        properties = getattr(info, "properties", {})
        for attribute in ("mute", "hide"):
            if attribute not in properties:
                continue
            try:
                setattr(node, attribute, properties[attribute])
            except (AttributeError, TypeError):
                pass

    def created_nodes_for(
        self,
        node_name: str,
        node_map: dict[str, "bpy.types.Node"],
    ) -> list["bpy.types.Node"]:
        variants = self._created_variants.get(node_name)
        if variants is not None:
            return list(variants)
        node = node_map.get(node_name)
        return [node] if node is not None else []

    def source_node_for(
        self,
        link_info,
        node_map: dict[str, "bpy.types.Node"],
    ):
        identifier = getattr(link_info, "from_socket_identifier", "")
        return (
            self._output_variants.get((link_info.from_node, identifier))
            or self._output_variants.get((link_info.from_node, link_info.from_socket))
            or node_map.get(link_info.from_node)
        )

    def _expand_channel_split(
        self,
        target_tree: "bpy.types.NodeTree",
        node_name: str,
        info,
        primary: "bpy.types.Node",
    ):
        """Expand RGB Separate nodes into one Octane Channel Picker per edge."""
        if info.bl_idname not in ("ShaderNodeSeparateColor", "ShaderNodeSeparateRGB"):
            return primary

        channel_aliases = {
            "Red": "Red",
            "R": "Red",
            "Green": "Green",
            "G": "Green",
            "Blue": "Blue",
            "B": "Blue",
        }
        outgoing = [
            link for link in self._outgoing.get(node_name, [])
            if link.from_socket in channel_aliases
        ]
        used_channels = list(dict.fromkeys(
            channel_aliases[link.from_socket] for link in outgoing
        ))
        if not used_channels:
            return primary

        # A native multi-output wrapper needs no expansion.
        native_outputs = []
        for channel in used_channels:
            names = (channel, channel[0])
            socket = next(
                (primary.outputs.get(name) for name in names if primary.outputs.get(name) is not None),
                None,
            )
            if socket is not None:
                native_outputs.append(socket)
        if (len(native_outputs) == len(used_channels)
                and len({id(socket) for socket in native_outputs}) == len(used_channels)):
            return primary

        variants: dict[str, "bpy.types.Node"] = {}
        for index, channel in enumerate(used_channels):
            variant = create_node_from_candidates(
                target_tree,
                ("ShaderNodeOctChannelPickerTex", "OctaneChannelPicker"),
                label=f"{info.label} [{channel}]",
            )
            if variant is None:
                continue
            variant.location = (
                info.location[0],
                info.location[1] - (index * 150),
            )
            self._apply_common_state(variant, info)
            configured = False
            channel_input = variant.inputs.get("Channel")
            if channel_input is not None and hasattr(channel_input, "default_value"):
                enum_value = {
                    "Red": "1",
                    "Green": "2",
                    "Blue": "3",
                }[channel]
                for value in (enum_value, channel, channel[0]):
                    try:
                        channel_input.default_value = value
                        configured = True
                        break
                    except (AttributeError, TypeError, ValueError):
                        continue
            for attr in ("channel", "channel_type"):
                if configured:
                    break
                try:
                    setattr(variant, attr, channel)
                    configured = True
                except (AttributeError, TypeError):
                    continue
            variants[channel] = variant

        for link in outgoing:
            variant = variants.get(channel_aliases[link.from_socket])
            if variant is not None:
                self._output_variants[(node_name, link.from_socket)] = variant
                identifier = getattr(link, "from_socket_identifier", "")
                if identifier:
                    self._output_variants[(node_name, identifier)] = variant

        if len(variants) == len(used_channels):
            try:
                target_tree.nodes.remove(primary)
            except (RuntimeError, TypeError):
                pass
            created = list(variants.values())
            self._created_variants[node_name] = created
            return created[0]

        if variants:
            self._created_variants[node_name] = [primary, *variants.values()]
        from .report import report_data
        report_data.add_warning(
            f"[{self.context_name or target_tree.name}] Could not create all Channel Picker variants "
            f"for '{node_name}' ({len(variants)}/{len(used_channels)})"
        )
        return primary

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

            if bl_id == "ShaderNodeMix":
                data_type = info.properties.get("data_type", "FLOAT")
                if data_type == "FLOAT":
                    bl_id = "ShaderNodeMixFloat"
                elif data_type == "VECTOR":
                    bl_id = "ShaderNodeMixFloat3"

            # Skip nodes that are handled separately or are passthrough
            if bl_id in SKIP_TYPES:
                continue

            if bl_id in PASSTHROUGH_TYPES:
                # For output material, reuse or create
                if bl_id == "ShaderNodeOutputMaterial":
                    existing = target_tree.nodes.get(node_name)
                    if (existing is not None
                            and existing.bl_idname != "ShaderNodeOutputMaterial"):
                        existing = None
                    if existing is None:
                        existing = target_tree.nodes.new("ShaderNodeOutputMaterial")
                        existing.name = node_name
                    # Set target to Octane
                    try:
                        existing.target = "octane"
                    except (AttributeError, TypeError):
                        pass
                    existing.location = info.location
                    self._apply_common_state(existing, info)
                    node_map[node_name] = existing
                elif bl_id == "ShaderNodeGroup":
                    new_node = target_tree.nodes.new("ShaderNodeGroup")
                    new_node.location = info.location
                    self._apply_common_state(new_node, info)
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

            # Standard creation through registry. Some nodes, especially
            # image textures, need link context to choose the best Octane type.
            preferred_candidates = get_contextual_node_candidates(
                info.bl_idname,
                self.analysis,
                node_name,
                outgoing_links=self._outgoing.get(node_name, []),
            )
            new_node = create_octane_node(
                target_tree,
                bl_id,
                label=info.label,
                preferred_candidates=preferred_candidates,
            )
            
            if new_node is not None:
                new_node.location = info.location
                self._apply_common_state(new_node, info)
                new_node = self._expand_channel_split(
                    target_tree, node_name, info, new_node
                )
                node_map[node_name] = new_node
                
                from .report import report_data
                report_data.nodes_translated += 1
                approximation = APPROXIMATION_NOTES.get(info.bl_idname)
                if approximation:
                    report_data.add_approximation(
                        f"[{self.context_name or target_tree.name}] {node_name}: {approximation}"
                    )
            else:
                # Fallback node creation
                from .report import report_data
                mat_name = self.context_name or target_tree.name
                short_type = info.bl_idname.replace('ShaderNode', '')
                report_data.add_unsupported(f"[{mat_name}] Unsupported: {short_type}")
                
                try:
                    fallback = target_tree.nodes.new("ShaderNodeOctRGBColorTex")
                    fallback.label = f"[UNSUPPORTED] {info.label}"
                    fallback.location = info.location
                    self._apply_common_state(fallback, info)
                    fallback.use_custom_color = True
                    fallback.color = (0.8, 0.2, 0.2)
                    node_map[node_name] = fallback
                except Exception:
                    try:
                        fallback = target_tree.nodes.new("OctaneRGBColor")
                        fallback.label = f"[UNSUPPORTED] {info.label}"
                        fallback.location = info.location
                        self._apply_common_state(fallback, info)
                        node_map[node_name] = fallback
                    except Exception:
                        log.error("Cannot create fallback node for '%s'", node_name)

        return node_map
