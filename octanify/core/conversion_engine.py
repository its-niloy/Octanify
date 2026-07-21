"""Octanify — Conversion engine (orchestrator).

This is the main pipeline that coordinates the full conversion of a
single Cycles material into an Octane material:

1. Preserve the Cycles graph in-place (smart mode) or duplicate the material
2. Analyze the original tree
3. Clear the new tree (keeping output node)
4. Build conversion schedule via the graph engine
5. Create Octane nodes
6. Transfer properties
7. Rebuild links
8. Post-process (glass, emission, alpha, displacement)
9. Apply gamma corrections
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Callable, Iterable

import bpy

from .shader_detection import analyze_tree, TreeAnalysis
from .graph_engine import GraphEngine
from .property_mapper import transfer_properties
from .node_registry import (
    resolve_input_socket,
    resolve_output_socket,
    PASSTHROUGH_TYPES,
    SKIP_TYPES,
    create_octane_node,
    create_node_from_candidates,
    is_standard_surface_node,
)
from .gamma_system import apply_gamma
from .geonodes_scan import collect_geometry_node_materials
from .shading_intent import (
    CoordinateSource,
    Role,
    ShadingIntentMap,
    trace_shading_intent,
)
from .layout_engine import style_converted_graph, style_smart_graphs
from .volumetric_handler import handle_volumetrics
from .report import report_data
from ..utils.logger import get_logger
from ..utils.cache import ConversionCache

if TYPE_CHECKING:
    pass

log = get_logger()


# ---------------------------------------------------------------------------
# Module-level conversion cache
# ---------------------------------------------------------------------------

_cache = ConversionCache()


def _rna_identity(value) -> int:
    """Return a stable identity for Blender RNA wrappers and test doubles."""
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def get_cache() -> ConversionCache:
    return _cache


def reset_cache() -> None:
    _cache.clear()


def _selected_material_output(node_tree: bpy.types.NodeTree):
    """Return Blender's Cycles output choice with deterministic fallbacks."""
    try:
        output = node_tree.get_output_node("CYCLES")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        output = None
    if output is not None:
        return output
    outputs = [
        node for node in node_tree.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputMaterial"
    ]
    return next(
        (node for node in outputs if bool(getattr(node, "is_active_output", False))),
        outputs[0] if outputs else None,
    )


def _apply_intent_flags(
    analysis: TreeAnalysis,
    intent_map: ShadingIntentMap | None,
    source_tree: bpy.types.NodeTree,
) -> None:
    """Extend snapshot flags with path-aware alpha and emission findings."""
    if intent_map is None:
        return
    source_ids = {_rna_identity(node) for node in source_tree.nodes}
    if intent_map.has_active_emission():
        analysis.has_emission = True
    for (node, output_name), roles in intent_map.items():
        if _rna_identity(node) not in source_ids:
            continue
        if (getattr(node, "bl_idname", "") == "ShaderNodeTexImage"
                and output_name == "Alpha"
                and Role.ALPHA in roles):
            analysis.has_alpha = True
            break


def _group_intent_signature(
    group_tree: bpy.types.NodeTree,
    intent_map: ShadingIntentMap | None,
) -> str:
    """Return a stable cache discriminator for a group's path intent."""
    if intent_map is None:
        return "legacy"
    node_ids = {_rna_identity(node) for node in group_tree.nodes}
    parts: list[str] = []
    for (node, socket_name), roles in intent_map.items():
        if _rna_identity(node) not in node_ids:
            continue
        treatments = intent_map.treatments_for(node, socket_name)
        parts.append(
            ":".join((
                getattr(node, "name", ""),
                socket_name,
                ",".join(sorted(role.value for role in roles)),
                ",".join(sorted(value.value for value in treatments)),
            ))
        )
    for (node, socket_name), roles in intent_map.terminal_inputs.items():
        if _rna_identity(node) not in node_ids:
            continue
        parts.append(
            "terminal:"
            + ":".join((
                getattr(node, "name", ""),
                socket_name,
                ",".join(sorted(role.value for role in roles)),
            ))
        )
    for node, sources in intent_map.coordinate_sources.items():
        if _rna_identity(node) not in node_ids:
            continue
        parts.append(
            "coordinates:"
            + ":".join((
                getattr(node, "name", ""),
                ",".join(sorted(source.value for source in sources)),
            ))
        )
    payload = "|".join(sorted(parts)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Tree clearing
# ---------------------------------------------------------------------------

def _clear_tree(node_tree: bpy.types.NodeTree) -> None:
    """Remove every copied node before constructing an Octane-only tree."""
    for n in list(node_tree.nodes):
        node_tree.nodes.remove(n)


# ---------------------------------------------------------------------------
# Socket compatibility hints
# ---------------------------------------------------------------------------

def _maybe_report_socket_mismatch(
    out_socket: bpy.types.NodeSocket,
    in_socket: bpy.types.NodeSocket,
    from_name: str,
    to_name: str,
) -> None:
    """Report suspicious Octane pin mismatches without blocking conversion."""
    out_pin = getattr(out_socket, "octane_pin_type", None)
    in_pin = getattr(in_socket, "octane_pin_type", None)
    if out_pin in (None, 0) or in_pin in (None, 0):
        return
    if out_pin == in_pin:
        return

    report_data.add_approximation(
        f"Check link {from_name}.{out_socket.name} -> {to_name}.{in_socket.name}: "
        f"Octane pin types differ ({out_pin} -> {in_pin})"
    )


# ---------------------------------------------------------------------------
# Link reconstruction
# ---------------------------------------------------------------------------

_COORDINATE_NODE_TYPES = {"ShaderNodeTexCoord", "ShaderNodeUVMap"}
_C4D_PROCEDURAL_TYPES = {
    "ShaderNodeTexNoise",
    "ShaderNodeTexVoronoi",
    "ShaderNodeTexMusgrave",
}
_C4D_LINKABLE_INPUTS = {
    "Vector",
    "Detail",
    "Roughness",
    "Lacunarity",
    "W",
}


def _first_named_socket(collection, *names: str):
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    return None


def _contextual_vector_input(
    from_type: str,
    to_socket_name: str,
    octane_node: bpy.types.Node,
):
    """Resolve Cycles Vector edges using Octane pin semantics.

    Octane does not pass UV coordinates through a transformation node. A
    Mapping node emits a transform and a Texture Coordinate/UV Map node emits
    a projection; both connect independently to the destination texture.
    """
    if to_socket_name != "Vector":
        return None
    if from_type == "ShaderNodeMapping":
        return _first_named_socket(
            octane_node.inputs,
            "UVW transform",
            "UV transform",
            "Transform",
            "UVTransform",
        )
    if from_type in _COORDINATE_NODE_TYPES:
        return _first_named_socket(octane_node.inputs, "Projection", "UV")
    return None


def _mapping_consumers(
    analysis: TreeAnalysis,
    mapping_name: str,
):
    """Yield non-Mapping consumers reached from a Mapping output."""
    queue = [mapping_name]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in analysis.links:
            if edge.from_node != current or edge.from_socket != "Vector":
                continue
            target_info = analysis.nodes.get(edge.to_node)
            if target_info is None:
                continue
            if target_info.bl_idname == "ShaderNodeMapping":
                queue.append(edge.to_node)
            else:
                yield edge


def _link_projection_through_mapping(
    coordinate_link,
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None,
) -> None:
    """Route a coordinate projection around Mapping to texture consumers."""
    source_info = analysis.nodes.get(coordinate_link.from_node)
    octane_source = node_map.get(coordinate_link.from_node)
    if source_info is None or octane_source is None:
        return
    projection_output = resolve_output_socket(
        source_info.bl_idname,
        coordinate_link.from_socket,
        octane_source,
        socket_identifier=getattr(coordinate_link, "from_socket_identifier", ""),
    )
    if projection_output is None:
        report_data.add_link_failure(
            f"Cannot resolve projection output {coordinate_link.from_node}."
            f"{coordinate_link.from_socket}"
        )
        return

    connected = False
    for consumer in _mapping_consumers(analysis, coordinate_link.to_node):
        octane_targets = (
            graph_engine.created_nodes_for(consumer.to_node, node_map)
            if graph_engine is not None
            else ([node_map[consumer.to_node]] if consumer.to_node in node_map else [])
        )
        for octane_target in octane_targets:
            projection_input = _first_named_socket(
                octane_target.inputs,
                "Projection",
                "UV",
            )
            if projection_input is None:
                report_data.add_approximation(
                    f"[{target_tree.name}] {consumer.to_node} has no Projection "
                    "input for the original coordinate chain"
                )
                continue
            try:
                for existing in list(projection_input.links):
                    target_tree.links.remove(existing)
                target_tree.links.new(projection_output, projection_input)
                report_data.links_created += 1
                connected = True
            except Exception as exc:
                report_data.add_link_failure(
                    f"Failed projection link {coordinate_link.from_node}."
                    f"{coordinate_link.from_socket} -> {consumer.to_node}.Projection: {exc}"
                )

    if not connected:
        report_data.add_approximation(
            f"[{target_tree.name}] Coordinate input for Mapping "
            f"'{coordinate_link.to_node}' could not be attached to a downstream Projection"
        )

def _rebuild_links(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Reconstruct all links using the socket mapping tables.

    Uses socket identifiers and indices from LinkInfo for proper
    disambiguation when nodes have duplicate socket names.
    """
    for link_info in analysis.links:
        from_name = link_info.from_node
        to_name = link_info.to_node
        from_sock_name = link_info.from_socket
        to_sock_name = link_info.to_socket

        if graph_engine is not None:
            oct_pairs = graph_engine.link_node_pairs(link_info, node_map)
        else:
            oct_from = node_map.get(from_name)
            oct_to = node_map.get(to_name)
            oct_pairs = (
                [(oct_from, oct_to)]
                if oct_from is not None and oct_to is not None
                else []
            )
        oct_targets = [target for _source, target in oct_pairs]

        # Inactive duplicate Material Outputs are intentionally not rebuilt.
        # Their shader branches remain available as disconnected converted
        # nodes, while the active authored output determines the render path.
        if (
            not oct_targets
            and graph_engine is not None
            and graph_engine.is_skipped_material_output(to_name)
        ):
            continue

        if not oct_pairs:
            report_data.add_link_failure(
                f"Skipped link {from_name}.{from_sock_name} -> "
                f"{to_name}.{to_sock_name}: converted node missing"
            )
            log.warning(
                "Skipping link %s.%s → %s.%s (node not in map)",
                from_name, from_sock_name, to_name, to_sock_name,
            )
            continue

        # Get the Cycles node types for socket resolution
        from_info = analysis.nodes.get(from_name)
        to_info = analysis.nodes.get(to_name)
        if from_info is None or to_info is None:
            continue

        from_type = from_info.bl_idname
        to_type = to_info.bl_idname
        standard_surface_target = (
            to_type == "ShaderNodeBsdfPrincipled"
            and all(is_standard_surface_node(node) for node in oct_targets)
        )

        # Cycles feeds coordinates into Mapping, but Octane's 3D
        # Transformation has no coordinate input (its first pin is Rotation
        # order). Route the projection around Mapping to every downstream
        # texture instead of creating a type-invalid link.
        if to_type == "ShaderNodeMapping" and to_sock_name == "Vector":
            if from_type in _COORDINATE_NODE_TYPES:
                _link_projection_through_mapping(
                    link_info,
                    analysis,
                    node_map,
                    target_tree,
                    graph_engine,
                )
            else:
                report_data.add_approximation(
                    f"[{target_tree.name}] {from_name}.{from_sock_name} drives "
                    f"Mapping '{to_name}', but an Octane Transform cannot accept "
                    "a vector input"
                )
            continue

        if from_type == "ShaderNodeMapping" and to_type == "ShaderNodeMapping":
            report_data.add_approximation(
                f"[{target_tree.name}] Chained Mapping nodes '{from_name}' and "
                f"'{to_name}' require transform composition; the downstream "
                "transform is preserved"
            )
            continue

        # Octane's native material output has only a Material input.  Volume
        # and displacement are properties of the connected material and are
        # rebuilt by their topology-aware post-processing passes below.
        if (
            to_type == "ShaderNodeOutputMaterial"
            and to_sock_name in {"Volume", "Displacement"}
        ):
            continue

        # Normal Map/Bump placeholder nodes have no compatible inputs.  Their
        # complete incoming/outgoing topology is reconstructed by the
        # dedicated fallback pass, so generic linking here would only produce
        # false failures and provisional wrong links.
        fallback_types = {"ShaderNodeNormalMap", "ShaderNodeBump"}
        from_fallback = (
            from_type in fallback_types
            and "[UNSUPPORTED]" in getattr(node_map.get(from_name), "label", "")
        )
        to_fallback = (
            to_type in fallback_types
            and "[UNSUPPORTED]" in getattr(node_map.get(to_name), "label", "")
        )
        if from_fallback or to_fallback:
            continue

        if (
            to_type in ("ShaderNodeBsdfPrincipled", "ShaderNodeEmission")
            and to_sock_name in (
                "Emission Color",
                "Emission",
                "Emission Strength",
                "Color",
                "Strength",
            )
        ):
            # Texture Emission pins have a different Octane pin type.  The
            # emission reconstruction pass owns these links end-to-end.
            continue

        if to_type == "ShaderNodeBsdfPrincipled":
            if standard_surface_target:
                if to_sock_name in {"Specular IOR Level", "Specular"}:
                    # Both targets need the Cycles 0.5 -> Octane 1.0 scale.
                    continue
                unsupported_standard_inputs = {"Tangent", "Subsurface IOR"}
                if to_sock_name not in unsupported_standard_inputs:
                    # Standard Surface has semantically matching sockets for
                    # the remaining Principled layers, so generic link
                    # reconstruction is correct.
                    pass
                else:
                    report_data.add_approximation(
                        f"[{target_tree.name}] Principled {to_sock_name} has no "
                        "direct Standard Surface control"
                    )
                    continue
            elif to_sock_name in {
                "Specular IOR Level",
                "Specular",
                "Coat Weight",
                "Clearcoat",
                "Coat Tint",
                "Sheen Weight",
                "Sheen",
                "Sheen Tint",
            }:
                # Universal encodes coat/sheen as coloured contribution, so
                # weights and tints must be composed by the dedicated pass.
                continue
            elif to_sock_name in {
                "Base Weight",
                "Diffuse Roughness",
                "Specular Tint",
                "Tangent",
                "Subsurface Weight",
                "Subsurface",
                "Subsurface Color",
                "Subsurface Radius",
                "Subsurface Scale",
                "Subsurface IOR",
                "Subsurface Anisotropy",
                "Transmission Roughness",
            }:
                report_data.add_approximation(
                    f"[{target_tree.name}] Principled {to_sock_name} has no "
                    "safe direct Universal Material socket; connection kept "
                    "out of the main specular controls"
                )
                continue

        for oct_from, oct_to in oct_pairs:
            if (
                to_type in _C4D_PROCEDURAL_TYPES
                and getattr(oct_to, "bl_idname", "") == "OctaneCinema4DNoise"
                and to_sock_name not in _C4D_LINKABLE_INPUTS
            ):
                if to_sock_name == "Scale":
                    detail = (
                        "is baked into the generated UVW transform; a driven "
                        "Scale cannot be preserved dynamically"
                    )
                else:
                    detail = "has no compatible Cinema 4D Noise input"
                report_data.add_approximation(
                    f"[{target_tree.name}] {to_name}.{to_sock_name} {detail}"
                )
                continue

            # Resolve the output per treatment pair: mixed-intent links may
            # originate from different duplicated nodes.
            out_socket = resolve_output_socket(
                from_type,
                from_sock_name,
                oct_from,
                socket_identifier=getattr(
                    link_info, "from_socket_identifier", ""
                ),
            )
            if out_socket is None:
                report_data.add_link_failure(
                    f"Cannot resolve output socket {from_name}.{from_sock_name}"
                )
                log.warning(
                    "Cannot resolve output socket: %s.%s on %s",
                    from_name,
                    from_sock_name,
                    oct_from.bl_idname,
                )
                continue

            in_socket = None
            if from_type == "ShaderNodeBevel":
                in_socket = (
                    oct_to.inputs.get("Round edges")
                    or oct_to.inputs.get("Round Edges")
                )
            if in_socket is None:
                in_socket = _contextual_vector_input(
                    from_type,
                    to_sock_name,
                    oct_to,
                )
            if in_socket is None:
                in_socket = resolve_input_socket(
                    to_type,
                    to_sock_name,
                    oct_to,
                    socket_identifier=getattr(link_info, "to_socket_identifier", ""),
                    socket_index=getattr(link_info, "to_socket_index", -1),
                )
            if in_socket is None:
                report_data.add_link_failure(
                    f"Cannot resolve input socket {to_name}.{to_sock_name}"
                )
                log.warning(
                    "Cannot resolve input socket: %s.%s on %s",
                    to_name, to_sock_name, oct_to.bl_idname,
                )
                continue

            _maybe_report_socket_mismatch(out_socket, in_socket, from_name, to_name)

            try:
                target_tree.links.new(out_socket, in_socket)
                report_data.links_created += 1
                log.debug(
                    "Linked: %s.%s → %s.%s",
                    oct_from.name, out_socket.name, oct_to.name, in_socket.name,
                )
            except Exception as exc:
                report_data.add_link_failure(
                    f"Failed link {from_name}.{from_sock_name} -> "
                    f"{to_name}.{to_sock_name}: {exc}"
                )
                log.warning(
                    "Failed to create link %s.%s → %s.%s: %s",
                    from_name, from_sock_name, to_name, to_sock_name, exc,
                )


# ---------------------------------------------------------------------------
# Principled BSDF / Universal Material physical mapping
# ---------------------------------------------------------------------------

def _incoming_link(
    analysis: TreeAnalysis,
    node_name: str,
    socket_name: str,
):
    return next(
        (
            link for link in analysis.links
            if link.to_node == node_name and link.to_socket == socket_name
        ),
        None,
    )


def _incoming_link_any(
    analysis: TreeAnalysis,
    node_name: str,
    socket_names: tuple[str, ...],
):
    for socket_name in socket_names:
        link = _incoming_link(analysis, node_name, socket_name)
        if link is not None:
            return link
    return None


def _source_socket_for_link(
    link_info,
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    graph_engine: GraphEngine | None,
):
    if link_info is None:
        return None
    source = (
        graph_engine.source_node_for(link_info, node_map)
        if graph_engine is not None
        else node_map.get(link_info.from_node)
    )
    source_info = analysis.nodes.get(link_info.from_node)
    if source is None or source_info is None:
        return None
    return resolve_output_socket(
        source_info.bl_idname,
        link_info.from_socket,
        source,
        socket_identifier=getattr(link_info, "from_socket_identifier", ""),
    )


def _first_socket(collection, names):
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    return None


def _link_generated(
    tree: bpy.types.NodeTree,
    output_socket,
    input_socket,
    description: str,
) -> bool:
    if output_socket is None or input_socket is None:
        report_data.add_warning(f"[{tree.name}] Cannot build {description}")
        return False
    try:
        for existing in list(input_socket.links):
            tree.links.remove(existing)
        tree.links.new(output_socket, input_socket)
        report_data.links_created += 1
        return True
    except (AttributeError, RuntimeError, TypeError) as exc:
        report_data.add_link_failure(
            f"[{tree.name}] Failed to build {description}: {exc}"
        )
        return False


def _set_rgb_constant(node, value) -> bool:
    values = list(value)[:3] if hasattr(value, "__len__") else [value] * 3
    while len(values) < 3:
        values.append(values[-1] if values else 1.0)
    try:
        node.a_value[0:3] = values
        return True
    except (AttributeError, TypeError):
        try:
            node.a_value = tuple(values)
            return True
        except (AttributeError, TypeError):
            socket = _first_socket(node.inputs, ("Color", "Value"))
            return _set_socket_default(socket, tuple(values))


def _set_float_constant(node, value: float) -> bool:
    try:
        node.a_value = float(value)
        return True
    except (AttributeError, TypeError):
        socket = _first_socket(node.inputs, ("Value", "Amount", "Texture"))
        return _set_socket_default(socket, float(value))


def _make_rgb_constant(tree, value, label: str):
    node = create_node_from_candidates(
        tree,
        ("OctaneRGBColor", "ShaderNodeOctRGBColorTex"),
        label=label,
    )
    if node is None or not _set_rgb_constant(node, value):
        if node is not None:
            tree.nodes.remove(node)
        return None
    return node


def _materialize_weighted_layer(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None,
    node_name: str,
    info,
    material_node,
    *,
    weight_names: tuple[str, ...],
    tint_names: tuple[str, ...],
    target_names: tuple[str, ...],
    label: str,
) -> None:
    """Build tint × weight only when either Cycles input is connected."""
    weight_link = _incoming_link_any(analysis, node_name, weight_names)
    tint_link = _incoming_link_any(analysis, node_name, tint_names)
    if weight_link is None and tint_link is None:
        return

    target = _first_socket(material_node.inputs, target_names)
    multiply = create_node_from_candidates(
        tree,
        ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"),
        label=f"{label}: tint × weight",
    )
    if multiply is None or target is None:
        report_data.add_warning(
            f"[{tree.name}] Cannot reconstruct linked Principled {label}"
        )
        return

    texture1 = _first_socket(multiply.inputs, ("Texture 1", "Texture1"))
    texture2 = _first_socket(multiply.inputs, ("Texture 2", "Texture2"))
    output = _first_socket(multiply.outputs, ("Texture out", "OutTex", "Output"))
    tint_source = _source_socket_for_link(
        tint_link, analysis, node_map, graph_engine
    )
    weight_source = _source_socket_for_link(
        weight_link, analysis, node_map, graph_engine
    )

    tint = next(
        (
            value
            for name in tint_names
            if (value := _get_node_input_value(info, name)) is not None
        ),
        (1.0, 1.0, 1.0, 1.0),
    )
    weight = next(
        (
            value
            for name in weight_names
            if (value := _get_node_input_value(info, name)) is not None
        ),
        0.0,
    )
    if tint_link is not None and tint_source is None:
        report_data.add_warning(
            f"[{tree.name}] Cannot resolve linked Principled {label} tint"
        )
    if weight_link is not None and weight_source is None:
        report_data.add_warning(
            f"[{tree.name}] Cannot resolve linked Principled {label} weight"
        )

    weight_routed_as_texture1 = False
    if tint_source is not None:
        _link_generated(tree, tint_source, texture1, f"{label} tint")
    else:
        tint_values = tuple(tint)[:3] if hasattr(tint, "__len__") else (tint,) * 3
        is_white = all(abs(float(component) - 1.0) < 1e-6 for component in tint_values)
        if weight_source is not None and is_white:
            # White × weight needs no extra RGB constant.
            _link_generated(tree, weight_source, texture1, f"{label} weight")
            weight_source = None
            weight_routed_as_texture1 = True
        else:
            constant = _make_rgb_constant(tree, tint_values, f"{label} tint")
            constant_output = (
                _first_socket(constant.outputs, ("Texture out", "OutTex", "Output"))
                if constant is not None else None
            )
            _link_generated(tree, constant_output, texture1, f"{label} tint")

    if weight_source is not None:
        _link_generated(tree, weight_source, texture2, f"{label} weight")
    elif not weight_routed_as_texture1:
        _set_socket_default(texture2, weight)

    _link_generated(tree, output, target, f"Principled {label} layer")


def _get_node_input_value(info, name: str, default=None):
    value = info.inputs.get(name)
    if value is not None:
        return value
    for identifier, display_name in info.input_identifiers.items():
        if display_name == name and info.inputs.get(identifier) is not None:
            return info.inputs[identifier]
    return default


def _handle_principled_material_inputs(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Finish Principled inputs that require target-aware graph operations."""
    for node_name, info in analysis.nodes.items():
        if info.bl_idname != "ShaderNodeBsdfPrincipled":
            continue
        material_nodes = (
            graph_engine.created_nodes_for(node_name, node_map)
            if graph_engine is not None
            else ([node_map[node_name]] if node_name in node_map else [])
        )
        for material_node in material_nodes:
            standard_surface = is_standard_surface_node(material_node)
            if not standard_surface:
                _materialize_weighted_layer(
                    analysis, node_map, tree, graph_engine, node_name, info,
                    material_node,
                    weight_names=("Coat Weight", "Clearcoat"),
                    tint_names=("Coat Tint",),
                    target_names=("Coating", "Coating color"),
                    label="coat",
                )
                _materialize_weighted_layer(
                    analysis, node_map, tree, graph_engine, node_name, info,
                    material_node,
                    weight_names=("Sheen Weight", "Sheen"),
                    tint_names=("Sheen Tint",),
                    target_names=("Sheen", "Sheen color"),
                    label="sheen",
                )

            specular_link = _incoming_link_any(
                analysis, node_name, ("Specular IOR Level", "Specular")
            )
            if specular_link is not None:
                source = _source_socket_for_link(
                    specular_link, analysis, node_map, graph_engine
                )
                target = _first_socket(
                    material_node.inputs,
                    (
                        ("Specular weight",)
                        if standard_surface
                        else ("Specular", "Specular float")
                    ),
                )
                multiply = create_node_from_candidates(
                    tree,
                    ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"),
                    label="Principled specular × 2",
                )
                scaled = False
                if multiply is not None and source is not None and target is not None:
                    texture1 = _first_socket(
                        multiply.inputs, ("Texture 1", "Texture1")
                    )
                    texture2 = _first_socket(
                        multiply.inputs, ("Texture 2", "Texture2")
                    )
                    _set_socket_default(texture2, 2.0)
                    scaled = _link_generated(
                        tree, source, texture1, "Principled specular"
                    ) and _link_generated(
                        tree,
                        _first_socket(
                            multiply.outputs,
                            ("Texture out", "OutTex", "Output"),
                        ),
                        target,
                        "scaled Principled specular",
                    )
                if not scaled:
                    if multiply is not None:
                        try:
                            tree.nodes.remove(multiply)
                        except (AttributeError, RuntimeError, TypeError, ValueError):
                            pass
                    report_data.add_approximation(
                        f"[{tree.name}] Linked Principled specular could not be "
                        "scaled by 2; preserving the source connection"
                    )
                    _link_generated(
                        tree, source, target, "unscaled Principled specular"
                    )

            transmission_link = _incoming_link(
                analysis, node_name, "Transmission Weight"
            ) or _incoming_link(analysis, node_name, "Transmission")
            transmission = _get_node_input_value(
                info, "Transmission Weight",
                _get_node_input_value(info, "Transmission", 0.0),
            )
            if standard_surface:
                # Cycles shares Base Color with transmission and SSS.  When
                # Base Color is textured, fan that texture out to the active
                # Standard Surface layer colours as well.
                base_link = _incoming_link(analysis, node_name, "Base Color")
                base_source = _source_socket_for_link(
                    base_link, analysis, node_map, graph_engine
                )
                layer_colors = (
                    (
                        transmission_link is not None
                        or (isinstance(transmission, (int, float)) and transmission > 0.0),
                        "Transmission color",
                    ),
                    (
                        _incoming_link_any(
                            analysis,
                            node_name,
                            ("Subsurface Weight", "Subsurface"),
                        ) is not None
                        or (
                            isinstance(
                                _get_node_input_value(
                                    info,
                                    "Subsurface Weight",
                                    _get_node_input_value(info, "Subsurface", 0.0),
                                ),
                                (int, float),
                            )
                            and _get_node_input_value(
                                info,
                                "Subsurface Weight",
                                _get_node_input_value(info, "Subsurface", 0.0),
                            ) > 0.0
                        ),
                        "Subsurface color",
                    ),
                )
                for active, socket_name in layer_colors:
                    if active and base_source is not None:
                        _link_generated(
                            tree,
                            base_source,
                            _first_socket(material_node.inputs, (socket_name,)),
                            f"Principled {socket_name}",
                        )
                continue

            target = _first_socket(
                material_node.inputs, ("Transmission", "Transmission float")
            )
            if (
                transmission_link is None
                and isinstance(transmission, (int, float))
                and transmission > 0.0
                and target is not None
            ):
                if hasattr(target, "default_value"):
                    _set_socket_default(target, transmission)
                else:
                    constant = create_node_from_candidates(
                        tree,
                        ("OctaneGreyscaleColor", "ShaderNodeOctFloatTex"),
                        label="Principled transmission weight",
                    )
                    if constant is not None:
                        _set_float_constant(constant, transmission)
                        _link_generated(
                            tree,
                            _first_socket(
                                constant.outputs,
                                ("Texture out", "OutTex", "Output"),
                            ),
                            target,
                            "Principled transmission weight",
                        )


# ---------------------------------------------------------------------------
# MixShader socket swap post-process
# ---------------------------------------------------------------------------

def _fix_mix_shader_links(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Octane MixMaterial has slots 1 and 2 swapped relative to Cycles.

    Uses socket identifiers to correctly distinguish the two Shader inputs
    that share the same display name.
    """
    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in ("ShaderNodeMixShader",):
            continue

        oct_node = node_map.get(node_name)
        if oct_node is None:
            continue

        # Find material input sockets on the Octane MixMaterial
        mat1_sock = None
        mat2_sock = None
        for name in ["First material", "Material1", "Shader1", "Material 1"]:
            mat1_sock = oct_node.inputs.get(name)
            if mat1_sock is not None:
                break
        for name in ["Second material", "Material2", "Shader2", "Material 2"]:
            mat2_sock = oct_node.inputs.get(name)
            if mat2_sock is not None:
                break

        if mat1_sock is None or mat2_sock is None:
            # Try by index: typically index 1 and 2
            if len(oct_node.inputs) >= 3:
                mat1_sock = oct_node.inputs[1]
                mat2_sock = oct_node.inputs[2]
            else:
                continue

        # Store current connections
        mat1_from = mat1_sock.links[0].from_socket if mat1_sock.links else None
        mat2_from = mat2_sock.links[0].from_socket if mat2_sock.links else None

        if mat1_from is None and mat2_from is None:
            continue  # nothing to swap

        # Remove existing links
        for link in list(mat1_sock.links):
            target_tree.links.remove(link)
        for link in list(mat2_sock.links):
            target_tree.links.remove(link)

        # Swap: what was in slot 1 goes to slot 2 and vice versa
        if mat1_from is not None:
            target_tree.links.new(mat1_from, mat2_sock)
        if mat2_from is not None:
            target_tree.links.new(mat2_from, mat1_sock)


# ---------------------------------------------------------------------------
# Alpha / Opacity post-process
# ---------------------------------------------------------------------------

def _handle_alpha(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Preserve Image Texture Alpha edges with a dedicated alpha image.

    Octane commonly exposes RGB and Alpha images as different node types.  If
    the selected image node has no true Alpha output, create one alpha variant
    and route every original Alpha edge through it, including intermediate
    math/color nodes rather than only direct material opacity links.
    """
    if not analysis.has_alpha:
        return

    alpha_variants: dict[str, bpy.types.Node] = {}
    for link_info in analysis.links:
        from_info = analysis.nodes.get(link_info.from_node)
        to_info = analysis.nodes.get(link_info.to_node)
        if from_info is None or to_info is None:
            continue

        if (from_info.bl_idname == "ShaderNodeTexImage"
                and link_info.from_socket == "Alpha"):
            if graph_engine is not None and graph_engine.intent_map is not None:
                source_node = graph_engine._source_node(link_info.from_node)
                if (source_node is None
                        or Role.ALPHA not in graph_engine.intent_map.roles_for(
                            source_node, "Alpha"
                        )):
                    continue
            oct_from = (
                graph_engine.source_node_for(link_info, node_map)
                if graph_engine is not None
                else node_map.get(link_info.from_node)
            )
            oct_to = node_map.get(link_info.to_node)
            if oct_from is None or oct_to is None:
                continue

            # Use a genuine Alpha output when the created node provides one.
            is_alpha_image = getattr(oct_from, "bl_idname", "") in {
                "OctaneAlphaImage",
                "ShaderNodeOctAlphaImage",
            }
            alpha_out = oct_from.outputs.get("Alpha")
            if alpha_out is None and is_alpha_image:
                alpha_out = _first_named_socket(
                    oct_from.outputs,
                    "Texture out",
                    "OutTex",
                    "Output",
                )
                if alpha_out is None and len(oct_from.outputs) == 1:
                    alpha_out = oct_from.outputs[0]
            alpha_node = oct_from

            if alpha_out is None:
                alpha_node = alpha_variants.get(link_info.from_node)
                if alpha_node is None:
                    for candidate in (
                        "OctaneAlphaImage",
                        "ShaderNodeOctAlphaImage",
                    ):
                        try:
                            alpha_node = target_tree.nodes.new(type=candidate)
                            break
                        except (RuntimeError, TypeError, KeyError):
                            continue

                    if alpha_node is None:
                        report_data.add_warning(
                            f"[{target_tree.name}] No Octane Alpha Image node for "
                            f"'{link_info.from_node}'; alpha link may be approximate"
                        )
                        alpha_node = oct_from
                    else:
                        alpha_node.label = f"{from_info.label} [Alpha]"
                        alpha_node.location = (
                            oct_from.location.x,
                            oct_from.location.y - 180,
                        )
                        try:
                            transfer_properties(from_info, alpha_node)
                        except Exception as exc:
                            report_data.add_warning(
                                f"[{target_tree.name}] Alpha image properties failed for "
                                f"'{link_info.from_node}': {exc}"
                            )

                        # Alpha is non-color data regardless of the source
                        # image's display colorspace.
                        try:
                            alpha_node.gamma = 1.0
                        except (AttributeError, TypeError):
                            pass
                        for gamma_name in ("Legacy gamma", "Gamma", "Power"):
                            gamma_input = alpha_node.inputs.get(gamma_name)
                            if gamma_input is not None:
                                _set_socket_default(gamma_input, 1.0)
                                break

                        # Duplicate projection/vector inputs onto the variant.
                        for incoming in analysis.links:
                            if incoming.to_node != link_info.from_node:
                                continue
                            incoming_info = analysis.nodes.get(incoming.from_node)
                            incoming_node = node_map.get(incoming.from_node)
                            if incoming_info is None or incoming_node is None:
                                continue
                            incoming_output = resolve_output_socket(
                                incoming_info.bl_idname,
                                incoming.from_socket,
                                incoming_node,
                                socket_identifier=incoming.from_socket_identifier,
                            )
                            variant_input = resolve_input_socket(
                                from_info.bl_idname,
                                incoming.to_socket,
                                alpha_node,
                                socket_identifier=incoming.to_socket_identifier,
                                socket_index=incoming.to_socket_index,
                            )
                            if incoming_output is not None and variant_input is not None:
                                try:
                                    target_tree.links.new(incoming_output, variant_input)
                                except Exception as exc:
                                    report_data.add_link_failure(
                                        f"Failed Alpha Image transform link for "
                                        f"'{link_info.from_node}': {exc}"
                                    )

                    alpha_variants[link_info.from_node] = alpha_node

                alpha_out = (
                    alpha_node.outputs.get("Alpha")
                    or alpha_node.outputs.get("OutTex")
                    or alpha_node.outputs.get("Output")
                )
                if alpha_out is None and len(alpha_node.outputs) == 1:
                    alpha_out = alpha_node.outputs[0]

            destination_input = resolve_input_socket(
                to_info.bl_idname,
                link_info.to_socket,
                oct_to,
                socket_identifier=link_info.to_socket_identifier,
                socket_index=link_info.to_socket_index,
            )

            if alpha_out is not None and destination_input is not None:
                try:
                    # Remove the provisional RGB-as-alpha link made by generic
                    # reconstruction before installing the true alpha edge.
                    for existing in list(destination_input.links):
                        if existing.from_node == oct_from:
                            target_tree.links.remove(existing)
                    target_tree.links.new(alpha_out, destination_input)
                except Exception as exc:
                    report_data.add_link_failure(
                        f"Failed alpha link {link_info.from_node} -> "
                        f"{link_info.to_node}.{link_info.to_socket}: {exc}"
                    )
                    log.warning("Failed to connect alpha: %s", exc)


# ---------------------------------------------------------------------------
# Output displacement post-process
# ---------------------------------------------------------------------------

def _handle_output_displacement(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Attach each output displacement branch to its Octane material."""
    for output_name, output_info in analysis.nodes.items():
        if output_info.bl_idname != "ShaderNodeOutputMaterial":
            continue

        displacement_link = _incoming_link(
            analysis, output_name, "Displacement"
        )
        surface_link = _incoming_link(analysis, output_name, "Surface")
        if displacement_link is None or surface_link is None:
            continue

        material_node = (
            graph_engine.source_node_for(surface_link, node_map)
            if graph_engine is not None
            else node_map.get(surface_link.from_node)
        )
        material_displacement = (
            material_node.inputs.get("Displacement")
            if material_node is not None
            else None
        )
        displacement_output = _source_socket_for_link(
            displacement_link,
            analysis,
            node_map,
            graph_engine,
        )
        if material_displacement is None or displacement_output is None:
            report_data.add_warning(
                f"[{target_tree.name}] Could not attach displacement for "
                f"'{output_name}' to its converted material"
            )
            continue

        _link_generated(
            target_tree,
            displacement_output,
            material_displacement,
            f"{output_name} displacement",
        )


# ---------------------------------------------------------------------------
# Converted tree validation
# ---------------------------------------------------------------------------

def _validate_converted_tree(material_name: str, target_tree: bpy.types.NodeTree) -> None:
    """Report obvious conversion leftovers that need manual review."""
    output_nodes = [
        node for node in target_tree.nodes
        if node.bl_idname in (
            "OctaneMaterialOutputNode",
            "ShaderNodeOutputMaterial",
        )
    ]
    if not output_nodes:
        # Shader node groups terminate at Group Output rather than Material
        # Output; that is valid and should not pollute the material report.
        if any(
            node.bl_idname == "NodeGroupOutput"
            for node in target_tree.nodes
        ):
            return
        report_data.add_warning(f"[{material_name}] No material output after conversion")
        return

    for output_node in output_nodes:
        surface = output_node.inputs.get("Material")
        if surface is None:
            surface = output_node.inputs.get("Surface")
        if surface is not None and not surface.links:
            report_data.add_warning(
                f"[{material_name}] Material output has no surface connection"
            )

    for node in target_tree.nodes:
        label = getattr(node, "label", "")
        if "[UNSUPPORTED]" in label:
            report_data.add_warning(
                f"[{material_name}] Unsupported fallback remains: {label}"
            )


# ---------------------------------------------------------------------------
# Emission post-process
# ---------------------------------------------------------------------------

def _handle_emission_post(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Enable surface brightness on emission materials."""
    if not analysis.has_emission:
        return

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in ("ShaderNodeBsdfPrincipled", "ShaderNodeEmission"):
            continue
        oct_node = node_map.get(node_name)
        if oct_node is None:
            continue

        try:
            oct_node.surface_brightness = True
        except (AttributeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# Emission node insertion — Octane needs TextureEmission / BlackBodyEmission
# ---------------------------------------------------------------------------

_TEXTURE_EMISSION_NODE_CANDIDATES = [
    "ShaderNodeOctTextureEmission",
    "OctaneTextureEmission",
]


def _snapshot_input_value(info, *display_names: str):
    """Read a NodeInfo input by identifier or display name."""
    for name in display_names:
        if name in info.inputs:
            return info.inputs[name]
        for identifier, display_name in info.input_identifiers.items():
            if display_name == name and identifier in info.inputs:
                return info.inputs[identifier]
    return None


def _find_input_link(
    analysis: TreeAnalysis,
    node_name: str,
    socket_names: tuple[str, ...],
):
    """Find an original link targeting one of the named input sockets."""
    for link_info in analysis.links:
        if (link_info.to_node == node_name
                and link_info.to_socket in socket_names):
            return link_info
    return None


def _remove_link_to_socket(
    target_tree: bpy.types.NodeTree,
    source_node: bpy.types.Node,
    target_socket: bpy.types.NodeSocket | None,
) -> None:
    """Remove a reconstructed direct link without disturbing shared sources."""
    if target_socket is None:
        return
    for link in list(target_socket.links):
        if link.from_node == source_node:
            target_tree.links.remove(link)


def _set_socket_default(socket: bpy.types.NodeSocket | None, value) -> bool:
    """Best-effort assignment for scalar/color emission defaults."""
    if socket is None or value is None or not hasattr(socket, "default_value"):
        return False
    try:
        target = socket.default_value
        if hasattr(target, "__len__") and not isinstance(target, (str, bytes)):
            source = list(value) if hasattr(value, "__len__") else [value]
            target_length = len(target)
            if target_length == 4 and len(source) == 3:
                source.append(1.0)
            socket.default_value = tuple(source[:target_length])
        else:
            socket.default_value = value
        return True
    except (AttributeError, TypeError, ValueError):
        return False

def _handle_emission_node_insertion(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Create and wire the Octane Texture Emission graph for each emitter.

    Octane material Emission pins accept an Emission node, not a raw texture
    or color.  Both linked inputs and unlinked defaults are reconstructed:
    color/texture -> Texture Emission.Texture, strength -> Power, and the
    emission output -> the converted material's Emission pin.
    """
    if not analysis.has_emission:
        return

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in ("ShaderNodeBsdfPrincipled", "ShaderNodeEmission"):
            continue

        oct_mat = node_map.get(node_name)
        if oct_mat is None:
            continue

        color_names = (
            ("Emission Color", "Emission")
            if info.bl_idname == "ShaderNodeBsdfPrincipled"
            else ("Color",)
        )
        strength_names = (
            ("Emission Strength",)
            if info.bl_idname == "ShaderNodeBsdfPrincipled"
            else ("Strength",)
        )
        color_link = _find_input_link(analysis, node_name, color_names)
        strength_link = _find_input_link(analysis, node_name, strength_names)
        color_default = _snapshot_input_value(info, *color_names)
        strength_default = _snapshot_input_value(info, *strength_names)

        if info.bl_idname == "ShaderNodeBsdfPrincipled":
            color_nonzero = (
                color_default is not None
                and any(float(channel) > 0.0 for channel in tuple(color_default)[:3])
            )
            if not (color_link or strength_link or color_nonzero):
                continue

        # Find the Emission input socket on the Octane material.
        emission_sock = None
        for name in ["Emission", "Emission color", "Emission Color"]:
            emission_sock = oct_mat.inputs.get(name)
            if emission_sock is not None:
                break

        if emission_sock is None:
            continue

        # Blackbody already creates an Octane Emission-typed node.  It must
        # connect directly to the material rather than being wrapped in a
        # Texture Emission (which expects a texture-typed input).
        color_source_info = (
            analysis.nodes.get(color_link.from_node)
            if color_link is not None
            else None
        )
        if (color_link is not None
                and color_source_info is not None
                and color_source_info.bl_idname == "ShaderNodeBlackbody"):
            blackbody_node = (
                graph_engine.source_node_for(color_link, node_map)
                if graph_engine is not None
                else node_map.get(color_link.from_node)
            )
            blackbody_output = (
                resolve_output_socket(
                    color_source_info.bl_idname,
                    color_link.from_socket,
                    blackbody_node,
                    socket_identifier=color_link.from_socket_identifier,
                )
                if blackbody_node is not None
                else None
            )
            power_input = (
                _find_input_socket_by_name(
                    blackbody_node,
                    ("Power", "Emission power", "Surface power", "Strength"),
                )
                if blackbody_node is not None
                else None
            )
            if strength_link is not None and power_input is not None:
                strength_info = analysis.nodes.get(strength_link.from_node)
                strength_node = (
                    graph_engine.source_node_for(strength_link, node_map)
                    if graph_engine is not None
                    else node_map.get(strength_link.from_node)
                )
                strength_output = (
                    resolve_output_socket(
                        strength_info.bl_idname,
                        strength_link.from_socket,
                        strength_node,
                        socket_identifier=strength_link.from_socket_identifier,
                    )
                    if strength_info is not None and strength_node is not None
                    else None
                )
                if strength_output is not None:
                    try:
                        target_tree.links.new(strength_output, power_input)
                    except Exception as exc:
                        report_data.add_link_failure(
                            f"Failed Blackbody strength link for '{node_name}': {exc}"
                        )
            elif isinstance(strength_default, (int, float)):
                _set_socket_default(power_input, strength_default * 100.0)

            _set_socket_default(
                _find_input_socket_by_name(
                    blackbody_node,
                    ("Surface brightness", "Surface Brightness"),
                ),
                True,
            )

            if blackbody_output is not None:
                try:
                    target_tree.links.new(blackbody_output, emission_sock)
                    log.info(
                        "Connected Black Body Emission '%s' directly to '%s'",
                        blackbody_node.name,
                        oct_mat.name,
                    )
                except Exception as exc:
                    report_data.add_link_failure(
                        f"Failed Blackbody emission link for '{node_name}': {exc}"
                    )
            continue

        # Create the Octane Texture Emission node
        emission_node = None
        for cand in _TEXTURE_EMISSION_NODE_CANDIDATES:
            try:
                emission_node = target_tree.nodes.new(type=cand)
                emission_node.label = "Emission"
                break
            except (RuntimeError, TypeError, KeyError):
                continue

        if emission_node is None:
            message = f"[{target_tree.name}] Could not create Octane Texture Emission for '{node_name}'"
            report_data.add_warning(message)
            log.warning(message)
            continue

        _set_socket_default(
            _find_input_socket_by_name(
                emission_node,
                ("Surface brightness", "Surface Brightness"),
            ),
            True,
        )
        try:
            emission_node.surface_brightness = True
        except (AttributeError, TypeError):
            pass

        # Position it immediately before the material.  A linked color source
        # is used as a better horizontal anchor when available.
        source_node = None
        if color_link is not None:
            source_node = (
                graph_engine.source_node_for(color_link, node_map)
                if graph_engine is not None
                else node_map.get(color_link.from_node)
            )
        source_x = source_node.location.x if source_node is not None else oct_mat.location.x - 400
        emission_node.location = (
            (source_x + oct_mat.location.x) / 2,
            oct_mat.location.y - 200,
        )

        # Find the texture input on the emission node
        tex_input = None
        for name in ["Texture", "Input", "Color", "Emission"]:
            tex_input = emission_node.inputs.get(name)
            if tex_input is not None:
                break
        if tex_input is None and emission_node.inputs:
            tex_input = emission_node.inputs[0]

        # Find the output of the emission node
        emission_out = None
        for name in ["OutEmission", "Emission out", "Output", "Emission"]:
            emission_out = emission_node.outputs.get(name)
            if emission_out is not None:
                break
        if emission_out is None and emission_node.outputs:
            emission_out = emission_node.outputs[0]

        # Rewire linked color/texture, or preserve the unlinked default.
        if color_link is not None:
            source_info = analysis.nodes.get(color_link.from_node)
            source_node = (
                graph_engine.source_node_for(color_link, node_map)
                if graph_engine is not None
                else node_map.get(color_link.from_node)
            )
            if source_info is not None and source_node is not None:
                source_output = resolve_output_socket(
                    source_info.bl_idname,
                    color_link.from_socket,
                    source_node,
                    socket_identifier=color_link.from_socket_identifier,
                )
                direct_target = resolve_input_socket(
                    info.bl_idname,
                    color_link.to_socket,
                    oct_mat,
                    socket_identifier=color_link.to_socket_identifier,
                    socket_index=color_link.to_socket_index,
                )
                _remove_link_to_socket(target_tree, source_node, direct_target)
                if source_output is not None and tex_input is not None:
                    try:
                        target_tree.links.new(source_output, tex_input)
                    except Exception as exc:
                        report_data.add_link_failure(
                            f"Failed emission texture link for '{node_name}': {exc}"
                        )
                        log.warning("Failed to link source to emission node: %s", exc)
        else:
            _set_socket_default(tex_input, color_default)

        # Rewire a driven strength, otherwise transfer the scalar default.
        power_input = None
        for name in ["Power", "Emission power", "Surface power", "Strength"]:
            power_input = emission_node.inputs.get(name)
            if power_input is not None:
                break

        if strength_link is not None:
            strength_info = analysis.nodes.get(strength_link.from_node)
            strength_node = (
                graph_engine.source_node_for(strength_link, node_map)
                if graph_engine is not None
                else node_map.get(strength_link.from_node)
            )
            if strength_info is not None and strength_node is not None:
                strength_output = resolve_output_socket(
                    strength_info.bl_idname,
                    strength_link.from_socket,
                    strength_node,
                    socket_identifier=strength_link.from_socket_identifier,
                )
                direct_target = resolve_input_socket(
                    info.bl_idname,
                    strength_link.to_socket,
                    oct_mat,
                    socket_identifier=strength_link.to_socket_identifier,
                    socket_index=strength_link.to_socket_index,
                )
                _remove_link_to_socket(target_tree, strength_node, direct_target)
                if strength_output is not None and power_input is not None:
                    try:
                        target_tree.links.new(strength_output, power_input)
                    except Exception as exc:
                        report_data.add_link_failure(
                            f"Failed emission strength link for '{node_name}': {exc}"
                        )
                        log.warning("Failed to link strength to emission node: %s", exc)
        elif isinstance(strength_default, (int, float)):
            # Retain the add-on's historical Cycles-to-Octane power scale.
            _set_socket_default(power_input, strength_default * 100.0)

        # Wire: TextureEmission.Output → Material.Emission
        if emission_out is not None:
            try:
                target_tree.links.new(emission_out, emission_sock)
            except Exception as exc:
                log.warning("Failed to link emission node to material: %s", exc)

        log.info(
            "Inserted Octane Emission node between '%s' and '%s'",
            source_node.name if source_node is not None else "default color",
            oct_mat.name,
        )


# ---------------------------------------------------------------------------
# Normal Map / Bump fallback — rewire [UNSUPPORTED] nodes
# ---------------------------------------------------------------------------

def _handle_normal_map_fallback(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
    graph_engine: GraphEngine | None = None,
) -> None:
    """Recover Normal Map/Bump fallbacks using Octane material inputs.

    Octane accepts RGB normal textures directly on Normal and greyscale
    height textures directly on Bump.  For a chained Cycles Bump node, both
    branches are preserved independently when the destination exposes both
    inputs.  A fallback node is removed only after every outgoing branch was
    recovered successfully.
    """
    fallback_types = {"ShaderNodeNormalMap", "ShaderNodeBump"}

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in fallback_types:
            continue

        oct_node = node_map.get(node_name)
        if oct_node is None:
            continue

        # Check if this is a fallback [UNSUPPORTED] node
        if not (oct_node.label and "[UNSUPPORTED]" in oct_node.label):
            continue  # It's a proper Octane node, skip

        incoming = [link for link in analysis.links if link.to_node == node_name]
        outgoing = [link for link in analysis.links if link.from_node == node_name]
        if not outgoing:
            report_data.add_warning(
                f"[{target_tree.name}] Unused {info.bl_idname} fallback '{node_name}' retained"
            )
            continue

        if info.bl_idname == "ShaderNodeNormalMap":
            normal_links = [link for link in incoming if link.to_socket == "Color"]
            height_links = []
        else:
            normal_links = [link for link in incoming if link.to_socket == "Normal"]
            height_links = [link for link in incoming if link.to_socket == "Height"]

        def connect_source(source_link, destination, input_names) -> bool:
            source_info = analysis.nodes.get(source_link.from_node)
            source_node = (
                graph_engine.source_node_for(source_link, node_map)
                if graph_engine is not None
                else node_map.get(source_link.from_node)
            )

            # Unwrap a temporary Normal Map fallback when it feeds a Bump
            # fallback.  The real RGB image can then connect to the final
            # material Normal input while the Bump height image connects to
            # Bump independently.
            if (source_info is not None
                    and source_info.bl_idname == "ShaderNodeNormalMap"
                    and source_node is not None
                    and "[UNSUPPORTED]" in getattr(source_node, "label", "")):
                upstream = next(
                    (
                        link for link in analysis.links
                        if link.to_node == source_link.from_node
                        and link.to_socket == "Color"
                    ),
                    None,
                )
                if upstream is not None:
                    source_link = upstream
                    source_info = analysis.nodes.get(upstream.from_node)
                    source_node = (
                        graph_engine.source_node_for(upstream, node_map)
                        if graph_engine is not None
                        else node_map.get(upstream.from_node)
                    )

            if source_info is None or source_node is None:
                return False
            source_output = resolve_output_socket(
                source_info.bl_idname,
                source_link.from_socket,
                source_node,
                socket_identifier=source_link.from_socket_identifier,
            )
            destination_input = None
            for input_name in input_names:
                destination_input = destination.inputs.get(input_name)
                if destination_input is not None:
                    break
            if source_output is None or destination_input is None:
                return False
            try:
                for existing in list(destination_input.links):
                    if existing.from_node == oct_node:
                        target_tree.links.remove(existing)
                target_tree.links.new(source_output, destination_input)
                return True
            except Exception as exc:
                log.warning("Normal/Bump fallback rewire failed: %s", exc)
                return False

        recovered_destinations = 0
        for destination_link in outgoing:
            destination = node_map.get(destination_link.to_node)
            if destination is None:
                continue

            recovered = False
            if height_links:
                recovered = connect_source(
                    height_links[0],
                    destination,
                    ("Bump", "Bump texture", "Height"),
                ) or recovered

                strength = _snapshot_input_value(info, "Strength")
                distance = _snapshot_input_value(info, "Distance")
                if isinstance(strength, (int, float)) and isinstance(distance, (int, float)):
                    bump_height = strength * distance
                    if info.properties.get("invert", False):
                        bump_height *= -1.0
                    _set_socket_default(
                        _find_input_socket_by_name(
                            destination,
                            ("Bump height", "Bump Height"),
                        ),
                        bump_height,
                    )

            if normal_links:
                recovered = connect_source(
                    normal_links[0],
                    destination,
                    ("Normal", "Normal texture", "ShaderNormal"),
                ) or recovered

            if recovered:
                recovered_destinations += 1

        if recovered_destinations != len(outgoing):
            deferred_to_bump = (
                info.bl_idname == "ShaderNodeNormalMap"
                and all(
                    analysis.nodes.get(link.to_node) is not None
                    and analysis.nodes[link.to_node].bl_idname == "ShaderNodeBump"
                    and "[UNSUPPORTED]" in getattr(
                        node_map.get(link.to_node), "label", ""
                    )
                    for link in outgoing
                )
            )
            if deferred_to_bump:
                continue
            report_data.add_warning(
                f"[{target_tree.name}] Could not fully recover {info.bl_idname} "
                f"'{node_name}' ({recovered_destinations}/{len(outgoing)} branches)"
            )
            continue

        report_data.add_approximation(
            f"[{target_tree.name}] {info.bl_idname} '{node_name}' was folded into "
            "Octane material Normal/Bump inputs"
        )
        try:
            for link in list(target_tree.links):
                if link.from_node == oct_node or link.to_node == oct_node:
                    target_tree.links.remove(link)
            target_tree.nodes.remove(oct_node)
            del node_map[node_name]
            report_data.recover_unsupported(
                info.bl_idname.replace("ShaderNode", "")
            )
        except (RuntimeError, TypeError, KeyError) as exc:
            report_data.add_warning(
                f"[{target_tree.name}] Recovered '{node_name}' but could not remove fallback: {exc}"
            )

    # Normal Map fallbacks that fed successfully recovered Bump nodes are now
    # orphaned and can be removed safely.
    for node_name, info in analysis.nodes.items():
        if info.bl_idname != "ShaderNodeNormalMap":
            continue
        oct_node = node_map.get(node_name)
        if (oct_node is None
                or "[UNSUPPORTED]" not in getattr(oct_node, "label", "")):
            continue
        outgoing = [link for link in analysis.links if link.from_node == node_name]
        if not outgoing or any(link.to_node in node_map for link in outgoing):
            continue
        try:
            for link in list(target_tree.links):
                if link.from_node == oct_node or link.to_node == oct_node:
                    target_tree.links.remove(link)
            target_tree.nodes.remove(oct_node)
            del node_map[node_name]
            report_data.recover_unsupported("NormalMap")
            report_data.add_approximation(
                f"[{target_tree.name}] ShaderNodeNormalMap '{node_name}' was "
                "folded through the recovered Bump chain"
            )
        except (RuntimeError, TypeError, KeyError) as exc:
            report_data.add_warning(
                f"[{target_tree.name}] Could not remove recovered Normal Map "
                f"fallback '{node_name}': {exc}"
            )


def _find_input_socket_by_name(
    node: bpy.types.Node,
    names: tuple[str, ...],
) -> bpy.types.NodeSocket | None:
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


# ---------------------------------------------------------------------------
# Procedural scale correction
# ---------------------------------------------------------------------------

def _apply_scale_correction(
    obj: bpy.types.Object | None,
    node_map: dict[str, bpy.types.Node],
    analysis: TreeAnalysis,
    intent_map: ShadingIntentMap | None = None,
    source_tree: bpy.types.NodeTree | None = None,
    target_tree: bpy.types.NodeTree | None = None,
) -> None:
    """Match Cycles' bounding-box-relative procedural texture period.

    Generated coordinates are normalized across the local bounding box before
    Cycles applies a procedural node's logical Scale.  Octane evaluates a 3D
    procedural with the inverse UVW transform, so the equivalent transform is
    ``local_bbox_extent / logical_scale`` with the bounding-box minimum as its
    origin.  Object and UV coordinates are already unnormalized and therefore
    use ``1 / logical_scale`` without the bounding-box correction.  Cinema 4D
    Noise has half the spatial frequency at a unit transform, measured against
    Cycles Noise in Blender 5.1 + Octane 31.9, so its transform scale receives
    an additional 0.5 factor.
    """
    if intent_map is None or target_tree is None:
        return

    dimensions = None
    bbox_minima = None
    bounds = list(getattr(obj, "bound_box", ()) or ()) if obj is not None else []
    if bounds:
        try:
            minima = [
                min(float(point[axis]) for point in bounds)
                for axis in range(3)
            ]
            maxima = [
                max(float(point[axis]) for point in bounds)
                for axis in range(3)
            ]
            candidate_dimensions = [
                maximum - minimum
                for minimum, maximum in zip(minima, maxima)
            ]
            if all(dimension > 1.0e-8 for dimension in candidate_dimensions):
                dimensions = candidate_dimensions
                bbox_minima = minima
            else:
                report_data.add_warning(
                    f"[{getattr(obj, 'name', 'Object')}] Bounding-box scale "
                    "matching skipped because a local axis has zero length"
                )
        except (IndexError, TypeError, ValueError):
            dimensions = None

    procedural_types = {
        "ShaderNodeTexNoise",
        "ShaderNodeTexVoronoi",
        "ShaderNodeTexMusgrave",
    }

    def input_value(info, name: str, default: float) -> float:
        value = info.inputs.get(name)
        if value is None:
            for identifier, display_name in info.input_identifiers.items():
                if display_name == name:
                    value = info.inputs.get(identifier)
                    break
        try:
            return float(default if value is None else value)
        except (TypeError, ValueError):
            return float(default)

    def input_vector(
        info,
        name: str,
        default: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        value = info.inputs.get(name)
        if value is None:
            for identifier, display_name in info.input_identifiers.items():
                if display_name == name:
                    value = info.inputs.get(identifier)
                    break
        try:
            values = tuple(float(component) for component in value)
        except (TypeError, ValueError):
            return default
        return values[:3] if len(values) >= 3 else default

    def source_node(node_name: str):
        if source_tree is None:
            return None
        getter = getattr(source_tree.nodes, "get", None)
        if callable(getter):
            found = getter(node_name)
            if found is not None:
                return found
        return next(
            (
                node for node in source_tree.nodes
                if getattr(node, "name", "") == node_name
            ),
            None,
        )

    def first_input(node, names: tuple[str, ...]):
        return next(
            (socket for name in names if (socket := node.inputs.get(name)) is not None),
            None,
        )

    def copy_transform_inputs(source, destination) -> None:
        if source is None:
            return
        for name in ("Rotation order", "Translation", "Rotation", "Scale"):
            source_socket = source.inputs.get(name)
            destination_socket = destination.inputs.get(name)
            if source_socket is None or destination_socket is None:
                continue
            try:
                destination_socket.default_value = source_socket.default_value
            except (AttributeError, TypeError):
                pass

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in procedural_types:
            continue
        original_node = source_node(node_name)
        if original_node is None:
            continue
        coordinate_sources = intent_map.coordinate_sources_for(original_node)
        matched_sources = coordinate_sources & {
            CoordinateSource.GENERATED,
            CoordinateSource.OBJECT,
        }
        uses_generated = CoordinateSource.GENERATED in matched_sources

        octane_node = node_map.get(node_name)
        if octane_node is None:
            continue
        is_c4d_noise = (
            getattr(octane_node, "bl_idname", "") == "OctaneCinema4DNoise"
        )
        if not matched_sources and not is_c4d_noise:
            continue
        transform_input = first_input(
            octane_node,
            ("UVW transform", "UV transform", "Transform", "UVTransform"),
        )
        projection_input = first_input(octane_node, ("Projection",))
        needs_object_projection = bool(matched_sources)
        if (
            transform_input is None
            or (needs_object_projection and projection_input is None)
        ):
            report_data.add_approximation(
                f"[{target_tree.name}] '{node_name}' uses object-relative "
                "coordinates, but its Octane fallback has no transform/projection pair"
            )
            continue

        previous_transform_links = [
            link.from_socket for link in list(getattr(transform_input, "links", ()))
        ]
        previous_projection_links = (
            [
                link.from_socket
                for link in list(getattr(projection_input, "links", ()))
            ]
            if projection_input is not None
            else []
        )
        previous_transform = (
            transform_input.links[0].from_node
            if getattr(transform_input, "links", None)
            else None
        )
        mapping_info = next(
            (
                analysis.nodes.get(source_name)
                for source_name, mapped_node in node_map.items()
                if previous_transform is not None
                and _rna_identity(mapped_node) == _rna_identity(previous_transform)
                and getattr(analysis.nodes.get(source_name), "bl_idname", "")
                == "ShaderNodeMapping"
            ),
            None,
        )
        created_nodes = []
        transform = create_node_from_candidates(
            target_tree,
            ("Octane3DTransformation", "ShaderNodeOct3DTransform"),
            label=f"{info.label} [Scale Match]",
        )
        projection = (
            create_node_from_candidates(
                target_tree,
                ("OctaneXYZToUVW",),
                label=f"{info.label} [Object Coordinates]",
            )
            if needs_object_projection
            else None
        )
        if transform is not None:
            created_nodes.append(transform)
        if projection is not None:
            created_nodes.append(projection)
        if transform is None or (needs_object_projection and projection is None):
            for created in created_nodes:
                try:
                    target_tree.nodes.remove(created)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            report_data.add_warning(
                f"[{target_tree.name}] Could not create procedural scale "
                f"correction nodes for '{node_name}'"
            )
            continue

        transform.location = (
            info.location[0] - 380,
            info.location[1] - 120,
        )
        if projection is not None:
            projection.location = (
                info.location[0] - 380,
                info.location[1] + 120,
            )
        copy_transform_inputs(previous_transform, transform)
        logical_scale = input_value(info, "Scale", 5.0)
        scale_socket = first_input(transform, ("Scale", "Scaling"))
        base_scale = (1.0, 1.0, 1.0)
        if scale_socket is not None:
            try:
                current = tuple(scale_socket.default_value)
                if len(current) >= 3:
                    base_scale = tuple(float(value) for value in current[:3])
            except (AttributeError, TypeError, ValueError):
                pass
            mapping_type = (
                getattr(mapping_info, "properties", {}).get(
                    "vector_type", "POINT"
                )
                if mapping_info is not None
                else ""
            )
            mapping_scale = (
                input_vector(mapping_info, "Scale", (1.0, 1.0, 1.0))
                if mapping_info is not None
                else (1.0, 1.0, 1.0)
            )
            if mapping_type in {"POINT", "VECTOR"}:
                base_scale = tuple(
                    1.0 / value if abs(value) > 1.0e-8 else 1.0e8
                    for value in mapping_scale
                )
            if abs(logical_scale) <= 1.0e-8:
                correction = (1.0e8, 1.0e8, 1.0e8)
                report_data.add_approximation(
                    f"[{target_tree.name}] '{node_name}' uses a zero procedural "
                    "Scale; approximated it with a very large Octane transform"
                )
            elif uses_generated and dimensions is not None:
                correction = tuple(
                    dimension / logical_scale for dimension in dimensions
                )
            else:
                correction = (1.0 / logical_scale,) * 3
            if is_c4d_noise:
                correction = tuple(0.5 * value for value in correction)
            try:
                scale_socket.default_value = tuple(
                    base * factor
                    for base, factor in zip(base_scale, correction)
                )
            except (AttributeError, TypeError):
                pass
        translation_socket = first_input(transform, ("Translation",))
        if translation_socket is not None:
            translation = None
            mapping_type = (
                getattr(mapping_info, "properties", {}).get(
                    "vector_type", "POINT"
                )
                if mapping_info is not None
                else ""
            )
            if mapping_info is not None:
                mapping_location = input_vector(
                    mapping_info, "Location", (0.0, 0.0, 0.0)
                )
                mapping_scale = input_vector(
                    mapping_info, "Scale", (1.0, 1.0, 1.0)
                )
                if mapping_type == "POINT":
                    mapped_translation = tuple(
                        -location / scale if abs(scale) > 1.0e-8 else 0.0
                        for location, scale in zip(
                            mapping_location, mapping_scale
                        )
                    )
                elif mapping_type == "TEXTURE":
                    mapped_translation = mapping_location
                else:
                    mapped_translation = (0.0, 0.0, 0.0)
                if uses_generated and bbox_minima is not None and dimensions is not None:
                    translation = tuple(
                        minimum + dimension * mapped
                        for minimum, dimension, mapped in zip(
                            bbox_minima, dimensions, mapped_translation
                        )
                    )
                else:
                    translation = mapped_translation
                mapping_rotation = input_vector(
                    mapping_info, "Rotation", (0.0, 0.0, 0.0)
                )
                if any(abs(angle) > 1.0e-8 for angle in mapping_rotation):
                    report_data.add_approximation(
                        f"[{target_tree.name}] '{node_name}' combines a rotated "
                        "Cycles Mapping node with procedural scale matching; "
                        "non-uniform bounds may require manual rotation verification"
                    )
            elif uses_generated and bbox_minima is not None:
                translation = tuple(bbox_minima)
            if translation is not None:
                try:
                    translation_socket.default_value = translation
                except (AttributeError, TypeError):
                    pass
        coordinate_space = (
            projection.inputs.get("Coordinate space")
            if projection is not None
            else None
        )
        if coordinate_space is not None:
            try:
                coordinate_space.default_value = "Object space"
            except (AttributeError, TypeError):
                pass
        try:
            for link in list(getattr(transform_input, "links", ())):
                target_tree.links.remove(link)
            if projection_input is not None and projection is not None:
                for link in list(getattr(projection_input, "links", ())):
                    target_tree.links.remove(link)
            target_tree.links.new(transform.outputs[0], transform_input)
            if projection is not None and projection_input is not None:
                target_tree.links.new(projection.outputs[0], projection_input)
            for created in created_nodes:
                try:
                    created["octanify_scale_correction"] = True
                    created["octanify_source_node"] = node_name
                except (AttributeError, RuntimeError, TypeError):
                    pass
            if uses_generated and dimensions is not None:
                report_data.add_notice(
                    f"[{target_tree.name}] Matched "
                    f"{info.bl_idname.replace('ShaderNodeTex', '')} scale for "
                    "Generated coordinates using bounding box "
                    f"{tuple(round(value, 6) for value in dimensions)}"
                )
            elif CoordinateSource.OBJECT in matched_sources:
                report_data.add_notice(
                    f"[{target_tree.name}] Matched "
                    f"{info.bl_idname.replace('ShaderNodeTex', '')} scale for "
                    "Object coordinates"
                )
        except (AttributeError, IndexError, RuntimeError, TypeError) as exc:
            for created in created_nodes:
                try:
                    target_tree.nodes.remove(created)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            for from_socket in previous_transform_links:
                try:
                    target_tree.links.new(from_socket, transform_input)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            if projection_input is not None:
                for from_socket in previous_projection_links:
                    try:
                        target_tree.links.new(from_socket, projection_input)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
            report_data.add_warning(
                f"[{target_tree.name}] Procedural scale correction for "
                f"'{node_name}' was rolled back: {exc}"
            )


def convert_node_group(
    group_tree: bpy.types.NodeTree,
    gamma_value: float = 2.2,
    intent_map: ShadingIntentMap | None = None,
    report_context_name: str = "",
    auto_arrange: bool = True,
    # Kept after the established positional parameters for API compatibility.
    color_nodes: bool = True,
    object_context: bpy.types.Object | None = None,
) -> bpy.types.NodeTree | None:
    """Convert a ShaderNodeTree used by a NodeGroup."""
    if group_tree is None:
        return None

    tree_name = group_tree.name
    layout_signature = "arranged" if auto_arrange else "source_layout"
    color_signature = "colored" if color_nodes else "default_color"
    bounds = list(getattr(object_context, "bound_box", ()) or ())
    if bounds:
        normalized_bounds = tuple(
            tuple(round(float(value), 6) for value in point)
            for point in bounds
        )
        bounds_signature = hashlib.sha1(
            repr(normalized_bounds).encode("utf-8")
        ).hexdigest()[:10]
    else:
        bounds_signature = "no_object_bounds"
    cache_key = (
        f"GRP_{tree_name}_{_group_intent_signature(group_tree, intent_map)}_"
        f"{layout_signature}_{color_signature}_{bounds_signature}"
    )

    if _cache.has_material(cache_key):
        cached_name = _cache.get_converted_material_name(cache_key)
        cached_tree = bpy.data.node_groups.get(cached_name)
        if cached_tree is not None:
            return cached_tree
        _cache.unregister_material(cache_key)

    if not _cache.begin(cache_key):
        message = (
            f"[Group: {tree_name}] Recursive node-group reference detected; "
            "the recursive instance was left unassigned"
        )
        report_data.add_warning(message)
        log.warning(message)
        return None

    log.info("Converting node group: %s", tree_name)
    new_tree = None
    try:
        analysis = analyze_tree(group_tree)
        _apply_intent_flags(analysis, intent_map, group_tree)

        # Always copy the source group.  Reusing a name-matched datablock can
        # destructively clear an unrelated user group or mutate a group still
        # referenced by an earlier conversion.
        new_tree = group_tree.copy()
        new_tree.name = f"{tree_name}_OCTANE"
        try:
            new_tree["octanify_source_group"] = tree_name
            new_tree["octanify_converted"] = True
        except (AttributeError, TypeError):
            pass

        # Clear all but I/O nodes
        to_remove = [n for n in new_tree.nodes if n.bl_idname not in ("NodeGroupInput", "NodeGroupOutput")]
        for n in to_remove:
            new_tree.nodes.remove(n)

        engine = GraphEngine(
            analysis,
            group_converter_cb=lambda t: convert_node_group(
                t,
                gamma_value,
                intent_map,
                report_context_name,
                auto_arrange,
                color_nodes,
                object_context,
            ),
            context_name=new_tree.name,
            intent_map=intent_map,
            source_tree=group_tree,
            report_context_name=report_context_name or new_tree.name,
        )
        node_map = engine.create_nodes(new_tree)

        # Re-register I/O nodes for link mapping
        for n in new_tree.nodes:
            if n.bl_idname in ("NodeGroupInput", "NodeGroupOutput"):
                node_map[n.name] = n

    # WEAKNESS 2 FIX: Validate I/O socket counts match the analysis.
    # If socket count/order changed during conversion, link rebuild will
    # silently connect wrong sockets. This warning catches the mismatch.
        for n in new_tree.nodes:
            if n.bl_idname == "NodeGroupInput":
                orig_info = analysis.nodes.get(n.name)
                if orig_info and len(n.outputs) != len(orig_info.outputs):
                    report_data.add_warning(
                        f"[Group: {tree_name}] GroupInput socket count mismatch "
                        f"(expected {len(orig_info.outputs)}, got {len(n.outputs)}) — links may be wrong"
                    )
            if n.bl_idname == "NodeGroupOutput":
                orig_info = analysis.nodes.get(n.name)
                if orig_info and len(n.inputs) != len(orig_info.inputs):
                    report_data.add_warning(
                        f"[Group: {tree_name}] GroupOutput socket count mismatch "
                        f"(expected {len(orig_info.inputs)}, got {len(n.inputs)}) — links may be wrong"
                    )

        for node_name in node_map:
            info = analysis.nodes.get(node_name)
            if info is None:
                continue
            for oct_node in engine.created_nodes_for(node_name, node_map):
                if oct_node.bl_idname in ("NodeGroupInput", "NodeGroupOutput"):
                    continue
                try:
                    transfer_properties(info, oct_node)
                except Exception as exc:
                    log.warning("Property transfer failed for '%s' in group '%s': %s", node_name, tree_name, exc)

        _rebuild_links(analysis, node_map, new_tree, engine)
        _handle_principled_material_inputs(
            analysis, node_map, new_tree, engine
        )
        _handle_normal_map_fallback(analysis, node_map, new_tree, engine)
        _handle_output_displacement(analysis, node_map, new_tree, engine)
        _fix_mix_shader_links(analysis, node_map, new_tree)
        _handle_alpha(analysis, node_map, new_tree, engine)
        _handle_emission_node_insertion(analysis, node_map, new_tree, engine)
        handle_volumetrics(analysis, node_map, new_tree)
        apply_gamma(
            new_tree,
            gamma_value,
            analysis=analysis,
            node_map=node_map,
            graph_engine=engine,
        )
        _apply_scale_correction(
            object_context,
            node_map,
            analysis,
            intent_map=intent_map,
            source_tree=group_tree,
            target_tree=new_tree,
        )
        _validate_converted_tree(new_tree.name, new_tree)

        _preserve_drivers(group_tree, analysis, node_map, new_tree)

        style_converted_graph(
            new_tree,
            list(new_tree.nodes),
            auto_arrange=auto_arrange,
            colorize=color_nodes,
        )

        _cache.register_material(cache_key, new_tree.name)
        return new_tree
    except Exception as exc:
        message = f"[Group: {tree_name}] Conversion failed: {exc}"
        report_data.add_warning(message)
        log.error(message, exc_info=True)
        if new_tree is not None:
            try:
                bpy.data.node_groups.remove(new_tree)
            except (AttributeError, RuntimeError, TypeError):
                pass
        return None
    finally:
        _cache.end(cache_key)


# ---------------------------------------------------------------------------
# Driver Data Preservation
# ---------------------------------------------------------------------------

def _preserve_drivers(
    orig_tree: bpy.types.NodeTree,
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Attempt to preserve drivers by rebinding data paths to new Octane sockets."""
    anim_data = getattr(orig_tree, "animation_data", None)
    if not anim_data or not getattr(anim_data, "drivers", None):
        return

    import re

    # Smart conversion can add drivers to the same node tree. Snapshot the
    # collection so appending converted drivers cannot extend this iteration.
    for driver in list(anim_data.drivers):
        dp = driver.data_path
        match = re.search(r'nodes\["([^"]+)"\]\.(inputs|outputs)\[(\d+|"[^"]+")\]\.default_value', dp)
        if not match:
            continue

        node_name, io_type, idx_str = match.groups()
        oct_node = node_map.get(node_name)
        orig_info = analysis.nodes.get(node_name)
        if not oct_node or not orig_info:
            continue

        orig_socket_name = ""
        is_output_driven = (io_type == "outputs")

        if idx_str.startswith('"'):
            orig_socket_name = idx_str.strip('"')
        else:
            try:
                idx = int(idx_str)
                orig_node = orig_tree.nodes.get(node_name)
                if orig_node:
                    collection = orig_node.outputs if is_output_driven else orig_node.inputs
                    if len(collection) > idx:
                        orig_socket_name = collection[idx].name
            except ValueError:
                pass

        oct_idx = -1
        if is_output_driven and orig_info.bl_idname == "ShaderNodeValue":
            # Value nodes are driven on their output in Cycles, but Octane expects input 0 to be driven.
            oct_idx = 0
        elif not is_output_driven and orig_socket_name:
            if (
                orig_info.bl_idname in _C4D_PROCEDURAL_TYPES
                and getattr(oct_node, "bl_idname", "")
                == "OctaneCinema4DNoise"
                and orig_socket_name not in _C4D_LINKABLE_INPUTS
            ):
                report_data.add_approximation(
                    f"[{target_tree.name}] Driver on {node_name}."
                    f"{orig_socket_name} cannot be rebound to Cinema 4D Noise"
                )
                continue
            oct_socket = resolve_input_socket(orig_info.bl_idname, orig_socket_name, oct_node)
            if oct_socket:
                for i, s in enumerate(oct_node.inputs):
                    if s == oct_socket:
                        oct_idx = i
                        break

        if oct_idx == -1:
            continue

        new_dp = f'nodes["{oct_node.name}"].inputs[{oct_idx}].default_value'

        try:
            if not target_tree.animation_data:
                target_tree.animation_data_create()
            d = target_tree.driver_add(new_dp, driver.array_index)
            d.driver.type = driver.driver.type
            d.driver.expression = driver.driver.expression

            for var in driver.driver.variables:
                new_var = d.driver.variables.new()
                new_var.name = var.name
                new_var.type = var.type
                for i, target in enumerate(var.targets):
                    new_target = new_var.targets[i]
                    new_target.id = target.id
                    new_target.data_path = target.data_path
                    new_target.transform_type = target.transform_type
                    new_target.transform_space = target.transform_space
                    if hasattr(target, "id_type"):
                        new_target.id_type = target.id_type
        except Exception as exc:
            log.warning("Failed to preserve driver for '%s': %s", dp, exc)


# ---------------------------------------------------------------------------
# Material Conversion
# ---------------------------------------------------------------------------

def _is_octane_material(mat: bpy.types.Material) -> bool:
    """Return True for tagged conversions and native/legacy Octane trees."""
    try:
        if bool(mat.get("octanify_converted", False)):
            return True
    except (AttributeError, TypeError):
        pass

    node_tree = getattr(mat, "node_tree", None)
    if node_tree is None:
        return False
    return any(
        node.bl_idname.startswith(("ShaderNodeOct", "Octane"))
        for node in node_tree.nodes
    )


def _capture_node_visual_state(nodes: Iterable[bpy.types.Node]) -> list[tuple]:
    """Snapshot editor-only state so smart conversion can roll back fully."""
    state = []
    for node in nodes:
        graph_tag = None
        had_graph_tag = False
        try:
            had_graph_tag = "octanify_graph" in node
            if had_graph_tag:
                graph_tag = node["octanify_graph"]
        except (AttributeError, KeyError, TypeError):
            pass
        state.append((
            node,
            (float(node.location.x), float(node.location.y)),
            bool(getattr(node, "use_custom_color", False)),
            tuple(getattr(node, "color", (0.0, 0.0, 0.0))),
            had_graph_tag,
            graph_tag,
            getattr(node, "target", None),
            getattr(node, "is_active_output", None),
        ))
    return state


def _restore_node_visual_state(state: list[tuple]) -> None:
    for (
        node,
        location,
        use_custom_color,
        color,
        had_tag,
        graph_tag,
        target,
        _is_active_output,
    ) in state:
        try:
            node.location = location
            node.use_custom_color = use_custom_color
            node.color = color
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            if had_tag:
                node["octanify_graph"] = graph_tag
            elif "octanify_graph" in node:
                del node["octanify_graph"]
        except (AttributeError, KeyError, TypeError):
            pass
        if target is not None:
            try:
                node.target = target
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
    _restore_active_output_state(state)


def _restore_active_output_state(state: list[tuple]) -> None:
    """Restore authored output selection after creating an Octane output."""
    # Activate the original winner last. Blender enforces one active Material
    # Output globally, so assignment order matters when several outputs exist.
    for expected in (False, True):
        for item in state:
            node = item[0]
            is_active_output = item[-1]
            if is_active_output is None or bool(is_active_output) != expected:
                continue
            try:
                node.is_active_output = expected
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass


def _route_original_outputs_to_cycles(
    nodes: Iterable[bpy.types.Node],
) -> None:
    """Reserve generic Blender outputs for Cycles before adding Octane's ALL output."""
    for node in nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeOutputMaterial":
            continue
        try:
            if node.target in ("", "ALL"):
                node.target = "CYCLES"
        except (AttributeError, TypeError, ValueError):
            continue


def _populate_converted_material(
    original: bpy.types.Material,
    converted: bpy.types.Material,
    analysis: TreeAnalysis,
    gamma_value: float,
    obj: bpy.types.Object | None,
    clear_existing: bool = True,
    progress_callback: Callable[[float, str], None] | None = None,
    intent_map: ShadingIntentMap | None = None,
    auto_arrange: bool = True,
    color_nodes: bool = True,
) -> list[bpy.types.Node]:
    """Build an Octane graph and return every node owned by that graph."""
    if clear_existing:
        _clear_tree(converted.node_tree)
    baseline_ids = {_rna_identity(node) for node in converted.node_tree.nodes}

    engine = GraphEngine(
        analysis,
        group_converter_cb=lambda tree: convert_node_group(
            tree,
            gamma_value,
            intent_map,
            original.name,
            auto_arrange,
            color_nodes,
            obj,
        ),
        context_name=converted.name,
        reuse_output_nodes=clear_existing,
        intent_map=intent_map,
        source_tree=original.node_tree,
        report_context_name=original.name,
    )

    def _node_progress(completed: int, total: int, label: str) -> None:
        if progress_callback is None:
            return
        fraction = 1.0 if total == 0 else completed / total
        progress_callback(0.05 + (0.55 * fraction), f"Creating {label}")

    node_map = engine.create_nodes(
        converted.node_tree,
        progress_callback=_node_progress if progress_callback is not None else None,
    )

    property_total = max(1, len(node_map))
    for property_index, node_name in enumerate(node_map, start=1):
        info = analysis.nodes.get(node_name)
        if info is not None:
            for octane_node in engine.created_nodes_for(node_name, node_map):
                try:
                    transfer_properties(info, octane_node)
                except Exception as exc:
                    report_data.add_warning(
                        f"[{converted.name}] Property transfer failed for "
                        f"'{node_name}': {exc}"
                    )
                    log.warning("Property transfer failed for '%s': %s", node_name, exc)
        if progress_callback is not None:
            progress_callback(
                0.60 + (0.18 * property_index / property_total),
                f"Transferring {node_name}",
            )

    if progress_callback is not None:
        progress_callback(0.80, "Rebuilding node links")
    _rebuild_links(analysis, node_map, converted.node_tree, engine)
    _handle_principled_material_inputs(
        analysis, node_map, converted.node_tree, engine
    )
    _handle_normal_map_fallback(
        analysis, node_map, converted.node_tree, engine
    )
    _handle_output_displacement(
        analysis, node_map, converted.node_tree, engine
    )
    _fix_mix_shader_links(analysis, node_map, converted.node_tree)
    _handle_alpha(analysis, node_map, converted.node_tree, engine)
    _handle_emission_post(analysis, node_map, converted.node_tree)
    _handle_emission_node_insertion(
        analysis, node_map, converted.node_tree, engine
    )
    if progress_callback is not None:
        progress_callback(0.90, "Finalizing material graph")
    handle_volumetrics(analysis, node_map, converted.node_tree)
    apply_gamma(
        converted,
        gamma_value,
        analysis=analysis,
        node_map=node_map,
        graph_engine=engine,
    )
    _apply_scale_correction(
        obj,
        node_map,
        analysis,
        intent_map=intent_map,
        source_tree=original.node_tree,
        target_tree=converted.node_tree,
    )
    _preserve_drivers(original.node_tree, analysis, node_map, converted.node_tree)
    _validate_converted_tree(converted.name, converted.node_tree)

    graph_nodes: list[bpy.types.Node] = []
    seen: set[int] = set()
    for node in converted.node_tree.nodes:
        identity = _rna_identity(node)
        if identity not in baseline_ids:
            graph_nodes.append(node)
            seen.add(identity)
    # Include every mapped node even if a compatibility path reused one that
    # existed at the baseline.
    for mapped in node_map.values():
        identity = _rna_identity(mapped)
        if identity not in seen:
            graph_nodes.append(mapped)
            seen.add(identity)
    return graph_nodes

def convert_material(
    mat: bpy.types.Material,
    gamma_value: float = 2.2,
    obj: bpy.types.Object | None = None,
    smart_conversion: bool = True,
    auto_arrange: bool = True,
    progress_callback: Callable[[float, str], None] | None = None,
    color_nodes: bool = True,
) -> bpy.types.Material | None:
    """
    Convert a single Cycles material to Octane.

    Smart conversion appends an Octane-compatible ALL output graph to the original
    material so Cycles and Octane can coexist. Legacy mode creates a separate
    material datablock and assigns it through the calling object slot.

    Returns the new Octane material, or None on failure.
    """
    if mat is None or mat.node_tree is None:
        log.warning("Material '%s' has no node tree, skipping", getattr(mat, "name", "?"))
        return None

    mat_name = mat.name

    # Check cache
    if _cache.has_material(mat_name):
        cached_name = _cache.get_converted_material_name(mat_name)
        cached_material = bpy.data.materials.get(cached_name)
        if cached_material is not None:
            log.info("Material '%s' already converted as '%s', reusing", mat_name, cached_name)
            return cached_material
        _cache.unregister_material(mat_name)

    # Prevent converting an already converted or natively-authored Octane
    # material.  Name suffixes alone are not reliable because Blender appends
    # .001 and users may legitimately use `_OCTANE` in a Cycles material name.
    if _is_octane_material(mat):
        log.info("Material '%s' is already an Octane material, skipping", mat_name)
        return None

    log.info("Converting material: %s", mat_name)

    new_mat = None
    original_nodes = list(mat.node_tree.nodes)
    original_node_ids = {_rna_identity(node) for node in original_nodes}
    original_visual_state = _capture_node_visual_state(original_nodes)
    try:
        if progress_callback is not None:
            progress_callback(0.02, f"Analyzing {mat_name}")
        analysis = analyze_tree(mat.node_tree)
        intent_map = trace_shading_intent(
            _selected_material_output(mat.node_tree)
        )
        _apply_intent_flags(analysis, intent_map, mat.node_tree)
        if smart_conversion:
            new_mat = mat
            _route_original_outputs_to_cycles(original_nodes)
            converted_nodes = _populate_converted_material(
                mat,
                mat,
                analysis,
                gamma_value,
                obj,
                clear_existing=False,
                progress_callback=progress_callback,
                intent_map=intent_map,
                auto_arrange=auto_arrange,
                color_nodes=color_nodes,
            )
            style_smart_graphs(
                mat.node_tree,
                original_nodes,
                converted_nodes,
                auto_arrange=auto_arrange,
                colorize=color_nodes,
            )
            _restore_active_output_state(original_visual_state)
        else:
            new_mat = mat.copy()
            new_mat.name = f"{mat_name}_OCTANE"
            new_mat.use_nodes = True
            converted_nodes = _populate_converted_material(
                mat,
                new_mat,
                analysis,
                gamma_value,
                obj,
                clear_existing=True,
                progress_callback=progress_callback,
                intent_map=intent_map,
                auto_arrange=auto_arrange,
                color_nodes=color_nodes,
            )
            style_converted_graph(
                new_mat.node_tree,
                converted_nodes,
                auto_arrange=auto_arrange,
                colorize=color_nodes,
            )

        try:
            new_mat["octanify_source_material"] = mat_name
            new_mat["octanify_converted"] = True
            new_mat["octanify_smart_conversion"] = smart_conversion
        except (AttributeError, TypeError):
            pass
        if progress_callback is not None:
            progress_callback(1.0, f"Completed {mat_name}")
    except Exception as exc:
        message = f"[{mat_name}] Conversion failed and was rolled back: {exc}"
        report_data.add_warning(message)
        log.error(message, exc_info=True)
        if progress_callback is not None:
            progress_callback(1.0, f"Failed {mat_name}")
        if smart_conversion:
            # In-place conversion is transactional: remove only nodes created
            # during this attempt and leave the authored Cycles tree intact.
            for node in list(mat.node_tree.nodes):
                if _rna_identity(node) not in original_node_ids:
                    try:
                        mat.node_tree.nodes.remove(node)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
            _restore_node_visual_state(original_visual_state)
            try:
                if "octanify_converted" in mat:
                    del mat["octanify_converted"]
                if "octanify_smart_conversion" in mat:
                    del mat["octanify_smart_conversion"]
            except (AttributeError, KeyError, TypeError):
                pass
        elif new_mat is not None:
            try:
                bpy.data.materials.remove(new_mat)
            except (AttributeError, RuntimeError, TypeError):
                pass
        return None

    _cache.register_material(mat_name, new_mat.name)

    report_data.materials_converted += 1

    log.info("Successfully converted '%s' → '%s'", mat_name, new_mat.name)
    return new_mat


# ---------------------------------------------------------------------------
# Public API — batch conversion
# ---------------------------------------------------------------------------

def collect_material_work_items(
    objects: Iterable[bpy.types.Object],
) -> list[tuple[bpy.types.Object, bpy.types.Material, object | None]]:
    """Collect slot and Geometry Nodes materials in deterministic order.

    The optional third tuple item is the assignable material slot. Geometry
    Nodes references deliberately use ``None`` because this phase discovers
    and converts materials without rewriting Set Material nodes.
    """
    work_items: list[
        tuple[bpy.types.Object, bpy.types.Material, object | None]
    ] = []
    normal_slot_ids: set[int] = set()
    geometry_nodes_ids: set[int] = set()

    for obj in objects:
        if obj is None:
            continue
        for slot in getattr(obj, "material_slots", ()):
            material = getattr(slot, "material", None)
            if material is None:
                continue
            work_items.append((obj, material, slot))
            normal_slot_ids.add(_rna_identity(material))

        for material in collect_geometry_node_materials(obj):
            work_items.append((obj, material, None))
            geometry_nodes_ids.add(_rna_identity(material))

    if geometry_nodes_ids:
        message = (
            f"[Geometry Nodes] Found {len(geometry_nodes_ids)} unique "
            f"material(s) via Geometry Nodes vs {len(normal_slot_ids)} "
            f"via normal slots."
        )
        if message not in report_data.notices:
            report_data.add_notice(message)
            log.info(message)

    return work_items


def _object_bounds_signature(obj: bpy.types.Object | None) -> str:
    """Return a stable discriminator for an object's local bounding box."""
    bounds = list(getattr(obj, "bound_box", ()) or ()) if obj is not None else []
    if not bounds:
        return "no_object_bounds"
    try:
        normalized = tuple(
            tuple(round(float(value), 6) for value in point)
            for point in bounds
        )
    except (TypeError, ValueError):
        return "no_object_bounds"
    return hashlib.sha1(repr(normalized).encode("utf-8")).hexdigest()[:10]


def _material_uses_generated_scale_matching(
    material: bpy.types.Material,
) -> bool:
    """Check Phase 1 intent data for bbox-relative procedural coordinates."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False
    try:
        intent_map = trace_shading_intent(_selected_material_output(tree))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    procedural_types = {
        "ShaderNodeTexNoise",
        "ShaderNodeTexVoronoi",
        "ShaderNodeTexMusgrave",
    }
    return any(
        getattr(node, "bl_idname", "") in procedural_types
        and CoordinateSource.GENERATED in sources
        for node, sources in intent_map.coordinate_sources.items()
    )


def _specialize_bbox_relative_materials(
    work_items: list[tuple[bpy.types.Object, bpy.types.Material, object | None]],
) -> tuple[
    list[tuple[bpy.types.Object, bpy.types.Material, object | None]],
    list[bpy.types.Material],
]:
    """Copy shared Generated-coordinate materials for distinct local bounds.

    Octane UVW transforms are material data, while Cycles Generated coordinates
    normalize separately for each object's local bounding box.  A single shared
    material therefore cannot carry exact constants for differently shaped mesh
    datablocks.  Assignable material slots are specialized before conversion;
    Geometry Nodes references remain shared and are reported when their bounds
    conflict because rewriting authored node groups is outside material conversion.
    """
    grouped: dict[int, list[tuple[int, str, object | None]]] = {}
    materials: dict[int, bpy.types.Material] = {}
    for index, (obj, material, slot) in enumerate(work_items):
        identity = _rna_identity(material)
        materials[identity] = material
        grouped.setdefault(identity, []).append(
            (index, _object_bounds_signature(obj), slot)
        )

    specialized = list(work_items)
    created_sources: list[bpy.types.Material] = []
    for identity, entries in grouped.items():
        concrete_signatures = {
            signature
            for _index, signature, _slot in entries
            if signature != "no_object_bounds"
        }
        if len(concrete_signatures) <= 1:
            continue
        material = materials[identity]
        if not _material_uses_generated_scale_matching(material):
            continue

        geometry_signatures = [
            signature
            for _index, signature, slot in entries
            if slot is None and signature != "no_object_bounds"
        ]
        primary_signature = (
            geometry_signatures[0]
            if geometry_signatures
            else next(
                signature
                for _index, signature, _slot in entries
                if signature != "no_object_bounds"
            )
        )
        copies: dict[str, bpy.types.Material] = {}
        unassignable_signatures: set[str] = set()
        for index, signature, slot in entries:
            if signature in (primary_signature, "no_object_bounds"):
                continue
            if slot is None:
                unassignable_signatures.add(signature)
                continue
            copy = copies.get(signature)
            if copy is None:
                copy = material.copy()
                copy.name = f"{material.name}_OCTANIFY_BOUNDS_{signature}"
                copies[signature] = copy
                created_sources.append(copy)
            obj, _source, original_slot = specialized[index]
            specialized[index] = (obj, copy, original_slot)

        if copies:
            report_data.add_notice(
                f"[{material.name}] Created {len(copies)} material variant(s) "
                "to preserve Generated-coordinate scale across distinct local bounds"
            )
        if unassignable_signatures:
            report_data.add_approximation(
                f"[{material.name}] Geometry Nodes shares this Generated-coordinate "
                "material across incompatible local bounds; Set Material references "
                "cannot be specialized without rewriting the authored Geometry Nodes graph"
            )

    return specialized, created_sources


def convert_object_materials(
    obj: bpy.types.Object,
    gamma_value: float = 2.2,
    smart_conversion: bool = True,
    auto_arrange: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
    color_nodes: bool = True,
) -> list[bpy.types.Material]:
    """Convert all Cycles materials on a single object."""
    return convert_objects_materials(
        [obj] if obj is not None else [],
        gamma_value=gamma_value,
        smart_conversion=smart_conversion,
        auto_arrange=auto_arrange,
        color_nodes=color_nodes,
        progress_callback=progress_callback,
        reset_conversion_cache=False,
    )


def convert_objects_materials(
    objects: Iterable[bpy.types.Object],
    gamma_value: float = 2.2,
    smart_conversion: bool = True,
    auto_arrange: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
    reset_conversion_cache: bool = True,
    color_nodes: bool = True,
) -> list[bpy.types.Material]:
    """Convert discovered materials across a deterministic object collection."""
    if reset_conversion_cache:
        reset_cache()

    work_items = collect_material_work_items(objects)
    work_items, specialized_sources = _specialize_bbox_relative_materials(
        work_items
    )

    converted: list[bpy.types.Material] = []
    converted_ids: set[int] = set()
    total = len(work_items)
    if progress_callback is not None:
        progress_callback(0, 1000, "Preparing materials")

    for index, (obj, mat, slot) in enumerate(work_items, start=1):
        label = getattr(mat, "name", getattr(obj, "name", "Material"))

        def _material_progress(fraction: float, detail: str) -> None:
            if progress_callback is None:
                return
            clamped = min(1.0, max(0.0, float(fraction)))
            overall = ((index - 1) + clamped) / max(1, total)
            progress_callback(round(overall * 1000), 1000, detail)

        new_mat = convert_material(
            mat,
            gamma_value=gamma_value,
            obj=obj,
            smart_conversion=smart_conversion,
            auto_arrange=auto_arrange,
            color_nodes=color_nodes,
            progress_callback=_material_progress,
        )
        if new_mat is not None:
            if slot is not None:
                slot.material = new_mat
            identity = _rna_identity(new_mat)
            if identity not in converted_ids:
                converted.append(new_mat)
                converted_ids.add(identity)
        if progress_callback is not None:
            _material_progress(1.0, label)

    for source in specialized_sources:
        if getattr(source, "users", 0) != 0:
            continue
        try:
            bpy.data.materials.remove(source)
        except (AttributeError, RuntimeError, TypeError):
            pass

    return converted


def convert_scene_materials(
    gamma_value: float = 2.2,
    smart_conversion: bool = True,
    auto_arrange: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
    color_nodes: bool = True,
) -> list[bpy.types.Material]:
    """Convert all Cycles materials across all objects in the scene."""
    objects = list(bpy.context.scene.objects)
    return convert_objects_materials(
        objects,
        gamma_value=gamma_value,
        smart_conversion=smart_conversion,
        auto_arrange=auto_arrange,
        color_nodes=color_nodes,
        progress_callback=progress_callback,
        reset_conversion_cache=True,
    )
