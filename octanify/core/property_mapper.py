"""Octanify — Property mapper.

Per-node-type transfer functions that copy values, properties, and
configuration from the original Cycles node info snapshot to the
newly created Octane node.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .node_registry import INPUT_MAP, MATH_OPERATION_MAP, is_standard_surface_node
from ..utils.logger import get_logger

if TYPE_CHECKING:
    import bpy
    from .shader_detection import NodeInfo

log = get_logger()

MIX_BLEND_TYPE_MAP = {
    "MIX": "Mix", "DARKEN": "Darken", "MULTIPLY": "Multiply", "BURN": "Burn",
    "LIGHTEN": "Lighten", "SCREEN": "Screen", "DODGE": "Dodge", "ADD": "Add",
    "OVERLAY": "Overlay", "SOFT_LIGHT": "Soft Light", "LINEAR_LIGHT": "Linear Light",
    "DIFFERENCE": "Difference", "EXCLUSION": "Exclusion", "SUBTRACT": "Subtract",
    "DIVIDE": "Divide", "HUE": "Hue", "SATURATION": "Saturation", "COLOR": "Color",
    "VALUE": "Value"
}

# Live RNA values from Octane 31.9's Composite Texture Layer enum.  These
# identifiers include their category prefix; the human-readable Blender blend
# names are not accepted by the socket.
COMPOSITE_BLEND_TYPE_MAP = {
    "MIX": "Mix|Normal",
    "ADD": "Blend|Add",
    "MULTIPLY": "Blend|Multiply",
    "DARKEN": "Photometric|Darken",
    "BURN": "Photometric|Color burn",
    "LIGHTEN": "Photometric|Lighten",
    "SCREEN": "Photometric|Screen",
    "DODGE": "Photometric|Color dodge",
    "OVERLAY": "Translucent|Overlay",
    "SOFT_LIGHT": "Translucent|Soft light",
    "LINEAR_LIGHT": "Translucent|Linear light",
    "SUBTRACT": "Arithmetic|Subtract",
    "DIVIDE": "Arithmetic|Divide",
    "DIFFERENCE": "Arithmetic|Difference",
    "EXCLUSION": "Arithmetic|Exclusion",
    "HUE": "Spectral|Hue",
    "SATURATION": "Spectral|Saturation",
    "COLOR": "Spectral|Color",
    "VALUE": "Spectral|Value",
}

MATH_TYPE_MAP = {
    "ADD": "Add", "SUBTRACT": "Subtract", "MULTIPLY": "Multiply", "DIVIDE": "Divide",
    "MULTIPLY_ADD": "Multiply Add", "POWER": "Power", "LOGARITHM": "Logarithm",
    "SQRT": "Square Root", "INV_SQRT": "Inverse Square Root", "ABSOLUTE": "Absolute",
    "EXPONENT": "Exponent", "MINIMUM": "Minimum", "MAXIMUM": "Maximum",
    "LESS_THAN": "Less Than", "GREATER_THAN": "Greater Than", "SIGN": "Sign",
    "COMPARE": "Compare", "SMOOTH_MIN": "Smooth min", "SMOOTH_MAX": "Smooth max",
    "ROUND": "Round", "FLOOR": "Floor", "CEIL": "Ceil", "TRUNC": "Truncate",
    "FRACT": "Fraction", "MODULO": "Truncated Modulo", "FLOORED_MODULO": "Floored Modulo",
    "WRAP": "Wrap", "SNAP": "Snap", "PINGPONG": "Pingpong", "SINE": "Sine",
    "COSINE": "Cosine", "TANGENT": "Tangent", "ARCSINE": "Arcsine",
    "ARCCOSINE": "Arccosine", "ARCTANGENT": "Arctangent", "ARCTAN2": "Arctan2",
    "SINH": "Hyperbolic Sine", "COSH": "Hyperbolic Cosine", "TANH": "Hyperbolic Tangent",
    "RADIANS": "Radians", "DEGREES": "Degrees"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_input(node: "bpy.types.Node", candidates: list[str], value: Any) -> bool:
    """Try to set a default_value on the first matching input socket."""
    for name in candidates:
        inp = node.inputs.get(name)
        if inp is not None and hasattr(inp, "default_value"):
            try:
                inp.default_value = _coerce_value_for_socket(inp, value)
                return True
            except (TypeError, AttributeError):
                continue
    return False


def _is_sequence_value(value: Any) -> bool:
    return hasattr(value, "__len__") and not isinstance(value, (str, bytes))


def _coerce_value_for_socket(socket: "bpy.types.NodeSocket", value: Any) -> Any:
    """Adapt a Cycles default value to the target Octane socket shape."""
    if value is None or not hasattr(socket, "default_value"):
        return value

    target_value = socket.default_value
    target_is_sequence = _is_sequence_value(target_value)
    source_is_sequence = _is_sequence_value(value)

    if target_is_sequence:
        target_len = len(target_value)
        if source_is_sequence:
            source = list(value)
        else:
            source = [value] * target_len

        if len(source) >= target_len:
            return tuple(source[:target_len])
        if target_len == 4 and len(source) == 3:
            return tuple([*source, 1.0])
        if source:
            return tuple([*source, *([source[-1]] * (target_len - len(source)))])
        return value

    if source_is_sequence:
        source = list(value)
        if source:
            return source[0]

    return value


def _set_prop(node: "bpy.types.Node", attr: str, value: Any) -> bool:
    """Try to set an attribute on a node."""
    try:
        setattr(node, attr, value)
        return True
    except (AttributeError, TypeError):
        return False


def _node_tag(node: "bpy.types.Node", key: str, default: Any = None) -> Any:
    """Read an Octanify custom property from RNA nodes and test doubles."""
    getter = getattr(node, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return getattr(node, key, default)


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


def _get_mix_input_value(
    info: "NodeInfo",
    name: str,
    default: Any = None,
) -> Any:
    """Resolve the enabled socket of Blender's polymorphic Mix node."""
    data_type = info.properties.get("data_type", "RGBA")
    suffix = {
        "FLOAT": "Float",
        "VECTOR": "Vector",
        "RGBA": "Color",
        "ROTATION": "Rotation",
    }.get(data_type)
    if name == "Factor":
        suffix = (
            "Vector"
            if data_type == "VECTOR"
            and info.properties.get("factor_mode") == "NON_UNIFORM"
            else "Float"
        )
    if suffix is not None:
        identifier_value = _get_input_value(
            info, f"{name}_{suffix}", None
        )
        if identifier_value is not None:
            return identifier_value
    return _get_input_value(info, name, default)


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


def _weighted_color(color: Any, weight: Any) -> tuple[float, float, float]:
    """Return an Octane layer colour representing Cycles tint × weight."""
    try:
        scalar = float(weight)
    except (TypeError, ValueError):
        scalar = 0.0
    if not _is_sequence_value(color):
        color = (color, color, color)
    values = list(color)[:3]
    while len(values) < 3:
        values.append(values[-1] if values else 1.0)
    return tuple(float(component) * scalar for component in values)


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
    """Map Principled BSDF using the semantics of the actual target node."""
    if is_standard_surface_node(node):
        _transfer_principled_standard_surface(info, node)
    else:
        _transfer_principled_universal(info, node)


def _transfer_principled_standard_surface(
    info: "NodeInfo",
    node: "bpy.types.Node",
) -> None:
    """Principled BSDF -> Standard Surface with one-to-one PBR layers.

    Unlike Universal Material, Standard Surface exposes independent weights
    and colours for the base, specular, transmission, coating, sheen, and
    subsurface lobes.  Keeping those controls separate avoids enabling glossy
    layers merely because a white tint is present in the Cycles node.
    """
    direct_transfers = [
        (("Base Color",), "Base color"),
        (("Diffuse Roughness",), "Diffuse roughness"),
        (("Metallic",), "Metalness"),
        (("Roughness",), "Specular roughness"),
        (("IOR",), "Specular IOR"),
        (("Anisotropic",), "Specular anisotropy"),
        (("Anisotropic Rotation",), "Specular rotation"),
        (("Transmission Weight", "Transmission"), "Transmission weight"),
        (("Alpha",), "Opacity"),
        (("Coat Weight", "Clearcoat"), "Coating weight"),
        (("Coat Tint",), "Coating color"),
        (("Coat Roughness", "Clearcoat Roughness"), "Coating roughness"),
        (("Coat IOR",), "Coating IOR"),
        (("Sheen Weight", "Sheen"), "Sheen weight"),
        (("Sheen Tint",), "Sheen color"),
        (("Sheen Roughness",), "Sheen roughness"),
        (("Subsurface Weight", "Subsurface"), "Subsurface weight"),
        (("Subsurface Radius",), "Subsurface radius"),
        (("Subsurface Scale",), "Subsurface scale"),
        (("Subsurface Anisotropy",), "Subsurface anisotropy"),
        (("Thin Film Thickness",), "Film thickness (nm)"),
        (("Thin Film IOR",), "Film IOR"),
    ]
    for cycles_names, octane_name in direct_transfers:
        value = next(
            (
                candidate
                for name in cycles_names
                if (candidate := _get_input_value(info, name)) is not None
            ),
            None,
        )
        if value is not None:
            _set_input(node, [octane_name], value)

    # Standard Surface defaults this to 0.8, while pre-Blender-4 Principled
    # nodes had no exposed Base Weight and behaved as 1.0.
    _set_input(
        node,
        ["Base weight"],
        _get_input_value(info, "Base Weight", 1.0),
    )

    # Blender's 0.5 Principled default represents the normal dielectric
    # response.  Both OTOY's Universal converter and Standard Surface's 1.0
    # default use twice that scale.
    specular_level = _get_input_value(info, "Specular IOR Level")
    if specular_level is None:
        specular_level = _get_input_value(info, "Specular")
    if isinstance(specular_level, (int, float)):
        _set_input(node, ["Specular weight"], specular_level * 2.0)

    base_color = _get_input_value(info, "Base Color")
    if base_color is not None:
        # Cycles uses Base Color for transmission and subsurface tint too.
        # These Standard Surface lobes are inactive at zero weight, so setting
        # their tint is safe and remains correct when a weight is textured.
        _set_input(node, ["Transmission color"], base_color)
        _set_input(node, ["Subsurface color"], base_color)

    specular_tint = _get_input_value(info, "Specular Tint")
    if specular_tint is not None:
        if isinstance(specular_tint, (int, float)) and base_color is not None:
            base = list(base_color)[:3]
            tint = float(specular_tint)
            specular_tint = tuple(1.0 + (channel - 1.0) * tint for channel in base)
        _set_input(node, ["Specular color"], specular_tint)


def _transfer_principled_universal(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Principled BSDF → Universal Material without corrupting layer defaults.

    Universal's Coating and Sheen inputs encode coloured layer strength;
    Cycles exposes independent weight and tint controls.  Diffuse Roughness is
    also not Universal's main specular Roughness.  Treating these sockets as
    interchangeable was the root cause of default Principled nodes becoming
    perfectly smooth, fully coated materials.
    """

    # Octane Universal defaults to its legacy "Octane" lobe. Cycles
    # Principled uses a GGX microfacet response, so leaving the fresh-node
    # default here changes highlight shape and perceived roughness.
    _set_input(node, ["BSDF model", "BRDF model"], "GGX")

    # Only semantically equivalent socket values belong in this direct list.
    _simple_transfers = [
        ("Base Color",           "Albedo color", "Albedo", "Diffuse", "Base color"),
        ("Metallic",             "Metallic", "Metallic float", "Metalness"),
        ("Roughness",            "Roughness", "Roughness float", "Specular roughness"),
        ("IOR",                  "Dielectric IOR", "Index", "IOR", "Specular IOR"),
        ("Alpha",                "Opacity", "Opacity float"),
        ("Anisotropic",          "Anisotropy", "Anisotropy float"),
        ("Anisotropic Rotation", "Anisotropy rotation", "Rotation"),
    ]

    for mapping in _simple_transfers:
        cycles_name = mapping[0]
        oct_candidates = list(mapping[1:])
        value = _get_input_value(info, cycles_name)
        if value is not None:
            _set_input(node, oct_candidates, value)

    # Cycles' default Specular IOR Level is 0.5 while Octane's physically
    # equivalent Universal Specular channel defaults to 1.0.  OTOY's own
    # converter applies this factor of two as well.
    specular_level = _get_input_value(info, "Specular IOR Level")
    if specular_level is None:
        specular_level = _get_input_value(info, "Specular")
    if isinstance(specular_level, (int, float)):
        _set_input(node, ["Specular", "Specular float"], specular_level * 2.0)

    # Cycles weight × tint corresponds to Octane's coloured layer strength.
    coat_weight = _get_input_value(info, "Coat Weight")
    if coat_weight is None:
        coat_weight = _get_input_value(info, "Clearcoat", 0.0)
    coat_tint = _get_input_value(info, "Coat Tint", (1.0, 1.0, 1.0, 1.0))
    _set_input(node, ["Coating", "Coating color"],
               _weighted_color(coat_tint, coat_weight))
    if isinstance(coat_weight, (int, float)) and coat_weight > 0.0:
        coat_roughness = _get_input_value(info, "Coat Roughness")
        if coat_roughness is None:
            coat_roughness = _get_input_value(info, "Clearcoat Roughness", 0.03)
        _set_input(
            node,
            ["Coating roughness", "Coating roughness float"],
            coat_roughness,
        )
        _set_input(node, ["Coating IOR"],
                   _get_input_value(info, "Coat IOR", 1.5))

    sheen_weight = _get_input_value(info, "Sheen Weight")
    if sheen_weight is None:
        sheen_weight = _get_input_value(info, "Sheen", 0.0)
    sheen_tint = _get_input_value(info, "Sheen Tint", (1.0, 1.0, 1.0, 1.0))
    _set_input(node, ["Sheen", "Sheen color"],
               _weighted_color(sheen_tint, sheen_weight))
    if isinstance(sheen_weight, (int, float)) and sheen_weight > 0.0:
        _set_input(node, ["Sheen roughness", "Sheen roughness float"],
                   _get_input_value(info, "Sheen Roughness", 0.5))

    # Film IOR has no effect when width is zero.  Preserve Octane's own IOR
    # default for inactive thin film instead of writing an unrelated Cycles
    # default into hidden state.
    film_width = _get_input_value(info, "Thin Film Thickness", 0.0)
    if isinstance(film_width, (int, float)) and film_width > 0.0:
        _set_input(node, ["Film width", "Thin film thickness"], film_width)
        _set_input(node, ["Film IOR", "Thin film IOR"],
                   _get_input_value(info, "Thin Film IOR", 1.33))

    # Transmission is a link-only texture input in current Octane.  Linked
    # values are rebuilt normally and unlinked non-zero values are materialised
    # by the Principled post-process in conversion_engine.
    tw = _get_input_value(info, "Transmission Weight")
    if tw is None:
        tw = _get_input_value(info, "Transmission")
    if isinstance(tw, (int, float)):
        _set_input(node, ["Transmission float"], tw)


def _transfer_glass(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Glass BSDF → Specular Material."""
    _set_input(node, ["Reflection", "Specular"],
               (1.0, 1.0, 1.0, 1.0))
    _set_input(node, ["Transmission color", "Transmission"],
               _get_input_value(info, "Color", (1.0, 1.0, 1.0, 1.0)))
    _set_input(node, ["Roughness", "Roughness float"],
               _get_input_value(info, "Roughness", 0.0))
    _set_input(node, ["Index", "IOR", "Dielectric IOR"],
               _get_input_value(info, "IOR", 1.45))
    # Transparent shadow approximation for dedicated glass materials.
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
    _set_input(node, ["Emission power", "Power"], strength * 100.0)
    _set_prop(node, "surface_brightness", True)


def _transfer_translucent(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Translucent BSDF → Diffuse Material (transmission channel)."""
    _set_input(node, ["Diffuse", "Albedo color", "Albedo"],
               (0.0, 0.0, 0.0, 1.0))
    _set_input(node, ["Transmission", "Transmission color"],
               _get_input_value(info, "Color", (0.8, 0.8, 0.8, 1.0)))


def _transfer_refraction(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Refraction BSDF → Specular Material."""
    _set_input(node, ["Reflection", "Specular"],
               (1.0, 1.0, 1.0, 1.0))
    _set_input(node, ["Transmission color", "Transmission"],
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

    image_user = info.properties.get("image_user", {})
    for attr, val in image_user.items():
        if val is not None:
            transferred = False
            target_image_user = getattr(node, "image_user", None)
            if target_image_user is not None:
                try:
                    setattr(target_image_user, attr, val)
                    transferred = True
                except (AttributeError, TypeError):
                    pass
            # Older Octane builds expose some animation controls directly on
            # the node rather than through Blender's image_user wrapper.
            if not transferred:
                _set_prop(node, attr, val)

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
        _set_input(node, ["Legacy gamma", "Gamma", "Power"], 1.0)
        _set_prop(node, "gamma", 1.0)
    else:
        # sRGB → gamma 2.2
        _set_input(node, ["Legacy gamma", "Gamma", "Power"], 2.2)
        _set_prop(node, "gamma", 2.2)

    extension = info.properties.get("extension", "REPEAT")
    if extension == "EXTEND":
        _set_input(node, ["Border mode (U)"], "Clamp value")
        _set_input(node, ["Border mode (V)"], "Clamp value")
    elif extension == "CLIP":
        _set_input(node, ["Border mode (U)"], "Black color")
        _set_input(node, ["Border mode (V)"], "Black color")
    else:
        _set_input(node, ["Border mode (U)"], "Wrap around")
        _set_input(node, ["Border mode (V)"], "Wrap around")

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
    _set_input(node, ["Height", "Amount"], _get_input_value(info, "Scale", 1.0))
    midlevel = _get_input_value(info, "Midlevel", 0.5)
    _set_input(node, ["Mid level", "Mid Level"], midlevel)

    try:
        scene = bpy.context.scene
        disp_mode = getattr(scene, "octanify_disp_mode", "TEXTURE")
        if disp_mode == "TEXTURE":
            lod_value = str(
                getattr(scene, "octanify_disp_level_of_detail", "3")
            )
            # Current UI identifiers are 0..5.  Versions before 1.1 used
            # Octane's enum identifiers 8..14, so retain those as a migration
            # path while mapping both schemes to the displayed resolution.
            lod_dict = {
                "0": "256x256",
                "1": "512x512",
                "2": "1024x1024",
                "3": "2048x2048",
                "4": "4096x4096",
                "5": "8192x8192",
                "8": "256x256",
                "9": "512x512",
                "10": "1024x1024",
                "11": "2048x2048",
                "12": "4096x4096",
                "13": "8192x8192",
                "14": "16384x16384",
            }
            lod_value = lod_dict.get(lod_value, lod_value)
            _set_input(node, ["Level of detail"], lod_value)

        pref_mid = getattr(scene, "octanify_disp_mid_level", 0.5)
        if pref_mid != 0.5:
            _set_input(node, ["Mid level", "Mid Level"], pref_mid)
    except Exception:
        pass


def _transfer_mapping(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Mapping → Octane 3D Transform."""
    loc = _get_input_value(info, "Location", (0.0, 0.0, 0.0))
    rot = _get_input_value(info, "Rotation", (0.0, 0.0, 0.0))
    scl = _get_input_value(info, "Scale", (1.0, 1.0, 1.0))

    # Blender stores Mapping rotations in radians. Octane's generated 3D
    # Transformation socket is a raw degree vector and defaults to YXZ, so a
    # direct copy changes both units and Euler order.
    rotation_degrees = tuple(math.degrees(float(angle)) for angle in rot)
    _set_input(node, ["Rotation order"], "XYZ")
    _set_input(node, ["Translation", "Position", "Location"], loc)
    _set_input(node, ["Rotation"], rotation_degrees)
    _set_input(node, ["Scale", "Scaling"], scl)


def _transfer_mix_rgb(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """MixRGB → Composite Texture layers or the legacy wrapper fallback."""
    fac = _get_input_value(info, "Fac")
    c1 = _get_input_value(info, "Color1")
    c2 = _get_input_value(info, "Color2")
    blend_type = info.properties.get("blend_type", "MIX")
    layer_role = _node_tag(node, "octanify_mix_layer", "")

    if getattr(node, "bl_idname", "") == "OctaneCompositeTexture":
        _set_input(node, ["Clamp"], info.properties.get("use_clamp", False))
        return
    if layer_role == "base":
        _set_input(node, ["Enabled"], True)
        _set_input(node, ["Input"], c1)
        _set_input(node, ["Opacity"], 1.0)
        _set_input(node, ["Blend mode"], "Mix|Normal")
        return
    if layer_role == "blend":
        _set_input(node, ["Enabled"], True)
        _set_input(node, ["Input"], c2)
        _set_input(node, ["Opacity"], 1.0 if fac is None else fac)
        _set_input(
            node,
            ["Blend mode"],
            COMPOSITE_BLEND_TYPE_MAP.get(blend_type, "Mix|Normal"),
        )
        return

    if fac is not None:
        _set_input(node, ["Factor", "Amount"], fac)

    if c1 is not None:
        _set_input(node, ["A", "Texture1", "Color1", "Input1"], c1)

    if c2 is not None:
        _set_input(node, ["B", "Texture2", "Color2", "Input2"], c2)

    use_clamp = info.properties.get("use_clamp", False)
    _set_input(node, ["Clamp Result(Int)"], 1 if use_clamp else 0)
    _set_input(node, ["Clamp Result"], use_clamp)

    oct_blend_type = MIX_BLEND_TYPE_MAP.get(blend_type, "Mix")
    _set_input(node, ["Blend Type"], oct_blend_type)


def _transfer_mix(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Blender 4+ color Mix → Composite layers or wrapper fallback."""
    fac = _get_mix_input_value(info, "Factor")
    if fac is None:
        fac = _get_input_value(info, "Fac")
    a = _get_mix_input_value(info, "A")
    b = _get_mix_input_value(info, "B")
    blend_type = info.properties.get("blend_type", "MIX")
    layer_role = _node_tag(node, "octanify_mix_layer", "")

    if getattr(node, "bl_idname", "") == "OctaneCompositeTexture":
        _set_input(node, ["Clamp"], info.properties.get("clamp_result", False))
        return
    if layer_role == "base":
        _set_input(node, ["Enabled"], True)
        _set_input(node, ["Input"], a)
        _set_input(node, ["Opacity"], 1.0)
        _set_input(node, ["Blend mode"], "Mix|Normal")
        return
    if layer_role == "blend":
        _set_input(node, ["Enabled"], True)
        _set_input(node, ["Input"], b)
        _set_input(node, ["Opacity"], 1.0 if fac is None else fac)
        _set_input(
            node,
            ["Blend mode"],
            COMPOSITE_BLEND_TYPE_MAP.get(blend_type, "Mix|Normal"),
        )
        return

    if fac is not None:
        _set_input(node, ["Factor", "Amount"], fac)
    if a is not None:
        _set_input(node, ["A", "Texture1", "Color1", "Input1"], a)
    if b is not None:
        _set_input(node, ["B", "Texture2", "Color2", "Input2"], b)

    oct_blend_type = MIX_BLEND_TYPE_MAP.get(blend_type, "Mix")
    _set_input(node, ["Blend Type"], oct_blend_type)

    clamp_result = info.properties.get("clamp_result", False)
    _set_input(node, ["Clamp Result(Int)"], 1 if clamp_result else 0)
    _set_input(node, ["Clamp Result"], clamp_result)

    clamp_factor = info.properties.get("clamp_factor", False)
    _set_input(node, ["Clamp Factor(Int)"], 1 if clamp_factor else 0)
    _set_input(node, ["Clamp Factor"], clamp_factor)


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


def _transfer_rgb_curve(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """RGB Curves → Octane Color Correction approximation."""
    factor = _get_input_value(info, "Factor")
    if factor is None:
        factor = _get_input_value(info, "Fac", 1.0)
    _set_input(node, ["Mask", "Amount", "Factor"], factor)


def _transfer_math(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Math → Octane Math Wrapper."""
    op = info.properties.get("operation", "ADD")
    oct_op = MATH_TYPE_MAP.get(op, "Add")
    _set_input(node, ["Type", "Math Type"], oct_op)
    # Native math fallback
    _set_prop(node, "operation", MATH_OPERATION_MAP.get(op, "ADD"))

    v1 = _get_input_value(info, "Value")
    if v1 is not None:
        _set_input(node, ["Value", "Value1", "Input1", "Base", "Degrees", "Radians", "A"], v1)

    v2 = _get_input_value(info, "Value_001")
    if v2 is None:
        vals = [v for k, v in info.inputs.items() if k.startswith("Value")]
        if len(vals) > 1:
            v2 = vals[1]
    if v2 is not None:
        _set_input(node, ["Value2", "Input2", "Exponent", "Multiplier", "Threshold", "Scale", "Increment", "Max", "B"], v2)

    v3 = _get_input_value(info, "Value_002")
    if v3 is None:
        vals = [v for k, v in info.inputs.items() if k.startswith("Value")]
        if len(vals) > 2:
            v3 = vals[2]
    if v3 is not None:
        _set_input(node, ["Value3", "Addend", "Epsilon", "Distance", "Min"], v3)

    clamp = info.properties.get("use_clamp", False)
    _set_input(node, ["Clamp(Int)"], 1 if clamp else 0)
    _set_input(node, ["Clamp"], clamp)



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
        _set_prop(node, "a_value", tuple(color[:3]))
        _set_prop(node, "default_value", color)


def _transfer_value(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Value constant → Octane Float."""
    val = _get_output_value(info, "Value")
    if val is not None:
        _set_input(node, ["Value", "Input"], val)
        _set_prop(node, "a_value", val)
        _set_prop(node, "default_value", val)


def _transfer_fresnel(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Fresnel / Layer Weight → Octane Fresnel."""
    ior = _get_input_value(info, "IOR")
    if ior is None:
        ior = _get_input_value(info, "Blend", 0.5)
    if ior is not None:
        _set_input(node, ["IOR", "Index", "Power", "Falloff skew factor"], ior)


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
    """Noise/Musgrave → native Cinema 4D noise, with legacy fallback."""
    scale = _get_input_value(info, "Scale", 5.0)
    detail = _get_input_value(info, "Detail", 2.0)
    roughness = _get_input_value(info, "Roughness", 0.5)
    lacunarity = _get_input_value(info, "Lacunarity", 2.0)

    if getattr(node, "bl_idname", "") == "OctaneCinema4DNoise":
        cycles_noise_type = (
            info.properties.get("musgrave_type", "FBM")
            if info.bl_idname == "ShaderNodeTexMusgrave"
            else info.properties.get("noise_type", "FBM")
        )
        noise_type = {
            "MULTIFRACTAL": "FBM",
            "RIDGED_MULTIFRACTAL": "Ridged Multi Fractal",
            "HYBRID_MULTIFRACTAL": "Ridged Multi Fractal",
            "FBM": "FBM",
            "HETERO_TERRAIN": "Displaced Turbulence",
        }.get(cycles_noise_type, "FBM")
        _set_input(node, ["Power"], 1.0)
        _set_input(node, ["Noise type"], noise_type)
        _set_input(node, ["Octaves"], max(0.0, min(15.0, float(detail))))
        _set_input(node, ["Lacunarity"], max(0.1, min(10.0, float(lacunarity))))
        _set_input(node, ["Gain"], max(-10.0, min(10.0, float(roughness))))

        dimensions = info.properties.get(
            "musgrave_dimensions"
            if info.bl_idname == "ShaderNodeTexMusgrave"
            else "noise_dimensions",
            "3D",
        )
        # Cinema 4D Noise switches between a two-coordinate and a
        # four-coordinate implementation.  Cycles 3D Noise needs the latter
        # too (with a fixed T), otherwise Z is discarded and a front-facing
        # cube degenerates into one-dimensional vertical bands.
        _set_input(node, ["Use 4D noise"], dimensions in {"3D", "4D"})
        if dimensions == "4D":
            _set_input(node, ["T"], _get_input_value(info, "W", 0.0))

        from .report import report_data
        if dimensions == "1D":
            report_data.add_approximation(
                f"[{node.name}] One-dimensional Cycles Noise has no direct "
                "Cinema 4D Noise coordinate mode"
            )
        if info.properties.get("normalize") is False:
            report_data.add_approximation(
                f"[{node.name}] Unnormalized Cycles Noise has no direct "
                "Cinema 4D Noise output-range control"
            )

        distortion = float(_get_input_value(info, "Distortion", 0.0) or 0.0)
        if distortion != 0.0:
            report_data.add_warning(
                f"[{node.name}] Cycles Noise Distortion {distortion:g} has no "
                "direct Cinema 4D FBM parameter"
            )
        return

    _set_input(node, ["Omega", "W", "Scale"], scale)
    _set_input(node, ["Octaves", "Detail"], detail)
    _set_input(node, ["Roughness", "Lacunarity"], roughness)
    _set_input(node, ["Distortion"], _get_input_value(info, "Distortion", 0.0))


def _transfer_voronoi(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """Voronoi Texture → Cinema 4D Voronoi, with legacy fallback."""
    if getattr(node, "bl_idname", "") == "OctaneCinema4DNoise":
        feature = info.properties.get("feature", "F1")
        noise_type = {
            "F1": "Voronoi 1",
            "F2": "Voronoi 2",
            "SMOOTH_F1": "Displaced Voronoi",
            "DISTANCE_TO_EDGE": "Voronoi 1",
            "N_SPHERE_RADIUS": "Voronoi 2",
        }.get(feature, "Voronoi 1")
        _set_input(node, ["Power"], 1.0)
        _set_input(node, ["Noise type"], noise_type)
        detail = float(_get_input_value(info, "Detail", 0.0) or 0.0)
        roughness = float(_get_input_value(info, "Roughness", 0.5) or 0.0)
        lacunarity = float(_get_input_value(info, "Lacunarity", 2.0) or 0.0)
        _set_input(node, ["Octaves"], max(0.0, min(15.0, detail)))
        _set_input(node, ["Gain"], max(-10.0, min(10.0, roughness)))
        _set_input(node, ["Lacunarity"], max(0.1, min(10.0, lacunarity)))
        dimensions = info.properties.get("voronoi_dimensions", "3D")
        _set_input(node, ["Use 4D noise"], dimensions in {"3D", "4D"})
        if dimensions == "4D":
            _set_input(node, ["T"], _get_input_value(info, "W", 0.0))
        randomness = float(_get_input_value(info, "Randomness", 1.0) or 0.0)
        if abs(randomness - 1.0) > 1.0e-8:
            from .report import report_data
            report_data.add_approximation(
                f"[{node.name}] Cycles Voronoi Randomness {randomness:g} has "
                "no direct Cinema 4D Voronoi control"
            )
        return
    _set_input(node, ["Scale"], _get_input_value(info, "Scale", 5.0))


def _transfer_white_noise(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """White Noise → established generic Octane Noise fallback."""
    _set_input(node, ["W"], _get_input_value(info, "W", 0.0))


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
    """UV Map → Octane Mesh UV Projection.

    BUG 3 FIX: OctaneMeshUVProjection uses UV index, not name. We attempt
    to set the name via input socket, label the node for manual reference,
    and warn if the UV layer is non-default so users verify the UV index.
    """
    uv_map_name = info.properties.get("uv_map", "")
    # Legacy attempts — may work on some Octane versions
    _set_prop(node, "uv_map", uv_map_name)
    _set_prop(node, "name", uv_map_name)

    if uv_map_name:
        # Try setting by name via input socket (some Octane versions support this)
        uv_name_sock = (
            node.inputs.get("UV set name")
            or node.inputs.get("UV name")
            or node.inputs.get("Attribute")
        )
        if uv_name_sock and hasattr(uv_name_sock, "default_value"):
            try:
                uv_name_sock.default_value = uv_map_name
            except (TypeError, AttributeError):
                pass

        # Label the node so users can manually verify the UV index
        if uv_map_name != "UVMap":
            node.label = f"UV: {uv_map_name}"

        # Warn on non-default UV layer names — Octane may need manual index
        if uv_map_name != "UVMap":
            from .report import report_data
            report_data.add_warning(
                f"[{node.name}] UV layer '{uv_map_name}' — verify UV index in Octane"
            )

def _transfer_color_ramp(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """ColorRamp → Octane Gradient Texture.

    WEAKNESS 1 FIX: Previously only first/last stop colors were transferred.
    Now we attempt full gradient element transfer via oct_node.elements, then
    fall back to start/end color inputs if elements aren't available.
    """
    stops = info.properties.get("stops", [])
    if not stops:
        return

    # Strategy 1: Full element transfer (if the Octane node exposes elements)
    elements_transferred = False
    if hasattr(node, "elements") and len(stops) > 0:
        try:
            elements = node.elements
            # Clear existing elements beyond the first (Octane starts with at least 1)
            while len(elements) > 1:
                elements.remove(elements[-1])

            for i, stop in enumerate(stops):
                if i < len(elements):
                    elem = elements[i]
                else:
                    try:
                        elem = elements.new(stop["position"])
                    except Exception:
                        continue
                try:
                    elem.position = stop["position"]
                    if hasattr(elem, "color"):
                        elem.color = stop["color"]
                except (TypeError, AttributeError):
                    pass

            elements_transferred = True
        except (TypeError, AttributeError, RuntimeError):
            pass  # elements API not available — fall through to start/end

    # Current Octane builds store the editable ramp in a hidden helper node.
    if not elements_transferred and hasattr(node, "color_ramp_name"):
        try:
            from octane.utils import utility as octane_utility

            helper = octane_utility.get_octane_helper_node(node.color_ramp_name)
            color_ramp = helper.color_ramp if helper is not None else None
            if color_ramp is not None:
                while len(color_ramp.elements) > 1:
                    color_ramp.elements.remove(color_ramp.elements[-1])
                for index, stop in enumerate(stops):
                    element = (
                        color_ramp.elements[0]
                        if index == 0
                        else color_ramp.elements.new(stop["position"])
                    )
                    element.position = stop["position"]
                    element.color = stop["color"]
                color_ramp.interpolation = info.properties.get(
                    "interpolation", "LINEAR"
                )
                if hasattr(node, "dumps_color_ramp_data"):
                    node.dumps_color_ramp_data()
                elements_transferred = True
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            pass

    # Strategy 2: Fallback — set start and end colors via input sockets
    if not elements_transferred:
        first_color = stops[0].get("color", (0.0, 0.0, 0.0, 1.0))
        last_color = stops[-1].get("color", (1.0, 1.0, 1.0, 1.0))
        _set_input(node, ["Start color", "Color1", "Start value"], first_color)
        _set_input(node, ["End color", "Color2", "End value"], last_color)

    # Transfer interpolation mode if the Octane node supports it
    interp = info.properties.get("interpolation", "LINEAR")
    interp_sock = node.inputs.get("Interpolation") or node.inputs.get("Mode")
    if interp_sock and hasattr(interp_sock, "default_value"):
        try:
            interp_sock.default_value = interp
        except (TypeError, AttributeError):
            pass

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


# ---------------------------------------------------------------------------
# BUG 1 FIX: Separate / Combine channel node handlers
# When Octane doesn't have a native SeparateColor, the fallback is
# ColorCorrection. We report a warning so users know to verify channels.
# ---------------------------------------------------------------------------

def _transfer_separate_color(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """SeparateColor/RGB/XYZ → Octane equivalent or ColorCorrection fallback.

    If the created node is a ColorCorrection (fallback), we note the channel
    split in the label and emit a conversion report warning. The actual
    per-channel isolation is handled at the link level by OUTPUT_MAP.
    """
    from .report import report_data

    is_fallback = node.bl_idname in (
        "ShaderNodeOctColorCorrectionTex", "OctaneColorCorrection",
    )
    if is_fallback:
        short_type = info.bl_idname.replace("ShaderNode", "")
        node.label = f"[Channel Split] {info.label}"
        mat_name = node.id_data.name.replace("_OCTANE", "") if node.id_data else "?"
        report_data.add_approximation(
            f"[{mat_name}] Channel split preserved via workaround: {short_type}"
        )


def _transfer_combine_color(info: "NodeInfo", node: "bpy.types.Node") -> None:
    """CombineColor/RGB/XYZ → Octane equivalent or Add fallback.

    Transfer individual channel default values if available.
    """
    from .report import report_data

    # Transfer default channel values
    bid = info.bl_idname
    if bid in ("ShaderNodeCombineColor", "ShaderNodeCombineRGB"):
        r = _get_input_value(info, "Red")
        g = _get_input_value(info, "Green")
        b = _get_input_value(info, "Blue")
        if r is None:
            r = _get_input_value(info, "R")
        if g is None:
            g = _get_input_value(info, "G")
        if b is None:
            b = _get_input_value(info, "B")
        if r is not None:
            _set_input(node, ["First channel", "Texture1", "Input1", "Color1", "R"], r)
        if g is not None:
            _set_input(node, ["Second channel", "Texture2", "Input2", "Color2", "G"], g)
        if b is not None:
            _set_input(node, ["Third channel", "Texture3", "Input3", "B"], b)
    elif bid == "ShaderNodeCombineXYZ":
        x = _get_input_value(info, "X")
        y = _get_input_value(info, "Y")
        z = _get_input_value(info, "Z")
        if x is not None:
            _set_input(node, ["Texture1", "Input1", "X"], x)
        if y is not None:
            _set_input(node, ["Texture2", "Input2", "Y"], y)
        if z is not None:
            _set_input(node, ["Texture3", "Input3", "Z"], z)

    is_fallback = node.bl_idname in (
        "ShaderNodeOctAddTex", "OctaneAddTexture",
    )
    if is_fallback:
        short_type = info.bl_idname.replace("ShaderNode", "")
        node.label = f"[Channel Combine] {info.label}"
        mat_name = node.id_data.name.replace("_OCTANE", "") if node.id_data else "?"
        report_data.add_approximation(
            f"[{mat_name}] Channel combine preserved via workaround: {short_type}"
        )


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
    "ShaderNodeRGBCurve": _transfer_rgb_curve,
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

    # BUG 1 FIX: Channel split/combine handlers
    "ShaderNodeSeparateColor": _transfer_separate_color,
    "ShaderNodeSeparateRGB": _transfer_separate_color,
    "ShaderNodeSeparateXYZ": _transfer_separate_color,
    "ShaderNodeCombineColor": _transfer_combine_color,
    "ShaderNodeCombineRGB": _transfer_combine_color,
    "ShaderNodeCombineXYZ": _transfer_combine_color,
}
