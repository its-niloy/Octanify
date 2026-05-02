"""Octanify — Shader detection and tree analysis.

Walks a Cycles node tree and produces a structured analysis with:
- Node classification by bl_idname
- Default value snapshots
- Property snapshots (blend_type, operation, image, colorspace, etc.)
- Flattened link list (reroutes resolved, duplicates removed)
- Transparent node flattening (SeparateColor/RGB/XYZ pass through)
- Socket disambiguation via identifier for nodes with duplicate names
- Pattern detection: glass, emission, volume, alpha, bump/normal
"""

from __future__ import annotations

import bpy
from dataclasses import dataclass, field
from typing import Any

from ..utils.logger import get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NodeInfo:
    """Snapshot of a single Cycles node."""
    name: str
    bl_idname: str
    label: str
    location: tuple[float, float]
    # socket_name → default value
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    # special properties
    properties: dict[str, Any] = field(default_factory=dict)
    # socket identifier → socket name (for disambiguation)
    input_identifiers: dict[str, str] = field(default_factory=dict)
    output_identifiers: dict[str, str] = field(default_factory=dict)


@dataclass
class LinkInfo:
    """Snapshot of a single link (reroutes already resolved)."""
    from_node: str
    from_socket: str          # display name
    to_node: str
    to_socket: str            # display name
    from_socket_identifier: str = ""   # unique identifier
    to_socket_identifier: str = ""     # unique identifier
    to_socket_index: int = -1          # index in the node's inputs


@dataclass
class TreeAnalysis:
    """Complete analysis of a Cycles node tree."""
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    links: list[LinkInfo] = field(default_factory=list)
    # Pattern flags
    has_glass: bool = False
    has_emission: bool = False
    has_volume: bool = False
    has_alpha: bool = False
    has_bump: bool = False
    has_normal_map: bool = False
    has_sss: bool = False
    has_displacement: bool = False
    # Transmission weight for glass detection
    transmission_weight: float = 0.0


# ---------------------------------------------------------------------------
# Node types to flatten / skip during analysis
# ---------------------------------------------------------------------------

# These node types are "transparent" — links pass through them.
# During link analysis, when a link's from_node is one of these,
# we trace backward to find the real source (like reroute flattening).
#
# IMPORTANT: Only nodes that have NO Octane equivalent belong here.
# Nodes like Math, Clamp, Invert, HueSat, BrightContrast, Gamma,
# and RGBCurves have valid Octane mappings and MUST be analyzed normally.
_TRANSPARENT_TYPES: set[str] = {
    # Channel split / combine nodes — no direct Octane equivalent
    "ShaderNodeSeparateColor",
    "ShaderNodeSeparateRGB",
    "ShaderNodeSeparateXYZ",
    "ShaderNodeCombineColor",
    "ShaderNodeCombineRGB",
    "ShaderNodeCombineXYZ",
    # Info nodes with no Octane equivalent
    "ShaderNodeNewGeometry",
    "ShaderNodeLightPath",
}


# ---------------------------------------------------------------------------
# Reroute / transparent node flattening
# ---------------------------------------------------------------------------

def _trace_reroute_output(node: bpy.types.Node) -> tuple[bpy.types.Node, bpy.types.NodeSocket]:
    """Follow reroute chain backwards to find the real source node and socket."""
    visited: set[str] = set()
    current = node
    while current.bl_idname == "NodeReroute" and current.name not in visited:
        visited.add(current.name)
        if current.inputs and current.inputs[0].links:
            link = current.inputs[0].links[0]
            current = link.from_node
            if current.bl_idname != "NodeReroute":
                return current, link.from_socket
        else:
            break
    if current.bl_idname == "NodeReroute":
        return current, current.outputs[0]
    return current, current.outputs[0] if current.outputs else None


def _trace_reroute_input(node: bpy.types.Node) -> tuple[bpy.types.Node, bpy.types.NodeSocket]:
    """Follow reroute chain forward to find the real destination node and socket."""
    visited: set[str] = set()
    current = node
    while current.bl_idname == "NodeReroute" and current.name not in visited:
        visited.add(current.name)
        if current.outputs and current.outputs[0].links:
            link = current.outputs[0].links[0]
            current = link.to_node
            if current.bl_idname != "NodeReroute":
                return current, link.to_socket
        else:
            break
    if current.bl_idname == "NodeReroute":
        return current, current.inputs[0]
    return current, current.inputs[0] if current.inputs else None


def _trace_transparent_source(
    node: bpy.types.Node,
) -> tuple[bpy.types.Node, bpy.types.NodeSocket] | None:
    """Trace backward through a transparent node to find the real source.

    For SeparateColor/RGB/XYZ: follow the single Color/Vector input backward.
    For CombineColor/RGB/XYZ: follow the first connected input backward.
    """
    if not node.inputs:
        return None

    # Find the first connected input
    for inp in node.inputs:
        if inp.links:
            link = inp.links[0]
            source_node = link.from_node
            source_socket = link.from_socket

            # Recursively handle chained transparents
            if source_node.bl_idname == "NodeReroute":
                return _trace_reroute_output(source_node)
            if source_node.bl_idname in _TRANSPARENT_TYPES:
                return _trace_transparent_source(source_node)
            return source_node, source_socket

    return None


# ---------------------------------------------------------------------------
# Socket index helper
# ---------------------------------------------------------------------------

def _get_socket_index(socket: bpy.types.NodeSocket, collection) -> int:
    """Return the index of a socket within its node's input/output collection."""
    for i, s in enumerate(collection):
        if s == socket:
            return i
    return -1


# ---------------------------------------------------------------------------
# Property snapshot helpers
# ---------------------------------------------------------------------------

_PROPERTY_KEYS: dict[str, list[str]] = {
    "ShaderNodeMixRGB": ["blend_type", "use_clamp"],
    "ShaderNodeMix": ["blend_type", "data_type", "clamp_factor", "clamp_result", "factor_mode"],
    "ShaderNodeMath": ["operation", "use_clamp"],
    "ShaderNodeMapping": ["vector_type"],
    "ShaderNodeNormalMap": ["space", "uv_map"],
    "ShaderNodeBump": ["invert"],
    "ShaderNodeTexImage": ["interpolation", "projection", "extension"],
    "ShaderNodeTexNoise": ["noise_dimensions"],
    "ShaderNodeTexVoronoi": ["voronoi_dimensions", "feature", "distance"],
    "ShaderNodeTexWave": ["wave_type", "wave_profile", "bands_direction", "rings_direction"],
    "ShaderNodeTexMusgrave": ["musgrave_dimensions", "musgrave_type"],
    "ShaderNodeTexGradient": ["gradient_type"],
    "ShaderNodeMapRange": ["data_type", "interpolation_type", "clamp"],
    "ShaderNodeClamp": ["clamp_type"],
    "ShaderNodeValToRGB": [],  # color_ramp handled specially
    "ShaderNodeAttribute": ["attribute_name", "attribute_type"],
    "ShaderNodeUVMap": ["uv_map"],
    "ShaderNodeVertexColor": ["layer_name"],
    "ShaderNodeTexEnvironment": ["interpolation", "projection", "color_space"],
    "ShaderNodeTexMagic": ["turbulence_depth"],
    "ShaderNodeTexSky": ["sky_type", "sun_direction", "turbidity", "ground_albedo", "sun_dust", "air_density", "dust_density", "ozone_density"],
    "ShaderNodeTexWhiteNoise": ["noise_dimensions"],
    "ShaderNodeTexGabor": ["gabor_type"],
    "ShaderNodeVectorMath": ["operation"],
    "ShaderNodeGroup": ["node_tree"],
}


def _snapshot_properties(node: bpy.types.Node, info: NodeInfo) -> None:
    """Capture important Cycles node properties into the info dict."""
    keys = _PROPERTY_KEYS.get(node.bl_idname, [])
    for key in keys:
        val = getattr(node, key, None)
        if val is not None:
            info.properties[key] = val

    # Image texture special handling
    if node.bl_idname == "ShaderNodeTexImage":
        img = getattr(node, "image", None)
        if img is not None:
            info.properties["image"] = img
            info.properties["image_name"] = img.name
            info.properties["filepath"] = img.filepath
            info.properties["colorspace"] = img.colorspace_settings.name

    # ColorRamp special handling
    if node.bl_idname == "ShaderNodeValToRGB":
        cr = node.color_ramp
        info.properties["interpolation"] = cr.interpolation
        info.properties["color_mode"] = cr.color_mode
        stops = []
        for elem in cr.elements:
            stops.append({
                "position": elem.position,
                "color": tuple(elem.color),
            })
        info.properties["stops"] = stops

    # NodeGroup special handling
    if node.bl_idname == "ShaderNodeGroup" and getattr(node, "node_tree", None):
        info.properties["node_tree_name"] = node.node_tree.name


# ---------------------------------------------------------------------------
# Socket default value snapshot
# ---------------------------------------------------------------------------

def _snapshot_default(socket: bpy.types.NodeSocket) -> Any:
    """Return the default value of a socket in a serialisable form."""
    if not hasattr(socket, "default_value"):
        return None
    val = socket.default_value
    if val is None:
        return None
    # Color / Vector → tuple
    if hasattr(val, "__len__"):
        return tuple(val)
    return val


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyze_tree(node_tree: bpy.types.NodeTree) -> TreeAnalysis:
    """Walk a Cycles node tree and return a structured TreeAnalysis."""
    analysis = TreeAnalysis()

    # ── Snapshot nodes ────────────────────────────────────────────────────
    for node in node_tree.nodes:
        if node.bl_idname in ("NodeFrame",):
            continue
        if node.bl_idname == "NodeReroute":
            continue  # handled at link level
        if node.bl_idname in _TRANSPARENT_TYPES:
            continue  # flattened at link level

        info = NodeInfo(
            name=node.name,
            bl_idname=node.bl_idname,
            label=node.label or node.name,
            location=(node.location.x, node.location.y),
        )

        # Inputs — use identifier as key to avoid collisions
        for inp in node.inputs:
            identifier = getattr(inp, "identifier", inp.name)
            info.inputs[identifier] = _snapshot_default(inp)
            info.input_identifiers[identifier] = inp.name

        # Outputs — use identifier as key
        for out in node.outputs:
            identifier = getattr(out, "identifier", out.name)
            info.outputs[identifier] = _snapshot_default(out)
            info.output_identifiers[identifier] = out.name

        # Properties
        _snapshot_properties(node, info)

        analysis.nodes[node.name] = info

        # ── Pattern detection ────────────────────────────────────────────
        bid = node.bl_idname

        if bid == "ShaderNodeBsdfGlass":
            analysis.has_glass = True

        if bid == "ShaderNodeBsdfPrincipled":
            tw_input = node.inputs.get("Transmission Weight")
            if tw_input is None:
                tw_input = node.inputs.get("Transmission")
            if tw_input is not None:
                tw = getattr(tw_input, "default_value", 0.0)
                if tw > 0.5:
                    analysis.has_glass = True
                    analysis.transmission_weight = tw

            em_input = node.inputs.get("Emission Color")
            em_str = node.inputs.get("Emission Strength")
            if em_input is not None:
                em_col = getattr(em_input, "default_value", None)
                if em_col is not None:
                    if any(c > 0.0 for c in tuple(em_col)[:3]):
                        analysis.has_emission = True

            ss_input = node.inputs.get("Subsurface Weight")
            if ss_input is None:
                ss_input = node.inputs.get("Subsurface")
            if ss_input is not None:
                if getattr(ss_input, "default_value", 0.0) > 0.0:
                    analysis.has_sss = True

        if bid == "ShaderNodeEmission":
            analysis.has_emission = True

        if bid == "ShaderNodeBlackbody":
            analysis.has_emission = True

        if bid in ("ShaderNodeVolumeAbsorption", "ShaderNodeVolumeScatter", "ShaderNodeVolumePrincipled"):
            analysis.has_volume = True

        if bid == "ShaderNodeBump":
            analysis.has_bump = True

        if bid == "ShaderNodeNormalMap":
            analysis.has_normal_map = True

        if bid in ("ShaderNodeDisplacement", "ShaderNodeVectorDisplacement"):
            analysis.has_displacement = True

        if bid == "ShaderNodeSubsurfaceScattering":
            analysis.has_sss = True

        if bid in ("ShaderNodeBsdfMetallic", "ShaderNodeBsdfSheen", "ShaderNodeBsdfToon", "ShaderNodeBsdfHair", "ShaderNodeBsdfHairPrincipled"):
            pass  # Future proofing for specialized flags if needed

    # ── Snapshot links (flatten reroutes + transparents, deduplicate) ─────
    seen_links: set[tuple[str, str, str, str]] = set()

    for link in node_tree.links:
        from_node = link.from_node
        to_node = link.to_node
        from_socket = link.from_socket
        to_socket = link.to_socket

        # ── Flatten transparent source nodes (SeparateColor etc.) ─────
        if from_node.bl_idname in _TRANSPARENT_TYPES:
            result = _trace_transparent_source(from_node)
            if result is not None:
                from_node, from_socket = result
            else:
                continue  # Dead end — no upstream connection

        # ── Skip links TO transparent nodes (handled by FROM links) ───
        if to_node.bl_idname in _TRANSPARENT_TYPES:
            continue

        # ── Resolve reroutes on the source side ──────────────────────
        if from_node.bl_idname == "NodeReroute":
            from_node, from_socket = _trace_reroute_output(from_node)
            if from_socket is None:
                continue

        # ── Resolve reroutes on the destination side ─────────────────
        if to_node.bl_idname == "NodeReroute":
            to_node, to_socket = _trace_reroute_input(to_node)
            if to_socket is None:
                continue

        # Skip if still reroute or transparent (broken chain)
        if from_node.bl_idname in {"NodeReroute"} | _TRANSPARENT_TYPES:
            continue
        if to_node.bl_idname in {"NodeReroute"} | _TRANSPARENT_TYPES:
            continue

        # Use identifier for unique disambiguation
        from_sock_id = getattr(from_socket, "identifier", from_socket.name)
        to_sock_id = getattr(to_socket, "identifier", to_socket.name)

        # Get socket index on the destination node
        to_sock_index = _get_socket_index(to_socket, to_node.inputs)

        # Deduplicate flattened links
        link_key = (from_node.name, from_sock_id, to_node.name, to_sock_id)
        if link_key in seen_links:
            continue
        seen_links.add(link_key)

        analysis.links.append(LinkInfo(
            from_node=from_node.name,
            from_socket=from_socket.name,
            to_node=to_node.name,
            to_socket=to_socket.name,
            from_socket_identifier=from_sock_id,
            to_socket_identifier=to_sock_id,
            to_socket_index=to_sock_index,
        ))

        # Alpha detection: Image Texture Alpha output used
        if (from_node.bl_idname == "ShaderNodeTexImage"
                and from_socket.name == "Alpha"):
            analysis.has_alpha = True

    return analysis
