"""Live Blender 5.1 + Octane 31.9 validation for the Glossy option.

Run with Blender's installed Octane add-on enabled::

  blender --background --python tools/blender_validate_glossy_material.py
"""

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


def _close(actual, expected, tolerance: float = 1.0e-5) -> bool:
    return all(
        abs(float(value) - float(reference)) <= tolerance
        for value, reference in zip(actual, expected)
    )


def main() -> None:
    octane_addon_root = os.environ.get("OCTANE_ADDON_ROOT", "")
    if not octane_addon_root and "--" in sys.argv:
        script_args = sys.argv[sys.argv.index("--") + 1:]
        if script_args:
            octane_addon_root = " ".join(script_args)
    if octane_addon_root and octane_addon_root not in sys.path:
        sys.path.insert(0, octane_addon_root)
    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=True, persistent=False)
    if not addon_utils.check("octane")[1]:
        raise RuntimeError(
            "Octane failed to register; set OCTANE_ADDON_ROOT to the directory "
            "that contains the octane package"
        )

    import octanify
    from octanify.core.conversion_engine import convert_material, reset_cache
    from octanify.core.report import report_data

    octanify.register()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.octanify_base_material = "GLOSSY"

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = "GLOSSY_VALIDATION_CUBE"

    material = bpy.data.materials.new("GLOSSY_VALIDATION")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    color = tree.nodes.new("ShaderNodeRGB")
    color.name = "Driven Base Color"
    color.outputs[0].default_value = (0.2, 0.4, 0.6, 1.0)
    roughness = tree.nodes.new("ShaderNodeValue")
    roughness.name = "Driven Roughness"
    roughness.outputs[0].default_value = 0.35
    specular = tree.nodes.new("ShaderNodeValue")
    specular.name = "Driven Specular"
    specular.outputs[0].default_value = 0.3
    metallic = tree.nodes.new("ShaderNodeValue")
    metallic.name = "Unsupported Metallic"
    metallic.outputs[0].default_value = 0.75

    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled"
    principled.inputs["Diffuse Roughness"].default_value = 0.2
    principled.inputs["IOR"].default_value = 1.4
    principled.inputs["Anisotropic"].default_value = 0.25
    principled.inputs["Anisotropic Rotation"].default_value = 0.3
    principled.inputs["Sheen Weight"].default_value = 0.4
    principled.inputs["Sheen Tint"].default_value = (0.5, 0.25, 1.0, 1.0)
    principled.inputs["Sheen Roughness"].default_value = 0.6
    principled.inputs["Thin Film Thickness"].default_value = 250.0
    principled.inputs["Thin Film IOR"].default_value = 1.33
    principled.inputs["Alpha"].default_value = 0.8

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(color.outputs[0], principled.inputs["Base Color"])
    tree.links.new(roughness.outputs[0], principled.inputs["Roughness"])
    tree.links.new(specular.outputs[0], principled.inputs["Specular IOR Level"])
    tree.links.new(metallic.outputs[0], principled.inputs["Metallic"])
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    obj.data.materials.append(material)

    reset_cache()
    report_data.clear()
    converted = convert_material(
        material,
        obj=obj,
        smart_conversion=True,
        auto_arrange=False,
        color_nodes=False,
    )
    assert converted is material

    glossy_nodes = [
        node for node in tree.nodes
        if node.bl_idname in {"OctaneGlossyMaterial", "ShaderNodeOctGlossyMat"}
    ]
    assert len(glossy_nodes) == 1
    glossy = glossy_nodes[0]
    assert glossy.bl_idname == "OctaneGlossyMaterial"
    assert not any(
        node.bl_idname in {
            "OctaneStandardSurfaceMaterial",
            "ShaderNodeOctStandardSurfaceMat",
            "OctaneUniversalMaterial",
            "ShaderNodeOctUniversalMat",
        }
        for node in tree.nodes
    )

    assert glossy.inputs["Diffuse"].links
    assert glossy.inputs["Roughness"].links
    assert glossy.inputs["Specular"].links
    assert (
        glossy.inputs["Specular"].links[0].from_node.bl_idname
        == "OctaneMultiplyTexture"
    )
    specular_scale = glossy.inputs["Specular"].links[0].from_node
    assert abs(specular_scale.inputs["Texture 2"].default_value - 2.0) <= 1.0e-5
    assert glossy.inputs["BRDF model"].default_value == "GGX"
    assert glossy.inputs["Diffuse BRDF model"].default_value == "Oren-Nayar"
    assert abs(glossy.inputs["Index of refraction"].default_value - 1.4) <= 1.0e-5
    assert abs(glossy.inputs["Anisotropy"].default_value - 0.25) <= 1.0e-5
    assert abs(glossy.inputs["Rotation"].default_value - 0.3) <= 1.0e-5
    assert abs(glossy.inputs["Film width (um)"].default_value - 0.25) <= 1.0e-5
    assert abs(glossy.inputs["Film IOR"].default_value - 1.33) <= 1.0e-5
    assert _close(glossy.inputs["Sheen"].default_value, (0.2, 0.1, 0.4))
    assert abs(glossy.inputs["Sheen Roughness"].default_value - 0.6) <= 1.0e-5
    assert abs(glossy.inputs["Opacity"].default_value - 0.8) <= 1.0e-5
    assert any(
        "Metallic" in message and "Glossy Material" in message
        for message in report_data.approximations
    )

    cycles_output = next(
        node for node in tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and node.get("octanify_graph") == "cycles"
    )
    octane_output = next(
        node for node in tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and node.get("octanify_graph") == "octane"
    )
    assert cycles_output.target == "CYCLES"
    assert octane_output.target == "ALL"
    assert (
        octane_output.inputs["Surface"].links[0].from_node.as_pointer()
        == glossy.as_pointer()
    )
    try:
        resolved_output = tree.get_output_node("octane")
    except (TypeError, ValueError):
        resolved_output = tree.get_output_node("ALL")
    assert resolved_output.as_pointer() == octane_output.as_pointer()

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3.0, 0.0, 0.0))
    basic_object = bpy.context.object
    basic_object.name = "GLOSSY_BASIC_MATERIAL_CUBE"
    assert bpy.ops.octanify.create_basic_material() == {"FINISHED"}
    basic_glossy = next(
        node for node in basic_object.active_material.node_tree.nodes
        if node.bl_idname in {
            "OctaneGlossyMaterial",
            "ShaderNodeOctGlossyMat",
        }
    )
    assert basic_glossy.bl_idname == "OctaneGlossyMaterial"

    result = {
        "material_node": glossy.bl_idname,
        "basic_material_node": basic_glossy.bl_idname,
        "cycles_output_target": cycles_output.target,
        "octane_output_target": octane_output.target,
        "specular_scale_node": specular_scale.bl_idname,
        "thin_film_width_um": glossy.inputs["Film width (um)"].default_value,
        "approximations": list(report_data.approximations),
    }
    print("OCTANIFY_GLOSSY_VALIDATION_BEGIN")
    print(json.dumps(result, indent=2))
    print("OCTANIFY_GLOSSY_VALIDATION_END")


if __name__ == "__main__":
    main()
