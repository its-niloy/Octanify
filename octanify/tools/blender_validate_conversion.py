"""Validate Octanify fixture conversion inside Blender/Octane.

Example:
blender --background octanify_fixture.blend \
  --python tools/blender_validate_conversion.py -- --json octanify-results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import bpy


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="octanify-validation.json")
    if "--" in sys.argv:
        return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    return parser.parse_args([])


def _output_state(output) -> dict:
    if output is None:
        return {}
    surface = output.inputs.get("Surface")
    root = (
        surface.links[0].from_node
        if surface is not None and surface.links
        else None
    )
    return {
        "name": output.name,
        "target": getattr(output, "target", ""),
        "active": bool(getattr(output, "is_active_output", False)),
        "graph": output.get("octanify_graph", ""),
        "root_graph": root.get("octanify_graph", "") if root else "",
        "root_type": getattr(root, "bl_idname", ""),
        "root_label": getattr(root, "label", ""),
    }


def _engine_output_state(tree: bpy.types.NodeTree) -> dict:
    """Mirror Blender and Octane's engine-specific output lookup."""
    try:
        cycles_output = tree.get_output_node("CYCLES")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        cycles_output = None
    try:
        octane_output = tree.get_output_node("octane")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            octane_output = tree.get_output_node("ALL")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            octane_output = None
    return {
        "cycles": _output_state(cycles_output),
        "octane": _output_state(octane_output),
    }


def _material_result(material: bpy.types.Material) -> dict:
    tree = material.node_tree
    unsupported = [
        node.label
        for node in tree.nodes
        if "[UNSUPPORTED]" in getattr(node, "label", "")
    ]
    outputs = [
        node for node in tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
    ]
    unlinked_outputs = []
    for output in outputs:
        surface = output.inputs.get("Surface")
        if surface is not None and not surface.links:
            unlinked_outputs.append(output.name)
    converted_surface = next(
        (
            node for node in tree.nodes
            if node.bl_idname in (
                "OctaneStandardSurfaceMaterial",
                "ShaderNodeOctStandardSurfaceMat",
                "OctaneUniversalMaterial",
                "ShaderNodeOctUniversalMat",
            )
        ),
        None,
    )
    material_state = {}
    if converted_surface is not None:
        for name in (
            "Base weight",
            "Base color",
            "Diffuse roughness",
            "Metalness",
            "Specular weight",
            "Specular color",
            "Specular roughness",
            "Specular IOR",
            "Transmission weight",
            "Coating weight",
            "Coating color",
            "Sheen weight",
            "Sheen color",
            "Albedo",
            "Metallic",
            "Roughness",
            "Specular",
            "Coating",
            "Coating roughness",
            "Sheen",
            "Sheen roughness",
            "BSDF model",
        ):
            socket = converted_surface.inputs.get(name)
            if socket is not None and hasattr(socket, "default_value"):
                value = socket.default_value
                material_state[name] = (
                    list(value)
                    if hasattr(value, "__len__") and not isinstance(value, str)
                    else value
                )

    mapped_image = next(
        (
            node for node in tree.nodes
            if node.bl_idname in ("OctaneRGBImage", "OctaneGreyscaleImage")
            and node.label == "Albedo_sRGB"
        ),
        None,
    )
    mapping_state = {}
    if mapped_image is not None:
        transform_input = mapped_image.inputs.get("UV transform")
        projection_input = mapped_image.inputs.get("Projection")
        transform_node = (
            transform_input.links[0].from_node
            if transform_input is not None and transform_input.links
            else None
        )
        projection_node = (
            projection_input.links[0].from_node
            if projection_input is not None and projection_input.links
            else None
        )
        mapping_state = {
            "transform_type": getattr(transform_node, "bl_idname", ""),
            "projection_type": getattr(projection_node, "bl_idname", ""),
            "rotation_order": (
                transform_node.inputs["Rotation order"].default_value
                if transform_node is not None
                and transform_node.inputs.get("Rotation order") is not None
                else None
            ),
            "rotation": (
                list(transform_node.inputs["Rotation"].default_value)
                if transform_node is not None
                and transform_node.inputs.get("Rotation") is not None
                else None
            ),
        }

    mix_material = next(
        (
            node for node in tree.nodes
            if node.bl_idname in ("OctaneMixMaterial", "ShaderNodeOctMixMat")
        ),
        None,
    )
    mix_state = {}
    if mix_material is not None:
        first = mix_material.inputs.get("First material")
        second = mix_material.inputs.get("Second material")
        mix_state = {
            "type": mix_material.bl_idname,
            "first_linked": bool(first and first.links),
            "second_linked": bool(second and second.links),
        }

    cycles_nodes = [node for node in tree.nodes if node.get("octanify_graph") == "cycles"]
    octane_nodes = [node for node in tree.nodes if node.get("octanify_graph") == "octane"]
    style_state = {
        "cycles_nodes": len(cycles_nodes),
        "octane_nodes": len(octane_nodes),
        "cycles_color": list(cycles_nodes[0].color) if cycles_nodes else [],
        "octane_color": list(octane_nodes[0].color) if octane_nodes else [],
        "cycles_right": max(
            (node.location.x + node.width for node in cycles_nodes),
            default=0.0,
        ),
        "octane_left": min(
            (node.location.x for node in octane_nodes),
            default=0.0,
        ),
    }
    return {
        "name": material.name,
        "nodes": len(tree.nodes),
        "links": len(tree.links),
        "unsupported": unsupported,
        "unlinked_outputs": unlinked_outputs,
        "active_outputs": [
            output.name
            for output in outputs
            if bool(getattr(output, "is_active_output", False))
        ],
        "output_targets": [getattr(output, "target", "") for output in outputs],
        "engine_outputs": _engine_output_state(tree),
        "material_state": material_state,
        "material_type": getattr(converted_surface, "bl_idname", ""),
        "mapping_state": mapping_state,
        "mix_state": mix_state,
        "style_state": style_state,
    }


def _exercise_auto_connect() -> dict:
    """Verify the utility targets Octane, including material displacement."""
    obj = bpy.data.objects.get("OCTANIFY_FIDELITY_rubber_object")
    material = obj.active_material
    tree = material.node_tree
    roughness = tree.nodes.new("ShaderNodeTexImage")
    roughness.name = "OCTANIFY_AUTOCONNECT_roughness"
    roughness.image = bpy.data.images.new(
        "rubber_roughness.png", width=2, height=2, alpha=False
    )
    height = tree.nodes.new("ShaderNodeTexImage")
    height.name = "OCTANIFY_AUTOCONNECT_height"
    height.image = bpy.data.images.new(
        "rubber_height.png", width=2, height=2, alpha=False
    )

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    operator_result = sorted(bpy.ops.octanify.auto_connect_textures())

    octane = next(
        node for node in tree.nodes
        if node.bl_idname == "OctaneStandardSurfaceMaterial"
    )
    cycles = next(
        node for node in tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    octane_roughness = octane.inputs.get("Specular roughness")
    octane_displacement = octane.inputs.get("Displacement")
    cycles_roughness = cycles.inputs.get("Roughness")
    return {
        "operator_result": operator_result,
        "roughness_to_octane": bool(
            octane_roughness
            and octane_roughness.links
            and octane_roughness.links[0].from_node == roughness
        ),
        "displacement_to_octane": bool(
            octane_displacement
            and octane_displacement.links
            and octane_displacement.links[0].from_node == height
        ),
        "cycles_untouched": not bool(
            cycles_roughness
            and any(link.from_node == roughness for link in cycles_roughness.links)
        ),
    }


def _exercise_cycles_cleanup(materials: list[bpy.types.Material]) -> dict:
    """Verify the destructive utility deletes only tagged Cycles graphs."""
    rubber = bpy.data.materials.get("OCTANIFY_FIDELITY_rubber")
    untagged_names = {
        "OCTANIFY_AUTOCONNECT_roughness",
        "OCTANIFY_AUTOCONNECT_height",
    }
    cycles_before = sum(
        1
        for material in materials
        for node in material.node_tree.nodes
        if node.get("octanify_graph", "") == "cycles"
    )
    operator_result = sorted(bpy.ops.octanify.delete_cycles_nodes())
    cycles_after = sum(
        1
        for material in materials
        for node in material.node_tree.nodes
        if node.get("octanify_graph", "") == "cycles"
    )
    octane_graphs_preserved = all(
        any(
            node.get("octanify_graph", "") == "octane"
            for node in material.node_tree.nodes
        )
        for material in materials
    )
    octane_outputs_active = all(
        any(
            node.bl_idname == "ShaderNodeOutputMaterial"
            and node.get("octanify_graph", "") == "octane"
            and bool(getattr(node, "is_active_output", False))
            for node in material.node_tree.nodes
        )
        for material in materials
    )
    remaining_untagged = {
        node.name for node in rubber.node_tree.nodes
        if node.name in untagged_names
    } if rubber is not None else set()
    return {
        "operator_result": operator_result,
        "cycles_before": cycles_before,
        "cycles_after": cycles_after,
        "octane_graphs_preserved": octane_graphs_preserved,
        "octane_outputs_active": octane_outputs_active,
        "untagged_nodes_preserved": remaining_untagged == untagged_names,
    }


def main() -> None:
    args = _args()
    hierarchy_parent = bpy.data.objects.new("OCTANIFY_FIXTURE_hierarchy_root", None)
    bpy.context.scene.collection.objects.link(hierarchy_parent)
    hierarchy_child = bpy.data.objects.get("OCTANIFY_FIXTURE_mix_material_object")
    hierarchy_child.parent = hierarchy_parent
    bpy.ops.object.select_all(action="DESELECT")
    hierarchy_parent.select_set(True)
    bpy.context.view_layer.objects.active = hierarchy_parent
    from octanify.ui.operators import _active_hierarchy_objects
    hierarchy_targets = _active_hierarchy_objects(bpy.context)
    hierarchy_ok = any(obj.name == hierarchy_child.name for obj in hierarchy_targets)

    bpy.context.scene.octanify_batch_mode = "ALL"
    bpy.context.scene.octanify_base_material = "STANDARD_SURFACE"
    result = bpy.ops.octanify.convert()
    if "FINISHED" not in result:
        raise SystemExit("Octanify conversion operator did not finish")

    from octanify.core.report import report_data

    auto_connect = _exercise_auto_connect()

    materials = [
        material for material in bpy.data.materials
        if material.name.startswith(("OCTANIFY_FIXTURE_", "OCTANIFY_FIDELITY_"))
        and bool(material.get("octanify_converted", False))
    ]
    results = [_material_result(material) for material in materials]
    cycles_cleanup = _exercise_cycles_cleanup(materials)
    payload = {
        "materials": results,
        "report": {
            "materials_converted": report_data.materials_converted,
            "nodes_translated": report_data.nodes_translated,
            "nodes_unsupported": report_data.nodes_unsupported,
            "links_created": report_data.links_created,
            "links_failed": report_data.links_failed,
            "approximations": list(report_data.approximations),
            "warnings": list(report_data.warnings),
        },
        "hierarchy_selection_ok": hierarchy_ok,
        "auto_connect": auto_connect,
        "cycles_cleanup": cycles_cleanup,
        "progress": bpy.context.scene.octanify_progress,
        "progress_widget": (
            "native_bar"
            if hasattr(bpy.types.UILayout, "progress")
            else "slider_bar"
        ),
    }

    output_path = os.path.abspath(args.json)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    failures = []
    if not hierarchy_ok:
        failures.append("active hierarchy collection did not include the child mesh")
    if auto_connect["operator_result"] != ["FINISHED"]:
        failures.append(f"auto-connect operator did not finish: {auto_connect}")
    if not auto_connect["roughness_to_octane"]:
        failures.append(f"auto-connect missed Octane roughness: {auto_connect}")
    if not auto_connect["displacement_to_octane"]:
        failures.append(f"auto-connect missed Octane displacement: {auto_connect}")
    if not auto_connect["cycles_untouched"]:
        failures.append(f"auto-connect modified the Cycles graph: {auto_connect}")
    if cycles_cleanup["operator_result"] != ["FINISHED"]:
        failures.append(f"Cycles cleanup operator did not finish: {cycles_cleanup}")
    if cycles_cleanup["cycles_before"] == 0 or cycles_cleanup["cycles_after"] != 0:
        failures.append(f"Cycles cleanup did not remove tagged nodes: {cycles_cleanup}")
    if not cycles_cleanup["octane_graphs_preserved"]:
        failures.append(f"Cycles cleanup removed an Octane graph: {cycles_cleanup}")
    if not cycles_cleanup["octane_outputs_active"]:
        failures.append(f"Cycles cleanup did not activate Octane outputs: {cycles_cleanup}")
    if not cycles_cleanup["untagged_nodes_preserved"]:
        failures.append(f"Cycles cleanup removed untagged user nodes: {cycles_cleanup}")
    if bpy.context.scene.octanify_progress != 100:
        failures.append(
            f"conversion progress did not reach 100: {bpy.context.scene.octanify_progress}"
        )
    if bpy.context.scene.octanify_progress_active:
        failures.append("conversion progress remained active after completion")
    expected_materials = 10
    if len(materials) != expected_materials:
        failures.append(
            f"expected {expected_materials} converted fixture materials, got {len(materials)}"
        )
    for material in results:
        if material["unlinked_outputs"]:
            failures.append(
                f"{material['name']} has unlinked outputs: {material['unlinked_outputs']}"
            )
        output_targets = material["output_targets"]
        if "ALL" not in output_targets:
            failures.append(
                f"{material['name']} has no Octane-compatible ALL output: {output_targets}"
            )
        if "CYCLES" not in output_targets:
            failures.append(
                f"{material['name']} lost its Cycles-compatible output"
            )
        engine_outputs = material["engine_outputs"]
        if engine_outputs["cycles"].get("root_graph") != "cycles":
            failures.append(
                f"{material['name']} Cycles selected the wrong graph: {engine_outputs}"
            )
        if engine_outputs["octane"].get("root_graph") != "octane":
            failures.append(
                f"{material['name']} Octane selected the wrong graph: {engine_outputs}"
            )
        cycles_root_type = material["engine_outputs"]["cycles"].get("root_type")
        needs_principled_target = cycles_root_type in {
            "ShaderNodeBsdfPrincipled",
            "ShaderNodeMixShader",
        }
        if (
            needs_principled_target
            and material["material_type"] != "OctaneStandardSurfaceMaterial"
        ):
            failures.append(
                f"{material['name']} did not use modern Standard Surface: "
                f"{material['material_type']}"
            )
        if material["name"].startswith("OCTANIFY_FIXTURE_complex_pbr"):
            mapping = material["mapping_state"]
            if mapping.get("transform_type") != "Octane3DTransformation":
                failures.append(f"complex PBR Mapping did not feed UV transform: {mapping}")
            if mapping.get("projection_type") != "OctaneMeshUVProjection":
                failures.append(f"complex PBR coordinates did not feed Projection: {mapping}")
            if mapping.get("rotation_order") != "XYZ":
                failures.append(f"complex PBR Mapping rotation order is wrong: {mapping}")
            rotation = mapping.get("rotation")
            if not rotation or abs(rotation[2] - 30.0) > 1e-5:
                failures.append(f"complex PBR Mapping angle was not converted to degrees: {mapping}")
            style = material["style_state"]
            if style["cycles_nodes"] == 0 or style["octane_nodes"] == 0:
                failures.append(f"smart graph theme tags are missing: {style}")
            elif style["octane_left"] - style["cycles_right"] < 500.0:
                failures.append(f"Cycles and Octane graphs are not separated: {style}")
            expected_colors = {
                "cycles_color": [0.16, 0.20, 0.27],
                "octane_color": [0.07, 0.30, 0.25],
            }
            for color_name, expected_color in expected_colors.items():
                actual_color = style.get(color_name, [])
                if len(actual_color) != 3 or any(
                    abs(actual - expected) > 1e-6
                    for actual, expected in zip(actual_color, expected_color)
                ):
                    failures.append(
                        f"complex PBR {color_name} is wrong: {actual_color}"
                    )
        if material["name"].startswith("OCTANIFY_FIXTURE_mix_material"):
            mix = material["mix_state"]
            if mix.get("type") != "OctaneMixMaterial":
                failures.append(f"Mix Shader did not create modern Mix Material: {mix}")
            if not mix.get("first_linked") or not mix.get("second_linked"):
                failures.append(f"Mix Material branches are not both linked: {mix}")
        if material["name"].startswith("OCTANIFY_FIXTURE_multiple_outputs"):
            cycles_output = material["engine_outputs"]["cycles"]
            if cycles_output.get("name") != "Secondary Output":
                failures.append(
                    "Multiple-output material did not preserve the authored "
                    f"active Cycles output: {cycles_output}"
                )
            if material["active_outputs"] != ["Primary Output"]:
                failures.append(
                    "Multiple-output material did not preserve the authored global "
                    f"output activity: {material['active_outputs']}"
                )
            octane_output = material["engine_outputs"]["octane"]
            if octane_output.get("root_label") != "Secondary Principled":
                failures.append(
                    "Multiple-output material converted the wrong active branch: "
                    f"{octane_output}"
                )
            if material["output_targets"].count("ALL") != 1:
                failures.append(
                    "Multiple-output material must create exactly one active-path "
                    f"Octane output: {material['output_targets']}"
                )
        expected_by_prefix = {
            "OCTANIFY_FIXTURE_grouped": {
                "Base weight": 1.0,
                "Specular roughness": 0.5,
                "Specular weight": 1.0,
                "Coating weight": 0.0,
                "Sheen weight": 0.0,
            },
            "OCTANIFY_FIDELITY_hard_plastic": {
                "Metalness": 0.0,
                "Specular roughness": 0.22,
                "Specular weight": 1.0,
                "Coating weight": 0.08,
                "Coating color": [1.0, 1.0, 1.0],
                "Sheen weight": 0.0,
            },
            "OCTANIFY_FIDELITY_rubber": {
                "Metalness": 0.0,
                "Specular roughness": 0.75,
                "Specular weight": 0.5,
                "Coating weight": 0.0,
                "Sheen weight": 0.0,
            },
            "OCTANIFY_FIDELITY_fabric": {
                "Metalness": 0.0,
                "Specular roughness": 0.88,
                "Specular weight": 0.4,
                "Coating weight": 0.0,
                "Sheen weight": 0.35,
                "Sheen color": [0.8, 0.5, 0.3],
                "Sheen roughness": 0.65,
            },
            "OCTANIFY_FIDELITY_metal": {
                "Metalness": 1.0,
                "Specular roughness": 0.28,
                "Specular weight": 1.0,
                "Coating weight": 0.0,
                "Sheen weight": 0.0,
            },
        }
        expected = next(
            (
                values for prefix, values in expected_by_prefix.items()
                if material["name"].startswith(prefix)
            ),
            None,
        )
        if expected is not None:
            state = material["material_state"]
            for key, expected_value in expected.items():
                actual = state.get(key)
                if isinstance(expected_value, float):
                    matches = actual is not None and abs(actual - expected_value) < 1e-6
                elif isinstance(expected_value, list) and isinstance(actual, list):
                    matches = len(actual) == len(expected_value) and all(
                        abs(a - b) < 1e-6 for a, b in zip(actual, expected_value)
                    )
                else:
                    matches = actual == expected_value
                if not matches:
                    failures.append(
                        f"{material['name']} {key}: expected {expected_value}, got {actual}"
                    )
    if report_data.links_failed:
        failures.append(f"conversion report contains {report_data.links_failed} failed links")

    print(json.dumps(payload, indent=2))
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
