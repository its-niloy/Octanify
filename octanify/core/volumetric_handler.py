"""Octanify — topology-aware volumetric reconstruction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bpy

from .node_registry import create_node_from_candidates, resolve_output_socket
from .property_mapper import OCTANE_MEDIUM_DENSITY_SCALE
from .report import report_data
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .shader_detection import LinkInfo, TreeAnalysis

log = get_logger()

_VOLUME_NODE_TYPES = {
    "ShaderNodeVolumeAbsorption",
    "ShaderNodeVolumeScatter",
    "ShaderNodeVolumePrincipled",
}


def _find_input(node: bpy.types.Node, *names: str):
    if node is None:
        return None
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _find_output_link(
    analysis: "TreeAnalysis",
    output_name: str,
    socket_name: str,
) -> "LinkInfo | None":
    for link_info in analysis.links:
        if (link_info.to_node == output_name
                and link_info.to_socket == socket_name):
            return link_info
    return None


def _remove_direct_output_link(
    target_tree: bpy.types.NodeTree,
    output_node: bpy.types.Node,
    volume_node: bpy.types.Node,
) -> None:
    """Remove the generic Volume-output link once a material medium is wired."""
    for name in ("Volume", "Medium"):
        socket = output_node.inputs.get(name)
        if socket is None:
            continue
        for link in list(socket.links):
            if link.from_node == volume_node:
                target_tree.links.remove(link)


def _copy_input_value_or_link(
    target_tree: bpy.types.NodeTree,
    source_socket,
    target_socket,
) -> None:
    for link in list(getattr(target_socket, "links", ())):
        target_tree.links.remove(link)
    links = getattr(source_socket, "links", ())
    if links:
        target_tree.links.new(links[0].from_socket, target_socket)
        return
    try:
        target_socket.default_value = source_socket.default_value
    except (AttributeError, TypeError, ValueError):
        pass


def _rna_identity(value) -> int:
    """Return a stable identity for Blender RNA wrappers and test doubles."""
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _unwrapped_density_source(socket):
    """Return the authored source behind an Octanify density multiplier."""
    links = getattr(socket, "links", ())
    if not links:
        return None
    link = links[0]
    source_node = link.from_node
    try:
        is_density_scale = bool(source_node.get("octanify_medium_density_scale"))
    except (AttributeError, ReferenceError, TypeError):
        is_density_scale = False
    if is_density_scale:
        source_input = _find_input(
            source_node, "Texture 1", "Texture1", "Input 1", "Input1"
        )
        source_links = getattr(source_input, "links", ())
        if source_links:
            link = source_links[0]
    return link.from_node, link.from_socket


def _density_inputs_match(left, right) -> bool:
    """Compare constant or linked density inputs after scale insertion."""
    left_source = _unwrapped_density_source(left)
    right_source = _unwrapped_density_source(right)
    if left_source is None or right_source is None:
        if left_source is not None or right_source is not None:
            return False
        try:
            return abs(
                float(left.default_value) - float(right.default_value)
            ) <= 1e-6
        except (AttributeError, TypeError, ValueError):
            return False

    left_node, left_socket = left_source
    right_node, right_socket = right_source
    left_identifier = (
        getattr(left_socket, "identifier", "")
        or getattr(left_socket, "name", "")
    )
    right_identifier = (
        getattr(right_socket, "identifier", "")
        or getattr(right_socket, "name", "")
    )
    return (
        _rna_identity(left_node) == _rna_identity(right_node)
        and left_identifier == right_identifier
    )


def _scale_linked_medium_densities(
    analysis: "TreeAnalysis",
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Apply the Cycles→Octane density scale to linked density textures."""
    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in _VOLUME_NODE_TYPES:
            continue
        medium = node_map.get(node_name)
        density = _find_input(medium, "Density", "Density float")
        if density is None or not getattr(density, "links", None):
            continue
        original_link = density.links[0]
        source_socket = original_link.from_socket
        multiply = create_node_from_candidates(
            target_tree,
            ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"),
            label=f"{node_name} density scale",
        )
        if multiply is None:
            report_data.add_warning(
                f"[{target_tree.name}] Could not scale linked density for "
                f"'{node_name}'"
            )
            continue
        first = _find_input(
            multiply, "Texture 1", "Texture1", "Input 1", "Input1"
        )
        second = _find_input(
            multiply, "Texture 2", "Texture2", "Input 2", "Input2"
        )
        output = next(iter(getattr(multiply, "outputs", ())), None)
        if first is None or second is None or output is None:
            target_tree.nodes.remove(multiply)
            report_data.add_warning(
                f"[{target_tree.name}] Octane density multiplier sockets "
                f"were unavailable for '{node_name}'"
            )
            continue
        try:
            second.default_value = OCTANE_MEDIUM_DENSITY_SCALE
            target_tree.links.remove(original_link)
            target_tree.links.new(source_socket, first)
            target_tree.links.new(output, density)
            multiply.location = (
                medium.location.x - 220.0,
                medium.location.y - 120.0,
            )
            multiply["octanify_medium_density_scale"] = True
        except Exception as exc:
            for socket in (*getattr(multiply, "inputs", ()), *getattr(multiply, "outputs", ())):
                for link in list(getattr(socket, "links", ())):
                    try:
                        target_tree.links.remove(link)
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
            if not getattr(density, "links", None):
                try:
                    target_tree.links.new(source_socket, density)
                except Exception:
                    pass
            try:
                target_tree.nodes.remove(multiply)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            report_data.add_link_failure(
                f"Failed linked density scaling for '{node_name}': {exc}"
            )


def _combined_absorption_scatter_sources(
    analysis: "TreeAnalysis",
    source_name: str,
) -> tuple[str, str] | None:
    """Return Absorption and Scatter branches of a direct Add Shader."""
    source_info = analysis.nodes.get(source_name)
    if source_info is None or source_info.bl_idname != "ShaderNodeAddShader":
        return None
    absorption = None
    scatter = None
    for link_info in analysis.links:
        if link_info.to_node != source_name:
            continue
        branch_info = analysis.nodes.get(link_info.from_node)
        if branch_info is None:
            continue
        if branch_info.bl_idname == "ShaderNodeVolumeAbsorption":
            absorption = link_info.from_node
        elif branch_info.bl_idname == "ShaderNodeVolumeScatter":
            scatter = link_info.from_node
    if absorption is None or scatter is None:
        return None
    return absorption, scatter


def _merge_absorption_into_scattering(
    absorption_name: str,
    scatter_name: str,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> bpy.types.Node | None:
    """Reconstruct an Add Shader volume as one native Scattering medium."""
    absorption = node_map.get(absorption_name)
    scatter = node_map.get(scatter_name)
    absorption_input = _find_input(absorption, "Absorption", "Color")
    combined_input = _find_input(scatter, "Absorption", "Absorption color")
    if absorption_input is None or combined_input is None:
        report_data.add_warning(
            f"[{target_tree.name}] Octane Scattering has no Absorption input; "
            "combined Absorption + Scatter volume was not reconstructed"
        )
        return None

    _copy_input_value_or_link(target_tree, absorption_input, combined_input)
    source_invert = _find_input(absorption, "Invert absorption")
    target_invert = _find_input(scatter, "Invert absorption")
    if source_invert is not None and target_invert is not None:
        _copy_input_value_or_link(target_tree, source_invert, target_invert)

    absorption_density = _find_input(absorption, "Density", "Density float")
    scatter_density = _find_input(scatter, "Density", "Density float")
    if (
        absorption_density is not None
        and scatter_density is not None
        and not _density_inputs_match(absorption_density, scatter_density)
    ):
        report_data.add_approximation(
            f"[{target_tree.name}] Combined Absorption + Scatter uses "
            "different densities; Octane's shared Scattering density "
            "uses the Scatter branch value"
        )

    report_data.add_notice(
        f"[{target_tree.name}] Combined Volume Absorption + Scatter rebuilt "
        "as one Octane Scattering medium"
    )
    return scatter


def _create_volume_only_material(
    output_node: bpy.types.Node,
    target_tree: bpy.types.NodeTree,
) -> bpy.types.Node | None:
    """Create the native invisible surface required to own a mesh medium."""
    if output_node is None:
        return None
    surface_input = _find_input(output_node, "Surface", "Material", "Shader")
    if surface_input is None:
        return None
    material = create_node_from_candidates(
        target_tree,
        ("OctaneNullMaterial", "ShaderNodeOctNullMat"),
        label="Volume-only material",
    )
    if material is None:
        report_data.add_warning(
            f"[{target_tree.name}] No Octane Null Material is available for "
            "the volume-only graph"
        )
        return None
    material_output = next(iter(getattr(material, "outputs", ())), None)
    if material_output is None:
        try:
            target_tree.nodes.remove(material)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        return None
    try:
        target_tree.links.new(material_output, surface_input)
        material.location = (
            output_node.location.x - 260.0,
            output_node.location.y,
        )
        material["octanify_volume_only_material"] = True
    except Exception as exc:
        try:
            target_tree.nodes.remove(material)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        report_data.add_link_failure(
            f"Failed volume-only Null Material link: {exc}"
        )
        return None
    return material


def handle_volumetrics(
    analysis: "TreeAnalysis",
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Attach each output's original volume branch to its surface material.

    Octane media belong on the corresponding material's Medium input.  The
    previous implementation selected the first material in the tree and then
    connected every medium to it, making the result depend on node order and
    overwriting earlier links.  This reconstruction follows the original
    Material Output topology instead.
    """
    if not analysis.has_volume:
        return

    _scale_linked_medium_densities(analysis, node_map, target_tree)

    handled = 0
    for output_name, output_info in analysis.nodes.items():
        if output_info.bl_idname != "ShaderNodeOutputMaterial":
            continue

        volume_link = _find_output_link(analysis, output_name, "Volume")
        if volume_link is None:
            continue

        surface_link = _find_output_link(analysis, output_name, "Surface")
        direct_volume_info = analysis.nodes.get(volume_link.from_node)
        direct_volume_node = node_map.get(volume_link.from_node)
        combined_sources = _combined_absorption_scatter_sources(
            analysis, volume_link.from_node
        )
        if combined_sources is not None:
            absorption_name, scatter_name = combined_sources
            volume_info = analysis.nodes.get(scatter_name)
            volume_node = _merge_absorption_into_scattering(
                absorption_name,
                scatter_name,
                node_map,
                target_tree,
            )
            medium_socket_name = "Volume"
            medium_socket_identifier = "Volume"
        else:
            volume_info = direct_volume_info
            volume_node = direct_volume_node
            medium_socket_name = volume_link.from_socket
            medium_socket_identifier = volume_link.from_socket_identifier
        output_node = node_map.get(output_name)
        if volume_info is None or volume_node is None:
            report_data.add_warning(
                f"[{target_tree.name}] Volume source for '{output_name}' was not converted"
            )
            continue

        if combined_sources is None and volume_info.bl_idname not in _VOLUME_NODE_TYPES:
            report_data.add_warning(
                f"[{target_tree.name}] Volume branch '{volume_link.from_node}' "
                "does not resolve to a supported Octane medium"
            )
            continue

        medium_output = resolve_output_socket(
            volume_info.bl_idname,
            medium_socket_name,
            volume_node,
            socket_identifier=medium_socket_identifier,
        )
        if medium_output is None:
            report_data.add_warning(
                f"[{target_tree.name}] Volume node '{volume_link.from_node}' has no medium output"
            )
            continue

        material_node = (
            node_map.get(surface_link.from_node)
            if surface_link is not None
            else None
        )
        generated_volume_material = False
        if material_node is None and surface_link is None:
            material_node = _create_volume_only_material(
                output_node, target_tree
            )
            generated_volume_material = material_node is not None
        medium_input = (
            _find_input(
                material_node,
                "Medium",
                "Transmission medium",
                "Medium input",
            )
            if material_node is not None
            else None
        )

        if medium_input is None:
            material_name = material_node.name if material_node is not None else "<none>"
            if generated_volume_material:
                try:
                    target_tree.nodes.remove(material_node)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            report_data.add_warning(
                f"[{target_tree.name}] Surface '{material_name}' has no Medium input; "
                f"cannot attach volume '{volume_link.from_node}' to the native "
                "Octane Material Output"
            )
            continue

        try:
            # Inputs accept one link.  Remove a stale/duplicate medium before
            # creating the topology-derived connection.
            for existing in list(medium_input.links):
                target_tree.links.remove(existing)
            target_tree.links.new(medium_output, medium_input)
            if output_node is not None:
                _remove_direct_output_link(
                    target_tree, output_node, direct_volume_node
                )
            handled += 1
            if generated_volume_material:
                report_data.add_notice(
                    f"[{target_tree.name}] Volume-only Cycles graph rebuilt "
                    "with an Octane Null Material"
                )
            log.info(
                "Connected volume '%s' to '%s'.%s",
                volume_node.name,
                material_node.name,
                medium_input.name,
            )
        except Exception as exc:
            if generated_volume_material:
                try:
                    target_tree.nodes.remove(material_node)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            report_data.add_link_failure(
                f"Failed volume link {volume_link.from_node} -> "
                f"{material_node.name}.Medium: {exc}"
            )
            log.warning("Failed to connect volume: %s", exc)

    if handled == 0 and not any(
        "Volume" in warning for warning in report_data.warnings
    ):
        report_data.add_warning(
            f"[{target_tree.name}] Volume nodes were detected but no output volume branch was found"
        )
