"""Octanify — Gamma system.

Applies gamma correction to albedo / base color image textures in
converted Octane materials.  Skips non-color data textures (roughness,
normal maps, metallic, displacement).
"""

from __future__ import annotations

import os

import bpy

from .shading_intent import Role, TextureTreatment
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

_LINEAR_COLORSPACES = {
    "Non-Color",
    "Linear",
    "Raw",
    "Utility - Linear - sRGB",
    "Utility - Raw",
    "Linear ACES",
    "Utility - Linear - Rec.709",
}


def _is_linear_colorspace(name: str) -> bool:
    return name in _LINEAR_COLORSPACES or name.lower().startswith("linear")


def _is_srgb_colorspace(name: str) -> bool:
    return not _is_linear_colorspace(name) and "srgb" in name.lower().replace(" ", "")


def _set_node_gamma(node: bpy.types.Node, value: float) -> bool:
    """Set an Octane image node's legacy gamma through either API shape."""
    gamma_set = False
    if hasattr(node, "gamma"):
        try:
            node.gamma = value
            gamma_set = True
        except (AttributeError, TypeError):
            pass
    for name in ("Legacy gamma", "Gamma", "Power"):
        inp = node.inputs.get(name)
        if inp is None or not hasattr(inp, "default_value"):
            continue
        try:
            inp.default_value = value
            gamma_set = True
            break
        except (TypeError, AttributeError):
            continue
    return gamma_set


def _image_filename(info) -> str:
    filepath = info.properties.get("filepath", "")
    filename = os.path.basename(filepath.replace("\\", "/")) if filepath else ""
    return filename or info.properties.get("image_name", info.name)


def _role_destination(roles: set[Role], treatment: TextureTreatment) -> str:
    if treatment == TextureTreatment.COLOR:
        destinations = (
            (Role.ALBEDO, "Base Color"),
            (Role.EMISSION, "Emission"),
            (Role.SUBSURFACE, "Subsurface"),
            (Role.COAT, "Coat"),
            (Role.SHEEN, "Sheen"),
            (Role.TRANSMISSION, "Transmission"),
        )
        return next(
            (label for role, label in destinations if role in roles),
            "color",
        )
    destinations = (
        (Role.ROUGHNESS, "Roughness"),
        (Role.METALLIC, "Metallic"),
        (Role.NORMAL, "Normal"),
        (Role.BUMP, "Bump"),
        (Role.ALPHA, "Alpha"),
        (Role.DISPLACEMENT, "Displacement"),
        (Role.SUBSURFACE, "Subsurface"),
        (Role.COAT, "Coat"),
        (Role.SHEEN, "Sheen"),
        (Role.TRANSMISSION, "Transmission"),
    )
    return next((label for role, label in destinations if role in roles), "data")


def _report_colorspace_mismatch(
    material_name: str,
    info,
    roles: set[Role],
    treatments: set[TextureTreatment],
) -> None:
    """Surface destination-overrides-source colorspace mismatches."""
    colorspace = info.properties.get("colorspace", "sRGB")
    filename = _image_filename(info)
    from .report import report_data

    if (TextureTreatment.COLOR in treatments
            and roles & {Role.ALBEDO, Role.EMISSION}
            and _is_linear_colorspace(colorspace)):
        destination = _role_destination(roles, TextureTreatment.COLOR)
        report_data.add_warning(
            f"[{material_name}] '{filename}' feeds {destination} but is set to "
            f"{colorspace} — treating as sRGB, verify source asset."
        )
    if (TextureTreatment.DATA in treatments
            and roles & {
                Role.ROUGHNESS,
                Role.METALLIC,
                Role.NORMAL,
                Role.BUMP,
                Role.ALPHA,
            }
            and _is_srgb_colorspace(colorspace)):
        destination = _role_destination(roles, TextureTreatment.DATA)
        report_data.add_warning(
            f"[{material_name}] '{filename}' feeds {destination} but is set to "
            f"{colorspace} — treating as linear/data, verify source asset."
        )


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
        "Albedo color", "Albedo", "Diffuse", "Base color", "Base Color",
        "Emission", "Emission color",
        "Texture1", "Texture2", "Color1", "Color2", "Input1", "Input2", "A", "B",
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
    *,
    analysis=None,
    node_map: dict[str, bpy.types.Node] | None = None,
    graph_engine=None,
) -> int:
    """
    Apply gamma correction to albedo image texture nodes in the material.

    Returns the number of nodes affected.
    """
    if material is None:
        return 0

    tree = getattr(material, "node_tree", material)
    if tree is None or not hasattr(tree, "nodes"):
        return 0
    count = 0

    # Conversion-time path: the destination intent map is authoritative.  It
    # also exposes both nodes created for a color/data conflict.
    if graph_engine is not None and node_map is not None and analysis is not None:
        material_name = (
            getattr(graph_engine, "report_context_name", "")
            or getattr(material, "name", tree.name)
        )
        for node_name, info in analysis.nodes.items():
            if info.bl_idname != "ShaderNodeTexImage":
                continue
            treatments = graph_engine.intent_treatments_for(node_name)
            if not treatments:
                continue
            roles = graph_engine.intent_roles_for(node_name)
            _report_colorspace_mismatch(
                material_name, info, roles, treatments
            )
            for node, treatment in graph_engine.image_variants_for(
                node_name, node_map
            ):
                if treatment == TextureTreatment.COLOR:
                    value = gamma_value
                elif treatment == TextureTreatment.DATA:
                    value = 1.0
                else:
                    continue
                if _set_node_gamma(node, value):
                    count += 1
                    log.debug(
                        "Intent gamma %.2f applied to '%s' (%s)",
                        value,
                        node.name,
                        treatment.value,
                    )
        return count

    # Update-time path for materials converted by the intent engine.  Tags
    # persist on Octane image nodes, so changing the user's albedo gamma never
    # accidentally applies it to a linear/data variant.
    tagged = False
    for node in tree.nodes:
        try:
            treatment_name = node.get("octanify_intent_treatment")
        except (AttributeError, TypeError):
            treatment_name = None
        if treatment_name not in {
            TextureTreatment.COLOR.value,
            TextureTreatment.DATA.value,
        }:
            continue
        tagged = True
        value = gamma_value if treatment_name == "color" else 1.0
        if _set_node_gamma(node, value):
            count += 1
    if tagged:
        return count

    # Legacy fallback for materials converted before role tags existed.
    image_nodes = _find_albedo_image_nodes(tree)
    for node in image_nodes:
        img = getattr(node, "image", None)
        if img is not None:
            cs = getattr(img, "colorspace_settings", None)
            if cs is not None and _is_linear_colorspace(cs.name):
                continue
        if _set_node_gamma(node, gamma_value):
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
