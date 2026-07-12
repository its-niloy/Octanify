"""Octanify — Operators.

Provides:
- OCTANIFY_OT_convert        — main conversion (active object or all)
- OCTANIFY_OT_update_selected_gamma — update gamma on active material
- OCTANIFY_OT_update_all_gamma     — update gamma on all converted materials
"""

from __future__ import annotations

import re

import bpy

from ..core.conversion_engine import (
    convert_object_materials,
    convert_scene_materials,
    reset_cache,
)
from ..core.gamma_system import update_material_gamma, update_all_materials_gamma
from ..utils.logger import get_logger

log = get_logger()


def _find_socket(collection, *names: str):
    """Return the first named socket, or the sole/first socket as fallback."""
    for name in names:
        socket = collection.get(name)
        if socket is not None:
            return socket
    return collection[0] if len(collection) else None


# ---------------------------------------------------------------------------
# Convert operator
# ---------------------------------------------------------------------------

class OCTANIFY_OT_convert(bpy.types.Operator):
    """Convert Cycles materials to Octane materials"""

    bl_idname = "octanify.convert"
    bl_label = "Convert to Octane"
    bl_description = "Convert Cycles materials to Octane materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if context.scene.octanify_batch_mode == "ACTIVE":
            return context.active_object is not None
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        batch_mode = context.scene.octanify_batch_mode
        gamma = context.scene.octanify_albedo_gamma
        
        from ..core.report import report_data
        report_data.clear()

        try:
            if batch_mode == "ACTIVE":
                obj = context.active_object
                if obj is None:
                    self.report({"WARNING"}, "No active object selected")
                    return {"CANCELLED"}

                reset_cache()
                converted = convert_object_materials(obj, gamma_value=gamma)
                count = len(converted)
                self.report(
                    {"INFO"},
                    f"Converted {count} material(s) on '{obj.name}'",
                )

            else:  # ALL
                converted = convert_scene_materials(gamma_value=gamma)
                count = len(converted)
                self.report(
                    {"INFO"},
                    f"Converted {count} material(s) across all objects",
                )

        except Exception as exc:
            log.error("Conversion failed: %s", exc, exc_info=True)
            self.report({"ERROR"}, f"Conversion error: {exc}")
            return {"CANCELLED"}

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
# Utility: Revert to Cycles
# ---------------------------------------------------------------------------

class OCTANIFY_OT_revert_material(bpy.types.Operator):
    """Revert materials to their original Cycles versions"""

    bl_idname = "octanify.revert_material"
    bl_label = "Revert to Cycles"
    bl_description = "Restore the original Cycles materials (respects Batch Mode)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if context.scene.octanify_batch_mode == "ACTIVE":
            return context.active_object is not None
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        batch_mode = context.scene.octanify_batch_mode
        
        objects = []
        if batch_mode == "ACTIVE":
            if context.active_object:
                objects.append(context.active_object)
        else:
            objects = list(context.scene.objects)
            
        reverted = 0
        for obj in objects:
            if not hasattr(obj, "material_slots"):
                continue
            for slot in obj.material_slots:
                converted_mat = slot.material
                if converted_mat is None:
                    continue

                source_name = ""
                try:
                    source_name = converted_mat.get(
                        "octanify_source_material", ""
                    )
                except (AttributeError, TypeError):
                    pass

                # Compatibility for materials converted before provenance
                # tags were introduced, including Blender's .001 suffixes.
                if not source_name:
                    match = re.match(
                        r"^(?P<source>.+)_OCTANE(?:\.\d{3})?$",
                        converted_mat.name,
                    )
                    if match:
                        source_name = match.group("source")

                if not source_name:
                    continue
                original_mat = bpy.data.materials.get(source_name)
                if original_mat is not None:
                    slot.material = original_mat
                    reverted += 1
        
        if batch_mode == "ACTIVE" and objects:
            self.report({"INFO"}, f"Reverted {reverted} material(s) on '{objects[0].name}'")
        else:
            self.report({"INFO"}, f"Reverted {reverted} material(s) across all objects")
            
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
        mat_type = getattr(scene, "octanify_base_material", "UNIVERSAL")

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
            for idname in ("ShaderNodeOctStandardSurfaceMat", "OctaneStandardSurfaceMaterial"):
                try:
                    mat_node = nodes.new(idname)
                    break
                except (RuntimeError, TypeError, KeyError):
                    continue
        if mat_node is None:
            for idname in ("ShaderNodeOctUniversalMat", "OctaneUniversalMaterial"):
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
        
        # Find main material node
        target_mat = None
        for n in nodes:
            if n.bl_idname in ("ShaderNodeBsdfPrincipled", "ShaderNodeOctUniversalMat", "OctaneUniversalMaterial", "ShaderNodeOctStandardSurfaceMat", "OctaneStandardSurfaceMaterial"):
                target_mat = n
                break
        
        if not target_mat:
            self.report({"WARNING"}, "No target material found (e.g. Principled BSDF or Universal Material)")
            return {"CANCELLED"}

        # Find Material Output node (for displacement connections)
        output_node = None
        for n in nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial":
                output_node = n
                break
            
        def guess_socket(filename: str, node_type: str) -> str:
            name = filename.lower()
            # Use word-boundary patterns to avoid false positives 
            # (e.g. 'col' should not match 'metallic_collection')
            if re.search(r'(diffuse|albedo|_col_|_col\b|\bcol_|_base_|base.?color|_color)', name):
                if "Principled" in node_type: return "Base Color"
                return "Albedo color"
            if re.search(r'(rough|rgh)', name):
                if "Principled" in node_type: return "Roughness"
                return "Roughness"
            if re.search(r'(metal|met(?:al)?ness)', name):
                if "Principled" in node_type: return "Metallic"
                return "Metallic"
            if re.search(r'(norm|nrm|normal)', name):
                return "Normal"
            if re.search(r'(disp|height)', name):
                return "Displacement"
            if re.search(r'(bump)', name):
                return "Bump"
            return ""

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
                
                sock_name = guess_socket(img.name, target_mat.bl_idname)
                if not sock_name: continue
                
                in_sock = None
                
                # Displacement goes to Material Output, not the material node
                if sock_name == "Displacement" and output_node:
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
    OCTANIFY_OT_update_selected_gamma,
    OCTANIFY_OT_update_all_gamma,
    OCTANIFY_OT_preview_node_viewport,
    OCTANIFY_OT_create_basic_material,
    OCTANIFY_OT_auto_connect_textures,
    OCTANIFY_OT_revert_material,
)

def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
