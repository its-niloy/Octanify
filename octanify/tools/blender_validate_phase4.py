"""Live Blender 5.1 + Octane validation for Phase 4 node conversion.

Run with Blender's installed Octane add-on enabled::

  blender --background --python tools/blender_validate_phase4.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import addon_utils
import bpy


REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))


def _clear_scene() -> None:
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)


def _cube(name: str, size: float, material: bpy.types.Material):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material)
    return obj


def _shared_noise_material() -> bpy.types.Material:
    material = bpy.data.materials.new("PHASE4_SHARED_NOISE")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    coordinates.name = "Coordinates"
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.name = "Mapping"
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.name = "Noise"
    noise.noise_dimensions = "3D"
    noise.noise_type = "FBM"
    noise.inputs["Scale"].default_value = 5.0
    noise.inputs["Detail"].default_value = 3.25
    noise.inputs["Roughness"].default_value = 0.625
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    shader.name = "Principled"
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(coordinates.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Fac"], shader.inputs["Roughness"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _voronoi_material() -> bpy.types.Material:
    material = bpy.data.materials.new("PHASE4_VORONOI")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
    voronoi.name = "Voronoi"
    voronoi.voronoi_dimensions = "3D"
    voronoi.feature = "F2"
    voronoi.inputs["Scale"].default_value = 5.0
    voronoi.inputs["Detail"].default_value = 3.5
    voronoi.inputs["Roughness"].default_value = 0.7
    voronoi.inputs["Lacunarity"].default_value = 2.75
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(coordinates.outputs["Generated"], voronoi.inputs["Vector"])
    tree.links.new(voronoi.outputs["Distance"], shader.inputs["Roughness"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _mix_material() -> bpy.types.Material:
    material = bpy.data.materials.new("PHASE4_MIX")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    mix = tree.nodes.new("ShaderNodeMix")
    mix.name = "Multiply Mix"
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    next(socket for socket in mix.inputs if socket.identifier == "Factor_Float").default_value = 0.35
    next(socket for socket in mix.inputs if socket.identifier == "A_Color").default_value = (
        0.8,
        0.4,
        0.2,
        1.0,
    )
    next(socket for socket in mix.inputs if socket.identifier == "B_Color").default_value = (
        0.25,
        0.5,
        0.75,
        1.0,
    )
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(mix.outputs["Result"], shader.inputs["Base Color"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _close(actual, expected, tolerance: float = 1.0e-5) -> bool:
    return all(abs(float(a) - float(e)) <= tolerance for a, e in zip(actual, expected))


def main() -> None:
    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=False, persistent=False)
    from octanify.core.conversion_engine import convert_objects_materials
    _clear_scene()

    shared = _shared_noise_material()
    cubes = [
        _cube(f"PHASE4_{size:g}M", size, shared)
        for size in (1.0, 2.0, 5.0)
    ]
    mix_object = _cube("PHASE4_MIX_CUBE", 1.0, _mix_material())
    voronoi_object = _cube("PHASE4_VORONOI_CUBE", 2.0, _voronoi_material())
    converted = convert_objects_materials(
        [*cubes, mix_object, voronoi_object],
        smart_conversion=True,
        auto_arrange=False,
    )

    scale_results = []
    material_ids = set()
    for obj, size in zip(cubes, (1.0, 2.0, 5.0)):
        material = obj.active_material
        material_ids.add(material.as_pointer())
        c4d = next(
            node for node in material.node_tree.nodes
            if node.bl_idname == "OctaneCinema4DNoise"
        )
        transform = c4d.inputs["UVW transform"].links[0].from_node
        projection = c4d.inputs["Projection"].links[0].from_node
        expected_scale = (0.5 * size / 5.0,) * 3
        assert _close(transform.inputs["Scale"].default_value, expected_scale)
        assert projection.bl_idname == "OctaneXYZToUVW"
        assert projection.inputs["Coordinate space"].default_value == "Object space"
        assert c4d.inputs["Noise type"].default_value == "FBM"
        assert c4d.inputs["Use 4D noise"].default_value is True
        assert abs(c4d.inputs["Octaves"].default_value - 3.25) <= 1.0e-5
        assert abs(c4d.inputs["Gain"].default_value - 0.625) <= 1.0e-5
        cycles_output = next(
            node for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeOutputMaterial"
            and node.get("octanify_graph") == "cycles"
        )
        assert cycles_output.target == "CYCLES"
        scale_results.append({
            "object": obj.name,
            "material": material.name,
            "transform_scale": list(transform.inputs["Scale"].default_value),
        })
    assert len(material_ids) == 3

    voronoi_tree = voronoi_object.active_material.node_tree
    voronoi = next(
        node for node in voronoi_tree.nodes
        if node.bl_idname == "OctaneCinema4DNoise"
    )
    assert voronoi.inputs["Noise type"].default_value == "Voronoi 2"
    assert voronoi.inputs["Use 4D noise"].default_value is True
    assert abs(voronoi.inputs["Octaves"].default_value - 3.5) <= 1.0e-5
    assert abs(voronoi.inputs["Gain"].default_value - 0.7) <= 1.0e-5
    assert abs(voronoi.inputs["Lacunarity"].default_value - 2.75) <= 1.0e-5
    voronoi_transform = voronoi.inputs["UVW transform"].links[0].from_node
    assert _close(voronoi_transform.inputs["Scale"].default_value, (0.2,) * 3)

    mix_tree = mix_object.active_material.node_tree
    composite = next(
        node for node in mix_tree.nodes
        if node.bl_idname == "OctaneCompositeTexture"
    )
    assert composite.a_layer_count == 2
    blend_layer = composite.inputs["Layer 2"].links[0].from_node
    base_layer = composite.inputs["Layer 1"].links[0].from_node
    assert blend_layer.bl_idname == "OctaneTexLayerTexture"
    assert base_layer.bl_idname == "OctaneTexLayerTexture"
    assert blend_layer.inputs["Blend mode"].default_value == "Blend|Multiply"
    assert abs(blend_layer.inputs["Opacity"].default_value - 0.35) <= 1.0e-5
    base_constant = base_layer.inputs["Input"].links[0].from_node
    blend_constant = blend_layer.inputs["Input"].links[0].from_node
    assert base_constant.bl_idname == "OctaneRGBColor"
    assert blend_constant.bl_idname == "OctaneRGBColor"
    assert _close(base_constant.a_value, (0.8, 0.4, 0.2))
    assert _close(blend_constant.a_value, (0.25, 0.5, 0.75))
    try:
        octane_output = mix_tree.get_output_node("octane")
    except (TypeError, ValueError):
        octane_output = mix_tree.get_output_node("ALL")
    assert octane_output is not None
    assert octane_output.target == "ALL"
    assert octane_output.get("octanify_graph") == "octane"
    assert octane_output.inputs["Surface"].links
    assert (
        octane_output.inputs["Surface"].links[0].from_node.bl_idname
        == "OctaneStandardSurfaceMaterial"
    )

    result = {
        "converted_material_count": len(converted),
        "distinct_noise_materials": len(material_ids),
        "scale_results": scale_results,
        "composite": {
            "type": composite.bl_idname,
            "blend_mode": blend_layer.inputs["Blend mode"].default_value,
            "opacity": blend_layer.inputs["Opacity"].default_value,
        },
        "voronoi": {
            "type": voronoi.bl_idname,
            "noise_type": voronoi.inputs["Noise type"].default_value,
            "transform_scale": list(
                voronoi_transform.inputs["Scale"].default_value
            ),
        },
    }
    print("OCTANIFY_PHASE4_VALIDATION_BEGIN")
    print(json.dumps(result, indent=2))
    print("OCTANIFY_PHASE4_VALIDATION_END")


if __name__ == "__main__":
    main()
