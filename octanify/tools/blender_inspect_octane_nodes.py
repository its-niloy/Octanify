"""Print installed Octane node class IDs and sockets for compatibility work."""

from __future__ import annotations

import json

import addon_utils
import bpy


TERMS = (
    "gradient",
    "normal",
    "bump",
    "absorption",
    "scatter",
    "channelpicker",
    "channelmerger",
    "emission",
    "portal",
)


def main() -> None:
    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=False, persistent=False)
    matches = []
    material = bpy.data.materials.new("OCTANIFY_NODE_INSPECTION")
    material.use_nodes = True
    tree = material.node_tree

    for type_name in dir(bpy.types):
        if not any(term in type_name.lower() for term in TERMS):
            continue
        node_type = getattr(bpy.types, type_name, None)
        try:
            if not isinstance(node_type, type) or not issubclass(node_type, bpy.types.Node):
                continue
        except TypeError:
            continue

        bl_idname = getattr(node_type, "bl_idname", "") or type_name
        record = {
            "rna_name": type_name,
            "bl_idname": bl_idname,
            "bl_label": getattr(node_type, "bl_label", ""),
            "inputs": [],
            "outputs": [],
        }
        try:
            node = tree.nodes.new(type=bl_idname)
            record["inputs"] = [socket.name for socket in node.inputs]
            record["outputs"] = [socket.name for socket in node.outputs]
            tree.nodes.remove(node)
        except Exception as exc:
            record["creation_error"] = str(exc)
        matches.append(record)

    print("OCTANIFY_NODE_INSPECTION_BEGIN")
    print(json.dumps(matches, indent=2))
    print("OCTANIFY_NODE_INSPECTION_END")


if __name__ == "__main__":
    main()
