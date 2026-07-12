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
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Octanify",
    "description": "Convert Cycles materials to Octane materials with one click",
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
            ("ACTIVE", "Active Object", "Convert only the active object's materials"),
            ("ALL", "All Objects", "Convert all materials across all scene objects"),
        ],
        default="ACTIVE",
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
        name="Target Material",
        description="Base material to use for Principled BSDF conversions",
        items=[
            ("UNIVERSAL", "Universal Material", "Highly flexible, standard Octane material"),
            ("STANDARD_SURFACE", "Standard Surface", "Good for standard PBR workflows"),
        ],
        default="UNIVERSAL",
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
        "octanify_albedo_gamma",
        "octanify_base_material",
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
