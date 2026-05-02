"""Octanify — Gamma system.

Applies gamma correction to albedo / base color image textures in
converted Octane materials.  Skips non-color data textures (roughness,
normal maps, metallic, displacement).
"""

from __future__ import annotations

import bpy

from ..utils.logger import get_logger

log = get_logger()

# ---------------------------------------------------------------------------
# Heuristics: socket names that should NOT receive gamma correction
# ---------------------------------------------------------------------------

_NON_COLOR_INPUTS: set[str] = {
    "Roughness", "Roughness float",
    "Metallic", "Metallic float",
    "Normal", "Bump", "ShaderNormal",
    "Coating normal", "Coating bump",
    "Displacement", "Height",
    "Opacity", "Opacity float", "Alpha",
    "Anisotropy", "Anisotropy float",
    "Specular", "Specular float",
    "SSS", "Subsurface",
    "Density", "Medium scale",
    "Coating", "Coating float",
    "Sheen", "Sheen float",
    "Coating roughness", "Coating roughness float",
    "Sheen roughness", "Sheen roughness float",
}


# ---------------------------------------------------------------------------
# Apply gamma
# ---------------------------------------------------------------------------

def _is_color_texture_link(link: bpy.types.NodeLink) -> bool:
    """Return True if a link feeds into a color/albedo input (not data)."""
    to_name = link.to_socket.name
    return to_name not in _NON_COLOR_INPUTS


def _find_albedo_image_nodes(
    node_tree: bpy.types.NodeTree,
) -> list[bpy.types.Node]:
    """Find image texture nodes connected to albedo/base color/diffuse inputs."""
    albedo_names = {
        "Albedo color", "Albedo", "Diffuse",
        "Emission", "Emission color",
        "Texture1", "Texture2", "Color1", "Color2",
        "Reflection", "Specular",
    }

    result: list[bpy.types.Node] = []
    visited: set[str] = set()

    for link in node_tree.links:
        to_name = link.to_socket.name
        if to_name not in albedo_names:
            continue

        # Walk backward from this link to find image textures
        _collect_image_nodes(link.from_node, result, visited, node_tree)

    return result


def _collect_image_nodes(
    node: bpy.types.Node,
    result: list[bpy.types.Node],
    visited: set[str],
    node_tree: bpy.types.NodeTree,
) -> None:
    """Recursively collect Octane image texture nodes feeding into a chain."""
    if node.name in visited:
        return
    visited.add(node.name)

    # Check if this is an image texture node
    idname = node.bl_idname
    image_types = {
        "ShaderNodeOctImageTex", "OctaneImageTexture", "OctaneRGBImage",
    }
    if idname in image_types:
        result.append(node)
        return

    # Recurse into inputs
    for inp in node.inputs:
        for link in inp.links:
            _collect_image_nodes(link.from_node, result, visited, node_tree)


def apply_gamma(
    material: bpy.types.Material,
    gamma_value: float,
) -> int:
    """
    Apply gamma correction to albedo image texture nodes in the material.

    Returns the number of nodes affected.
    """
    if material is None or material.node_tree is None:
        return 0

    tree = material.node_tree
    image_nodes = _find_albedo_image_nodes(tree)
    count = 0

    for node in image_nodes:
        # Check if the image is already non-color (shouldn't get gamma)
        img = getattr(node, "image", None)
        if img is not None:
            cs = getattr(img, "colorspace_settings", None)
            if cs is not None:
                cs_name = cs.name
                if cs_name in ("Non-Color", "Linear", "Raw"):
                    continue  # Skip non-color data

        # Try to set gamma on the node
        gamma_set = False

        # Method 1: Direct gamma attribute
        if hasattr(node, "gamma"):
            try:
                node.gamma = gamma_value
                gamma_set = True
            except (AttributeError, TypeError):
                pass

        # Method 2: Gamma input socket
        if not gamma_set:
            for name in ("Gamma", "Power", "Legacy gamma"):
                inp = node.inputs.get(name)
                if inp is not None and hasattr(inp, "default_value"):
                    try:
                        inp.default_value = gamma_value
                        gamma_set = True
                        break
                    except (TypeError, AttributeError):
                        continue

        if gamma_set:
            count += 1
            log.debug("Gamma %.2f applied to '%s'", gamma_value, node.name)

    return count


def update_material_gamma(
    material: bpy.types.Material,
    gamma_value: float,
) -> int:
    """Re-apply gamma to an already-converted material."""
    return apply_gamma(material, gamma_value)


def update_all_materials_gamma(
    materials: list[bpy.types.Material],
    gamma_value: float,
) -> int:
    """Re-apply gamma to a list of converted materials."""
    total = 0
    for mat in materials:
        total += apply_gamma(mat, gamma_value)
    return total
