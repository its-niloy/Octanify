"""Build Octanify conversion fixture materials inside Blender.

Run from Blender, for example:
blender --background --python tools/blender_fixture_scene.py -- --output /tmp/octanify_fixture.blend

Add --convert to invoke octanify.convert after creating the fixtures.
"""

from __future__ import annotations

import argparse
import os

import bpy


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="octanify_fixture.blend")
    parser.add_argument("--convert", action="store_true")
    if "--" in os.sys.argv:
        return parser.parse_args(os.sys.argv[os.sys.argv.index("--") + 1:])
    return parser.parse_args([])


def _clear_scene() -> None:
    # Keep Octane's own material migration handlers from converting a freshly
    # created Principled node when the next bpy operator runs.  The fixture is
    # intentionally authored as a Cycles graph and converted only by Octanify.
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _image(name: str, color_space: str = "sRGB") -> bpy.types.Image:
    image = bpy.data.images.new(name=name, width=4, height=4, alpha=True)
    image.colorspace_settings.name = color_space
    return image


def _node(nodes: bpy.types.Nodes, bl_idname: str, name: str, location: tuple[int, int]):
    node = nodes.new(type=bl_idname)
    node.name = name
    node.label = name
    node.location = location
    return node


def _new_material(name: str) -> tuple[bpy.types.Material, bpy.types.Node, bpy.types.Node]:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = _node(nodes, "ShaderNodeOutputMaterial", "Material Output", (700, 0))
    principled = _node(nodes, "ShaderNodeBsdfPrincipled", "Principled BSDF", (350, 0))
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material, principled, output


def create_complex_pbr_material() -> bpy.types.Material:
    material, principled, output = _new_material("OCTANIFY_FIXTURE_complex_pbr")
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    albedo = _node(nodes, "ShaderNodeTexImage", "Albedo_sRGB", (-900, 260))
    albedo.image = _image("fixture_albedo_srgb", "sRGB")
    links.new(albedo.outputs["Color"], principled.inputs["Base Color"])
    links.new(albedo.outputs["Alpha"], principled.inputs["Alpha"])

    orm = _node(nodes, "ShaderNodeTexImage", "ORM_non_color", (-900, -40))
    orm.image = _image("fixture_orm_non_color", "Non-Color")
    separate = _node(nodes, "ShaderNodeSeparateColor", "Separate ORM", (-650, -40))
    separate.mode = "RGB"
    links.new(orm.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], principled.inputs["Roughness"])
    links.new(separate.outputs["Blue"], principled.inputs["Metallic"])

    normal_img = _node(nodes, "ShaderNodeTexImage", "Normal_non_color", (-900, -360))
    normal_img.image = _image("fixture_normal_non_color", "Non-Color")
    normal = _node(nodes, "ShaderNodeNormalMap", "Normal Map", (-650, -360))
    links.new(normal_img.outputs["Color"], normal.inputs["Color"])

    height_img = _node(nodes, "ShaderNodeTexImage", "Height_non_color", (-900, -600))
    height_img.image = _image("fixture_height_non_color", "Non-Color")
    bump = _node(nodes, "ShaderNodeBump", "Bump From Height", (-420, -500))
    links.new(height_img.outputs["Color"], bump.inputs["Height"])
    links.new(normal.outputs["Normal"], bump.inputs["Normal"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])

    emission_img = _node(nodes, "ShaderNodeTexImage", "Emission_sRGB", (-900, 520))
    emission_img.image = _image("fixture_emission_srgb", "sRGB")
    links.new(emission_img.outputs["Color"], principled.inputs["Emission Color"])
    principled.inputs["Emission Strength"].default_value = 2.0

    ramp = _node(nodes, "ShaderNodeValToRGB", "Roughness Ramp", (-420, -120))
    links.new(separate.outputs["Green"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], principled.inputs["Specular Tint"])

    displacement = _node(nodes, "ShaderNodeDisplacement", "Output Displacement", (350, -360))
    displacement.inputs["Scale"].default_value = 0.08
    links.new(height_img.outputs["Color"], displacement.inputs["Height"])
    links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])

    return material


def create_grouped_material() -> bpy.types.Material:
    material, principled, _output = _new_material("OCTANIFY_FIXTURE_grouped")
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    inner_tree = bpy.data.node_groups.new(
        "OCTANIFY_FIXTURE_inner_color_group", "ShaderNodeTree"
    )
    inner_in = inner_tree.nodes.new("NodeGroupInput")
    inner_out = inner_tree.nodes.new("NodeGroupOutput")
    inner_tree.interface.new_socket(
        "Color In", in_out="INPUT", socket_type="NodeSocketColor"
    )
    inner_tree.interface.new_socket(
        "Color Out", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    inner_tree.links.new(inner_in.outputs["Color In"], inner_out.inputs["Color Out"])

    group_tree = bpy.data.node_groups.new(
        "OCTANIFY_FIXTURE_outer_color_group", "ShaderNodeTree"
    )
    group_in = group_tree.nodes.new("NodeGroupInput")
    group_out = group_tree.nodes.new("NodeGroupOutput")
    group_tree.interface.new_socket(
        "Color In", in_out="INPUT", socket_type="NodeSocketColor"
    )
    group_tree.interface.new_socket(
        "Color Out", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    nested = group_tree.nodes.new("ShaderNodeGroup")
    nested.name = "Nested Inner Group"
    nested.node_tree = inner_tree
    group_tree.links.new(group_in.outputs["Color In"], nested.inputs["Color In"])
    group_tree.links.new(nested.outputs["Color Out"], group_out.inputs["Color Out"])

    image = _node(nodes, "ShaderNodeTexImage", "Grouped Albedo", (-700, 0))
    image.image = _image("fixture_grouped_albedo", "sRGB")
    group = _node(nodes, "ShaderNodeGroup", "Passthrough Group", (-350, 0))
    group.node_tree = group_tree
    links.new(image.outputs["Color"], group.inputs["Color In"])
    links.new(group.outputs["Color Out"], principled.inputs["Base Color"])
    return material


def create_glass_volume_material() -> bpy.types.Material:
    material = bpy.data.materials.new("OCTANIFY_FIXTURE_glass_volume")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = _node(nodes, "ShaderNodeOutputMaterial", "Material Output", (500, 0))
    glass = _node(nodes, "ShaderNodeBsdfGlass", "Tinted Glass", (150, 100))
    glass.inputs["Color"].default_value = (0.2, 0.55, 0.9, 1.0)
    glass.inputs["Roughness"].default_value = 0.08
    glass.inputs["IOR"].default_value = 1.52
    volume = _node(
        nodes,
        "ShaderNodeVolumeAbsorption",
        "Glass Absorption",
        (150, -180),
    )
    volume.inputs["Color"].default_value = (0.1, 0.35, 0.8, 1.0)
    volume.inputs["Density"].default_value = 0.35
    links.new(glass.outputs["BSDF"], output.inputs["Surface"])
    links.new(volume.outputs["Volume"], output.inputs["Volume"])
    return material


def create_robustness_material() -> bpy.types.Material:
    """Exercise unsupported sources, disconnected nodes, and graph cycles."""
    material, principled, _output = _new_material("OCTANIFY_FIXTURE_robustness")
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    geometry = _node(nodes, "ShaderNodeNewGeometry", "Unsupported Geometry", (-700, 220))
    vector_math = _node(nodes, "ShaderNodeVectorMath", "Geometry Length", (-450, 220))
    vector_math.operation = "LENGTH"
    links.new(geometry.outputs["Position"], vector_math.inputs[0])
    links.new(vector_math.outputs["Value"], principled.inputs["Roughness"])

    light_path = _node(nodes, "ShaderNodeLightPath", "Unsupported Light Path", (-700, -20))
    math_node = _node(nodes, "ShaderNodeMath", "Camera Ray Metallic", (-450, -20))
    math_node.operation = "MULTIPLY"
    links.new(light_path.outputs["Is Camera Ray"], math_node.inputs[0])
    links.new(math_node.outputs[0], principled.inputs["Metallic"])

    _node(nodes, "ShaderNodeValue", "Disconnected Staging Value", (-700, -260))

    cycle_a = _node(nodes, "ShaderNodeMath", "Disconnected Cycle A", (-350, -320))
    cycle_b = _node(nodes, "ShaderNodeMath", "Disconnected Cycle B", (-100, -320))
    cycle_a.operation = "ADD"
    cycle_b.operation = "MULTIPLY"
    links.new(cycle_a.outputs[0], cycle_b.inputs[0])
    links.new(cycle_b.outputs[0], cycle_a.inputs[0])
    return material


def create_fidelity_material(
    name: str,
    *,
    base_color: tuple[float, float, float, float],
    roughness: float,
    metallic: float = 0.0,
    specular_level: float = 0.5,
    coat_weight: float = 0.0,
    coat_tint: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    sheen_weight: float = 0.0,
    sheen_tint: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    sheen_roughness: float = 0.5,
) -> bpy.types.Material:
    """Create a distinct real-world surface archetype for fidelity checks."""
    material, principled, _output = _new_material(name)
    principled.inputs["Base Color"].default_value = base_color
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Specular IOR Level"].default_value = specular_level
    principled.inputs["Coat Weight"].default_value = coat_weight
    principled.inputs["Coat Tint"].default_value = coat_tint
    principled.inputs["Sheen Weight"].default_value = sheen_weight
    principled.inputs["Sheen Tint"].default_value = sheen_tint
    principled.inputs["Sheen Roughness"].default_value = sheen_roughness
    return material


def create_fixture_scene() -> None:
    _clear_scene()
    bpy.ops.mesh.primitive_uv_sphere_add(location=(-1.25, 0, 0))
    sphere = bpy.context.object
    sphere.name = "OCTANIFY_FIXTURE_complex_pbr_object"
    sphere.data.materials.append(create_complex_pbr_material())

    bpy.ops.mesh.primitive_cube_add(location=(1.25, 0, 0))
    cube = bpy.context.object
    cube.name = "OCTANIFY_FIXTURE_grouped_object"
    cube.data.materials.append(create_grouped_material())

    bpy.ops.mesh.primitive_ico_sphere_add(location=(0, 2.5, 0))
    glass_object = bpy.context.object
    glass_object.name = "OCTANIFY_FIXTURE_glass_volume_object"
    glass_object.data.materials.append(create_glass_volume_material())

    bpy.ops.mesh.primitive_cube_add(location=(0, -2.5, 0))
    robustness_object = bpy.context.object
    robustness_object.name = "OCTANIFY_FIXTURE_robustness_object"
    robustness_object.data.materials.append(create_robustness_material())

    archetypes = [
        (
            "OCTANIFY_FIDELITY_hard_plastic",
            (-3.0, 0.0, 0.0),
            dict(
                base_color=(0.04, 0.05, 0.06, 1.0),
                roughness=0.22,
                specular_level=0.5,
                coat_weight=0.08,
            ),
        ),
        (
            "OCTANIFY_FIDELITY_rubber",
            (-1.0, 0.0, 0.0),
            dict(
                base_color=(0.025, 0.025, 0.025, 1.0),
                roughness=0.75,
                specular_level=0.25,
            ),
        ),
        (
            "OCTANIFY_FIDELITY_fabric",
            (1.0, 0.0, 0.0),
            dict(
                base_color=(0.18, 0.055, 0.025, 1.0),
                roughness=0.88,
                specular_level=0.2,
                sheen_weight=0.35,
                sheen_tint=(0.8, 0.5, 0.3, 1.0),
                sheen_roughness=0.65,
            ),
        ),
        (
            "OCTANIFY_FIDELITY_metal",
            (3.0, 0.0, 0.0),
            dict(
                base_color=(0.55, 0.57, 0.6, 1.0),
                roughness=0.28,
                metallic=1.0,
                specular_level=0.5,
            ),
        ),
    ]
    for name, location, settings in archetypes:
        bpy.ops.mesh.primitive_uv_sphere_add(location=location)
        obj = bpy.context.object
        obj.name = f"{name}_object"
        obj.data.materials.append(
            create_fidelity_material(name, **settings)
        )


def main() -> None:
    args = _args()
    create_fixture_scene()
    if args.convert:
        bpy.context.scene.octanify_batch_mode = "ALL"
        bpy.ops.octanify.convert()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))


if __name__ == "__main__":
    main()
