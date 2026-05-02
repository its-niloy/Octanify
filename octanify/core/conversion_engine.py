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
)
from .gamma_system import apply_gamma
from .volumetric_handler import handle_volumetrics
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
# Link reconstruction
# ---------------------------------------------------------------------------

def _rebuild_links(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
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

        oct_from = node_map.get(from_name)
        oct_to = node_map.get(to_name)

        if oct_from is None or oct_to is None:
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

        # Resolve output socket on source node (with identifier fallback)
        out_socket = resolve_output_socket(
            from_type,
            from_sock_name,
            oct_from,
            socket_identifier=getattr(link_info, "from_socket_identifier", ""),
        )

        # Resolve input socket on destination node (with identifier + index)
        in_socket = resolve_input_socket(
            to_type,
            to_sock_name,
            oct_to,
            socket_identifier=getattr(link_info, "to_socket_identifier", ""),
            socket_index=getattr(link_info, "to_socket_index", -1),
        )

        if out_socket is None:
            log.warning(
                "Cannot resolve output socket: %s.%s on %s",
                from_name, from_sock_name, oct_from.bl_idname,
            )
            continue

        if in_socket is None:
            log.warning(
                "Cannot resolve input socket: %s.%s on %s",
                to_name, to_sock_name, oct_to.bl_idname,
            )
            continue

        try:
            target_tree.links.new(out_socket, in_socket)
            log.debug(
                "Linked: %s.%s → %s.%s",
                oct_from.name, out_socket.name, oct_to.name, in_socket.name,
            )
        except Exception as exc:
            log.warning(
                "Failed to create link %s.%s → %s.%s: %s",
                from_name, from_sock_name, to_name, to_sock_name, exc,
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
    """If alpha is detected, ensure proper Opacity setup on the material."""
    if not analysis.has_alpha:
        return

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

            # Try to connect to Opacity input on the material node
            opacity_sock = None
            for name in ["Opacity", "Opacity float", "Alpha"]:
                opacity_sock = oct_to.inputs.get(name)
                if opacity_sock is not None:
                    break

            if opacity_sock is None:
                continue

            # Get alpha output from Octane image node
            alpha_out = oct_from.outputs.get("Alpha")
            if alpha_out is None:
                alpha_out = oct_from.outputs.get("OutTex")
            if alpha_out is None and oct_from.outputs:
                alpha_out = oct_from.outputs[0]

            if alpha_out is not None:
                try:
                    target_tree.links.new(alpha_out, opacity_sock)
                except Exception as exc:
                    log.warning("Failed to connect alpha: %s", exc)


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

_EMISSION_NODE_CANDIDATES = [
    "ShaderNodeOctTextureEmission",
    "OctaneTextureEmission",
    "ShaderNodeOctBlackBodyEmission",
    "OctaneBlackBodyEmission",
]

def _handle_emission_node_insertion(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Insert an Octane Texture Emission node between emissive source and material.

    In Octane, the Emission input on a Universal Material expects an
    Emission node (TextureEmission or BlackBodyEmission), not a raw
    color/texture. This handler:
    1. Finds any texture connected to the material's Emission input
    2. Creates an Octane Texture Emission node
    3. Rewires: source → TextureEmission.Texture → Material.Emission
    """
    if not analysis.has_emission:
        return

    for node_name, info in analysis.nodes.items():
        if info.bl_idname not in ("ShaderNodeBsdfPrincipled", "ShaderNodeEmission"):
            continue

        oct_mat = node_map.get(node_name)
        if oct_mat is None:
            continue

        # Find the Emission input socket on the Octane material
        emission_sock = None
        for name in ["Emission", "Emission color", "Emission Color"]:
            emission_sock = oct_mat.inputs.get(name)
            if emission_sock is not None:
                break

        if emission_sock is None:
            continue

        # Check if something is connected to the emission input
        if not emission_sock.links:
            continue

        # Get the source texture connected to emission
        source_link = emission_sock.links[0]
        source_out_socket = source_link.from_socket
        source_node = source_link.from_node

        # Create the Octane Texture Emission node
        emission_node = None
        for cand in _EMISSION_NODE_CANDIDATES:
            try:
                emission_node = target_tree.nodes.new(type=cand)
                emission_node.label = "Emission"
                break
            except (RuntimeError, TypeError, KeyError):
                continue

        if emission_node is None:
            log.warning("Could not create Octane Emission node — skipping")
            continue

        # Position it between the source and material
        emission_node.location = (
            (source_node.location.x + oct_mat.location.x) / 2,
            oct_mat.location.y - 200,
        )

        # Remove the direct source → material emission link
        target_tree.links.remove(source_link)

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

        # Wire: source → TextureEmission.Texture
        if tex_input is not None:
            try:
                target_tree.links.new(source_out_socket, tex_input)
            except Exception as exc:
                log.warning("Failed to link source to emission node: %s", exc)

        # Wire: TextureEmission.Output → Material.Emission
        if emission_out is not None:
            try:
                target_tree.links.new(emission_out, emission_sock)
            except Exception as exc:
                log.warning("Failed to link emission node to material: %s", exc)

        # Transfer emission power if available
        power_value = None
        for link_info in analysis.links:
            if (link_info.to_node == node_name
                    and link_info.to_socket in ("Emission Strength", "Strength")):
                # Power is connected via a link — skip default
                break
        else:
            # Use the default power value from the original node
            from .shader_detection import NodeInfo
            power_val = info.inputs.get("Emission Strength")
            if power_val is None:
                power_val = info.inputs.get("Strength")
            if power_val is not None:
                for pname in ["Power", "Emission power", "Surface power"]:
                    psock = emission_node.inputs.get(pname)
                    if psock is not None and hasattr(psock, "default_value"):
                        try:
                            psock.default_value = power_val
                        except (TypeError, AttributeError):
                            pass
                        break

        log.info(
            "Inserted Octane Emission node between '%s' and '%s'",
            source_node.name, oct_mat.name,
        )


# ---------------------------------------------------------------------------
# Normal Map / Bump fallback — rewire [UNSUPPORTED] nodes
# ---------------------------------------------------------------------------

def _handle_normal_map_fallback(
    analysis: TreeAnalysis,
    node_map: dict[str, bpy.types.Node],
    target_tree: bpy.types.NodeTree,
) -> None:
    """Handle Normal Map and Bump nodes that couldn't be created as Octane nodes.

    When the Octane plugin doesn't have a matching Normal Map bl_idname,
    the graph engine creates a useless [UNSUPPORTED] RGB fallback.
    This handler detects those, finds the source texture and destination
    material from the original analysis links, creates a direct connection,
    and removes the fallback node.

    In Octane, connecting a normal map image texture directly to the
    material's Normal input is the standard workflow.
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

        log.info(
            "Handling [UNSUPPORTED] %s node '%s' — rewiring connections",
            info.bl_idname, node_name,
        )

        # Find source texture (what connects TO NormalMap/Bump input)
        source_oct = None
        for link_info in analysis.links:
            if link_info.to_node == node_name:
                # Find the Octane node for the source
                candidate = node_map.get(link_info.from_node)
                if candidate is not None:
                    source_oct = candidate
                    break

        # Find destination material (what NormalMap/Bump output connects TO)
        dest_oct = None
        dest_socket_name = None
        for link_info in analysis.links:
            if link_info.from_node == node_name:
                candidate = node_map.get(link_info.to_node)
                if candidate is not None:
                    dest_oct = candidate
                    dest_socket_name = link_info.to_socket
                    break

        if source_oct is not None and dest_oct is not None:
            # Get the first output of the source texture
            out_sock = source_oct.outputs[0] if source_oct.outputs else None

            # Find the Normal/Bump input on the destination material
            in_sock = None
            for name in ["Normal", "Bump", "ShaderNormal", dest_socket_name]:
                if name:
                    in_sock = dest_oct.inputs.get(name)
                    if in_sock is not None:
                        break

            # Fallback: case-insensitive search
            if in_sock is None and dest_socket_name:
                target_lower = dest_socket_name.lower()
                for inp in dest_oct.inputs:
                    if inp.name.lower() == target_lower:
                        in_sock = inp
                        break

            if out_sock is not None and in_sock is not None:
                try:
                    # Remove any existing link to the fallback node
                    for link in list(target_tree.links):
                        if (link.from_node == oct_node or
                                link.to_node == oct_node):
                            target_tree.links.remove(link)

                    # Create direct connection
                    target_tree.links.new(out_sock, in_sock)
                    log.info(
                        "Normal/Bump fallback: connected %s → %s.%s directly",
                        source_oct.name, dest_oct.name, in_sock.name,
                    )
                except Exception as exc:
                    log.warning("Normal/Bump fallback rewire failed: %s", exc)

        # Remove the useless fallback node
        try:
            # Remove all remaining links to/from the fallback node
            for link in list(target_tree.links):
                if link.from_node == oct_node or link.to_node == oct_node:
                    target_tree.links.remove(link)
            target_tree.nodes.remove(oct_node)
            del node_map[node_name]
            log.info("Removed [UNSUPPORTED] fallback node '%s'", node_name)
        except Exception:
            pass


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
        return bpy.data.node_groups.get(cached_name)

    log.info("Converting node group: %s", tree_name)
    analysis = analyze_tree(group_tree)

    new_tree_name = f"{tree_name}_OCTANE"
    if new_tree_name in bpy.data.node_groups:
        new_tree = bpy.data.node_groups[new_tree_name]
    else:
        new_tree = group_tree.copy()
        new_tree.name = new_tree_name

    # Clear all but I/O nodes
    to_remove = [n for n in new_tree.nodes if n.bl_idname not in ("NodeGroupInput", "NodeGroupOutput")]
    for n in to_remove:
        new_tree.nodes.remove(n)

    engine = GraphEngine(
        analysis, 
        group_converter_cb=lambda t: convert_node_group(t, gamma_value)
    )
    node_map = engine.create_nodes(new_tree)

    # Re-register I/O nodes for link mapping
    for n in new_tree.nodes:
        if n.bl_idname in ("NodeGroupInput", "NodeGroupOutput"):
            node_map[n.name] = n

    for node_name, oct_node in node_map.items():
        if oct_node.bl_idname in ("NodeGroupInput", "NodeGroupOutput"):
            continue
        info = analysis.nodes.get(node_name)
        if info is not None:
            try:
                transfer_properties(info, oct_node)
            except Exception as exc:
                log.warning("Property transfer failed for '%s' in group '%s': %s", node_name, tree_name, exc)

    _rebuild_links(analysis, node_map, new_tree)
    _handle_normal_map_fallback(analysis, node_map, new_tree)
    _fix_mix_shader_links(analysis, node_map, new_tree)
    _handle_alpha(analysis, node_map, new_tree)
    
    _preserve_drivers(group_tree, analysis, node_map, new_tree)
    
    _cache.register_material(cache_key, new_tree.name)
    return new_tree


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
# Node Group Conversion
# ---------------------------------------------------------------------------

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

    # Check cache
    if _cache.has_material(mat_name):
        cached_name = _cache.get_converted_material_name(mat_name)
        log.info("Material '%s' already converted as '%s', reusing", mat_name, cached_name)
        return bpy.data.materials.get(cached_name)

    log.info("Converting material: %s", mat_name)

    # 1. Analyse the original tree
    analysis = analyze_tree(mat.node_tree)

    # 2. Duplicate material
    new_mat = mat.copy()
    new_mat.name = f"{mat_name}_OCTANE"
    new_mat.use_nodes = True

    # 3. Clear the new tree (keeping output node)
    _clear_tree_except_output(new_mat.node_tree)

    # 4. Build schedule and create nodes
    engine = GraphEngine(
        analysis,
        group_converter_cb=lambda t: convert_node_group(t, gamma_value)
    )
    node_map = engine.create_nodes(new_mat.node_tree)

    # 5. Transfer properties
    for node_name, oct_node in node_map.items():
        info = analysis.nodes.get(node_name)
        if info is not None:
            try:
                transfer_properties(info, oct_node)
            except Exception as exc:
                log.warning("Property transfer failed for '%s': %s", node_name, exc)

    # 6. Rebuild links
    _rebuild_links(analysis, node_map, new_mat.node_tree)

    # 6b. Handle Normal Map / Bump fallbacks (rewire [UNSUPPORTED] nodes)
    _handle_normal_map_fallback(analysis, node_map, new_mat.node_tree)

    # 7. Post-process: swap MixShader links
    _fix_mix_shader_links(analysis, node_map, new_mat.node_tree)

    # 8. Handle alpha/opacity
    _handle_alpha(analysis, node_map, new_mat.node_tree)

    # 9. Handle emission (surface brightness + insert TextureEmission node)
    _handle_emission_post(analysis, node_map, new_mat.node_tree)
    _handle_emission_node_insertion(analysis, node_map, new_mat.node_tree)

    # 10. Handle volumetrics
    handle_volumetrics(analysis, node_map, new_mat.node_tree)

    # 11. Apply gamma correction to albedo textures
    apply_gamma(new_mat, gamma_value)

    # 12. Scale correction
    _apply_scale_correction(obj, node_map, analysis)
    
    # 13. Preserve drivers
    _preserve_drivers(mat.node_tree, analysis, node_map, new_mat.node_tree)

    # 14. Register in cache
    _cache.register_material(mat_name, new_mat.name)

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

    for obj in bpy.context.scene.objects:
        if not hasattr(obj, "material_slots"):
            continue
        results = convert_object_materials(obj, gamma_value=gamma_value)
        converted.extend(results)

    return converted
