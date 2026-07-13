"""Octanify — Operators.

Provides:
- OCTANIFY_OT_convert        — main conversion (active object or all)
- OCTANIFY_OT_delete_cycles_nodes — remove preserved Cycles graphs on request
- OCTANIFY_OT_update_selected_gamma — update gamma on active material
- OCTANIFY_OT_update_all_gamma     — update gamma on all converted materials
"""

from __future__ import annotations

import re
import time

import bpy

from ..core.conversion_engine import (
    convert_material,
    convert_objects_materials,
    reset_cache,
)
from ..core.gamma_system import update_material_gamma, update_all_materials_gamma
from ..utils.logger import get_logger

log = get_logger()


_PROGRESS_REDRAW_INTERVAL = 0.075
_last_progress_redraw_at = 0.0


_OCTANE_MATERIAL_TYPES = (
    "OctaneStandardSurfaceMaterial",
    "ShaderNodeOctStandardSurfaceMat",
    "OctaneUniversalMaterial",
    "ShaderNodeOctUniversalMat",
)


def _find_preferred_material_node(nodes):
    """Prefer the converted Octane shader when both renderer graphs exist."""
    for node in nodes:
        if node.bl_idname in _OCTANE_MATERIAL_TYPES:
            return node
    for node in nodes:
        if node.bl_idname == "ShaderNodeBsdfPrincipled":
            return node
    return None


def _guess_texture_socket(filename: str, node_type: str) -> str:
    """Infer a material input from a texture filename."""
    name = filename.lower()
    # Word-boundary patterns avoid false positives such as matching ``col``
    # inside ``metallic_collection``.
    if re.search(
        r"(diffuse|albedo|_col_|_col\b|\bcol_|_base_|base.?color|_color)",
        name,
    ):
        return "Base Color" if "Principled" in node_type else "Albedo color"
    if re.search(r"(rough|rgh)", name):
        return "Roughness"
    if re.search(r"(metal|met(?:al)?ness)", name):
        return "Metallic"
    if re.search(r"(norm|nrm|normal)", name):
        return "Normal"
    if re.search(r"(disp|height)", name):
        return "Displacement"
    if re.search(r"(bump)", name):
        return "Bump"
    return ""


def _find_socket(collection, *names: str):
    """Return the first named socket, or the sole/first socket as fallback."""
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    return collection[0] if len(collection) else None


def _active_hierarchy_objects(context: bpy.types.Context) -> list[bpy.types.Object]:
    """Return selected objects plus the active object's full hierarchy."""
    def identity(obj: bpy.types.Object) -> int:
        try:
            return int(obj.as_pointer())
        except (AttributeError, ReferenceError, TypeError):
            return id(obj)

    candidates = list(getattr(context, "selected_objects", ()) or ())
    active = context.active_object
    if active is not None:
        candidates.append(active)
        candidates.extend(list(getattr(active, "children_recursive", ()) or ()))

    wanted = {identity(obj) for obj in candidates}
    # Scene order makes reports, progress, and cache behavior deterministic.
    return [obj for obj in context.scene.objects if identity(obj) in wanted]


def _objects_for_conversion(context: bpy.types.Context) -> list[bpy.types.Object]:
    """Return the object scope selected in the Octanify panel."""
    if context.scene.octanify_batch_mode == "ACTIVE":
        return _active_hierarchy_objects(context)
    return list(context.scene.objects)


def _material_work_items(objects) -> list[tuple[bpy.types.Object, object]]:
    """Collect material slots in deterministic object/slot order."""
    return [
        (obj, slot)
        for obj in objects
        for slot in getattr(obj, "material_slots", ())
        if slot.material is not None
    ]


def _rna_identity(value) -> int:
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _materials_for_objects(objects) -> list[bpy.types.Material]:
    """Return unique materials used by an object collection."""
    materials = []
    seen: set[int] = set()
    for _obj, slot in _material_work_items(objects):
        material = slot.material
        identity = _rna_identity(material)
        if identity not in seen:
            materials.append(material)
            seen.add(identity)
    return materials


def _node_graph_kind(node) -> str:
    try:
        return str(node.get("octanify_graph", ""))
    except (AttributeError, ReferenceError, TypeError):
        return ""


def _delete_cycles_nodes_from_material(material: bpy.types.Material) -> int:
    """Delete only nodes explicitly tagged as Octanify's preserved graph."""
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return 0

    cycles_nodes = [
        node for node in node_tree.nodes
        if _node_graph_kind(node) == "cycles"
    ]
    if not cycles_nodes:
        return 0

    deleted = 0
    for node in cycles_nodes:
        try:
            node_tree.nodes.remove(node)
            deleted += 1
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue
    if deleted == 0:
        return 0

    # Blender allows only one globally active Material Output. Once the
    # authored output is gone, explicitly activate the tagged Octane output.
    octane_outputs = [
        node for node in node_tree.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputMaterial"
        and _node_graph_kind(node) == "octane"
    ]
    if octane_outputs:
        output = next(
            (
                node for node in octane_outputs
                if getattr(node, "target", "") == "ALL"
            ),
            octane_outputs[0],
        )
        try:
            output.target = "ALL"
            output.is_active_output = True
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass

    return deleted


def _redraw_progress(context: bpy.types.Context, force: bool = False) -> None:
    """Redraw progress UI during long single-material conversions."""
    global _last_progress_redraw_at

    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", ()) if screen is not None else ():
        try:
            area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass

    now = time.monotonic()
    if not force and now - _last_progress_redraw_at < _PROGRESS_REDRAW_INTERVAL:
        return
    _last_progress_redraw_at = now

    app = getattr(bpy, "app", None)
    if getattr(app, "background", False) or getattr(context, "window", None) is None:
        return
    redraw_timer = getattr(
        getattr(getattr(bpy, "ops", None), "wm", None),
        "redraw_timer",
        None,
    )
    if redraw_timer is None:
        return
    try:
        redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except (AttributeError, RuntimeError, TypeError):
        pass


def _set_progress(
    context: bpy.types.Context,
    completed: int,
    total: int,
    label: str,
    force_redraw: bool = False,
) -> None:
    percent = 100 if total == 0 else round((completed / total) * 100)
    percent = max(0, min(100, percent))
    context.window_manager.progress_update(percent)
    scene = context.scene
    scene.octanify_progress = percent
    scene.octanify_progress_label = label
    try:
        context.workspace.status_text_set(
            text=f"Octanify: {percent}% — {label}"
        )
    except (AttributeError, RuntimeError, TypeError):
        pass
    _redraw_progress(context, force=force_redraw)


# ---------------------------------------------------------------------------
# Convert operator
# ---------------------------------------------------------------------------

class OCTANIFY_OT_convert(bpy.types.Operator):
    """Convert Cycles materials while preserving both renderer graphs"""

    bl_idname = "octanify.convert"
    bl_label = "Convert to Octane"
    bl_description = "Convert Cycles materials to Octane materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if getattr(context.scene, "octanify_progress_active", False):
            return False
        if context.scene.octanify_batch_mode == "ACTIVE":
            return context.active_object is not None
        return True

    def _prepare_job(self, context: bpy.types.Context) -> bool:
        from ..core.report import report_data
        report_data.clear()

        self._batch_mode = context.scene.octanify_batch_mode
        self._gamma = context.scene.octanify_albedo_gamma
        self._objects = _objects_for_conversion(context)

        if not self._objects:
            self.report({"WARNING"}, "No objects selected for conversion")
            return False

        self._work_items = _material_work_items(self._objects)
        if not self._work_items:
            self.report(
                {"WARNING"},
                "No materials found on the selected object hierarchy",
            )
            return False
        self._work_index = 0
        self._timer = None
        return True

    def _begin_progress(self, context: bpy.types.Context) -> None:
        context.window_manager.progress_begin(0, 100)
        context.scene.octanify_progress_active = True
        _set_progress(
            context,
            0,
            100,
            "Preparing materials",
            force_redraw=True,
        )

    def _end_progress(self, context: bpy.types.Context) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass
            self._timer = None
        try:
            context.window_manager.progress_end()
        except (AttributeError, RuntimeError, TypeError):
            pass
        context.scene.octanify_progress_active = False
        try:
            context.workspace.status_text_set(text=None)
        except (AttributeError, RuntimeError, TypeError):
            pass
        _redraw_progress(context, force=True)

    def _report_summary(self) -> None:
        from ..core.report import report_data

        count = report_data.materials_converted
        if count == 0:
            self.report(
                {"WARNING"},
                "No new Cycles materials were converted; check the conversion report",
            )
        elif self._batch_mode == "ACTIVE":
            self.report(
                {"INFO"},
                f"Converted {count} material(s) across "
                f"{len(self._objects)} selected/hierarchy object(s)",
            )
        else:
            self.report(
                {"INFO"},
                f"Converted {count} material(s) across all objects",
            )

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        app = getattr(bpy, "app", None)
        if (
            getattr(app, "background", False)
            or getattr(context, "window", None) is None
        ):
            return self.execute(context)
        if not self._prepare_job(context):
            return {"CANCELLED"}

        reset_cache()
        self._begin_progress(context)
        try:
            self._timer = context.window_manager.event_timer_add(
                0.05,
                window=context.window,
            )
            context.window_manager.modal_handler_add(self)
        except (AttributeError, RuntimeError, TypeError) as exc:
            self._end_progress(context)
            log.warning(
                "Live progress could not start; using synchronous conversion: %s",
                exc,
            )
            return self.execute(context)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event) -> set[str]:
        if event.type == "ESC":
            completed = self._work_index
            total = len(self._work_items)
            _set_progress(
                context,
                completed,
                total,
                f"Stopped after {completed} of {total} materials",
                force_redraw=True,
            )
            self._end_progress(context)
            self.report(
                {"WARNING"},
                "Conversion stopped; completed changes can be undone",
            )
            return {"FINISHED"}

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        total = len(self._work_items)
        if self._work_index >= total:
            _set_progress(context, 100, 100, "Conversion complete", force_redraw=True)
            self._end_progress(context)
            self._report_summary()
            return {"FINISHED"}

        obj, slot = self._work_items[self._work_index]
        material = slot.material
        material_index = self._work_index
        label = getattr(material, "name", getattr(obj, "name", "Material"))

        def _material_progress(fraction: float, detail: str) -> None:
            clamped = min(1.0, max(0.0, float(fraction)))
            overall = (material_index + clamped) / max(1, total)
            _set_progress(
                context,
                round(overall * 1000),
                1000,
                detail,
            )

        try:
            if material is not None:
                converted = convert_material(
                    material,
                    gamma_value=self._gamma,
                    obj=obj,
                    smart_conversion=True,
                    auto_arrange=True,
                    progress_callback=_material_progress,
                )
                if converted is not None:
                    slot.material = converted
        except Exception as exc:
            log.error("Conversion failed: %s", exc, exc_info=True)
            self.report({"ERROR"}, f"Conversion error: {exc}")
            self._end_progress(context)
            return {"FINISHED"}

        self._work_index += 1
        _set_progress(
            context,
            self._work_index,
            total,
            f"Completed {label}",
            force_redraw=True,
        )
        if self._work_index >= total:
            _set_progress(context, 100, 100, "Conversion complete", force_redraw=True)
            self._end_progress(context)
            self._report_summary()
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context: bpy.types.Context) -> None:
        self._end_progress(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Synchronous path used by scripts, tests, and background Blender."""
        if not self._prepare_job(context):
            return {"CANCELLED"}

        self._begin_progress(context)

        try:
            convert_objects_materials(
                self._objects,
                gamma_value=self._gamma,
                smart_conversion=True,
                auto_arrange=True,
                progress_callback=lambda completed, total, label: _set_progress(
                    context, completed, total, label
                ),
                reset_conversion_cache=True,
            )
            _set_progress(
                context,
                100,
                100,
                "Conversion complete",
                force_redraw=True,
            )

        except Exception as exc:
            log.error("Conversion failed: %s", exc, exc_info=True)
            self.report({"ERROR"}, f"Conversion error: {exc}")
            return {"CANCELLED"}
        finally:
            self._end_progress(context)

        self._report_summary()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Delete preserved Cycles graph
# ---------------------------------------------------------------------------

class OCTANIFY_OT_delete_cycles_nodes(bpy.types.Operator):
    """Delete Octanify-tagged Cycles nodes in the current object scope"""

    bl_idname = "octanify.delete_cycles_nodes"
    bl_label = "Delete Cycles Nodes"
    bl_description = (
        "Delete preserved Cycles nodes from converted materials in the current "
        "object scope; the converted Octane graphs are kept"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if getattr(context.scene, "octanify_progress_active", False):
            return False
        if context.scene.octanify_batch_mode == "ACTIVE":
            return context.active_object is not None
        return True

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        objects = _objects_for_conversion(context)
        materials = _materials_for_objects(objects)
        deleted_nodes = 0
        changed_materials = 0

        for material in materials:
            count = _delete_cycles_nodes_from_material(material)
            if count:
                deleted_nodes += count
                changed_materials += 1

        if deleted_nodes == 0:
            self.report(
                {"WARNING"},
                "No Octanify-preserved Cycles nodes found in the current scope",
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Deleted {deleted_nodes} Cycles node(s) from "
            f"{changed_materials} material(s)",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Update selected material gamma
# ---------------------------------------------------------------------------

class OCTANIFY_OT_update_selected_gamma(bpy.types.Operator):
    """Re-apply gamma correction to the active material"""

    bl_idname = "octanify.update_selected_gamma"
    bl_label = "Update Selected Material"
    bl_description = "Re-apply gamma correction to the active material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.active_object
        if obj is None:
            return False
        if not hasattr(obj, "active_material"):
            return False
        return obj.active_material is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        gamma = context.scene.octanify_albedo_gamma
        mat = context.active_object.active_material

        try:
            count = update_material_gamma(mat, gamma)
            self.report(
                {"INFO"},
                f"Updated gamma on {count} texture(s) in '{mat.name}'",
            )
        except Exception as exc:
            log.error("Gamma update failed: %s", exc, exc_info=True)
            self.report({"ERROR"}, f"Gamma update error: {exc}")
            return {"CANCELLED"}

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Update all materials gamma
# ---------------------------------------------------------------------------

class OCTANIFY_OT_update_all_gamma(bpy.types.Operator):
    """Re-apply gamma correction to all materials on the active object"""

    bl_idname = "octanify.update_all_gamma"
    bl_label = "Update All Materials"
    bl_description = "Re-apply gamma correction to all materials on the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        gamma = context.scene.octanify_albedo_gamma

        # Only target materials that use nodes
        materials = [mat for mat in bpy.data.materials if mat.use_nodes]

        try:
            count = update_all_materials_gamma(materials, gamma)
            self.report(
                {"INFO"},
                f"Updated gamma on {count} texture(s) across {len(materials)} material(s)",
            )
        except Exception as exc:
            log.error("Gamma update failed: %s", exc, exc_info=True)
            self.report({"ERROR"}, f"Gamma update error: {exc}")
            return {"CANCELLED"}

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Utility: Preview Node in Viewport
# ---------------------------------------------------------------------------

class OCTANIFY_OT_preview_node_viewport(bpy.types.Operator):
    """Preview the selected node in the viewport using an Emission material"""

    bl_idname = "octanify.preview_node_viewport"
    bl_label = "Preview Node in Viewport"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.active_object
        return obj is not None and obj.active_material is not None and obj.active_material.node_tree is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        mat = context.active_object.active_material
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        active_node = nodes.active
        if not active_node:
            self.report({"WARNING"}, "No node selected to preview")
            return {"CANCELLED"}

        if active_node.bl_idname in ("ShaderNodeOutputMaterial",):
            self.report({"WARNING"}, "Cannot preview the output node itself")
            return {"CANCELLED"}

        # Find or create Matte Diffuse (with fallback bl_idnames)
        matte = None
        for n in nodes:
            if n.bl_idname in ("ShaderNodeOctDiffuseMat", "OctaneDiffuseMaterial") and n.label == "Viewport Preview Diffuse":
                matte = n
                break
        if not matte:
            for diffuse_type in ("ShaderNodeOctDiffuseMat", "OctaneDiffuseMaterial"):
                try:
                    matte = nodes.new(diffuse_type)
                    matte.label = "Viewport Preview Diffuse"
                    break
                except (RuntimeError, TypeError, KeyError):
                    continue
            if not matte:
                self.report({"ERROR"}, "Cannot create Octane Diffuse node — is the Octane plugin loaded?")
                return {"CANCELLED"}

        # Find or create Texture Emission (with fallback bl_idnames)
        emission = None
        for n in nodes:
            if n.bl_idname in ("ShaderNodeOctTextureEmission", "OctaneTextureEmission") and n.label == "Viewport Preview Emission":
                emission = n
                break
        if not emission:
            for emission_type in ("ShaderNodeOctTextureEmission", "OctaneTextureEmission"):
                try:
                    emission = nodes.new(emission_type)
                    emission.label = "Viewport Preview Emission"
                    break
                except (RuntimeError, TypeError, KeyError):
                    continue
            if not emission:
                self.report({"ERROR"}, "Cannot create Octane Emission node — is the Octane plugin loaded?")
                return {"CANCELLED"}
            # Try to set exposure or power to reasonable defaults
            for sock in emission.inputs:
                if sock.name in ("Exposure", "Power") and hasattr(sock, "default_value"):
                    try:
                        sock.default_value = 1.0
                    except (AttributeError, TypeError, ValueError):
                        pass

        # Find Material Output
        output_node = None
        for n in nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial":
                output_node = n
                break
        if not output_node:
            output_node = nodes.new("ShaderNodeOutputMaterial")

        # Reposition
        matte.location = (output_node.location.x - 300, output_node.location.y)
        emission.location = (matte.location.x - 300, matte.location.y)

        # Link Active -> Emission -> Matte -> Output
        if active_node.outputs:
            out_sock = active_node.outputs[0]
            # special case: if it's already an emission node, just connect to matte
            if "Emission" in active_node.bl_idname:
                material_emission = _find_socket(
                    matte.inputs, "Emission", "Emission color"
                )
                if material_emission is None:
                    self.report({"ERROR"}, "Preview material has no Emission input")
                    return {"CANCELLED"}
                try:
                    links.new(out_sock, material_emission)
                except (RuntimeError, TypeError) as exc:
                    self.report({"ERROR"}, f"Cannot link preview emission: {exc}")
                    return {"CANCELLED"}
            else:
                tex_sock = _find_socket(emission.inputs, "Texture", "Color", "Input")
                if tex_sock is None:
                    self.report({"ERROR"}, "Texture Emission has no texture input")
                    return {"CANCELLED"}
                try:
                    links.new(out_sock, tex_sock)
                except (RuntimeError, TypeError) as exc:
                    self.report({"ERROR"}, f"Cannot link node to preview: {exc}")
                    return {"CANCELLED"}

                em_out = _find_socket(
                    emission.outputs, "Emission out", "OutEmission", "Output"
                )
                mat_em_in = _find_socket(matte.inputs, "Emission", "Emission color")
                if em_out is None or mat_em_in is None:
                    self.report({"ERROR"}, "Preview emission sockets are unavailable")
                    return {"CANCELLED"}
                try:
                    links.new(em_out, mat_em_in)
                except (RuntimeError, TypeError) as exc:
                    self.report({"ERROR"}, f"Cannot link preview material: {exc}")
                    return {"CANCELLED"}
        else:
            self.report({"WARNING"}, "Selected node has no output to preview")
            return {"CANCELLED"}

        mat_out = _find_socket(matte.outputs, "OutMat", "Material out", "Output")
        surf_in = _find_socket(output_node.inputs, "Surface")
        if mat_out is None or surf_in is None:
            self.report({"ERROR"}, "Preview output sockets are unavailable")
            return {"CANCELLED"}
        try:
            links.new(mat_out, surf_in)
        except (RuntimeError, TypeError) as exc:
            self.report({"ERROR"}, f"Cannot connect preview to output: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Previewing {active_node.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Utility: Create Basic Material
# ---------------------------------------------------------------------------

class OCTANIFY_OT_create_basic_material(bpy.types.Operator):
    """Create a fresh Octane material setup on the active object"""

    bl_idname = "octanify.create_basic_material"
    bl_label = "Create Basic Material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.active_object
        return (
            obj is not None
            and getattr(obj, "data", None) is not None
            and hasattr(obj.data, "materials")
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        obj = context.active_object
        scene = context.scene
        mat_type = getattr(scene, "octanify_base_material", "STANDARD_SURFACE")

        # Create a new material
        mat = bpy.data.materials.new(name=f"{obj.name}_Octane")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Clear default nodes
        nodes.clear()

        # Create Material Output
        output_node = nodes.new("ShaderNodeOutputMaterial")
        output_node.label = "Octane Output"
        output_node.location = (300, 0)

        # Create the chosen Octane material
        mat_node = None
        if mat_type == "STANDARD_SURFACE":
            for idname in ("OctaneStandardSurfaceMaterial", "ShaderNodeOctStandardSurfaceMat"):
                try:
                    mat_node = nodes.new(idname)
                    break
                except (RuntimeError, TypeError, KeyError):
                    continue
        if mat_node is None:
            for idname in ("OctaneUniversalMaterial", "ShaderNodeOctUniversalMat"):
                try:
                    mat_node = nodes.new(idname)
                    break
                except (RuntimeError, TypeError, KeyError):
                    continue

        if mat_node is None:
            try:
                bpy.data.materials.remove(mat)
            except (RuntimeError, TypeError):
                pass
            self.report({"ERROR"}, "Cannot create Octane material node — is the Octane plugin loaded?")
            return {"CANCELLED"}

        mat_node.label = "Material"
        mat_node.location = (0, 0)

        # Connect material to output
        mat_out = _find_socket(mat_node.outputs, "OutMat", "Material out", "Output")
        surf_in = _find_socket(output_node.inputs, "Surface")
        if mat_out is None or surf_in is None:
            bpy.data.materials.remove(mat)
            self.report({"ERROR"}, "Created nodes expose no compatible material output sockets")
            return {"CANCELLED"}
        links.new(mat_out, surf_in)

        # Assign to active object
        obj.data.materials.append(mat)
        obj.active_material = mat

        self.report({"INFO"}, f"Created Octane material '{mat.name}' on '{obj.name}'")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Utility: Auto-Connect Textures
# ---------------------------------------------------------------------------

class OCTANIFY_OT_auto_connect_textures(bpy.types.Operator):
    """Automatically connect loose image nodes to the main material based on their filenames"""

    bl_idname = "octanify.auto_connect_textures"
    bl_label = "Auto-Connect Textures"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.active_object
        return obj is not None and obj.active_material is not None and obj.active_material.node_tree is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        mat = context.active_object.active_material
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Smart conversion deliberately keeps both renderer graphs. Prefer
        # the converted Octane material instead of reconnecting loose images
        # back into the authored Cycles Principled shader.
        target_mat = _find_preferred_material_node(nodes)

        if not target_mat:
            self.report({"WARNING"}, "No target material found (e.g. Principled BSDF or Universal Material)")
            return {"CANCELLED"}

        # Find Material Output node (for displacement connections)
        output_node = None
        for n in nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial":
                output_node = n
                break

        connected = 0
        for node in nodes:
            if node.bl_idname in (
                "ShaderNodeTexImage",
                "ShaderNodeOctImageTex",
                "OctaneImageTexture",
                "OctaneRGBImage",
                "OctaneGreyscaleImage",
                "ShaderNodeOctGreyscaleImage",
                "OctaneAlphaImage",
                "ShaderNodeOctAlphaImage",
            ):
                # check if it has output links
                has_links = False
                for out in node.outputs:
                    if out.links:
                        has_links = True
                        break
                if has_links:
                    continue

                # Try to guess from filename
                img = None
                if hasattr(node, "image"): img = node.image
                if not img: continue

                sock_name = _guess_texture_socket(img.name, target_mat.bl_idname)
                if not sock_name: continue

                in_sock = None

                # Octane materials own displacement; only a Cycles Principled
                # fallback routes it through Blender's Material Output.
                if sock_name == "Displacement":
                    if target_mat.bl_idname in _OCTANE_MATERIAL_TYPES:
                        in_sock = target_mat.inputs.get("Displacement")
                    elif output_node:
                        in_sock = output_node.inputs.get("Displacement")
                elif sock_name == "Bump":
                    in_sock = target_mat.inputs.get("Bump") or target_mat.inputs.get("Normal")
                else:
                    in_sock = target_mat.inputs.get(sock_name)
                    # handle alternative names
                    if not in_sock:
                        if sock_name == "Albedo color": in_sock = target_mat.inputs.get("Base color") or target_mat.inputs.get("Albedo")
                        elif sock_name == "Metallic": in_sock = target_mat.inputs.get("Metalness")
                        elif sock_name == "Roughness": in_sock = target_mat.inputs.get("Specular roughness")

                if in_sock:
                    out_sock = _find_socket(node.outputs, "Color", "OutTex", "Texture out")
                    if out_sock is None:
                        continue
                    try:
                        links.new(out_sock, in_sock)
                        connected += 1
                    except (RuntimeError, TypeError) as exc:
                        log.warning(
                            "Auto-connect failed for '%s' -> '%s': %s",
                            node.name,
                            in_sock.name,
                            exc,
                        )

        self.report({"INFO"}, f"Auto-connected {connected} texture(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    OCTANIFY_OT_convert,
    OCTANIFY_OT_delete_cycles_nodes,
    OCTANIFY_OT_update_selected_gamma,
    OCTANIFY_OT_update_all_gamma,
    OCTANIFY_OT_preview_node_viewport,
    OCTANIFY_OT_create_basic_material,
    OCTANIFY_OT_auto_connect_textures,
)

def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
