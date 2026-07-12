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
    universal = next(
        (
            node for node in tree.nodes
            if node.bl_idname in (
                "OctaneUniversalMaterial",
                "ShaderNodeOctUniversalMat",
            )
        ),
        None,
    )
    universal_state = {}
    if universal is not None:
        for name in (
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
            socket = universal.inputs.get(name)
            if socket is not None and hasattr(socket, "default_value"):
                value = socket.default_value
                universal_state[name] = (
                    list(value)
                    if hasattr(value, "__len__") and not isinstance(value, str)
                    else value
                )
    return {
        "name": material.name,
        "nodes": len(tree.nodes),
        "links": len(tree.links),
        "unsupported": unsupported,
        "unlinked_outputs": unlinked_outputs,
        "universal_state": universal_state,
    }


def main() -> None:
    args = _args()
    bpy.context.scene.octanify_batch_mode = "ALL"
    result = bpy.ops.octanify.convert()
    if "FINISHED" not in result:
        raise SystemExit("Octanify conversion operator did not finish")

    from octanify.core.report import report_data

    materials = [
        material for material in bpy.data.materials
        if material.name.startswith(("OCTANIFY_FIXTURE_", "OCTANIFY_FIDELITY_"))
        and bool(material.get("octanify_converted", False))
    ]
    results = [_material_result(material) for material in materials]
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
    }

    output_path = os.path.abspath(args.json)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    failures = []
    expected_materials = 8
    if len(materials) != expected_materials:
        failures.append(
            f"expected {expected_materials} converted fixture materials, got {len(materials)}"
        )
    for material in results:
        if material["unlinked_outputs"]:
            failures.append(
                f"{material['name']} has unlinked outputs: {material['unlinked_outputs']}"
            )
        expected_by_prefix = {
            "OCTANIFY_FIXTURE_grouped": {
                "Roughness": 0.5,
                "Specular": 1.0,
                "Coating": [0.0, 0.0, 0.0],
                "Sheen": [0.0, 0.0, 0.0],
                "BSDF model": "Octane",
            },
            "OCTANIFY_FIDELITY_hard_plastic": {
                "Metallic": 0.0,
                "Roughness": 0.22,
                "Specular": 1.0,
                "Coating": [0.08, 0.08, 0.08],
                "Sheen": [0.0, 0.0, 0.0],
            },
            "OCTANIFY_FIDELITY_rubber": {
                "Metallic": 0.0,
                "Roughness": 0.75,
                "Specular": 0.5,
                "Coating": [0.0, 0.0, 0.0],
                "Sheen": [0.0, 0.0, 0.0],
            },
            "OCTANIFY_FIDELITY_fabric": {
                "Metallic": 0.0,
                "Roughness": 0.88,
                "Specular": 0.4,
                "Coating": [0.0, 0.0, 0.0],
                "Sheen": [0.28, 0.175, 0.105],
                "Sheen roughness": 0.65,
            },
            "OCTANIFY_FIDELITY_metal": {
                "Metallic": 1.0,
                "Roughness": 0.28,
                "Specular": 1.0,
                "Coating": [0.0, 0.0, 0.0],
                "Sheen": [0.0, 0.0, 0.0],
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
            state = material["universal_state"]
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
