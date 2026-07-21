"""Octanify — Cycles to Octane Material Converter

A production-grade Blender addon that converts Cycles shader trees
to Octane equivalents with full support for:

• Complex node chain traversal and reconstruction
• Glass, emission, bump, alpha, and volumetric handling
• Albedo gamma control with per-material update
• Object-level and scene-level batch conversion
• Procedural scale correction
• Duplicate material de-duplication
"""

from __future__ import annotations

# Legacy bl_info for compatibility with Blender's classic addon system.
# The blender_manifest.toml handles the newer extension system (4.2+).
bl_info = {
    "name": "Octanify",
    "author": "Niloy Bhowmick",
    "version": (1, 4, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Octanify",
    "description": "Convert Cycles materials, lights, and Worlds to Octane",
    "category": "Material",
}

import bpy

from .ui import panel, operators


# ---------------------------------------------------------------------------
# Scene properties
# ---------------------------------------------------------------------------

def _register_properties() -> None:
    bpy.types.Scene.octanify_batch_mode = bpy.props.EnumProperty(
        name="Batch Mode",
        description="Which objects to convert",
        items=[
            (
                "ACTIVE",
                "Active Object",
                "Convert selected objects and the active object's descendants",
            ),
            (
                "ALL",
                "Entire Scene",
                "Convert every material used by objects in the scene",
            ),
        ],
        default="ACTIVE",
    )

    bpy.types.Scene.octanify_smart_conversion = bpy.props.BoolProperty(
        name="Smart Conversion",
        description=(
            "Keep the original Cycles graph and append a separate, "
            "renderer-targeted Octane graph to the same material"
        ),
        default=True,
        options={"HIDDEN"},
    )

    bpy.types.Scene.octanify_auto_arrange = bpy.props.BoolProperty(
        name="Arrange After Conversion",
        description=(
            "Arrange converted material, light, World, and nested node-group "
            "graphs, including nodes inside frames"
        ),
        default=True,
    )

    bpy.types.Scene.octanify_color_nodes = bpy.props.BoolProperty(
        name="Color Converted Nodes",
        description=(
            "Color preserved Cycles and generated Octane graphs for easier "
            "visual identification"
        ),
        default=True,
    )

    bpy.types.Scene.octanify_progress = bpy.props.IntProperty(
        name="Conversion Progress",
        default=0,
        min=0,
        max=100,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    bpy.types.Scene.octanify_progress_label = bpy.props.StringProperty(
        name="Conversion Progress Label",
        default="Ready",
        options={"HIDDEN", "SKIP_SAVE"},
    )

    bpy.types.Scene.octanify_progress_active = bpy.props.BoolProperty(
        name="Conversion Progress Active",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    bpy.types.Scene.octanify_albedo_gamma = bpy.props.FloatProperty(
        name="Albedo Gamma",
        description="Gamma correction value for base color / albedo textures",
        default=2.2,
        min=0.1,
        max=3.0,
        step=10,
        precision=2,
    )

    bpy.types.Scene.octanify_base_material = bpy.props.EnumProperty(
        name="Octane Material Type",
        description="Octane material node created from each Cycles Principled BSDF",
        items=[
            (
                "STANDARD_SURFACE",
                "Standard Surface (Recommended)",
                "Closest semantic match to Cycles Principled BSDF and the fidelity-first default",
            ),
            (
                "UNIVERSAL",
                "Universal Material",
                "Octane-native material with compatibility mappings for Principled layers",
            ),
            (
                "GLOSSY",
                "Glossy Material",
                "Classic Octane diffuse and glossy workflow; advanced Principled lobes are approximated",
            ),
        ],
        default="STANDARD_SURFACE",
    )

    bpy.types.Scene.octanify_smart_material_override = bpy.props.BoolProperty(
        name="Auto-upgrade SSS materials to Standard Surface",
        description=(
            "When enabled, Principled materials with active subsurface "
            "scattering use Octane Standard Surface even when another target "
            "material is selected"
        ),
        default=False,
    )

    bpy.types.Scene.octanify_disp_mode = bpy.props.EnumProperty(
        name="Displacement Mode",
        description="Type of displacement node to create",
        items=[
            ("TEXTURE", "Texture Displacement", "Standard texture displacement"),
            ("VERTEX", "Vertex Displacement", "Displacement applied to vertices"),
        ],
        default="TEXTURE",
    )

    bpy.types.Scene.octanify_disp_mid_level = bpy.props.FloatProperty(
        name="Mid Level",
        description="Displacement mid level",
        default=0.5,
        min=0.0,
        max=1.0,
    )

    bpy.types.Scene.octanify_disp_level_of_detail = bpy.props.EnumProperty(
        name="Level of Detail",
        description="Resolution for texture displacement",
        items=[
            ("0", "256x256", ""),
            ("1", "512x512", ""),
            ("2", "1024x1024", ""),
            ("3", "2048x2048", ""),
            ("4", "4096x4096", ""),
            ("5", "8192x8192", ""),
        ],
        default="3",
    )


def _unregister_properties() -> None:
    for name in (
        "octanify_batch_mode",
        "octanify_smart_conversion",
        "octanify_auto_arrange",
        "octanify_color_nodes",
        "octanify_progress",
        "octanify_progress_label",
        "octanify_progress_active",
        "octanify_albedo_gamma",
        "octanify_base_material",
        "octanify_smart_material_override",
        "octanify_disp_mode",
        "octanify_disp_mid_level",
        "octanify_disp_level_of_detail",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register() -> None:
    _register_properties()
    try:
        panel.register()
        operators.register()
    except Exception:
        # Blender can call register repeatedly during add-on reloads.  Never
        # leave Scene RNA properties behind after a partial class failure.
        try:
            operators.unregister()
        except Exception:
            pass
        try:
            panel.unregister()
        except Exception:
            pass
        _unregister_properties()
        raise


def unregister() -> None:
    operators.unregister()
    panel.unregister()
    _unregister_properties()
