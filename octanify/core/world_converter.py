"""Octanify — Cycles World to Octane environment conversion."""

from __future__ import annotations

import math
from typing import Any

import bpy

from .layout_engine import style_smart_graphs
from ..utils.logger import get_logger


log = get_logger(__name__)

_GENERATED_NODE_TAG = "octanify_world_conversion"
_SOURCE_BACKGROUND_TAG = "octanify_world_source_background"
_WORLD_SIGNATURE_TAG = "octanify_world_source_signature"


def _socket(collection: Any, name: str):
    getter = getattr(collection, "get", None)
    if callable(getter):
        return getter(name)
    return next(
        (candidate for candidate in collection if getattr(candidate, "name", "") == name),
        None,
    )


def _node_tag(node: bpy.types.Node, name: str, default: bool = False) -> bool:
    getter = getattr(node, "get", None)
    if not callable(getter):
        return default
    try:
        return bool(getter(name, default))
    except (AttributeError, TypeError):
        return default


def _tag_node(node: bpy.types.Node, name: str) -> None:
    try:
        node[name] = True
    except (AttributeError, TypeError):
        pass


def _linked_source(socket: bpy.types.NodeSocket | None):
    if socket is None:
        return None
    links = getattr(socket, "links", ())
    return links[0].from_node if links else None


def _source_through_reroutes(socket: bpy.types.NodeSocket | None):
    source = _linked_source(socket)
    seen: set[int] = set()
    while source is not None and getattr(source, "bl_idname", "") == "NodeReroute":
        identity = _rna_identity(source)
        if identity in seen:
            raise ValueError("World node graph contains a reroute cycle")
        seen.add(identity)
        inputs = list(getattr(source, "inputs", ()))
        source = _linked_source(inputs[0]) if inputs else None
    return source


def _active_world_output(node_tree: bpy.types.NodeTree):
    outputs = [
        node
        for node in node_tree.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputWorld"
    ]
    return next(
        (node for node in outputs if getattr(node, "is_active_output", False)),
        outputs[0] if outputs else None,
    )


def _background_from_output(node_tree: bpy.types.NodeTree):
    output = _active_world_output(node_tree)
    if output is not None:
        surface = _socket(output.inputs, "Surface")
        source = _source_through_reroutes(surface)
        if source is not None:
            if getattr(source, "bl_idname", "") == "ShaderNodeBackground":
                return source
            raise ValueError(
                "World Output Surface must be directly fed by a Background node"
            )
    for node in node_tree.nodes:
        if (
            getattr(node, "bl_idname", "") == "ShaderNodeBackground"
            and _node_tag(node, _SOURCE_BACKGROUND_TAG)
        ):
            return node
    return next(
        (
            node
            for node in node_tree.nodes
            if getattr(node, "bl_idname", "") == "ShaderNodeBackground"
        ),
        None,
    )


def _ensure_cycles_world_output(
    node_tree: bpy.types.NodeTree,
    background: bpy.types.Node,
) -> bpy.types.Node | None:
    """Restore the output removed by the older destructive World converter."""
    existing = _active_world_output(node_tree)
    if existing is not None:
        return existing
    try:
        output = node_tree.nodes.new("ShaderNodeOutputWorld")
        output.name = "Cycles World Output (Restored)"
        output.label = "Restored by Octanify"
        output.target = "CYCLES"
        background_x, background_y = getattr(background, "location", (0.0, 0.0))
        output.location = (float(background_x) + 320.0, float(background_y))
        _link(node_tree, background, "Background", output, "Surface")
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None
    log.info("Restored the authored Cycles World Output")
    return output


def _environment_texture(background: bpy.types.Node):
    color_input = _socket(background.inputs, "Color")
    source = _source_through_reroutes(color_input)
    if source is None or getattr(source, "bl_idname", "") == "ShaderNodeRGB":
        return None
    if getattr(source, "bl_idname", "") == "ShaderNodeTexEnvironment":
        return source
    raise ValueError(
        "Background Color must be flat or directly fed by one "
        "Environment Texture"
    )


def _constant_color(background: bpy.types.Node) -> tuple[float, float, float]:
    color_input = _socket(background.inputs, "Color")
    source = _source_through_reroutes(color_input)
    value = getattr(color_input, "default_value", (0.0, 0.0, 0.0, 1.0))
    if source is not None and getattr(source, "bl_idname", "") == "ShaderNodeRGB":
        output = _socket(source.outputs, "Color")
        if output is not None and hasattr(output, "default_value"):
            value = output.default_value
    return tuple(float(component) for component in value[:3])


def _constant_strength(background: bpy.types.Node) -> float:
    strength = _socket(background.inputs, "Strength")
    source = _source_through_reroutes(strength)
    if source is not None and getattr(source, "bl_idname", "") == "ShaderNodeValue":
        output = _socket(source.outputs, "Value")
        if output is not None and hasattr(output, "default_value"):
            return float(output.default_value)
    if strength is None or not hasattr(strength, "default_value"):
        return 1.0
    if source is not None:
        raise ValueError(
            "Background Strength must be a constant or Value node"
        )
    return float(strength.default_value)


def _mapping_rotation(environment_texture: bpy.types.Node) -> tuple[float, float, float]:
    vector = _socket(environment_texture.inputs, "Vector")
    source = _source_through_reroutes(vector)
    if source is None or getattr(source, "bl_idname", "") == "ShaderNodeTexCoord":
        return (0.0, 0.0, 0.0)
    if getattr(source, "bl_idname", "") != "ShaderNodeMapping":
        raise ValueError(
            "Environment Texture Vector must be unconnected or directly fed "
            "by a Mapping node"
        )
    rotation = _socket(source.inputs, "Rotation")
    if rotation is None or not hasattr(rotation, "default_value"):
        return (0.0, 0.0, 0.0)
    if getattr(rotation, "links", ()):
        raise ValueError("Mapping Rotation must be a constant value")
    return tuple(float(component) for component in rotation.default_value[:3])


def _new_node(
    node_tree: bpy.types.NodeTree,
    bl_idname: str,
    location: tuple[float, float],
) -> bpy.types.Node:
    try:
        node = node_tree.nodes.new(bl_idname)
    except (RuntimeError, TypeError) as exc:
        raise RuntimeError(
            f"Octane node '{bl_idname}' is unavailable; enable the "
            "OctaneRender for Blender add-on"
        ) from exc
    try:
        node.location = location
    except (AttributeError, TypeError):
        pass
    _tag_node(node, _GENERATED_NODE_TAG)
    return node


def _set_input(node: bpy.types.Node, name: str, value: Any) -> None:
    socket = _socket(node.inputs, name)
    if socket is None or not hasattr(socket, "default_value"):
        raise RuntimeError(f"{node.bl_idname} is missing input '{name}'")
    socket.default_value = value


def _link(
    node_tree: bpy.types.NodeTree,
    from_node: bpy.types.Node,
    from_socket_name: str,
    to_node: bpy.types.Node,
    to_socket_name: str,
) -> None:
    from_socket = _socket(from_node.outputs, from_socket_name)
    to_socket = _socket(to_node.inputs, to_socket_name)
    if from_socket is None or to_socket is None:
        raise RuntimeError(
            f"Cannot link {from_node.bl_idname}.{from_socket_name} to "
            f"{to_node.bl_idname}.{to_socket_name}"
        )
    for existing_link in list(getattr(to_socket, "links", ())):
        try:
            node_tree.links.remove(existing_link)
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    node_tree.links.new(from_socket, to_socket)


def _same_rna_data(first: Any, second: Any) -> bool:
    if first is second:
        return True
    first_pointer = getattr(first, "as_pointer", None)
    second_pointer = getattr(second, "as_pointer", None)
    if callable(first_pointer) and callable(second_pointer):
        try:
            return first_pointer() == second_pointer()
        except (ReferenceError, RuntimeError, TypeError):
            return False
    return False


def _rna_identity(value: Any) -> int:
    pointer = getattr(value, "as_pointer", None)
    if callable(pointer):
        try:
            return int(pointer())
        except (ReferenceError, RuntimeError, TypeError):
            pass
    return id(value)


def _remove_replaced_nodes(
    node_tree: bpy.types.NodeTree,
    keep_nodes: list[bpy.types.Node],
) -> None:
    """Remove only Octanify's previous environment graph."""
    keep_ids = {_rna_identity(node) for node in keep_nodes}
    for node in list(node_tree.nodes):
        if _rna_identity(node) in keep_ids:
            continue
        if _node_tag(node, _GENERATED_NODE_TAG):
            node_tree.nodes.remove(node)


def _prepare_world_outputs(
    node_tree: bpy.types.NodeTree,
) -> list[tuple[bpy.types.Node, Any, Any]]:
    """Target authored Blender World outputs to Cycles without unlinking them."""
    state: list[tuple[bpy.types.Node, Any, Any]] = []
    for node in node_tree.nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeOutputWorld":
            continue
        state.append((
            node,
            getattr(node, "target", None),
            getattr(node, "is_active_output", None),
        ))
        try:
            node.target = "CYCLES"
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    return state


def _restore_world_outputs(state: list[tuple[bpy.types.Node, Any, Any]]) -> None:
    for node, target, is_active in state:
        if target is not None:
            try:
                node.target = target
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                pass
        if is_active is not None:
            try:
                node.is_active_output = is_active
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass


def _rollback_new_nodes(
    node_tree: bpy.types.NodeTree,
    existing_node_ids: set[int],
) -> None:
    for node in list(node_tree.nodes):
        if _rna_identity(node) not in existing_node_ids and _node_tag(
            node,
            _GENERATED_NODE_TAG,
        ):
            node_tree.nodes.remove(node)


def _notify_octane_tree(
    node_tree: bpy.types.NodeTree,
    owner: bpy.types.World,
) -> None:
    """Validate custom Octane links when its add-on API is available."""
    try:
        from octane.nodes.base_node_tree import OctaneBaseNodeTree
    except ImportError:
        OctaneBaseNodeTree = None
    if OctaneBaseNodeTree is not None:
        try:
            OctaneBaseNodeTree.update_link_validity(node_tree, owner, None)
        except Exception as exc:  # Octane versions expose different tree mixins.
            log.debug("Octane World link validation deferred: %s", exc)
    try:
        node_tree.update_tag()
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        owner.update_tag()
    except (AttributeError, RuntimeError, TypeError):
        pass


def _validate_environment_graph(
    output: bpy.types.Node,
    environment: bpy.types.Node,
) -> None:
    socket = _socket(output.inputs, "Environment")
    links = list(getattr(socket, "links", ())) if socket is not None else []
    if not any(_same_rna_data(link.from_node, environment) for link in links):
        raise RuntimeError(
            "Generated Octane environment is not linked to World Output"
        )


def _image_path(image: bpy.types.Image | None) -> str:
    if image is None:
        return ""
    path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
    if not path:
        return ""
    try:
        return bpy.path.abspath(path)
    except (AttributeError, RuntimeError, TypeError):
        return path


def _assign_image(node: bpy.types.Node, image: bpy.types.Image) -> str:
    path = _image_path(image)
    assigned = False
    try:
        node.image = image
        assigned = True
    except (AttributeError, TypeError):
        pass
    if hasattr(node, "a_filename") and path:
        try:
            node.a_filename = path
            assigned = True
        except (AttributeError, TypeError):
            pass
    if not assigned:
        raise RuntimeError("Octane RGB Image cannot receive the source image")
    return path


def _image_gamma(image: bpy.types.Image) -> float:
    colorspace = getattr(getattr(image, "colorspace_settings", None), "name", "")
    linear_names = {"Non-Color", "Linear", "Raw", "Utility - Raw"}
    if colorspace in linear_names or colorspace.lower().startswith("linear"):
        return 1.0
    return 2.2


def _world_source_signature(node_tree: bpy.types.NodeTree) -> str:
    """Return a stable signature for values that affect conversion output."""
    background = _background_from_output(node_tree)
    if background is None:
        return ""
    environment_texture = _environment_texture(background)
    image = (
        getattr(environment_texture, "image", None)
        if environment_texture is not None
        else None
    )
    rotation = (
        _mapping_rotation(environment_texture)
        if environment_texture is not None
        else (0.0, 0.0, 0.0)
    )
    return repr((
        "HDRI" if environment_texture is not None else "FLAT",
        _constant_color(background),
        _constant_strength(background),
        getattr(image, "name", ""),
        _image_path(image),
        rotation,
    ))


def world_needs_octane_conversion(world: bpy.types.World | None) -> bool:
    """Return whether the World lacks a current generated Octane branch."""
    if world is None:
        return False
    if not bool(getattr(world, "use_nodes", False)):
        return True
    node_tree = getattr(world, "node_tree", None)
    if node_tree is None:
        return True
    output = next(
        (
            node for node in node_tree.nodes
            if _node_tag(node, _GENERATED_NODE_TAG)
            and getattr(node, "bl_idname", "") == "OctaneEditorWorldOutputNode"
        ),
        None,
    )
    environment_socket = (
        _socket(output.inputs, "Environment") if output is not None else None
    )
    if not getattr(environment_socket, "links", ()):
        return True
    try:
        signature = _world_source_signature(node_tree)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return True
    getter = getattr(world, "get", None)
    stored = getter(_WORLD_SIGNATURE_TAG, "") if callable(getter) else ""
    return not signature or str(stored) != signature


def convert_world_to_octane(
    world: bpy.types.World,
    auto_arrange: bool = True,
    color_nodes: bool = True,
) -> dict[str, Any]:
    """Convert a Cycles Background World into an Octane environment graph."""
    if world is None:
        raise ValueError("No active World datablock")
    previous_use_nodes = bool(getattr(world, "use_nodes", False))
    try:
        world.use_nodes = True
    except (AttributeError, RuntimeError, TypeError) as exc:
        raise RuntimeError("The World datablock cannot create a node tree") from exc
    node_tree = getattr(world, "node_tree", None)
    if node_tree is None:
        try:
            world.use_nodes = previous_use_nodes
        except (AttributeError, RuntimeError, TypeError):
            pass
        raise RuntimeError("The World datablock has no node tree")

    background = _background_from_output(node_tree)
    if background is None:
        raise ValueError("World Output is not fed by a Background node")
    _tag_node(background, _SOURCE_BACKGROUND_TAG)
    _ensure_cycles_world_output(node_tree, background)

    flat_color = _constant_color(background)
    strength = _constant_strength(background)
    environment_texture = _environment_texture(background)
    image = (
        getattr(environment_texture, "image", None)
        if environment_texture is not None
        else None
    )
    if environment_texture is not None and image is None:
        raise ValueError("Environment Texture has no image assigned")
    mapping_rotation = (
        _mapping_rotation(environment_texture)
        if environment_texture is not None
        else (0.0, 0.0, 0.0)
    )
    source_signature = _world_source_signature(node_tree)

    existing_node_ids = {_rna_identity(node) for node in node_tree.nodes}
    original_nodes = [
        node
        for node in node_tree.nodes
        if not _node_tag(node, _GENERATED_NODE_TAG)
    ]
    output_state = _prepare_world_outputs(node_tree)
    created: list[bpy.types.Node] = []
    image_path = ""
    octane_rotation = (0.0, 0.0, 0.0)
    source_kind = "FLAT"
    try:
        output = _new_node(
            node_tree,
            "OctaneEditorWorldOutputNode",
            (650.0, 0.0),
        )
        created.append(output)
        environment = _new_node(
            node_tree,
            "OctaneTextureEnvironment",
            (390.0, 0.0),
        )
        created.append(environment)
        _set_input(environment, "Power", strength)
        _link(node_tree, environment, "Environment out", output, "Environment")

        if environment_texture is None:
            _set_input(environment, "Texture", flat_color)
        else:
            source_kind = "HDRI"
            image_node = _new_node(node_tree, "OctaneRGBImage", (80.0, 0.0))
            created.append(image_node)
            image_path = _assign_image(image_node, image)
            _set_input(image_node, "Legacy gamma", _image_gamma(image))
            _link(node_tree, image_node, "Texture out", environment, "Texture")

            z_rotation = mapping_rotation[2]
            spherical = _new_node(node_tree, "OctaneSpherical", (-185.0, -115.0))
            created.append(spherical)
            _link(node_tree, spherical, "Projection out", image_node, "Projection")
            if abs(z_rotation) > 1.0e-9:
                transform = _new_node(
                    node_tree,
                    "Octane3DTransformation",
                    (-440.0, -115.0),
                )
                created.append(transform)
                # Blender World Mapping rotates around Z (up).  Octane's
                # environment Spherical projection uses Y as its vertical axis.
                octane_rotation = (0.0, math.degrees(z_rotation), 0.0)
                _set_input(transform, "Rotation order", "XYZ")
                _set_input(transform, "Rotation", octane_rotation)
                _link(
                    node_tree,
                    transform,
                    "Transform out",
                    spherical,
                    "Sphere transformation",
                )
        _validate_environment_graph(output, environment)
    except Exception:
        _rollback_new_nodes(node_tree, existing_node_ids)
        _restore_world_outputs(output_state)
        try:
            world.use_nodes = previous_use_nodes
        except (AttributeError, RuntimeError, TypeError):
            pass
        raise

    # Only retire the previous generated Octane graph once a complete new
    # environment exists. The authored Cycles output and links remain intact.
    _remove_replaced_nodes(node_tree, created)

    # Octane 31.9 on Blender 5.1 raises from its active-output callback because
    # ShaderNodeTree no longer accepts the add-on's dynamic RNA attribute.  New
    # Octane World outputs are active by default, so only invoke the callback
    # when a version actually creates an inactive node.
    if not bool(getattr(output, "active", True)):
        try:
            output.active = True
        except (AttributeError, RuntimeError, TypeError):
            pass
    style_smart_graphs(
        node_tree,
        original_nodes,
        created,
        auto_arrange=auto_arrange,
        colorize=color_nodes,
    )
    try:
        world["octanify_world_converted"] = True
        world[_WORLD_SIGNATURE_TAG] = source_signature
    except (AttributeError, TypeError):
        pass
    _notify_octane_tree(node_tree, world)

    result: dict[str, Any] = {
        "world_name": getattr(world, "name", ""),
        "source_kind": source_kind,
        "source_color": flat_color,
        "source_strength": strength,
        "image_name": getattr(image, "name", "") if image is not None else "",
        "image_path": image_path,
        "mapping_rotation_radians": mapping_rotation,
        "mapping_z_rotation_degrees": math.degrees(mapping_rotation[2]),
        "octane_rotation_degrees": octane_rotation,
        "node_types": [node.bl_idname for node in created],
        "conversion_verified": True,
    }
    if source_kind == "HDRI":
        log.info(
            "Converted World '%s' HDRI '%s' at strength %.6g and Z rotation %.3f°",
            result["world_name"],
            result["image_name"] or result["image_path"],
            strength,
            result["mapping_z_rotation_degrees"],
        )
    else:
        log.info(
            "Converted World '%s' flat color %s at strength %.6g",
            result["world_name"],
            flat_color,
            strength,
        )
    return result
