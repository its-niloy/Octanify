"""Blender 5.1 + Octane 31.9 validation for Phase 5 material controls."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import addon_utils
import bpy


REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))


def _enable_addons() -> None:
    octane_root = os.environ.get("OCTANE_ADDON_ROOT", "")
    if not octane_root and "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
        if args:
            octane_root = " ".join(args)
    if octane_root and octane_root not in sys.path:
        sys.path.insert(0, octane_root)
    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=True, persistent=False)
    if not addon_utils.check("octane")[1]:
        raise RuntimeError("Octane 31.9 did not register")


def _close(actual, expected, tolerance: float = 1.0e-5) -> bool:
    return all(
        abs(float(value) - float(reference)) <= tolerance
        for value, reference in zip(actual, expected)
    )


def _material_object(name: str, sss_weight: float = 0.0):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(f"{name}_MAT")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled"
    principled.inputs["Base Color"].default_value = (0.3, 0.12, 0.06, 1.0)
    principled.inputs["Subsurface Weight"].default_value = sss_weight
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    obj.data.materials.append(material)
    return obj, material


def _nested_sss_object(name: str):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(f"{name}_MAT")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    group_tree = bpy.data.node_groups.new(
        f"{name}_GROUP", "ShaderNodeTree"
    )
    group_tree.interface.new_socket(
        name="Shader",
        in_out="OUTPUT",
        socket_type="NodeSocketShader",
    )
    principled = group_tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = (0.25, 0.08, 0.04, 1.0)
    principled.inputs["Subsurface Weight"].default_value = 0.4
    group_output = group_tree.nodes.new("NodeGroupOutput")
    group_tree.links.new(
        principled.outputs["BSDF"], group_output.inputs["Shader"]
    )

    group_node = tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = group_tree
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(group_node.outputs["Shader"], output.inputs["Surface"])
    obj.data.materials.append(material)
    return obj, material, group_tree


def _octane_material_nodes(material):
    return [
        node
        for node in material.node_tree.nodes
        if node.bl_idname
        in {
            "OctaneStandardSurfaceMaterial",
            "ShaderNodeOctStandardSurfaceMat",
            "OctaneUniversalMaterial",
            "ShaderNodeOctUniversalMat",
            "OctaneGlossyMaterial",
            "ShaderNodeOctGlossyMat",
        }
    ]


def _displacement_object(name: str, mode: str):
    obj, material = _material_object(name)
    obj.modifiers.new("Subdivision", "SUBSURF").levels = 2
    tree = material.node_tree
    output = tree.nodes.get("Cycles Output")
    height = tree.nodes.new("ShaderNodeValue")
    height.outputs[0].default_value = 0.4
    displacement = tree.nodes.new("ShaderNodeDisplacement")
    displacement.inputs["Scale"].default_value = 0.2
    displacement.inputs["Midlevel"].default_value = 0.5
    tree.links.new(height.outputs[0], displacement.inputs["Height"])
    tree.links.new(
        displacement.outputs["Displacement"], output.inputs["Displacement"]
    )
    bpy.context.scene.octanify_disp_mode = mode
    return obj, material


def _volume_object(name: str):
    obj, material = _material_object(name)
    tree = material.node_tree
    output = tree.nodes.get("Cycles Output")
    absorption = tree.nodes.new("ShaderNodeVolumeAbsorption")
    absorption.name = "Absorption"
    absorption.inputs["Color"].default_value = (0.2, 0.4, 0.7, 1.0)
    absorption.inputs["Density"].default_value = 0.5
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.name = "Scatter"
    scatter.inputs["Color"].default_value = (0.7, 0.6, 0.5, 1.0)
    scatter.inputs["Density"].default_value = 0.5
    scatter.inputs["Anisotropy"].default_value = 0.2
    add = tree.nodes.new("ShaderNodeAddShader")
    add.name = "Add Volume"
    tree.links.new(absorption.outputs["Volume"], add.inputs[0])
    tree.links.new(scatter.outputs["Volume"], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Volume"])
    return obj, material


def _linked_density_volume_object(name: str):
    obj, material = _material_object(name)
    tree = material.node_tree
    output = tree.nodes.get("Cycles Output")
    density = tree.nodes.new("ShaderNodeValue")
    density.name = "Driven Density"
    density.outputs[0].default_value = 0.5
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.name = "Linked Scatter"
    tree.links.new(density.outputs[0], scatter.inputs["Density"])
    tree.links.new(scatter.outputs["Volume"], output.inputs["Volume"])
    return obj, material


def _volume_only_object(name: str):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(f"{name}_MAT")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    density = tree.nodes.new("ShaderNodeValue")
    density.name = "Volume-only Density"
    density.outputs[0].default_value = 0.35
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.name = "Volume-only Scatter"
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(density.outputs[0], scatter.inputs["Density"])
    tree.links.new(scatter.outputs["Volume"], output.inputs["Volume"])
    obj.data.materials.append(material)
    return obj, material


def _converted_output(material):
    return next(
        node
        for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and node.get("octanify_graph") == "octane"
    )


def main() -> None:
    _enable_addons()
    import octanify
    from octanify.core.conversion_engine import (
        convert_material,
        reset_cache,
    )
    from octanify.core.report import report_data

    octanify.register()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    assert scene.octanify_smart_material_override is False
    scene.octanify_base_material = "UNIVERSAL"
    scene.octanify_smart_material_override = True
    scene.octanify_batch_mode = "ALL"
    scene.octanify_auto_arrange = False
    scene.octanify_color_nodes = False
    scene.world = None

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    sss_obj, sss_material = _material_object("SSS", 0.35)
    plain_obj, plain_material = _material_object("PLAIN", 0.0)
    reset_cache()
    report_data.clear()
    assert bpy.ops.octanify.convert() == {"FINISHED"}
    assert _octane_material_nodes(sss_material)[0].bl_idname in {
        "OctaneStandardSurfaceMaterial",
        "ShaderNodeOctStandardSurfaceMat",
    }
    assert _octane_material_nodes(plain_material)[0].bl_idname in {
        "OctaneUniversalMaterial",
        "ShaderNodeOctUniversalMat",
    }
    assert any("[SSS_MAT] Subsurface detected" in item for item in report_data.notices)
    assert not any("[PLAIN_MAT] Subsurface detected" in item for item in report_data.notices)

    off_obj, off_material = _material_object("SSS_OVERRIDE_OFF", 0.35)
    reset_cache()
    convert_material(
        off_material,
        obj=off_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    assert _octane_material_nodes(off_material)[0].bl_idname in {
        "OctaneUniversalMaterial",
        "ShaderNodeOctUniversalMat",
    }

    nested_obj, nested_material, source_group = _nested_sss_object("NESTED_SSS")
    reset_cache()
    convert_material(
        nested_material,
        obj=nested_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=True,
    )
    converted_groups = [
        node.node_tree
        for node in nested_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeGroup"
        and node.node_tree is not None
        and node.node_tree.as_pointer() != source_group.as_pointer()
    ]
    nested_targets = [
        node
        for group in converted_groups
        for node in group.nodes
        if node.bl_idname in {
            "OctaneStandardSurfaceMaterial",
            "ShaderNodeOctStandardSurfaceMat",
        }
    ]
    assert len(nested_targets) == 1

    texture_obj, texture_material = _displacement_object(
        "TEXTURE_DISPLACEMENT", "TEXTURE"
    )
    reset_cache()
    convert_material(
        texture_material,
        obj=texture_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    texture_nodes = [
        node for node in texture_material.node_tree.nodes
        if node.bl_idname in {"OctaneTextureDisplacement", "ShaderNodeOctDisplacementTex"}
    ]
    assert len(texture_nodes) == 1

    vertex_obj, vertex_material = _displacement_object(
        "VERTEX_DISPLACEMENT", "VERTEX"
    )
    reset_cache()
    convert_material(
        vertex_material,
        obj=vertex_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    vertex_nodes = [
        node for node in vertex_material.node_tree.nodes
        if node.bl_idname in {"OctaneVertexDisplacement", "ShaderNodeOctVertexDisplacement"}
    ]
    assert len(vertex_nodes) == 1
    assert texture_obj.modifiers["Subdivision"].levels == 2
    assert vertex_obj.modifiers["Subdivision"].levels == 2

    volume_obj, volume_material = _volume_object("THICK_SMOKE")
    scene.octanify_disp_mode = "TEXTURE"
    reset_cache()
    report_data.clear()
    convert_material(
        volume_material,
        obj=volume_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    scattering = [
        node for node in volume_material.node_tree.nodes
        if node.bl_idname == "OctaneScattering"
    ]
    assert len(scattering) == 1
    medium = scattering[0]
    assert abs(medium.inputs["Density"].default_value - 50.0) <= 1.0e-5
    assert _close(
        tuple(medium.inputs["Absorption"].default_value)[:3],
        (0.2, 0.4, 0.7),
    )
    output = _converted_output(volume_material)
    surface = output.inputs["Surface"].links[0].from_node
    assert surface.inputs["Medium"].links[0].from_node.as_pointer() == medium.as_pointer()
    assert any("rebuilt as one Octane Scattering medium" in item for item in report_data.notices)

    linked_obj, linked_material = _linked_density_volume_object("LINKED_FOG")
    reset_cache()
    convert_material(
        linked_material,
        obj=linked_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    linked_medium = next(
        node for node in linked_material.node_tree.nodes
        if node.bl_idname == "OctaneScattering"
    )
    density_scale = linked_medium.inputs["Density"].links[0].from_node
    assert density_scale.bl_idname == "OctaneMultiplyTexture"
    assert abs(density_scale.inputs["Texture 2"].default_value - 100.0) <= 1.0e-5

    volume_only_obj, volume_only_material = _volume_only_object("VOLUME_ONLY")
    reset_cache()
    report_data.clear()
    convert_material(
        volume_only_material,
        obj=volume_only_obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
        base_material_type="UNIVERSAL",
        smart_material_override=False,
    )
    volume_only_null = next(
        node for node in volume_only_material.node_tree.nodes
        if node.bl_idname == "OctaneNullMaterial"
    )
    volume_only_output = _converted_output(volume_only_material)
    assert volume_only_output.inputs["Surface"].links[0].from_node.as_pointer() == volume_only_null.as_pointer()
    volume_only_medium = volume_only_null.inputs["Medium"].links[0].from_node
    assert volume_only_medium.bl_idname == "OctaneScattering"
    assert volume_only_medium.inputs["Density"].links[0].from_node.bl_idname == "OctaneMultiplyTexture"
    cycles_output = next(
        node for node in volume_only_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and node.get("octanify_graph") == "cycles"
    )
    assert cycles_output.target == "CYCLES"
    assert cycles_output.inputs["Volume"].links
    assert any("Volume-only Cycles graph rebuilt" in item for item in report_data.notices)

    result = {
        "sss_target": _octane_material_nodes(sss_material)[0].bl_idname,
        "plain_target": _octane_material_nodes(plain_material)[0].bl_idname,
        "override_off_target": _octane_material_nodes(off_material)[0].bl_idname,
        "nested_sss_target": nested_targets[0].bl_idname,
        "texture_displacement": texture_nodes[0].bl_idname,
        "vertex_displacement": vertex_nodes[0].bl_idname,
        "combined_medium": medium.bl_idname,
        "combined_density": medium.inputs["Density"].default_value,
        "linked_density_scale": density_scale.inputs["Texture 2"].default_value,
        "volume_only_material": volume_only_null.bl_idname,
        "notices": list(report_data.notices),
    }
    print("OCTANIFY_PHASE5_VALIDATION_BEGIN")
    print(json.dumps(result, indent=2))
    print("OCTANIFY_PHASE5_VALIDATION_END")


if __name__ == "__main__":
    main()
