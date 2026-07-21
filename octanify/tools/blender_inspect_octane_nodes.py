"""Print installed Octane node class IDs and sockets for compatibility work."""

from __future__ import annotations

import json

import addon_utils
import bpy


TERMS = (
    "composite",
    "noise",
    "voronoi",
    "fractal",
    "fbm",
    "cell",
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

TARGETS = (
    "OctaneCompositeTexture",
    "OctaneTexLayerTexture",
    "OctaneCompositeTextureLayer",
    "OctaneCinema4DNoise",
    "OctaneSmoothVoronoiContours",
    "OctaneCellNoise",
    "OctaneFractalNoise",
    "OctaneFBMNoise",
    "Octane3DTransformation",
    "OctaneXYZToUVW",
    "OctaneTransformValue",
)


def _value(value):
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return list(value)
        except TypeError:
            pass
    return value


def _socket_record(socket) -> dict:
    record = {
        "name": socket.name,
        "identifier": getattr(socket, "identifier", ""),
        "bl_idname": getattr(socket, "bl_idname", ""),
    }
    if hasattr(socket, "default_value"):
        record["default"] = _value(socket.default_value)
    try:
        prop = socket.bl_rna.properties.get("default_value")
        if prop is not None and prop.type == "ENUM":
            record["enum_items"] = [
                {
                    "identifier": item.identifier,
                    "name": item.name,
                    "value": item.value,
                }
                for item in prop.enum_items
            ]
    except (AttributeError, RuntimeError, TypeError):
        pass
    return record


def main() -> None:
    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=False, persistent=False)
    matches = []
    material = bpy.data.materials.new("OCTANIFY_NODE_INSPECTION")
    material.use_nodes = True
    tree = material.node_tree

    type_names = list(TARGETS)
    type_names.extend(
        type_name for type_name in dir(bpy.types)
        if any(term in type_name.lower() for term in TERMS)
        and type_name not in TARGETS
    )
    for type_name in type_names:
        node_type = getattr(bpy.types, type_name, None)
        if type_name in TARGETS:
            bl_idname = type_name
        else:
            try:
                if not isinstance(node_type, type) or not issubclass(node_type, bpy.types.Node):
                    continue
            except TypeError:
                continue
            bl_idname = getattr(node_type, "bl_idname", "") or type_name
        if not (type_name.startswith("Octane") or bl_idname.startswith("Octane")):
            continue
        record = {
            "rna_name": type_name,
            "bl_idname": bl_idname,
            "bl_label": getattr(node_type, "bl_label", ""),
            "inputs": [],
            "outputs": [],
        }
        try:
            node = tree.nodes.new(type=bl_idname)
            record["bl_idname"] = node.bl_idname
            record["bl_label"] = node.bl_label
            record["inputs"] = [_socket_record(socket) for socket in node.inputs]
            record["outputs"] = [_socket_record(socket) for socket in node.outputs]
            tree.nodes.remove(node)
        except Exception as exc:
            record["creation_error"] = str(exc)
        matches.append(record)

    print("OCTANIFY_NODE_INSPECTION_BEGIN")
    print(json.dumps(matches, indent=2))
    print("OCTANIFY_NODE_INSPECTION_END")


if __name__ == "__main__":
    main()
