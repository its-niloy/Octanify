"""Octanify — topology-aware volumetric reconstruction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bpy

from .node_registry import resolve_output_socket
from .report import report_data
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .shader_detection import LinkInfo, TreeAnalysis

log = get_logger()


def _find_input(node: bpy.types.Node, *names: str):
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

    handled = 0
    for output_name, output_info in analysis.nodes.items():
        if output_info.bl_idname != "ShaderNodeOutputMaterial":
            continue

        volume_link = _find_output_link(analysis, output_name, "Volume")
        if volume_link is None:
            continue

        surface_link = _find_output_link(analysis, output_name, "Surface")
        volume_info = analysis.nodes.get(volume_link.from_node)
        volume_node = node_map.get(volume_link.from_node)
        output_node = node_map.get(output_name)
        if volume_info is None or volume_node is None:
            report_data.add_warning(
                f"[{target_tree.name}] Volume source for '{output_name}' was not converted"
            )
            continue

        medium_output = resolve_output_socket(
            volume_info.bl_idname,
            volume_link.from_socket,
            volume_node,
            socket_identifier=volume_link.from_socket_identifier,
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
            # A volume-only material may still be accepted by an Octane-aware
            # Material Output.  Link reconstruction already attempted that
            # route, so retain it and issue a precise warning for review.
            material_name = material_node.name if material_node is not None else "<none>"
            report_data.add_warning(
                f"[{target_tree.name}] Surface '{material_name}' has no Medium input; "
                f"kept '{volume_link.from_node}' on Material Output.Volume"
            )
            continue

        try:
            # Inputs accept one link.  Remove a stale/duplicate medium before
            # creating the topology-derived connection.
            for existing in list(medium_input.links):
                target_tree.links.remove(existing)
            target_tree.links.new(medium_output, medium_input)
            if output_node is not None:
                _remove_direct_output_link(target_tree, output_node, volume_node)
            handled += 1
            log.info(
                "Connected volume '%s' to '%s'.%s",
                volume_node.name,
                material_node.name,
                medium_input.name,
            )
        except Exception as exc:
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
