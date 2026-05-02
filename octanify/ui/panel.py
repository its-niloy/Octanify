"""Octanify — N-Panel UI.

Provides the panel in the N-Panel sidebar under the "Octanify" tab
with conversion controls, batch mode, gamma slider, and update tools.
"""

from __future__ import annotations

import bpy


class OCTANIFY_PT_main_panel(bpy.types.Panel):
    """Main Octanify panel in the N-Panel sidebar."""

    bl_label = "Octanify"
    bl_idname = "OCTANIFY_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Octanify"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene

        # ── Main conversion button ────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 1.4
        col.operator("octanify.convert", text="Convert to Octane", icon="SHADING_RENDERED")

        layout.separator(factor=0.5)

        # ── Batch mode ────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Batch Object Conversion:", icon="OBJECT_DATA")
        col = box.column(align=True)
        col.prop(scene, "octanify_batch_mode", expand=True)

        layout.separator(factor=0.5)

        # ── Albedo gamma ──────────────────────────────────────────────
        box = layout.box()
        box.label(text="Albedo Gamma Control:", icon="COLOR")
        col = box.column(align=True)
        col.prop(scene, "octanify_albedo_gamma", slider=True)

        layout.separator(factor=0.5)

        # ── Conversion Settings ───────────────────────────────────────
        box = layout.box()
        box.label(text="Conversion Settings:", icon="PREFERENCES")
        col = box.column(align=True)
        col.prop(scene, "octanify_base_material", expand=False)
        
        col.separator(factor=0.3)
        col.label(text="Displacement:", icon="MOD_DISPLACE")
        col.prop(scene, "octanify_disp_mode", expand=True)
        if scene.octanify_disp_mode == "TEXTURE":
            col.prop(scene, "octanify_disp_level_of_detail")
        col.prop(scene, "octanify_disp_mid_level")

        layout.separator(factor=0.5)

        # ── Update tools ──────────────────────────────────────────────
        box = layout.box()
        box.label(text="Material Update Tools:", icon="FILE_REFRESH")
        col = box.column(align=True)
        col.operator(
            "octanify.update_selected_gamma",
            text="Update Selected Material",
            icon="MATERIAL",
        )
        col.separator(factor=0.3)
        col.operator(
            "octanify.update_all_gamma",
            text="Update All Materials",
            icon="WORLD",
        )

        layout.separator(factor=0.5)

        # ── Utilities ─────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Utilities:", icon="TOOL_SETTINGS")
        col = box.column(align=True)
        col.operator(
            "octanify.preview_node_viewport",
            text="Preview Node in Viewport",
            icon="RESTRICT_VIEW_OFF",
        )
        col.separator(factor=0.3)
        col.operator(
            "octanify.create_basic_material",
            text="Create Basic Material",
            icon="MATERIAL_DATA",
        )
        col.separator(factor=0.3)
        col.operator(
            "octanify.auto_connect_textures",
            text="Auto-Connect Textures",
            icon="LINKED",
        )


class OCTANIFY_PT_shader_panel(bpy.types.Panel):
    """Octanify panel in the Shader Editor sidebar."""

    bl_label = "Octanify"
    bl_idname = "OCTANIFY_PT_shader_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Octanify"

    draw = OCTANIFY_PT_main_panel.draw


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    OCTANIFY_PT_main_panel,
    OCTANIFY_PT_shader_panel,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
