"""Octanify — Volumetric handler.

Detects Volume Absorption and Volume Scatter nodes from the Cycles
analysis and connects them as Octane Medium nodes on the material.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import bpy

from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .shader_detection import TreeAnalysis

log = get_logger()


def handle_volumetrics(
    analysis: "TreeAnalysis",
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """
    Post-process volumetric nodes:
    - Find Octane medium nodes (Absorption / Scatter) in node_map
    - Connect them to the material's Medium input
    """
    if not analysis.has_volume:
        return

    # Find the main material node (Universal, Specular, Glossy, Diffuse)
    material_node = None
    material_types = {
        "ShaderNodeOctUniversalMat", "OctaneUniversalMaterial",
        "ShaderNodeOctSpecularMat", "OctaneSpecularMaterial",
        "ShaderNodeOctGlossyMat", "OctaneGlossyMaterial",
        "ShaderNodeOctDiffuseMat", "OctaneDiffuseMaterial",
    }

    for node in target_tree.nodes:
        if node.bl_idname in material_types:
            material_node = node
            break

    if material_node is None:
        log.warning("No Octane material node found for volumetric connection")
        return

    # Find medium input on the material
    medium_input = None
    for name in ["Medium", "Transmission medium", "Medium input"]:
        medium_input = material_node.inputs.get(name)
        if medium_input is not None:
            break

    if medium_input is None:
        log.warning(
            "Material node '%s' has no Medium input for volumetrics",
            material_node.name,
        )
        return

    # Connect each volume node to the material
    volume_types = {
        "ShaderNodeVolumeAbsorption",
        "ShaderNodeVolumeScatter",
    }

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in volume_types:
            continue

        oct_node = node_map.get(node_name)
        if oct_node is None:
            continue

        # Find the medium output socket
        medium_output = None
        for out_name in ["OutMedium", "Medium out", "Output", "Medium"]:
            medium_output = oct_node.outputs.get(out_name)
            if medium_output is not None:
                break

        if medium_output is None and oct_node.outputs:
            medium_output = oct_node.outputs[0]

        if medium_output is None:
            log.warning(
                "Medium node '%s' has no output socket", oct_node.name
            )
            continue

        try:
            target_tree.links.new(medium_output, medium_input)
            log.info(
                "Connected volume '%s' → '%s'.Medium",
                oct_node.name, material_node.name,
            )
        except Exception as exc:
            log.warning("Failed to connect volume: %s", exc)

    # Also check if the original tree had Volume connections to Output Material
    # and ensure they're properly routed through Octane
    _connect_volume_to_output(analysis, node_map, target_tree)


def _connect_volume_to_output(
    analysis: "TreeAnalysis",
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """
    If the Cycles tree had a volume node connected directly to the
    Material Output's Volume input, connect the Octane medium to the
    output node as well (if Octane output supports it).
    """
    for link_info in analysis.links:
        to_info = analysis.nodes.get(link_info.to_node)
        if to_info is None:
            continue
        if to_info.bl_idname != "ShaderNodeOutputMaterial":
            continue
        if link_info.to_socket != "Volume":
            continue

        from_info = analysis.nodes.get(link_info.from_node)
        if from_info is None:
            continue

        oct_from = node_map.get(link_info.from_node)
        oct_to = node_map.get(link_info.to_node)

        if oct_from is None or oct_to is None:
            continue

        # Find volume/medium input on the output node
        vol_input = None
        for name in ["Volume", "Medium"]:
            vol_input = oct_to.inputs.get(name)
            if vol_input is not None:
                break

        if vol_input is None:
            continue

        # Find output on the medium node
        medium_out = None
        for out_name in ["OutMedium", "Medium out", "Output"]:
            medium_out = oct_from.outputs.get(out_name)
            if medium_out is not None:
                break

        if medium_out is None and oct_from.outputs:
            medium_out = oct_from.outputs[0]

        if medium_out is not None:
            try:
                target_tree.links.new(medium_out, vol_input)
            except Exception as exc:
                log.warning("Failed to connect volume to output: %s", exc)
