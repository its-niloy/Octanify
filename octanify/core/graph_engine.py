"""Octanify — Graph engine.

Recursive traversal engine that determines the correct conversion order
(dependency-first / leaves-first) by walking the node graph from the
output node backward through all inputs.

Also handles reroute flattening at the link level and provides
the node_map (Cycles node name → Octane node reference) used by the
conversion engine.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

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
from .shading_intent import Role, ShadingIntentMap, TextureTreatment

if TYPE_CHECKING:
    import bpy
    from .shader_detection import TreeAnalysis

log = get_logger()


def _rna_identity(value) -> int:
    """Return a stable identity for Blender RNA wrappers and test doubles."""
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


class GraphEngine:
    """Walks a TreeAnalysis and produces an ordered conversion schedule."""

    def __init__(
        self,
        analysis: "TreeAnalysis",
        group_converter_cb=None,
        context_name: str = "",
        reuse_output_nodes: bool = True,
        intent_map: ShadingIntentMap | None = None,
        source_tree=None,
        report_context_name: str = "",
    ) -> None:
        self.analysis = analysis
        self.group_converter_cb = group_converter_cb
        self.context_name = context_name
        self.report_context_name = report_context_name or context_name
        self.reuse_output_nodes = reuse_output_nodes
        self.intent_map = intent_map
        self.source_tree = source_tree
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
        self._image_role_variants: dict[
            str, dict[TextureTreatment, "bpy.types.Node"]
        ] = {}
        self._node_treatment_variants: dict[
            str, dict[TextureTreatment, list["bpy.types.Node"]]
        ] = {}
        self._output_treatment_variants: dict[
            tuple[str, str, TextureTreatment], "bpy.types.Node"
        ] = {}
        self._image_treatment_by_identity: dict[int, TextureTreatment] = {}
        selected_cycles_outputs = {
            name
            for name, info in self.analysis.nodes.items()
            if info.bl_idname == "ShaderNodeOutputMaterial"
            and bool(
                getattr(info, "properties", {}).get("octanify_cycles_output")
            )
        }
        active_outputs = {
            name
            for name, info in self.analysis.nodes.items()
            if info.bl_idname == "ShaderNodeOutputMaterial"
            and bool(getattr(info, "properties", {}).get("is_active_output"))
        }
        self._active_material_outputs = selected_cycles_outputs or active_outputs
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

    def _source_node(self, node_name: str):
        if self.source_tree is None:
            return None
        nodes = getattr(self.source_tree, "nodes", ())
        getter = getattr(nodes, "get", None)
        if callable(getter):
            node = getter(node_name)
            if node is not None:
                return node
        return next(
            (node for node in nodes if getattr(node, "name", "") == node_name),
            None,
        )

    def intent_roles_for(self, node_name: str) -> set[Role]:
        """Return the destination-role union for a source node."""
        source = self._source_node(node_name)
        if source is None or self.intent_map is None:
            return set()
        return self.intent_map.roles_for(source)

    def intent_treatments_for(self, node_name: str) -> set[TextureTreatment]:
        """Return the color/data treatment union for a source node."""
        source = self._source_node(node_name)
        if source is None or self.intent_map is None:
            return set()
        return self.intent_map.treatments_for(source)

    def intent_treatments_for_link(self, link_info) -> set[TextureTreatment]:
        """Return path treatments carried by one analyzed source-tree edge."""
        if self.intent_map is None:
            return set()
        from_node = self._source_node(link_info.from_node)
        to_node = self._source_node(link_info.to_node)
        if from_node is None or to_node is None:
            return set()
        return self.intent_map.treatments_for_link(
            from_node,
            link_info.from_socket,
            to_node,
            link_info.to_socket,
        )

    def image_variants_for(
        self,
        node_name: str,
        node_map: dict[str, "bpy.types.Node"],
    ) -> list[tuple["bpy.types.Node", TextureTreatment | None]]:
        """Return converted image variants paired with their treatment."""
        variants = self._image_role_variants.get(node_name)
        if variants:
            return [(node, treatment) for treatment, node in variants.items()]
        node = node_map.get(node_name)
        if node is None:
            return []
        return [(node, self._image_treatment_by_identity.get(
            _rna_identity(node)
        ))]

    @staticmethod
    def _tag_image_treatment(node, node_name: str,
                             treatment: TextureTreatment) -> None:
        try:
            node["octanify_source_node"] = node_name
            node["octanify_intent_treatment"] = treatment.value
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _register_image_treatment(
        self,
        node_name: str,
        node,
        treatment: TextureTreatment,
    ) -> None:
        self._image_treatment_by_identity[_rna_identity(node)] = treatment
        self._tag_image_treatment(node, node_name, treatment)
        self._register_node_treatment_variant(node_name, node, treatment)

    def _register_node_treatment_variant(
        self,
        node_name: str,
        node,
        treatment: TextureTreatment,
    ) -> None:
        variants = self._node_treatment_variants.setdefault(node_name, {})
        nodes = variants.setdefault(treatment, [])
        if all(
            _rna_identity(candidate) != _rna_identity(node)
            for candidate in nodes
        ):
            nodes.append(node)

    @staticmethod
    def _ordered_treatments(
        treatments: set[TextureTreatment],
    ) -> list[TextureTreatment]:
        return [
            treatment
            for treatment in (TextureTreatment.COLOR, TextureTreatment.DATA)
            if treatment in treatments
        ]

    def _create_image_conflict_variant(
        self,
        target_tree: "bpy.types.NodeTree",
        node_name: str,
        info,
        color_node: "bpy.types.Node",
    ) -> "bpy.types.Node | None":
        """Create the linear image instance for a color/data conflict."""
        roles = self.intent_roles_for(node_name)
        data_roles = roles & {
            Role.ROUGHNESS,
            Role.METALLIC,
            Role.NORMAL,
            Role.BUMP,
            Role.ALPHA,
            Role.DISPLACEMENT,
        }
        source_node = self._source_node(node_name)
        alpha_output_is_data = bool(
            source_node is not None
            and self.intent_map is not None
            and Role.ALPHA in self.intent_map.roles_for(source_node, "Alpha")
            and all(
                socket_name == "Alpha"
                or TextureTreatment.DATA not in treatments
                for (candidate, socket_name), treatments
                in self.intent_map.output_treatments.items()
                if _rna_identity(candidate) == _rna_identity(source_node)
            )
        )
        candidates = (
            ("OctaneAlphaImage", "ShaderNodeOctAlphaImage")
            if data_roles == {Role.ALPHA} and alpha_output_is_data
            else (
                "OctaneRGBImage",
                "ShaderNodeOctImageTex",
                "OctaneImageTexture",
            )
        )
        data_node = create_node_from_candidates(
            target_tree,
            candidates,
            label=f"{info.label} [Data]",
        )
        if data_node is None:
            from .report import report_data
            report_data.add_warning(
                f"[{self.report_context_name or target_tree.name}] "
                f"Could not split color/data texture '{node_name}'"
            )
            return None

        data_node.location = (info.location[0], info.location[1] - 180)
        self._apply_common_state(data_node, info)
        self._created_variants[node_name] = [color_node, data_node]
        self._image_role_variants[node_name] = {
            TextureTreatment.COLOR: color_node,
            TextureTreatment.DATA: data_node,
        }
        self._register_image_treatment(
            node_name, color_node, TextureTreatment.COLOR
        )
        self._register_image_treatment(
            node_name, data_node, TextureTreatment.DATA
        )

        filepath = info.properties.get("filepath", "")
        filename = (
            os.path.basename(filepath.replace("\\", "/"))
            if filepath else ""
        )
        filename = filename or info.properties.get("image_name", node_name)
        message = (
            f"[{self.report_context_name or target_tree.name}] '{filename}' used "
            "for both color and data roles — created 2 texture instances."
        )
        from .report import report_data
        report_data.add_notice(message)
        log.info(message)
        return data_node

    def _output_variant_for_treatment(
        self,
        link_info,
        treatment: TextureTreatment,
    ):
        identifier = getattr(link_info, "from_socket_identifier", "")
        for socket_key in (identifier, link_info.from_socket):
            if not socket_key:
                continue
            variant = self._output_treatment_variants.get(
                (link_info.from_node, socket_key, treatment)
            )
            if variant is not None:
                return variant
        return None

    def _source_node_for_treatment(
        self,
        link_info,
        node_map: dict[str, "bpy.types.Node"],
        treatment: TextureTreatment,
    ):
        output_variant = self._output_variant_for_treatment(
            link_info, treatment
        )
        if output_variant is not None:
            return output_variant

        treatment_nodes = self._node_treatment_variants.get(
            link_info.from_node, {}
        ).get(treatment, [])
        if treatment_nodes:
            return treatment_nodes[0]

        identifier = getattr(link_info, "from_socket_identifier", "")
        output_variant = self._output_variants.get(
            (link_info.from_node, identifier)
        )
        if output_variant is None:
            output_variant = self._output_variants.get(
                (link_info.from_node, link_info.from_socket)
            )
        if output_variant is not None:
            return output_variant
        return node_map.get(link_info.from_node)

    def link_node_pairs(
        self,
        link_info,
        node_map: dict[str, "bpy.types.Node"],
    ) -> list[tuple["bpy.types.Node", "bpy.types.Node"]]:
        """Return source/target pairs for one original edge.

        When an upstream edge carries both color and data intent, every
        duplicated processor branch must receive the matching image variant.
        A simple source-to-all-target fan-out would attach the color instance
        to both branches and leave the linear instance unused.
        """
        treatments = self.intent_treatments_for_link(link_info)
        target_variants = self._node_treatment_variants.get(
            link_info.to_node, {}
        )
        pairs: list[tuple["bpy.types.Node", "bpy.types.Node"]] = []

        if treatments and target_variants:
            for treatment in self._ordered_treatments(treatments):
                source = self._source_node_for_treatment(
                    link_info, node_map, treatment
                )
                if source is None:
                    continue
                for target in target_variants.get(treatment, []):
                    pairs.append((source, target))
        elif len(treatments) == 1:
            treatment = next(iter(treatments))
            source = self._source_node_for_treatment(
                link_info, node_map, treatment
            )
            targets = self.created_nodes_for(link_info.to_node, node_map)
            if source is not None:
                pairs.extend((source, target) for target in targets)

        if pairs:
            matched_targets = {
                _rna_identity(target) for _source, target in pairs
            }
            fallback_source = self.source_node_for(link_info, node_map)
            if fallback_source is not None:
                for target in self.created_nodes_for(
                    link_info.to_node, node_map
                ):
                    if _rna_identity(target) not in matched_targets:
                        pairs.append((fallback_source, target))

        if not pairs:
            source = self.source_node_for(link_info, node_map)
            if source is not None:
                pairs.extend(
                    (source, target)
                    for target in self.created_nodes_for(
                        link_info.to_node, node_map
                    )
                )

        unique: list[tuple["bpy.types.Node", "bpy.types.Node"]] = []
        seen: set[tuple[int, int]] = set()
        for source, target in pairs:
            key = _rna_identity(source), _rna_identity(target)
            if key in seen:
                continue
            seen.add(key)
            unique.append((source, target))
        return unique

    def source_node_for(
        self,
        link_info,
        node_map: dict[str, "bpy.types.Node"],
    ):
        treatments = self.intent_treatments_for_link(link_info)
        if len(treatments) == 1:
            return self._source_node_for_treatment(
                link_info, node_map, next(iter(treatments))
            )

        identifier = getattr(link_info, "from_socket_identifier", "")
        output_variant = self._output_variants.get(
            (link_info.from_node, identifier)
        )
        if output_variant is None:
            output_variant = self._output_variants.get(
                (link_info.from_node, link_info.from_socket)
            )
        if output_variant is not None:
            return output_variant

        image_variants = self._image_role_variants.get(link_info.from_node)
        if image_variants:
            treatments = self.intent_treatments_for_link(link_info)
            if treatments == {TextureTreatment.DATA}:
                preferred = image_variants.get(TextureTreatment.DATA)
                return preferred if preferred is not None else image_variants.get(
                    TextureTreatment.COLOR
                )
            if treatments == {TextureTreatment.COLOR}:
                preferred = image_variants.get(TextureTreatment.COLOR)
                return preferred if preferred is not None else image_variants.get(
                    TextureTreatment.DATA
                )
            if link_info.from_socket == "Alpha":
                preferred = image_variants.get(TextureTreatment.DATA)
                return preferred if preferred is not None else image_variants.get(
                    TextureTreatment.COLOR
                )
            preferred = image_variants.get(TextureTreatment.COLOR)
            return preferred if preferred is not None else image_variants.get(
                TextureTreatment.DATA
            )
        treatment_variants = self._node_treatment_variants.get(
            link_info.from_node, {}
        )
        for treatment in (TextureTreatment.COLOR, TextureTreatment.DATA):
            nodes = treatment_variants.get(treatment, [])
            if nodes:
                return nodes[0]
        return node_map.get(link_info.from_node)

    def is_skipped_material_output(self, node_name: str) -> bool:
        """Return whether an inactive duplicate output was intentionally omitted."""
        info = self.analysis.nodes.get(node_name)
        return bool(
            self._active_material_outputs
            and info is not None
            and info.bl_idname == "ShaderNodeOutputMaterial"
            and node_name not in self._active_material_outputs
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
                (
                    primary.outputs.get(name)
                    for name in names
                    if primary.outputs.get(name) is not None
                ),
                None,
            )
            if socket is not None:
                native_outputs.append(socket)
        if (len(native_outputs) == len(used_channels)
                and len({_rna_identity(socket) for socket in native_outputs})
                == len(used_channels)):
            return primary

        channel_treatments: dict[str, set[TextureTreatment]] = {
            channel: set() for channel in used_channels
        }
        for link in outgoing:
            channel = channel_aliases[link.from_socket]
            channel_treatments[channel].update(
                self.intent_treatments_for_link(link)
            )

        variant_specs: list[tuple[str, TextureTreatment | None]] = []
        for channel in used_channels:
            treatments = self._ordered_treatments(
                channel_treatments[channel]
            )
            variant_specs.extend(
                (channel, treatment) for treatment in treatments
            )
            if not treatments:
                variant_specs.append((channel, None))

        variants: dict[
            tuple[str, TextureTreatment | None], "bpy.types.Node"
        ] = {}
        for index, (channel, treatment) in enumerate(variant_specs):
            treatment_label = f" {treatment.value.title()}" if treatment else ""
            variant = create_node_from_candidates(
                target_tree,
                ("ShaderNodeOctChannelPickerTex", "OctaneChannelPicker"),
                label=f"{info.label} [{channel}{treatment_label}]",
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
            if channel_input is not None and hasattr(
                channel_input, "default_value"
            ):
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
            variants[(channel, treatment)] = variant
            if treatment is not None:
                self._register_node_treatment_variant(
                    node_name, variant, treatment
                )

        for link in outgoing:
            channel = channel_aliases[link.from_socket]
            treatments = self._ordered_treatments(
                self.intent_treatments_for_link(link)
            )
            candidates = treatments or [None]
            for treatment in candidates:
                variant = variants.get((channel, treatment))
                if variant is None:
                    continue
                self._output_variants.setdefault(
                    (node_name, link.from_socket), variant
                )
                identifier = getattr(link, "from_socket_identifier", "")
                if identifier:
                    self._output_variants.setdefault(
                        (node_name, identifier), variant
                    )
                if treatment is not None:
                    self._output_treatment_variants[
                        (node_name, link.from_socket, treatment)
                    ] = variant
                    if identifier:
                        self._output_treatment_variants[
                            (node_name, identifier, treatment)
                        ] = variant

        if len(variants) == len(variant_specs):
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
            f"[{self.context_name or target_tree.name}] Could not create all "
            f"Channel Picker variants for '{node_name}' "
            f"({len(variants)}/{len(variant_specs)})"
        )
        return primary

    def _expand_mixed_treatment_node(
        self,
        target_tree: "bpy.types.NodeTree",
        node_name: str,
        info,
        primary: "bpy.types.Node",
        cycles_type: str,
        preferred_candidates: list[str],
    ) -> "bpy.types.Node":
        """Duplicate a shared processor chain for color and data branches."""
        if info.bl_idname == "ShaderNodeTexImage":
            return primary
        if node_name in self._created_variants:
            return primary
        if self.intent_treatments_for(node_name) != {
            TextureTreatment.COLOR,
            TextureTreatment.DATA,
        }:
            return primary
        if not any(
            self.intent_treatments_for_link(link)
            for link in self._incoming.get(node_name, [])
        ):
            # Constants and procedural producers can safely fan out to both
            # treatments. Only a shared processing chain needs duplication.
            return primary

        data_node = create_octane_node(
            target_tree,
            cycles_type,
            label=f"{info.label} [Data]",
            preferred_candidates=preferred_candidates,
        )
        if data_node is None:
            from .report import report_data
            report_data.add_warning(
                f"[{self.report_context_name or target_tree.name}] Could not "
                f"duplicate mixed-intent processor '{node_name}'"
            )
            return primary

        primary.label = f"{info.label} [Color]"
        data_node.location = (info.location[0], info.location[1] - 180)
        self._apply_common_state(data_node, info)
        self._created_variants[node_name] = [primary, data_node]
        self._register_node_treatment_variant(
            node_name, primary, TextureTreatment.COLOR
        )
        self._register_node_treatment_variant(
            node_name, data_node, TextureTreatment.DATA
        )
        return primary

    def create_nodes(
        self,
        target_tree: "bpy.types.NodeTree",
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, "bpy.types.Node"]:
        """
        Create Octane nodes in *target_tree* following the computed schedule.
        Returns a mapping of original node name → new Octane node.
        """
        schedule = self.compute_schedule()
        node_map: dict[str, "bpy.types.Node"] = {}

        total = len(schedule)
        for index, node_name in enumerate(schedule):
            if progress_callback is not None:
                progress_callback(index, total, node_name)
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
                # Octane material trees intentionally use Blender's Material
                # Output. In smart mode the original output is CYCLES-only
                # and this converted output stays ALL, which is the fallback
                # selected by Octane's get_output_node("octane") lookup.
                if bl_id == "ShaderNodeOutputMaterial":
                    if self.is_skipped_material_output(node_name):
                        continue
                    existing = (
                        target_tree.nodes.get(node_name)
                        if self.reuse_output_nodes
                        else None
                    )
                    if (existing is not None
                            and existing.bl_idname != "ShaderNodeOutputMaterial"):
                        existing = None
                    if existing is None:
                        existing = target_tree.nodes.new(
                            "ShaderNodeOutputMaterial"
                        )
                        existing.name = node_name
                    try:
                        existing.target = "ALL"
                    except (AttributeError, TypeError):
                        pass
                    try:
                        # This is the sole selected render branch in a copied
                        # material. Smart conversion restores the authored
                        # graph's global activity after node creation.
                        existing.is_active_output = True
                    except (AttributeError, RuntimeError, TypeError):
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
            image_treatments = (
                self.intent_treatments_for(node_name)
                if info.bl_idname == "ShaderNodeTexImage"
                else set()
            )
            if TextureTreatment.COLOR in image_treatments:
                # A role-resolved color branch must retain all RGB channels,
                # even when another branch from the source is scalar data.
                preferred_candidates = [
                    "OctaneRGBImage",
                    "ShaderNodeOctImageTex",
                    "OctaneImageTexture",
                ]
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
                new_node = self._expand_mixed_treatment_node(
                    target_tree,
                    node_name,
                    info,
                    new_node,
                    bl_id,
                    preferred_candidates,
                )
                node_map[node_name] = new_node

                if info.bl_idname == "ShaderNodeTexImage":
                    if image_treatments == {
                        TextureTreatment.COLOR,
                        TextureTreatment.DATA,
                    }:
                        data_variant = self._create_image_conflict_variant(
                            target_tree, node_name, info, new_node
                        )
                        if data_variant is None:
                            self._image_role_variants[node_name] = {
                                TextureTreatment.COLOR: new_node
                            }
                            self._register_image_treatment(
                                node_name,
                                new_node,
                                TextureTreatment.COLOR,
                            )
                    elif len(image_treatments) == 1:
                        treatment = next(iter(image_treatments))
                        self._image_role_variants[node_name] = {
                            treatment: new_node
                        }
                        self._register_image_treatment(
                            node_name, new_node, treatment
                        )

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

        if progress_callback is not None:
            progress_callback(total, total, "Node creation complete")
        return node_map
