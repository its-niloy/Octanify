"""Blender + Octane operator-level validation for shader node groups."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import addon_utils
import bmesh
import bpy


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ADDON = (REPOSITORY_ROOT / "octanify" / "__init__.py").resolve()
PREFIX = "OCTANIFY_GROUP_VALIDATION_"

OCTANE_MATERIAL_TYPES = {
    "OctaneStandardSurfaceMaterial",
    "ShaderNodeOctStandardSurfaceMat",
    "OctaneUniversalMaterial",
    "ShaderNodeOctUniversalMat",
    "OctaneDiffuseMaterial",
    "ShaderNodeOctDiffuseMat",
    "OctaneSpecularMaterial",
    "ShaderNodeOctSpecularMat",
}
OCTANE_MIX_TYPES = {"OctaneMixMaterial", "ShaderNodeOctMixMat"}


def _enable_addons() -> None:
    for module in addon_utils.modules():
        module_file = getattr(module, "__file__", "")
        try:
            resolved = Path(module_file).resolve()
        except (OSError, TypeError, ValueError):
            continue
        if module.__name__.split(".")[-1] != "octanify":
            continue
        if resolved != LOCAL_ADDON and addon_utils.check(module.__name__)[1]:
            addon_utils.disable(module.__name__, default_set=False)

    octane_root = os.environ.get("OCTANE_ADDON_ROOT", "")
    if not octane_root and "--" in sys.argv:
        arguments = sys.argv[sys.argv.index("--") + 1:]
        if arguments:
            octane_root = " ".join(arguments)
    if octane_root and octane_root not in sys.path:
        sys.path.insert(0, octane_root)
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))

    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=True, persistent=False)
    if not addon_utils.check("octane")[1]:
        raise RuntimeError("Octane did not register")

    import octanify

    if Path(octanify.__file__).resolve() != LOCAL_ADDON:
        raise RuntimeError(
            f"Validation imported {octanify.__file__}, expected {LOCAL_ADDON}"
        )
    octanify.register()


def _clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for group in list(bpy.data.node_groups):
        if group.name.startswith(PREFIX):
            bpy.data.node_groups.remove(group)


def _group_output(tree: bpy.types.NodeTree):
    tree.interface.new_socket(
        name="Shader",
        in_out="OUTPUT",
        socket_type="NodeSocketShader",
    )
    output = tree.nodes.new("NodeGroupOutput")
    output.name = "Group Output"
    return output


def _simple_group(name: str) -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(f"{PREFIX}{name}", "ShaderNodeTree")
    output = _group_output(tree)
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled"
    principled.inputs["Base Color"].default_value = (0.25, 0.08, 0.03, 1.0)
    tree.links.new(principled.outputs["BSDF"], output.inputs["Shader"])
    return tree


def _mixed_group(name: str) -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(f"{PREFIX}{name}", "ShaderNodeTree")
    tree.interface.new_socket(
        name="Factor",
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    tree.interface.new_socket(
        name="Color A",
        in_out="INPUT",
        socket_type="NodeSocketColor",
    )
    tree.interface.new_socket(
        name="Color B",
        in_out="INPUT",
        socket_type="NodeSocketColor",
    )
    output = _group_output(tree)
    group_input = tree.nodes.new("NodeGroupInput")
    group_input.name = "Group Input"
    first = tree.nodes.new("ShaderNodeBsdfPrincipled")
    first.name = "First Principled"
    first.inputs["Base Color"].default_value = (0.4, 0.05, 0.02, 1.0)
    second = tree.nodes.new("ShaderNodeBsdfPrincipled")
    second.name = "Second Principled"
    second.inputs["Base Color"].default_value = (0.02, 0.08, 0.4, 1.0)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.name = "Mix Shader"
    tree.links.new(group_input.outputs["Factor"], mix.inputs[0])
    tree.links.new(group_input.outputs["Color A"], first.inputs["Base Color"])
    tree.links.new(group_input.outputs["Color B"], second.inputs["Base Color"])
    tree.links.new(first.outputs["BSDF"], mix.inputs[1])
    tree.links.new(second.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Shader"])
    return tree


def _wrapper_group(
    name: str,
    child_tree: bpy.types.NodeTree,
) -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(f"{PREFIX}{name}", "ShaderNodeTree")
    output = _group_output(tree)
    child = tree.nodes.new("ShaderNodeGroup")
    child.name = f"{name} Child"
    child.node_tree = child_tree
    tree.links.new(child.outputs["Shader"], output.inputs["Shader"])
    return tree


def _material_with_group(
    name: str,
    group_tree: bpy.types.NodeTree,
) -> tuple[bpy.types.Object, bpy.types.Material]:
    material = bpy.data.materials.new(f"{PREFIX}{name}_MAT")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    group = tree.nodes.new("ShaderNodeGroup")
    group.name = f"{name} Group"
    group.node_tree = group_tree
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(group.outputs["Shader"], output.inputs["Surface"])
    return _object_with_material(name, material), material


def _plain_material(
    name: str,
    shader_type: str,
) -> tuple[bpy.types.Object, bpy.types.Material]:
    material = bpy.data.materials.new(f"{PREFIX}{name}_MAT")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    shader = tree.nodes.new(shader_type)
    shader.name = name
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Cycles Output"
    tree.links.new(shader.outputs[0], output.inputs["Surface"])
    return _object_with_material(name, material), material


def _object_with_material(
    name: str,
    material: bpy.types.Material,
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{PREFIX}{name}_MESH")
    cube = bmesh.new()
    bmesh.ops.create_cube(cube, size=2.0)
    cube.to_mesh(mesh)
    cube.free()
    obj = bpy.data.objects.new(f"{PREFIX}{name}_OBJECT", mesh)
    bpy.context.scene.collection.objects.link(obj)
    material_slot = obj.data.materials
    material_slot.append(material)
    return obj


def _converted_group_node(material: bpy.types.Material) -> bpy.types.Node:
    candidates = [
        node
        for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeGroup"
        and node.node_tree is not None
        and bool(node.node_tree.get("octanify_converted", False))
    ]
    assert len(candidates) == 1, (
        f"{material.name}: expected one populated converted group, "
        f"found {len(candidates)}"
    )
    return candidates[0]


def _assert_output_linked(tree: bpy.types.NodeTree) -> None:
    output = next(
        node for node in tree.nodes if node.bl_idname == "NodeGroupOutput"
    )
    assert output.inputs["Shader"].links, f"{tree.name}: Group Output is unlinked"


def _validate_simple(material: bpy.types.Material) -> dict:
    tree = _converted_group_node(material).node_tree
    materials = [node for node in tree.nodes if node.bl_idname in OCTANE_MATERIAL_TYPES]
    assert len(materials) == 1, f"{tree.name}: expected one Octane material"
    _assert_output_linked(tree)
    assert tree.nodes.get("Group Output").inputs["Shader"].links[0].from_node == materials[0]
    return {"tree": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}


def _validate_mix(material: bpy.types.Material) -> dict:
    tree = _converted_group_node(material).node_tree
    materials = [node for node in tree.nodes if node.bl_idname in OCTANE_MATERIAL_TYPES]
    mixes = [node for node in tree.nodes if node.bl_idname in OCTANE_MIX_TYPES]
    assert len(materials) == 2, f"{tree.name}: expected two Octane materials"
    assert len(mixes) == 1, f"{tree.name}: expected one Octane Mix Material"
    _assert_output_linked(tree)
    assert tree.nodes.get("Group Output").inputs["Shader"].links[0].from_node == mixes[0]
    linked_materials = {
        link.from_node.as_pointer()
        for socket in mixes[0].inputs
        for link in socket.links
        if link.from_node.bl_idname in OCTANE_MATERIAL_TYPES
    }
    assert len(linked_materials) == 2, f"{tree.name}: Mix inputs are not intact"
    return {"tree": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}


def _validate_three_levels(material: bpy.types.Material) -> dict:
    trees = []
    tree = _converted_group_node(material).node_tree
    for depth in range(3):
        trees.append(tree)
        _assert_output_linked(tree)
        if depth == 2:
            materials = [
                node for node in tree.nodes if node.bl_idname in OCTANE_MATERIAL_TYPES
            ]
            assert len(materials) == 1, f"{tree.name}: nested leaf material missing"
            break
        children = [
            node
            for node in tree.nodes
            if node.bl_idname == "ShaderNodeGroup"
            and node.node_tree is not None
            and bool(node.node_tree.get("octanify_converted", False))
        ]
        assert len(children) == 1, (
            f"{tree.name}: expected one populated child group at depth {depth + 1}"
        )
        tree = children[0].node_tree
    assert len({tree.as_pointer() for tree in trees}) == 3
    return {"trees": [tree.name for tree in trees]}


def _validate_plain(materials: list[bpy.types.Material]) -> dict:
    result = {}
    for material in materials:
        octane_nodes = [
            node
            for node in material.node_tree.nodes
            if node.bl_idname.startswith(("Octane", "ShaderNodeOct"))
            and node.bl_idname not in {"ShaderNodeOutputMaterial"}
        ]
        assert octane_nodes, f"{material.name}: non-group conversion produced no Octane nodes"
        result[material.name] = [node.bl_idname for node in octane_nodes]
    return result


def _validate_traceback_reporting() -> str:
    from octanify.core import conversion_engine
    from octanify.core.conversion_engine import reset_cache
    from octanify.core.report import report_data

    _clean_scene()
    group_tree = _simple_group("FORCED_FAILURE")
    _object, _material = _material_with_group("FORCED_FAILURE", group_tree)
    original_analyze_tree = conversion_engine.analyze_tree

    def _fail_for_group(node_tree):
        if node_tree.name == group_tree.name:
            raise RuntimeError("injected node-group validation failure")
        return original_analyze_tree(node_tree)

    reset_cache()
    report_data.clear()
    conversion_engine.analyze_tree = _fail_for_group
    try:
        assert bpy.ops.octanify.convert() == {"FINISHED"}
    finally:
        conversion_engine.analyze_tree = original_analyze_tree

    warning = next(
        message
        for message in report_data.warnings
        if message.startswith(f"[Group: {group_tree.name}] Conversion failed")
    )
    assert "injected node-group validation failure" in warning
    assert "Traceback:" in warning
    assert " in " in warning
    return warning


def main() -> None:
    _enable_addons()
    from octanify.core.conversion_engine import reset_cache
    from octanify.core.report import report_data

    _clean_scene()
    scene = bpy.context.scene
    scene.world = None
    scene.render.engine = "BLENDER_EEVEE"
    scene.octanify_batch_mode = "ALL"
    scene.octanify_base_material = "STANDARD_SURFACE"
    scene.octanify_smart_material_override = False
    scene.octanify_auto_arrange = True
    scene.octanify_color_nodes = True

    simple_tree = _simple_group("SIMPLE")
    _simple_object, simple_material = _material_with_group("SIMPLE", simple_tree)

    mixed_tree = _mixed_group("MIXED")
    _mixed_object, mixed_material = _material_with_group("MIXED", mixed_tree)

    nested_leaf = _simple_group("NESTED_LEVEL_3")
    nested_middle = _wrapper_group("NESTED_LEVEL_2", nested_leaf)
    nested_outer = _wrapper_group("NESTED_LEVEL_1", nested_middle)
    _nested_object, nested_material = _material_with_group("NESTED", nested_outer)

    plain_materials = [
        _plain_material("PLAIN_PRINCIPLED", "ShaderNodeBsdfPrincipled")[1],
        _plain_material("PLAIN_DIFFUSE", "ShaderNodeBsdfDiffuse")[1],
        _plain_material("PLAIN_GLASS", "ShaderNodeBsdfGlass")[1],
    ]

    reset_cache()
    report_data.clear()
    operator_result = bpy.ops.octanify.convert()
    print("OCTANIFY_GROUP_WARNINGS", json.dumps(list(report_data.warnings)))
    assert operator_result == {"FINISHED"}
    assert not any("Conversion failed" in warning for warning in report_data.warnings)

    payload = {
        "operator_result": sorted(operator_result),
        "simple": _validate_simple(simple_material),
        "mixed": _validate_mix(mixed_material),
        "nested": _validate_three_levels(nested_material),
        "plain": _validate_plain(plain_materials),
        "warnings": list(report_data.warnings),
    }
    payload["traceback_warning"] = _validate_traceback_reporting()
    print("OCTANIFY_GROUP_VALIDATION_BEGIN")
    print(json.dumps(payload, indent=2))
    print("OCTANIFY_GROUP_VALIDATION_END")


if __name__ == "__main__":
    main()
