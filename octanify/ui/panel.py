"""Octanify panels for the 3D Viewport and Shader Editor sidebars.

The interface deliberately gives conversion one strong visual anchor. Less
frequently used settings live in native Blender subpanels so the sidebar stays
calm at narrow widths and remains familiar to Blender users.
"""

from __future__ import annotations

import bpy

from ..core.report import report_data


def _draw_progress(layout: bpy.types.UILayout, scene: bpy.types.Scene) -> None:
    """Draw conversion progress with a compatibility fallback for older APIs."""

    percent = max(0, min(100, int(getattr(scene, "octanify_progress", 0))))
    label = getattr(scene, "octanify_progress_label", "Converting materials")

    progress = getattr(layout, "progress", None)
    if callable(progress):
        progress(factor=percent / 100.0, type="BAR", text=f"{percent}%")
    else:
        # Blender versions without UILayout.progress can still render a clear,
        # read-only progress bar through a disabled slider property.
        bar = layout.row()
        bar.enabled = False
        bar.prop(scene, "octanify_progress", text=f"{percent}%", slider=True)

    status = layout.row(align=True)
    status.label(text=label or "Converting materials", icon="TIME")


def _draw_conversion_console(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
) -> None:
    """Draw the compact conversion dock at the top of the panel."""

    scene = context.scene
    is_active = bool(getattr(scene, "octanify_progress_active", False))
    progress = int(getattr(scene, "octanify_progress", 0))

    console = layout.box()

    action = console.row()
    action.scale_y = 1.9
    action.enabled = not is_active
    action.operator(
        "octanify.convert",
        text="Converting..." if is_active else "Convert to Octane",
        icon="SHADING_RENDERED",
    )

    console.separator(factor=0.45)

    console.label(text="Objects to Convert", icon="OBJECT_DATA")
    targets = console.row(align=True)
    targets.scale_y = 1.15
    targets.prop(scene, "octanify_batch_mode", expand=True)

    target_hint = console.row()
    target_hint.enabled = False
    if scene.octanify_batch_mode == "ACTIVE":
        target_hint.label(text="Selection + active object's children")
    else:
        target_hint.label(text="Every material used in this scene")

    console.separator(factor=0.45)

    console.label(text="Octane Material", icon="MATERIAL")
    material = console.row()
    material.scale_y = 1.15
    material.prop(scene, "octanify_base_material", text="")

    material_hint = console.row()
    material_hint.enabled = False
    if scene.octanify_base_material == "STANDARD_SURFACE":
        material_hint.label(text="Recommended - closest Principled match")
    else:
        material_hint.label(text="Compatibility - Universal workflow")

    console.separator(factor=0.35)
    layout_options = console.row(align=True)
    layout_options.prop(
        scene,
        "octanify_auto_arrange",
        text="Arrange Nodes",
        toggle=True,
        icon="NODETREE",
    )
    layout_options.prop(
        scene,
        "octanify_color_nodes",
        text="Color Nodes",
        toggle=True,
        icon="COLOR",
    )

    if is_active:
        console.separator(factor=0.45)
        _draw_progress(console, scene)
    elif progress >= 100:
        console.separator(factor=0.35)
        complete = console.row(align=True)
        complete.label(text="Last conversion completed", icon="CHECKMARK")


def _draw_displacement(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
) -> None:
    scene = context.scene
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(scene, "octanify_disp_mode", text="Mode")
    if scene.octanify_disp_mode == "TEXTURE":
        layout.prop(
            scene,
            "octanify_disp_level_of_detail",
            text="Level of Detail",
        )
    layout.prop(scene, "octanify_disp_mid_level", text="Mid Level")

    hint = layout.row()
    hint.enabled = False
    if scene.octanify_disp_mode == "TEXTURE":
        hint.label(text="Best for image and procedural height maps")
    else:
        hint.label(text="Uses mesh vertices for displacement")


def _draw_albedo_controls(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
) -> None:
    scene = context.scene
    box = layout.box()

    heading = box.row(align=True)
    heading.alignment = "CENTER"
    heading.label(text="Albedo Gamma", icon="COLOR")

    gamma = box.row()
    gamma.scale_y = 1.1
    gamma.prop(scene, "octanify_albedo_gamma", text="", slider=True)

    updates = box.row(align=True)
    updates.operator(
        "octanify.update_selected_gamma",
        text="Selected Material",
        icon="MATERIAL",
    )
    updates.operator(
        "octanify.update_all_gamma",
        text="All Materials",
        icon="FILE_REFRESH",
    )


def _draw_node_tools(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
) -> None:
    box = layout.box()

    heading = box.row(align=True)
    heading.alignment = "CENTER"
    heading.label(text="Node Tools", icon="NODETREE")

    utilities = box.column(align=True)
    utilities.operator(
        "octanify.preview_node_viewport",
        text="Preview Node in Viewport",
        icon="RESTRICT_VIEW_OFF",
    )
    utilities.operator(
        "octanify.arrange_node_tree",
        text="Arrange Current Node Tree",
        icon="NODETREE",
    )

    quick_actions = utilities.row(align=True)
    quick_actions.operator(
        "octanify.create_basic_material",
        text="Create Material",
        icon="ADD",
    )
    quick_actions.operator(
        "octanify.auto_connect_textures",
        text="Connect Textures",
        icon="LINKED",
    )

    utilities.separator(factor=0.35)
    cleanup = utilities.row()
    cleanup.enabled = not bool(
        getattr(context.scene, "octanify_progress_active", False)
    )
    cleanup.operator(
        "octanify.delete_cycles_nodes",
        text="Delete Cycles Nodes",
        icon="TRASH",
    )

    note = box.row()
    note.enabled = False
    note.label(text="Original Cycles nodes are kept by default")


def _draw_last_report(
    layout: bpy.types.UILayout,
    _context: bpy.types.Context,
) -> None:
    has_results = any(
        (
            report_data.materials_converted,
            report_data.nodes_translated,
            report_data.nodes_unsupported,
            report_data.links_created,
            report_data.links_failed,
            report_data.approximations,
            report_data.notices,
            report_data.warnings,
        )
    )
    if not has_results:
        layout.label(text="No conversion report yet.", icon="INFO")
        return

    summary = layout.column(align=True)
    summary.label(
        text=f"Materials Converted: {report_data.materials_converted}",
        icon="MATERIAL",
    )
    summary.label(
        text=f"Nodes Translated: {report_data.nodes_translated}",
        icon="NODETREE",
    )
    summary.label(
        text=f"Links Created: {report_data.links_created}",
        icon="LINKED",
    )

    if report_data.nodes_unsupported:
        summary.label(
            text=f"Unsupported Nodes: {report_data.nodes_unsupported}",
            icon="ERROR",
        )
    if report_data.links_failed:
        summary.label(
            text=f"Failed Links: {report_data.links_failed}",
            icon="ERROR",
        )

    if report_data.approximations:
        layout.separator()
        layout.label(
            text=f"Approximations ({len(report_data.approximations)})",
            icon="INFO",
        )
        for message in report_data.approximations[:5]:
            layout.label(text=message, icon="DOT")
        if len(report_data.approximations) > 5:
            layout.label(
                text=f"...and {len(report_data.approximations) - 5} more"
            )

    if report_data.notices:
        layout.separator()
        layout.label(
            text=f"Notices ({len(report_data.notices)})",
            icon="INFO",
        )
        for message in report_data.notices[:5]:
            layout.label(text=message, icon="DOT")
        if len(report_data.notices) > 5:
            layout.label(text=f"...and {len(report_data.notices) - 5} more")

    if report_data.warnings:
        layout.separator()
        layout.label(
            text=f"Warnings ({len(report_data.warnings)})",
            icon="ERROR",
        )
        for message in report_data.warnings[:5]:
            layout.label(text=message, icon="DOT")
        if len(report_data.warnings) > 5:
            layout.label(text=f"...and {len(report_data.warnings) - 5} more")


class OCTANIFY_PT_main_panel(bpy.types.Panel):
    """Primary conversion console in the 3D Viewport."""

    bl_label = "Octanify"
    bl_idname = "OCTANIFY_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_order = 0

    def draw(self, context: bpy.types.Context) -> None:
        _draw_conversion_console(self.layout, context)
        self.layout.separator(factor=0.55)
        _draw_albedo_controls(self.layout, context)
        self.layout.separator(factor=0.55)
        _draw_node_tools(self.layout, context)


class OCTANIFY_PT_shader_panel(bpy.types.Panel):
    """Primary conversion console in the Shader Editor."""

    bl_label = "Octanify"
    bl_idname = "OCTANIFY_PT_shader_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_order = 0

    def draw(self, context: bpy.types.Context) -> None:
        _draw_conversion_console(self.layout, context)
        self.layout.separator(factor=0.55)
        _draw_albedo_controls(self.layout, context)
        self.layout.separator(factor=0.55)
        _draw_node_tools(self.layout, context)


class OCTANIFY_PT_view3d_displacement(bpy.types.Panel):
    bl_label = "Displacement Settings"
    bl_idname = "OCTANIFY_PT_view3d_displacement"
    bl_parent_id = "OCTANIFY_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context: bpy.types.Context) -> None:
        _draw_displacement(self.layout, context)


class OCTANIFY_PT_view3d_report(bpy.types.Panel):
    bl_label = "Conversion Report"
    bl_idname = "OCTANIFY_PT_view3d_report"
    bl_parent_id = "OCTANIFY_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context: bpy.types.Context) -> None:
        _draw_last_report(self.layout, context)


class OCTANIFY_PT_shader_displacement(bpy.types.Panel):
    bl_label = "Displacement Settings"
    bl_idname = "OCTANIFY_PT_shader_displacement"
    bl_parent_id = "OCTANIFY_PT_shader_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context: bpy.types.Context) -> None:
        _draw_displacement(self.layout, context)


class OCTANIFY_PT_shader_report(bpy.types.Panel):
    bl_label = "Conversion Report"
    bl_idname = "OCTANIFY_PT_shader_report"
    bl_parent_id = "OCTANIFY_PT_shader_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Octanify"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context: bpy.types.Context) -> None:
        _draw_last_report(self.layout, context)


classes = (
    OCTANIFY_PT_main_panel,
    OCTANIFY_PT_shader_panel,
    OCTANIFY_PT_view3d_displacement,
    OCTANIFY_PT_view3d_report,
    OCTANIFY_PT_shader_displacement,
    OCTANIFY_PT_shader_report,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
