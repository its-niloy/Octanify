"""Octanify — Property mapper.

Per-node-type transfer functions that copy values, properties, and
configuration from the original Cycles node info snapshot to the
newly created Octane node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .node_registry import INPUT_MAP, MATH_OPERATION_MAP
from ..utils.logger import get_logger

if TYPE_CHECKING:
    import bpy
    from .shader_detection import NodeInfo

log = get_logger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_input(node: "bpy.types.Node", candidates: list[str], value: Any) -> bool:
    """Try to set a default_value on the first matching input socket."""
    for name in candidates:
        inp = node.inputs.get(name)
        if inp is not None and hasattr(inp, "default_value"):
            try:
                inp.default_value = value
                return True
            except (TypeError, AttributeError):
                continue
    return False


def _set_prop(node: "bpy.types.Node", attr: str, value: Any) -> bool:
    """Try to set an attribute on a node."""
    try:
        setattr(node, attr, value)
        return True
    except (AttributeError, TypeError):
        return False


def _get_candidates(cycles_type: str, socket_name: str) -> list[str]:
    """Look up Octane input candidates for a given Cycles socket."""
    return INPUT_MAP.get(cycles_type, {}).get(socket_name, [socket_name])


def _get_input_value(info: "NodeInfo", name: str, default: Any = None) -> Any:
    """Look up an input value from NodeInfo by display name or identifier.

    Since NodeInfo.inputs is now keyed by socket identifier (which may
    differ from the display name), this helper searches both:
      1. Direct key match (identifier == name)
      2. Reverse lookup via input_identifiers (display_name == name)
    """
    # Direct match by identifier
    val = info.inputs.get(name)
    if val is not None:
        return val

    # Reverse lookup: find identifier whose display name matches
    for identifier, display_name in info.input_identifiers.items():
        if display_name == name:
            val = info.inputs.get(identifier)
            if val is not None:
                return val

    return default


def _get_output_value(info: "NodeInfo", name: str, default: Any = None) -> Any:
    """Look up an output value from NodeInfo by display name or identifier."""
    val = info.outputs.get(name)
    if val is not None:
        return val

    for identifier, display_name in info.output_identifiers.items():
        if display_name == name:
            val = info.outputs.get(identifier)
            if val is not None:
                return val

    return default


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def transfer_properties(
    info: "NodeInfo",
    octane_node: "bpy.types.Node",
) -> None:
    """Dispatch to the correct per-type transfer function."""
    bid = info.bl_idname
    handler = _HANDLERS.get(bid)
    if handler is not None:
        handler(info, octane_node)
    else:
        # Generic fallback: try to copy matching input default values
        _transfer_generic(info, octane_node)


# ---------------------------------------------------------------------------
# Per-type handlers
# ---------------------------------------------------------------------------

def _transfer_principled(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Principled BSDF → Universal Material."""
    bid = "ShaderNodeBsdfPrincipled"

    # Simple float / color inputs
    _simple_transfers = [
        ("Base Color",           "Albedo color", "Albedo", "Diffuse", "Base color"),
        ("Metallic",             "Metallic", "Metallic float", "Metalness"),
        ("Roughness",            "Roughness", "Roughness float", "Specular roughness"),
        ("Diffuse Roughness",    "Roughness", "Roughness float", "Diffuse roughness"),
        ("Specular IOR Level",   "Specular", "Specular float"),
        ("Specular Tint",        "Specular tint", "Specular map", "Specular color"),
        ("IOR",                  "Dielectric IOR", "Index", "IOR", "Specular IOR"),
        ("Alpha",                "Opacity", "Opacity float"),
        ("Tangent",              "Anisotropy rotation", "Rotation"),
        ("Coat Weight",          "Coating", "Coating float"),
        ("Coat Roughness",       "Coating roughness", "Coating roughness float"),
        ("Coat IOR",             "Coating IOR"),
        ("Coat Tint",            "Coating", "Coating color"),
        ("Sheen Weight",         "Sheen", "Sheen float"),
        ("Sheen Roughness",      "Sheen roughness", "Sheen roughness float"),
        ("Sheen Tint",           "Sheen", "Sheen color", "Sheen tint"),
        ("Anisotropic",          "Anisotropy", "Anisotropy float"),
        ("Anisotropic Rotation", "Anisotropy rotation", "Rotation"),
        ("Thin Film Thickness",  "Film width", "Thin film thickness"),
        ("Thin Film IOR",        "Film IOR", "Thin film IOR"),
        ("Subsurface Weight",    "SSS", "Subsurface"),
        ("Subsurface IOR",       "Index", "IOR"),
        ("Subsurface Anisotropy", "Anisotropy", "Subsurface anisotropy"),
    ]

    for mapping in _simple_transfers:
        cycles_name = mapping[0]
        oct_candidates = list(mapping[1:])
        value = _get_input_value(info, cycles_name)
        if value is not None:
            _set_input(node, oct_candidates, value)

    # Emission — even if strength is 0, preserve if color is set
    em_color = _get_input_value(info, "Emission Color")
    em_strength = _get_input_value(info, "Emission Strength")
    if em_color is not None:
        _set_input(node, ["Emission", "Emission color"], em_color)
    if em_strength is not None:
        _set_input(node, ["Emission power", "Emission weight"], em_strength)
        # Enable surface brightness if emission detected
        if isinstance(em_strength, (int, float)) and em_strength > 0.0:
            _set_prop(node, "surface_brightness", True)
        elif em_color is not None:
            cols = tuple(em_color)[:3] if hasattr(em_color, "__len__") else (0,)
            if any(c > 0.0 for c in cols):
                _set_prop(node, "surface_brightness", True)

    # Transmission → glass treatment if > 0.5
    tw = _get_input_value(info, "Transmission Weight")
    if tw is None:
        tw = _get_input_value(info, "Transmission")
    if tw is not None and isinstance(tw, (int, float)):
        _set_input(node, ["Transmission", "Transmission float"], tw)
        if tw > 0.5:
            # Set albedo to black, move base color to transmission
            _set_input(node, ["Albedo color", "Albedo", "Diffuse"], (0.0, 0.0, 0.0, 1.0))
            bc = _get_input_value(info, "Base Color")
            if bc is not None:
                _set_input(node, ["Transmission color", "Transmission"], bc)
            # Enable fake shadows
            _set_prop(node, "fake_shadows", True)

    # Subsurface radius / scale
    ss_rad = _get_input_value(info, "Subsurface Radius")
    if ss_rad is not None:
        _set_input(node, ["Absorption", "Medium radius"], ss_rad)
    ss_scale = _get_input_value(info, "Subsurface Scale")
    if ss_scale is not None:
        _set_input(node, ["Density", "Medium scale"], ss_scale)


def _transfer_glass(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Glass BSDF → Specular Material."""
    _set_input(node, ["Reflection", "Specular", "Albedo color"],
               _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0)))
    _set_input(node, ["Roughness", "Roughness float"],
               _get_input_value(info, "Roughness", 0.0))
    _set_input(node, ["Index", "IOR", "Dielectric IOR"],
               _get_input_value(info, "IOR", 1.45))
    # Enable fake shadows and GGX
    _set_prop(node, "fake_shadows", True)


def _transfer_glossy(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Glossy BSDF → Glossy Material."""
    _set_input(node, ["Specular", "Reflection", "Albedo color"],
               _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0)))
    _set_input(node, ["Roughness", "Roughness float"],
               _get_input_value(info, "Roughness", 0.5))


def _transfer_diffuse(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Diffuse BSDF → Diffuse Material."""
    _set_input(node, ["Diffuse", "Albedo color", "Albedo"],
               _get_input_value(info, "Color", (0.8, 0.8, 0.8, 1.0)))
    _set_input(node, ["Roughness", "Roughness float"],
               _get_input_value(info, "Roughness", 0.0))


def _transfer_emission(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Emission → Diffuse Material with emission enabled."""
    color = _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0))
    strength = _get_input_value(info, "Strength", 1.0)
    _set_input(node, ["Diffuse", "Albedo color", "Albedo"], (0.0, 0.0, 0.0, 1.0))
    _set_input(node, ["Emission", "Emission color"], color)
    _set_input(node, ["Emission power", "Power"], strength)
    _set_prop(node, "surface_brightness", True)


def _transfer_translucent(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Translucent BSDF → Diffuse Material (transmission channel)."""
    _set_input(node, ["Diffuse", "Albedo color", "Albedo"],
               _get_input_value(info, "Color", (0.8, 0.8, 0.8, 1.0)))


def _transfer_refraction(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Refraction BSDF → Specular Material."""
    _set_input(node, ["Reflection", "Specular", "Albedo color"],
               _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0)))
    _set_input(node, ["Roughness", "Roughness float"],
               _get_input_value(info, "Roughness", 0.0))
    _set_input(node, ["Index", "IOR", "Dielectric IOR"],
               _get_input_value(info, "IOR", 1.45))
    _set_prop(node, "fake_shadows", True)


def _transfer_mix_shader(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Mix Shader → Mix Material. Octane swaps shader order."""
    fac = _get_input_value(info, "Fac")
    if fac is not None:
        _set_input(node, ["Amount", "Factor"], fac)


def _transfer_add_shader(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Add Shader → Mix Material with 0.5 factor."""
    _set_input(node, ["Amount", "Factor"], 0.5)


def _transfer_image_texture(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Image Texture → Octane Image Texture."""
    img = info.properties.get("image")
    if img is not None:
        _set_prop(node, "image", img)

    # Colorspace → gamma
    cs = info.properties.get("colorspace", "sRGB")
    
    # Comprehensive linear space detection matching Blender's internal colormanagement
    linear_spaces = {
        "Non-Color", "Linear", "Raw", "Utility - Linear - sRGB", 
        "Utility - Raw", "Linear ACES", "Utility - Linear - Rec.709"
    }
    is_linear = cs in linear_spaces or cs.lower().startswith("linear")
    
    if is_linear:
        # Set gamma to 1.0 (linear)
        _set_input(node, ["Gamma", "Power", "Legacy gamma"], 1.0)
        _set_prop(node, "gamma", 1.0)
    else:
        # sRGB → gamma 2.2
        _set_input(node, ["Gamma", "Power", "Legacy gamma"], 2.2)
        _set_prop(node, "gamma", 2.2)

    # Projection
    proj = info.properties.get("projection", "FLAT")
    proj_map = {
        "FLAT": "XY",
        "BOX": "BOX",
        "SPHERE": "SPHERICAL",
        "TUBE": "CYLINDRICAL",
    }
    oct_proj = proj_map.get(proj, "XY")
    _set_prop(node, "projection", oct_proj)


def _transfer_normal_map(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Normal Map → Octane Normal Map Texture."""
    strength = _get_input_value(info, "Strength", 1.0)
    _set_input(node, ["Strength", "Bump strength"], strength)


def _transfer_bump(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Bump → Octane Bump Texture."""
    strength = _get_input_value(info, "Strength", 1.0)
    # Cycles bump scale needs to be multiplied for Octane
    _set_input(node, ["Strength", "Height"], strength)
    _set_input(node, ["Mid Level", "Mid level"], _get_input_value(info, "Distance", 0.5))
    invert = info.properties.get("invert", False)
    _set_prop(node, "invert", invert)


def _transfer_displacement(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Displacement → Octane Displacement."""
    import bpy
    _set_input(node, ["Amount", "Height"], _get_input_value(info, "Scale", 1.0))
    midlevel = _get_input_value(info, "Midlevel", 0.5)
    _set_input(node, ["Mid Level", "Mid level"], midlevel)

    try:
        scene = bpy.context.scene
        disp_mode = getattr(scene, "octanify_disp_mode", "TEXTURE")
        if disp_mode == "TEXTURE":
            lod_value = int(getattr(scene, "octanify_disp_level_of_detail", "3"))
            # Try as node property first (Octane uses this as an enum attribute)
            if not _set_prop(node, "level_of_detail", lod_value):
                _set_input(node, ["Level of detail"], float(lod_value))
        
        pref_mid = getattr(scene, "octanify_disp_mid_level", 0.5)
        if pref_mid != 0.5:
            _set_input(node, ["Mid Level", "Mid level"], pref_mid)
    except Exception:
        pass


def _transfer_mapping(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Mapping → Octane 3D Transform."""
    loc = _get_input_value(info, "Location", (0.0, 0.0, 0.0))
    rot = _get_input_value(info, "Rotation", (0.0, 0.0, 0.0))
    scl = _get_input_value(info, "Scale", (1.0, 1.0, 1.0))

    _set_input(node, ["Translation", "Position", "Location"], loc)
    _set_input(node, ["Rotation", "Rotation"], rot)
    _set_input(node, ["Scale", "Scaling"], scl)


def _transfer_mix_rgb(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """MixRGB → Octane Mix Texture (or specialised variant)."""
    fac = _get_input_value(info, "Fac")
    if fac is not None:
        _set_input(node, ["Amount", "Factor"], fac)

    c1 = _get_input_value(info, "Color1")
    if c1 is not None:
        _set_input(node, ["Texture1", "Color1", "Input1"], c1)

    c2 = _get_input_value(info, "Color2")
    if c2 is not None:
        _set_input(node, ["Texture2", "Color2", "Input2"], c2)

    # Use clamp
    use_clamp = info.properties.get("use_clamp", False)
    _set_prop(node, "use_clamp", use_clamp)
    
    # Blend Type — try multiple attribute names for Wrapper node compatibility
    blend_type = info.properties.get("blend_type", "MIX")
    if not _set_prop(node, "blend_type", blend_type):
        if not _set_prop(node, "blendType", blend_type):
            _set_prop(node, "operation", blend_type)


def _transfer_mix(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Blender 4+ Mix node → Octane Mix Texture."""
    fac = _get_input_value(info, "Factor")
    if fac is None:
        fac = _get_input_value(info, "Fac")
    if fac is not None:
        _set_input(node, ["Amount", "Factor"], fac)

    a = _get_input_value(info, "A")
    if a is not None:
        _set_input(node, ["Texture1", "Color1", "Input1"], a)

    b = _get_input_value(info, "B")
    if b is not None:
        _set_input(node, ["Texture2", "Color2", "Input2"], b)

    # Blend Type — try multiple attribute names for Wrapper node compatibility
    blend_type = info.properties.get("blend_type", "MIX")
    if not _set_prop(node, "blend_type", blend_type):
        if not _set_prop(node, "blendType", blend_type):
            _set_prop(node, "operation", blend_type)
    
    data_type = info.properties.get("data_type", "FLOAT")
    _set_prop(node, "data_type", data_type)
    
    clamp_result = info.properties.get("clamp_result", False)
    _set_prop(node, "clamp_result", clamp_result)
    
    clamp_factor = info.properties.get("clamp_factor", False)
    _set_prop(node, "clamp_factor", clamp_factor)


def _transfer_invert(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Invert → Octane Invert Texture."""
    fac = _get_input_value(info, "Fac", 1.0)
    _set_input(node, ["Amount", "Factor"], fac)
    color = _get_input_value(info, "Color")
    if color is not None:
        _set_input(node, ["Texture", "Input"], color)


def _transfer_hue_sat(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Hue/Saturation/Value → Octane Color Correction."""
    _set_input(node, ["Hue", "HueShift"], _get_input_value(info, "Hue", 0.5))
    _set_input(node, ["Saturation"], _get_input_value(info, "Saturation", 1.0))
    _set_input(node, ["Brightness", "Value"], _get_input_value(info, "Value", 1.0))


def _transfer_bright_contrast(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Brightness/Contrast → Octane Color Correction."""
    _set_input(node, ["Brightness"], _get_input_value(info, "Bright", 0.0))
    _set_input(node, ["Contrast"], _get_input_value(info, "Contrast", 0.0))


def _transfer_gamma_node(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Gamma → Octane Gamma Correction."""
    _set_input(node, ["Gamma", "Power"], _get_input_value(info, "Gamma", 1.0))


def _transfer_math(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Math → Octane Float Math."""
    op = info.properties.get("operation", "ADD")
    oct_op = MATH_OPERATION_MAP.get(op, "ADD")
    _set_prop(node, "operation", oct_op)

    v1 = _get_input_value(info, "Value")
    if v1 is not None:
        _set_input(node, ["Input1", "Value", "A"], v1)

    # Math node has two Value sockets; the second one stored as Value_001
    v2 = _get_input_value(info, "Value_001")
    if v2 is None:
        # Try by index
        vals = [v for k, v in info.inputs.items() if k.startswith("Value")]
        if len(vals) > 1:
            v2 = vals[1]
    if v2 is not None:
        _set_input(node, ["Input2", "Value2", "B"], v2)

    clamp = info.properties.get("use_clamp", False)
    _set_prop(node, "use_clamp", clamp)


def _transfer_map_range(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Map Range → Octane Range."""
    _set_input(node, ["Input", "Value"], _get_input_value(info, "Value", 0.0))
    _set_input(node, ["Input min", "FromMin"], _get_input_value(info, "From Min", 0.0))
    _set_input(node, ["Input max", "FromMax"], _get_input_value(info, "From Max", 1.0))
    _set_input(node, ["Output min", "ToMin"], _get_input_value(info, "To Min", 0.0))
    _set_input(node, ["Output max", "ToMax"], _get_input_value(info, "To Max", 1.0))


def _transfer_clamp(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Clamp → Octane Clamp."""
    _set_input(node, ["Input", "Value"], _get_input_value(info, "Value", 0.0))
    _set_input(node, ["Minimum", "Min"], _get_input_value(info, "Min", 0.0))
    _set_input(node, ["Maximum", "Max"], _get_input_value(info, "Max", 1.0))


def _transfer_rgb(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """RGB constant → Octane RGB Color."""
    color = _get_output_value(info, "Color")
    if color is not None:
        _set_input(node, ["Color", "Input"], color)
        _set_prop(node, "default_value", color)


def _transfer_value(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Value constant → Octane Float."""
    val = _get_output_value(info, "Value")
    if val is not None:
        _set_input(node, ["Value", "Input"], val)
        _set_prop(node, "default_value", val)


def _transfer_fresnel(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Fresnel / Layer Weight → Octane Fresnel."""
    ior = _get_input_value(info, "IOR")
    if ior is None:
        ior = _get_input_value(info, "Blend", 0.5)
    if ior is not None:
        _set_input(node, ["IOR", "Index", "Power"], ior)


def _transfer_vertex_color(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Vertex Color → Octane Color Vertex Attribute."""
    layer = info.properties.get("layer_name", "")
    _set_prop(node, "attribute", layer)
    _set_prop(node, "layer_name", layer)
    _set_prop(node, "name", layer)


def _transfer_attribute(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Attribute → Octane Attribute."""
    attr_name = info.properties.get("attribute_name", "")
    _set_prop(node, "attribute", attr_name)
    _set_prop(node, "name", attr_name)


def _transfer_ambient_occlusion(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Ambient Occlusion → Octane Dirt Texture."""
    _set_input(node, ["Radius", "Distance"],
               _get_input_value(info, "Distance", 1.0))
    _set_input(node, ["Inclination color", "Bright color", "Color"],
               _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0)))


def _transfer_noise(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Noise Texture → Octane Noise Texture."""
    _set_input(node, ["Omega", "W", "Scale"], _get_input_value(info, "Scale", 5.0))
    _set_input(node, ["Octaves", "Detail"], _get_input_value(info, "Detail", 2.0))
    _set_input(node, ["Lacunarity", "Roughness"], _get_input_value(info, "Roughness", 0.5))


def _transfer_voronoi(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Voronoi Texture → Octane Voronoi Texture."""
    _set_input(node, ["Scale"], _get_input_value(info, "Scale", 5.0))


def _transfer_wave(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Wave Texture → Octane Wave Texture."""
    _set_input(node, ["Scale"], _get_input_value(info, "Scale", 5.0))


def _transfer_checker(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Checker Texture → Octane Checks Texture."""
    _set_input(node, ["Color1", "Checks color 1"],
               _get_input_value(info, "Color1", (0.8, 0.8, 0.8, 1.0)))
    _set_input(node, ["Color2", "Checks color 2"],
               _get_input_value(info, "Color2", (0.2, 0.2, 0.2, 1.0)))
    _set_input(node, ["Scale"], _get_input_value(info, "Scale", 5.0))


def _transfer_uv_map(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """UV Map → Octane Mesh UV Projection."""
    uv_name = info.properties.get("uv_map", "")
    _set_prop(node, "uv_map", uv_name)
    _set_prop(node, "name", uv_name)


def _transfer_color_ramp(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """ColorRamp → Octane Gradient Texture.

    Octane gradient textures accept start/end color; for complex ramps
    we transfer the first and last stop colors.
    """
    stops = info.properties.get("stops", [])
    if stops:
        first_color = stops[0].get("color", (0.0, 0.0, 0.0, 1.0))
        last_color = stops[-1].get("color", (1.0, 1.0, 1.0, 1.0))
        _set_input(node, ["Start color", "Color1", "Start value"], first_color)
        _set_input(node, ["End color", "Color2", "End value"], last_color)


def _transfer_volume_absorption(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Volume Absorption → Octane Absorption Medium."""
    _set_input(node, ["Absorption", "Color"],
               _get_input_value(info, "Color", (0.8, 0.8, 0.8, 1.0)))
    _set_input(node, ["Density", "Density float"],
               _get_input_value(info, "Density", 1.0))


def _transfer_volume_scatter(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Volume Scatter → Octane Scattering Medium."""
    _set_input(node, ["Scattering", "Color"],
               _get_input_value(info, "Color", (0.8, 0.8, 0.8, 1.0)))
    _set_input(node, ["Density", "Density float"],
               _get_input_value(info, "Density", 1.0))
    _set_input(node, ["Phase", "Anisotropy"],
               _get_input_value(info, "Anisotropy", 0.0))


# ---------------------------------------------------------------------------
# New Node Handlers
# ---------------------------------------------------------------------------

def _transfer_metallic(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Albedo color", "Albedo", "Diffuse"], _get_input_value(info, "Base Color", (1,1,1,1)))
    _set_input(node, ["Specular", "Specular color", "Specular map"], _get_input_value(info, "Edge Tint", (1,1,1,1)))
    _set_input(node, ["Roughness", "Roughness float"], _get_input_value(info, "Roughness", 0.0))
    _set_input(node, ["Anisotropy", "Anisotropy float"], _get_input_value(info, "Anisotropy", 0.0))
    _set_input(node, ["Anisotropy rotation", "Rotation"], _get_input_value(info, "Rotation", 0.0))


def _transfer_sheen(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Albedo color", "Albedo", "Diffuse"], _get_input_value(info, "Color", (1,1,1,1)))
    _set_input(node, ["Roughness", "Roughness float"], _get_input_value(info, "Roughness", 0.0))


def _transfer_toon(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Albedo color", "Albedo", "Diffuse"], _get_input_value(info, "Color", (1,1,1,1)))
    _set_input(node, ["Roughness", "Roughness float"], _get_input_value(info, "Size", 0.5))


def _transfer_sss_standalone(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Albedo color", "Albedo", "Diffuse", "Absorption"], _get_input_value(info, "Color", (0.8,0.8,0.8,1.0)))
    _set_input(node, ["Density", "Medium scale"], _get_input_value(info, "Scale", 1.0))
    _set_input(node, ["Absorption", "Medium radius"], _get_input_value(info, "Radius", (1,1,1)))
    _set_input(node, ["Index", "IOR"], _get_input_value(info, "IOR", 1.4))
    _set_input(node, ["Roughness", "Roughness float"], _get_input_value(info, "Roughness", 0.0))
    _set_input(node, ["Anisotropy", "Anisotropy float"], _get_input_value(info, "Anisotropy", 0.0))


def _transfer_environment(info: "NodeInfo", node: "bpy.types.Node") -> None:
    img = info.properties.get("image")
    if img is not None:
        _set_prop(node, "image", img)
    proj = info.properties.get("projection", "EQUIRECTANGULAR")
    if proj == "MIRROR_BALL":
        _set_prop(node, "projection", "SPHERICAL")


def _transfer_magic_texture(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Scale"], _get_input_value(info, "Scale", 5.0))
    _set_input(node, ["Distortion"], _get_input_value(info, "Distortion", 1.0))
    depth = info.properties.get("turbulence_depth", 2)
    _set_input(node, ["Detail"], depth)


def _transfer_sky_texture(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Sun direction"], _get_input_value(info, "Sun Direction", (0,0,1)))
    _set_input(node, ["Turbidity"], _get_input_value(info, "Turbidity", 2.2))


def _transfer_white_noise(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["W"], _get_input_value(info, "W", 0.0))


def _transfer_vector_math(info: "NodeInfo", node: "bpy.types.Node") -> None:
    v1 = _get_input_value(info, "Vector")
    if v1 is not None:
        _set_input(node, ["Texture1", "Color1", "Input1", "A"], v1)
    
    v2 = _get_input_value(info, "Vector_001")
    if v2 is None:
        vals = [v for k, v in info.inputs.items() if k.startswith("Vector")]
        if len(vals) > 1:
            v2 = vals[1]
    if v2 is not None:
        _set_input(node, ["Texture2", "Color2", "Input2", "B"], v2)
    
    s = _get_input_value(info, "Scale")
    if s is not None:
        _set_input(node, ["Amount", "Factor", "Value2", "B"], s)


def _transfer_blackbody(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Temperature"], _get_input_value(info, "Temperature", 1500.0))


def _transfer_rgb_to_bw(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Saturation"], 0.0)


def _transfer_volume_principled(info: "NodeInfo", node: "bpy.types.Node") -> None:
    _set_input(node, ["Absorption", "Color"], _get_input_value(info, "Color", (1,1,1,1)))
    _set_input(node, ["Density", "Density float"], _get_input_value(info, "Density", 1.0))
    _set_input(node, ["Phase", "Anisotropy"], _get_input_value(info, "Anisotropy", 0.0))
    _set_input(node, ["Emission", "Emission color"], _get_input_value(info, "Emission Color", (0,0,0,1)))
    _set_input(node, ["Emission power", "Power"], _get_input_value(info, "Emission Strength", 0.0))


def _transfer_generic(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Fallback: try to match inputs by identifier or display name."""
    for sock_id, value in info.inputs.items():
        if value is None:
            continue
        # Try by identifier first
        inp = node.inputs.get(sock_id)
        # Then try by display name
        if inp is None:
            display_name = info.input_identifiers.get(sock_id, sock_id)
            inp = node.inputs.get(display_name)
        if inp is not None and hasattr(inp, "default_value"):
            try:
                inp.default_value = value
            except (TypeError, AttributeError):
                pass


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, callable] = {
    "ShaderNodeBsdfPrincipled": _transfer_principled,
    "ShaderNodeBsdfGlass": _transfer_glass,
    "ShaderNodeBsdfGlossy": _transfer_glossy,
    "ShaderNodeBsdfDiffuse": _transfer_diffuse,
    "ShaderNodeEmission": _transfer_emission,
    "ShaderNodeBsdfTranslucent": _transfer_translucent,
    "ShaderNodeBsdfRefraction": _transfer_refraction,
    "ShaderNodeMixShader": _transfer_mix_shader,
    "ShaderNodeAddShader": _transfer_add_shader,
    "ShaderNodeTexImage": _transfer_image_texture,
    "ShaderNodeNormalMap": _transfer_normal_map,
    "ShaderNodeBump": _transfer_bump,
    "ShaderNodeDisplacement": _transfer_displacement,
    "ShaderNodeMapping": _transfer_mapping,
    "ShaderNodeMixRGB": _transfer_mix_rgb,
    "ShaderNodeMix": _transfer_mix,
    "ShaderNodeInvert": _transfer_invert,
    "ShaderNodeHueSaturation": _transfer_hue_sat,
    "ShaderNodeBrightContrast": _transfer_bright_contrast,
    "ShaderNodeGamma": _transfer_gamma_node,
    "ShaderNodeMath": _transfer_math,
    "ShaderNodeMapRange": _transfer_map_range,
    "ShaderNodeClamp": _transfer_clamp,
    "ShaderNodeRGB": _transfer_rgb,
    "ShaderNodeValue": _transfer_value,
    "ShaderNodeFresnel": _transfer_fresnel,
    "ShaderNodeLayerWeight": _transfer_fresnel,
    "ShaderNodeVertexColor": _transfer_vertex_color,
    "ShaderNodeAttribute": _transfer_attribute,
    "ShaderNodeAmbientOcclusion": _transfer_ambient_occlusion,
    "ShaderNodeTexNoise": _transfer_noise,
    "ShaderNodeTexVoronoi": _transfer_voronoi,
    "ShaderNodeTexMusgrave": _transfer_noise,  # reuse noise handler
    "ShaderNodeTexWave": _transfer_wave,
    "ShaderNodeTexChecker": _transfer_checker,
    "ShaderNodeUVMap": _transfer_uv_map,
    "ShaderNodeValToRGB": _transfer_color_ramp,
    "ShaderNodeVolumeAbsorption": _transfer_volume_absorption,
    "ShaderNodeVolumeScatter": _transfer_volume_scatter,
    "ShaderNodeTexCoord": lambda info, node: None,  # no params to transfer
    "ShaderNodeBsdfTransparent": lambda info, node: None,  # null material, no params
    "ShaderNodeTexGradient": lambda info, node: None,
    "ShaderNodeTexBrick": lambda info, node: None,
    "ShaderNodeRGBCurves": lambda info, node: None,  # complex, best-effort
    "ShaderNodeNewGeometry": lambda info, node: None,
    "ShaderNodeLightPath": lambda info, node: None,

    # New handlers
    "ShaderNodeBsdfMetallic": _transfer_metallic,
    "ShaderNodeBsdfSheen": _transfer_sheen,
    "ShaderNodeBsdfToon": _transfer_toon,
    "ShaderNodeBsdfHair": _transfer_principled, # Hair mapping varies, but principled works as a fallback
    "ShaderNodeBsdfHairPrincipled": _transfer_principled,
    "ShaderNodeSubsurfaceScattering": _transfer_sss_standalone,
    "ShaderNodeTexEnvironment": _transfer_environment,
    "ShaderNodeTexMagic": _transfer_magic_texture,
    "ShaderNodeTexSky": _transfer_sky_texture,
    "ShaderNodeTexWhiteNoise": _transfer_white_noise,
    "ShaderNodeTexGabor": _transfer_white_noise,
    "ShaderNodeVectorMath": _transfer_vector_math,
    "ShaderNodeBlackbody": _transfer_blackbody,
    "ShaderNodeRGBToBW": _transfer_rgb_to_bw,
    "ShaderNodeVolumePrincipled": _transfer_volume_principled,
}
