"""A/B probe for fresh versus Principled-populated Octane Universal nodes.

Run inside Blender with the Octane add-on enabled.  The probe deliberately
creates both Octane nodes through the same ``nodes.new`` API; any difference
afterwards is therefore caused by Octanify's property transfer rather than by
different node construction paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import bpy

from octanify.core.property_mapper import transfer_properties
from octanify.core.shader_detection import analyze_tree


OUTPUT_PATH = Path(
    os.environ.get(
        "OCTANIFY_PROBE_OUTPUT",
        str(Path.cwd() / "octanify-universal-ab.json"),
    )
)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "to_list"):
        return [_json_value(item) for item in value.to_list()]
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return [_json_value(item) for item in value]
        except TypeError:
            pass
    return repr(value)


def _socket_state(node: bpy.types.Node) -> dict[str, Any]:
    result = {}
    for socket in node.inputs:
        result[socket.name] = {
            "identifier": getattr(socket, "identifier", socket.name),
            "type": socket.bl_idname,
            "default": _json_value(getattr(socket, "default_value", None)),
            "enabled": bool(getattr(socket, "enabled", True)),
            "hide": bool(getattr(socket, "hide", False)),
            "hide_value": bool(getattr(socket, "hide_value", False)),
            "linked": bool(getattr(socket, "is_linked", False)),
        }
    return result


def _rna_state(node: bpy.types.Node) -> dict[str, Any]:
    ignored = {
        "rna_type", "inputs", "outputs", "internal_links", "id_data",
        "dimensions", "location", "name", "label", "select", "width",
        "width_hidden", "height", "parent", "color", "use_custom_color",
    }
    result = {}
    for prop in node.bl_rna.properties:
        if prop.identifier in ignored or prop.is_readonly:
            continue
        try:
            result[prop.identifier] = _json_value(getattr(node, prop.identifier))
        except (AttributeError, RuntimeError, TypeError):
            continue
    return result


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            result[key] = {"fresh": before.get(key), "converted": after.get(key)}
    return result


def main() -> None:
    cycles_material = bpy.data.materials.new("Octanify_AB_Cycles")
    cycles_material.use_nodes = True
    cycles_tree = cycles_material.node_tree
    cycles_tree.nodes.clear()
    principled = cycles_tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Default Principled"
    analysis = analyze_tree(cycles_tree)
    info = analysis.nodes[principled.name]

    octane_material = bpy.data.materials.new("Octanify_AB_Octane")
    octane_material.use_nodes = True
    octane_tree = octane_material.node_tree
    octane_tree.nodes.clear()
    fresh = octane_tree.nodes.new("OctaneUniversalMaterial")
    populated = octane_tree.nodes.new("OctaneUniversalMaterial")

    fresh_sockets = _socket_state(fresh)
    fresh_rna = _rna_state(fresh)
    transfer_properties(info, populated)
    populated_sockets = _socket_state(populated)
    populated_rna = _rna_state(populated)

    socket_diff = _diff(fresh_sockets, populated_sockets)
    rna_diff = _diff(fresh_rna, populated_rna)
    payload = {
        "blender_version": bpy.app.version_string,
        "octane_node_type": fresh.bl_idname,
        "source_principled_inputs": {
            socket.name: _json_value(getattr(socket, "default_value", None))
            for socket in principled.inputs
        },
        "fresh_sockets": fresh_sockets,
        "converted_sockets": populated_sockets,
        "socket_diff": socket_diff,
        "rna_diff": rna_diff,
        "custom_property_diff": _diff(dict(fresh.items()), dict(populated.items())),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("OCTANIFY_UNIVERSAL_AB", json.dumps({
        "socket_diff": socket_diff,
        "rna_diff": rna_diff,
        "output": str(OUTPUT_PATH),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
