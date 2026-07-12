"""Octanify — Conversion engine (orchestrator).

This is the main pipeline that coordinates the full conversion of a
single Cycles material into an Octane material:

1. Duplicate material
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

from typing import TYPE_CHECKING

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
)
from .gamma_system import apply_gamma
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


def get_cache() -> ConversionCache:
    return _cache


def reset_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# Tree clearing
# ---------------------------------------------------------------------------

def _clear_tree_except_output(node_tree: bpy.types.NodeTree) -> None:
    """Remove all nodes except ShaderNodeOutputMaterial."""
    to_remove = [
        n for n in node_tree.nodes
        if n.bl_idname != "ShaderNodeOutputMaterial"
    ]
    for n in to_remove:
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

        oct_from = (
            graph_engine.source_node_for(link_info, node_map)
            if graph_engine is not None
            else node_map.get(from_name)
        )
        oct_targets = (
            graph_engine.created_nodes_for(to_name, node_map)
            if graph_engine is not None
            else ([node_map[to_name]] if to_name in node_map else [])
        )

        if oct_from is None or not oct_targets:
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
                "Emission Strength",
                "Color",
                "Strength",
            )
        ):
            # Texture Emission pins have a different Octane pin type.  The
            # emission reconstruction pass owns these links end-to-end.
            continue

        if to_type == "ShaderNodeBsdfPrincipled":
            if to_sock_name in {
                "Specular IOR Level",
                "Coat Weight",
                "Coat Tint",
                "Sheen Weight",
                "Sheen Tint",
            }:
                # These require scaling or weight × tint graph composition.
                # Linking either Cycles socket directly changes the physical
                # meaning of the Octane material.
                continue
            if to_sock_name in {
                "Diffuse Roughness",
                "Specular Tint",
                "Tangent",
                "Subsurface Weight",
                "Subsurface Radius",
                "Subsurface Scale",
                "Subsurface IOR",
                "Subsurface Anisotropy",
            }:
                report_data.add_approximation(
                    f"[{target_tree.name}] Principled {to_sock_name} has no "
                    "safe direct Universal Material socket; connection kept "
                    "out of the main specular controls"
                )
                continue

        # Resolve output socket on source node (with identifier fallback)
        out_socket = resolve_output_socket(
            from_type,
            from_sock_name,
            oct_from,
            socket_identifier=getattr(link_info, "from_socket_identifier", ""),
        )

        if out_socket is None:
            report_data.add_link_failure(
                f"Cannot resolve output socket {from_name}.{from_sock_name}"
            )
            log.warning(
                "Cannot resolve output socket: %s.%s on %s",
                from_name, from_sock_name, oct_from.bl_idname,
            )
            continue

        for oct_to in oct_targets:
            in_socket = None
            if from_type == "ShaderNodeBevel":
                in_socket = (
                    oct_to.inputs.get("Round edges")
                    or oct_to.inputs.get("Round Edges")
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
    weight_name: str,
    tint_name: str,
    target_names: tuple[str, ...],
    label: str,
) -> None:
    """Build tint × weight only when either Cycles input is connected."""
    weight_link = _incoming_link(analysis, node_name, weight_name)
    tint_link = _incoming_link(analysis, node_name, tint_name)
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

    tint = _get_node_input_value(info, tint_name, (1.0, 1.0, 1.0, 1.0))
    weight = _get_node_input_value(info, weight_name, 0.0)
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
    """Finish Universal inputs that cannot be represented by direct copies."""
    for node_name, info in analysis.nodes.items():
        if info.bl_idname != "ShaderNodeBsdfPrincipled":
            continue
        material_nodes = (
            graph_engine.created_nodes_for(node_name, node_map)
            if graph_engine is not None
            else ([node_map[node_name]] if node_name in node_map else [])
        )
        for material_node in material_nodes:
            _materialize_weighted_layer(
                analysis, node_map, tree, graph_engine, node_name, info,
                material_node,
                weight_name="Coat Weight",
                tint_name="Coat Tint",
                target_names=("Coating", "Coating color"),
                label="coat",
            )
            _materialize_weighted_layer(
                analysis, node_map, tree, graph_engine, node_name, info,
                material_node,
                weight_name="Sheen Weight",
                tint_name="Sheen Tint",
                target_names=("Sheen", "Sheen color"),
                label="sheen",
            )

            specular_link = _incoming_link(
                analysis, node_name, "Specular IOR Level"
            )
            if specular_link is not None:
                source = _source_socket_for_link(
                    specular_link, analysis, node_map, graph_engine
                )
                multiply = create_node_from_candidates(
                    tree,
                    ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"),
                    label="Principled specular × 2",
                )
                if multiply is not None:
                    texture1 = _first_socket(
                        multiply.inputs, ("Texture 1", "Texture1")
                    )
                    texture2 = _first_socket(
                        multiply.inputs, ("Texture 2", "Texture2")
                    )
                    _set_socket_default(texture2, 2.0)
                    _link_generated(tree, source, texture1, "Principled specular")
                    _link_generated(
                        tree,
                        _first_socket(
                            multiply.outputs,
                            ("Texture out", "OutTex", "Output"),
                        ),
                        _first_socket(
                            material_node.inputs, ("Specular", "Specular float")
                        ),
                        "scaled Principled specular",
                    )

            transmission_link = _incoming_link(
                analysis, node_name, "Transmission Weight"
            ) or _incoming_link(analysis, node_name, "Transmission")
            transmission = _get_node_input_value(
                info, "Transmission Weight",
                _get_node_input_value(info, "Transmission", 0.0),
            )
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
        for name in ["Material1", "Shader1", "Material 1"]:
            mat1_sock = oct_node.inputs.get(name)
            if mat1_sock is not None:
                break
        for name in ["Material2", "Shader2", "Material 2"]:
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
            oct_from = node_map.get(link_info.from_node)
            oct_to = node_map.get(link_info.to_node)
            if oct_from is None or oct_to is None:
                continue

            # Use a genuine Alpha output when the created node provides one.
            alpha_out = oct_from.outputs.get("Alpha")
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
    target_tree: bpy.types.NodeTree,
) -> None:
    """Move output displacement links onto the Octane material when possible."""
    for output_node in target_tree.nodes:
        if output_node.bl_idname != "ShaderNodeOutputMaterial":
            continue

        surface_input = output_node.inputs.get("Surface")
        displacement_input = output_node.inputs.get("Displacement")
        if surface_input is None or displacement_input is None:
            continue
        if not surface_input.links or not displacement_input.links:
            continue

        material_node = surface_input.links[0].from_node
        material_displacement = material_node.inputs.get("Displacement")
        if material_displacement is None:
            continue

        displacement_link = displacement_input.links[0]
        displacement_output = displacement_link.from_socket

        try:
            target_tree.links.remove(displacement_link)
            target_tree.links.new(displacement_output, material_displacement)
            log.info(
                "Moved output displacement to '%s'.Displacement",
                material_node.name,
            )
        except Exception as exc:
            log.warning("Failed to reroute displacement: %s", exc)


# ---------------------------------------------------------------------------
# Converted tree validation
# ---------------------------------------------------------------------------

def _validate_converted_tree(material_name: str, target_tree: bpy.types.NodeTree) -> None:
    """Report obvious conversion leftovers that need manual review."""
    output_nodes = [
        node for node in target_tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
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
            ("Emission Color",)
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
            strength_nonzero = (
                isinstance(strength_default, (int, float))
                and strength_default > 0.0
            )
            if not (color_link or strength_link or (color_nonzero and strength_nonzero)):
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
    obj: bpy.types.Object,
    node_map: dict[str, bpy.types.Node],
    analysis: TreeAnalysis,
) -> None:
    """Apply scale compensation for object scale and coordinate differences."""
    if obj is None:
        return

    obj_scale = obj.scale
    if (abs(obj_scale.x - 1.0) < 0.001
            and abs(obj_scale.y - 1.0) < 0.001
            and abs(obj_scale.z - 1.0) < 0.001):
        return  # No correction needed

    # Find mapping / transform nodes and adjust their scale
    for node_name, info in analysis.nodes.items():
        if info.bl_idname != "ShaderNodeMapping":
            continue

        # UV coordinates are already object-scale independent in both
        # renderers.  Applying object scale to every Mapping node distorts UV
        # materials, so compensate only for coordinate spaces whose values
        # are derived from object/generated bounds.
        needs_object_scale = False
        for link_info in analysis.links:
            if (link_info.to_node != node_name
                    or link_info.to_socket != "Vector"):
                continue
            source_info = analysis.nodes.get(link_info.from_node)
            if (source_info is not None
                    and source_info.bl_idname == "ShaderNodeTexCoord"
                    and link_info.from_socket in ("Generated", "Object")):
                needs_object_scale = True
                break
        if not needs_object_scale:
            continue

        oct_node = node_map.get(node_name)
        if oct_node is None:
            continue

        # Try to find and adjust the Scale input
        scale_sock = oct_node.inputs.get("Scale") or oct_node.inputs.get("Scaling")
        if scale_sock is not None and hasattr(scale_sock, "default_value"):
            try:
                current = list(scale_sock.default_value)
                current[0] *= obj_scale.x
                current[1] *= obj_scale.y
                current[2] *= obj_scale.z
                scale_sock.default_value = current
                report_data.add_approximation(
                    f"Applied object-scale compensation to Mapping node '{node_name}'"
                )
            except (TypeError, IndexError):
                pass


def convert_node_group(
    group_tree: bpy.types.NodeTree,
    gamma_value: float = 2.2,
) -> bpy.types.NodeTree | None:
    """Convert a ShaderNodeTree used by a NodeGroup."""
    if group_tree is None:
        return None

    tree_name = group_tree.name
    cache_key = f"GRP_{tree_name}"
    
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
            group_converter_cb=lambda t: convert_node_group(t, gamma_value),
            context_name=new_tree.name,
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
        _handle_output_displacement(new_tree)
        _fix_mix_shader_links(analysis, node_map, new_tree)
        _handle_alpha(analysis, node_map, new_tree)
        _handle_emission_node_insertion(analysis, node_map, new_tree, engine)
        handle_volumetrics(analysis, node_map, new_tree)
        _validate_converted_tree(new_tree.name, new_tree)

        _preserve_drivers(group_tree, analysis, node_map, new_tree)

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
    
    for driver in anim_data.drivers:
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


def _populate_converted_material(
    original: bpy.types.Material,
    converted: bpy.types.Material,
    analysis: TreeAnalysis,
    gamma_value: float,
    obj: bpy.types.Object | None,
) -> None:
    """Populate a duplicated material; the caller owns transaction cleanup."""
    _clear_tree_except_output(converted.node_tree)

    engine = GraphEngine(
        analysis,
        group_converter_cb=lambda tree: convert_node_group(tree, gamma_value),
        context_name=converted.name,
    )
    node_map = engine.create_nodes(converted.node_tree)

    for node_name in node_map:
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

    _rebuild_links(analysis, node_map, converted.node_tree, engine)
    _handle_principled_material_inputs(
        analysis, node_map, converted.node_tree, engine
    )
    _handle_normal_map_fallback(
        analysis, node_map, converted.node_tree, engine
    )
    _handle_output_displacement(converted.node_tree)
    _fix_mix_shader_links(analysis, node_map, converted.node_tree)
    _handle_alpha(analysis, node_map, converted.node_tree)
    _handle_emission_post(analysis, node_map, converted.node_tree)
    _handle_emission_node_insertion(
        analysis, node_map, converted.node_tree, engine
    )
    handle_volumetrics(analysis, node_map, converted.node_tree)
    apply_gamma(converted, gamma_value)
    _apply_scale_correction(obj, node_map, analysis)
    _preserve_drivers(original.node_tree, analysis, node_map, converted.node_tree)
    _validate_converted_tree(converted.name, converted.node_tree)

def convert_material(
    mat: bpy.types.Material,
    gamma_value: float = 2.2,
    obj: bpy.types.Object | None = None,
) -> bpy.types.Material | None:
    """
    Convert a single Cycles material to Octane.

    Returns the new Octane material, or None on failure.
    """
    if mat is None or mat.node_tree is None:
        log.warning("Material '%s' has no node tree, skipping", getattr(mat, "name", "?"))
        return None

    mat_name = mat.name
    
    # Prevent converting an already converted or natively-authored Octane
    # material.  Name suffixes alone are not reliable because Blender appends
    # .001 and users may legitimately use `_OCTANE` in a Cycles material name.
    if _is_octane_material(mat):
        log.info("Material '%s' is already an Octane material, skipping", mat_name)
        return mat

    # Check cache
    if _cache.has_material(mat_name):
        cached_name = _cache.get_converted_material_name(mat_name)
        cached_material = bpy.data.materials.get(cached_name)
        if cached_material is not None:
            log.info("Material '%s' already converted as '%s', reusing", mat_name, cached_name)
            return cached_material
        _cache.unregister_material(mat_name)

    log.info("Converting material: %s", mat_name)

    new_mat = None
    try:
        analysis = analyze_tree(mat.node_tree)
        new_mat = mat.copy()
        new_mat.name = f"{mat_name}_OCTANE"
        new_mat.use_nodes = True
        try:
            new_mat["octanify_source_material"] = mat_name
            new_mat["octanify_converted"] = True
        except (AttributeError, TypeError):
            pass

        _populate_converted_material(mat, new_mat, analysis, gamma_value, obj)
    except Exception as exc:
        message = f"[{mat_name}] Conversion failed and was rolled back: {exc}"
        report_data.add_warning(message)
        log.error(message, exc_info=True)
        if new_mat is not None:
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

def convert_object_materials(
    obj: bpy.types.Object,
    gamma_value: float = 2.2,
) -> list[bpy.types.Material]:
    """Convert all Cycles materials on a single object."""
    converted = []
    if obj is None or not hasattr(obj, "material_slots"):
        return converted

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue

        new_mat = convert_material(mat, gamma_value=gamma_value, obj=obj)
        if new_mat is not None:
            slot.material = new_mat
            converted.append(new_mat)

    return converted


def convert_scene_materials(
    gamma_value: float = 2.2,
) -> list[bpy.types.Material]:
    """Convert all Cycles materials across all objects in the scene."""
    reset_cache()
    converted = []

    # Filter objects with material slots
    objects = [obj for obj in bpy.context.scene.objects if hasattr(obj, "material_slots")]
    total = len(objects)

    wm = bpy.context.window_manager
    wm.progress_begin(0, max(1, total))

    try:
        for i, obj in enumerate(objects):
            results = convert_object_materials(obj, gamma_value=gamma_value)
            converted.extend(results)
            wm.progress_update(i + 1)
    finally:
        wm.progress_end()

    return converted
