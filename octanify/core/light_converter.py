"""Octanify — Blender light to Octane light conversion.

The conversion is intentionally separate from material conversion.  Blender
stores finite-light energy as radiant power, while Sun energy is irradiance;
keeping those paths separate avoids treating every light as the same unit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import bpy

from .layout_engine import style_smart_graphs
from ..utils.logger import get_logger


log = get_logger(__name__)

SUPPORTED_LIGHT_TYPES = frozenset({"POINT", "SUN", "SPOT", "AREA"})
_GENERATED_NODE_TAG = "octanify_light_conversion"
_GENERATED_GOBO_TAG = "octanify_gobo_conversion"
_GOBO_SIGNATURE_TAG = "octanify_gobo_signature"
_MAX_GOBO_TRACE_DEPTH = 200

# Octane's emission sockets document Surface Brightness power per
# 7 / pi units of emitter area.  Blender's un-normalized finite lights scale
# output with area, so this converts the reference-area convention while the
# normal (normalized) path remains a total-Watt transfer.
OCTANE_SURFACE_BRIGHTNESS_REFERENCE_AREA = 7.0 / math.pi
OCTANE_BLACKBODY_DEFAULT_EFFICIENCY = 0.025
_CYCLES_ZERO_RADIUS_POINT_AREA = 4.0


@dataclass(frozen=True)
class _PowerConvention:
    source_unit: str
    octane_unit: str
    factor: float


@dataclass(frozen=True)
class GoboInfo:
    """A statically discoverable image gobo and its authored controls."""

    image: Any
    source_node: Any
    source_kind: str
    filepath: str
    image_name: str
    colorspace: str
    channel: str = "Color"
    rotation_radians: float = 0.0
    mapping_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    mapping_translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    focus: float | None = None
    vignette: float = 0.0
    invert: bool = False
    animated: bool = False
    playback_speed: float = 1.0

    @property
    def signature(self) -> str:
        """Return a stable value used by smart reconversion detection."""
        return "|".join(
            (
                self.source_kind,
                self.filepath,
                self.image_name,
                self.channel,
                f"{self.rotation_radians:.9g}",
                ",".join(f"{value:.9g}" for value in self.mapping_scale),
                ",".join(f"{value:.9g}" for value in self.mapping_translation),
                "" if self.focus is None else f"{self.focus:.9g}",
                f"{self.vignette:.9g}",
                "1" if self.invert else "0",
                "1" if self.animated else "0",
                f"{self.playback_speed:.9g}",
            )
        )


# Octane Black Body defaults its Texture/efficiency input to 0.025.  A linked
# RGB texture replaces that literal, so bake the default efficiency into Power
# while preserving Blender's light color through the texture link.  This also
# avoids the 40x jump caused by setting the efficiency texture to white.
#
# These remain separate per-type conventions because SPOT power is the
# hypothetical unclipped source power and SUN is irradiance, not radiant power.
POWER_CONVENTIONS: dict[str, _PowerConvention] = {
    "POINT": _PowerConvention(
        "W",
        "Black Body power at 2.5% efficiency",
        OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
    ),
    "SPOT": _PowerConvention(
        "W (before cone clipping)",
        "Black Body power at 2.5% efficiency",
        OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
    ),
    "AREA": _PowerConvention(
        "W",
        "Black Body power at 2.5% efficiency",
        OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
    ),
    "SUN": _PowerConvention(
        "W/m²",
        "directional Black Body power at 2.5% efficiency",
        OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
    ),
}


def _socket(collection: Any, name: str):
    getter = getattr(collection, "get", None)
    if callable(getter):
        return getter(name)
    return next(
        (candidate for candidate in collection if getattr(candidate, "name", "") == name),
        None,
    )


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


def _socket_casefold(collection: Any, *names: str):
    wanted = {name.casefold() for name in names}
    return next(
        (
            socket
            for socket in collection
            if getattr(socket, "name", "").casefold() in wanted
        ),
        None,
    )


def _input_value(node: Any, names: tuple[str, ...], default: Any) -> Any:
    socket = _socket_casefold(getattr(node, "inputs", ()), *names)
    return getattr(socket, "default_value", default) if socket is not None else default


def _is_group_node(node: Any) -> bool:
    return (
        getattr(node, "bl_idname", "") == "ShaderNodeGroup"
        or getattr(node, "type", "") == "GROUP"
    )


def _is_image_node(node: Any) -> bool:
    return (
        getattr(node, "bl_idname", "") == "ShaderNodeTexImage"
        or getattr(node, "type", "") == "TEX_IMAGE"
    )


def _image_path(image: Any, image_user: Any = None) -> str:
    if image is None:
        return ""
    path = ""
    resolver = getattr(image, "filepath_from_user", None)
    if callable(resolver):
        try:
            path = resolver(image_user=image_user) if image_user is not None else resolver()
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            try:
                path = resolver()
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                path = ""
    if not path:
        path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
    if not path:
        return ""
    try:
        return bpy.path.abspath(path)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return path


def _first_image_node(
    node_tree: Any,
    max_depth: int,
    depth: int = 0,
    tree_stack: tuple[int, ...] = (),
) -> Any | None:
    if node_tree is None or depth > max_depth:
        return None
    tree_id = _rna_identity(node_tree)
    if tree_id in tree_stack:
        return None
    nested_stack = (*tree_stack, tree_id)
    for node in getattr(node_tree, "nodes", ()):
        if _is_generated(node):
            continue
        if _is_image_node(node) and getattr(node, "image", None) is not None:
            return node
        if not _is_group_node(node):
            continue
        found = _first_image_node(
            getattr(node, "node_tree", None),
            max_depth,
            depth + 1,
            nested_stack,
        )
        if found is not None:
            return found
    return None


def _gobo_info(
    image_node: Any,
    source_kind: str,
    control_node: Any | None = None,
    *,
    focus: float | None = None,
    channel: str = "Color",
    rotation_radians: float | None = None,
    mapping_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    mapping_translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GoboInfo:
    image = getattr(image_node, "image", None)
    image_user = getattr(image_node, "image_user", None)
    colorspace = getattr(
        getattr(image, "colorspace_settings", None),
        "name",
        "",
    )
    source = getattr(image, "source", "FILE")
    control = control_node if control_node is not None else image_node
    if focus is None:
        value = _input_value(control, ("Focus",), None)
        focus = float(value) if value is not None else None
    if rotation_radians is None:
        rotation_radians = float(_input_value(control, ("Rotation",), 0.0))
    return GoboInfo(
        image=image,
        source_node=image_node,
        source_kind=source_kind,
        filepath=_image_path(image, image_user),
        image_name=getattr(image, "name", ""),
        colorspace=colorspace,
        channel=channel,
        rotation_radians=rotation_radians,
        mapping_scale=mapping_scale,
        mapping_translation=mapping_translation,
        focus=focus,
        vignette=float(
            _input_value(control, ("Vignette", "Border Width"), 0.0)
        ),
        invert=bool(_input_value(control, ("Invert Gobo",), False)),
        animated=source in {"MOVIE", "SEQUENCE"},
        playback_speed=float(
            _input_value(control, ("Playback Speed", "Playback speed"), 1.0)
        ),
    )


def _vector3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        values = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return default
    if len(values) < 3:
        return default
    return values[:3]


def _generic_image_mapping(
    image_node: Any,
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]]:
    """Read the common Mapping/Vector Rotate controls feeding an image."""
    vector = _socket(getattr(image_node, "inputs", ()), "Vector")
    links = list(getattr(vector, "links", ())) if vector is not None else []
    if not links:
        return 0.0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    node = links[0].from_node
    visited: set[int] = set()
    while (
        getattr(node, "bl_idname", "") == "NodeReroute"
        or getattr(node, "type", "") == "REROUTE"
    ):
        node_id = _rna_identity(node)
        if node_id in visited:
            break
        visited.add(node_id)
        inputs = getattr(node, "inputs", ())
        upstream = list(getattr(inputs[0], "links", ())) if inputs else []
        if not upstream:
            break
        node = upstream[0].from_node

    node_type = getattr(node, "bl_idname", "")
    if node_type == "ShaderNodeMapping":
        rotation = _vector3(
            _input_value(node, ("Rotation",), (0.0, 0.0, 0.0)),
            (0.0, 0.0, 0.0),
        )
        scale = _vector3(
            _input_value(node, ("Scale",), (1.0, 1.0, 1.0)),
            (1.0, 1.0, 1.0),
        )
        translation = _vector3(
            _input_value(node, ("Location",), (0.0, 0.0, 0.0)),
            (0.0, 0.0, 0.0),
        )
        return rotation[2], scale, translation
    if node_type == "ShaderNodeVectorRotate":
        angle = float(_input_value(node, ("Angle",), 0.0))
        return angle, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    return 0.0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)


def _find_light_wrangler_gobo(
    node_tree: Any,
    max_depth: int,
    depth: int = 0,
    tree_stack: tuple[int, ...] = (),
) -> GoboInfo | None:
    if node_tree is None or depth > max_depth:
        return None
    tree_id = _rna_identity(node_tree)
    if tree_id in tree_stack:
        return None
    nested_stack = (*tree_stack, tree_id)
    for node in getattr(node_tree, "nodes", ()):
        if not _is_group_node(node):
            continue
        child_tree = getattr(node, "node_tree", None)
        child_name = getattr(child_tree, "name", "").casefold()
        if "gobo light" in child_name:
            image_node = _first_image_node(child_tree, max_depth - depth)
            if image_node is not None:
                return _gobo_info(image_node, "LIGHT_WRANGLER", node)
        found = _find_light_wrangler_gobo(
            child_tree,
            max_depth,
            depth + 1,
            nested_stack,
        )
        if found is not None:
            return found
    return None


def _id_property(owner: Any, name: str, default: Any = None) -> Any:
    getter = getattr(owner, "get", None)
    if callable(getter):
        try:
            return getter(name, default)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    return getattr(owner, name, default)


def _find_light_wrangler_stencil(light_obj: Any, max_depth: int) -> GoboInfo | None:
    if not bool(_id_property(light_obj, "lw_has_eevee_gobo", False)):
        return None
    plane_name = _id_property(light_obj, "lw_eevee_gobo_plane", "")
    objects = getattr(getattr(bpy, "data", None), "objects", None)
    getter = getattr(objects, "get", None)
    plane = getter(plane_name) if callable(getter) and plane_name else None
    if plane is None:
        return None
    light_data = getattr(light_obj, "data", None)
    focus = getattr(light_data, "lw_stencil_focus", None)
    if focus is None:
        radius = float(getattr(light_data, "shadow_soft_size", 0.0015))
        focus = 100.0 - (
            (max(0.0015, min(0.1, radius)) - 0.0015) / (0.1 - 0.0015)
        ) * 100.0
    for material in getattr(getattr(plane, "data", None), "materials", ()):
        node_tree = getattr(material, "node_tree", None)
        for node in getattr(node_tree, "nodes", ()):
            if not _is_group_node(node):
                continue
            child_tree = getattr(node, "node_tree", None)
            if "gobo stencil" not in getattr(child_tree, "name", "").casefold():
                continue
            image_node = _first_image_node(child_tree, max_depth)
            if image_node is not None:
                return _gobo_info(
                    image_node,
                    "LIGHT_WRANGLER_STENCIL",
                    node,
                    focus=float(focus),
                )
    return None


def _socket_index(collection: Any, socket: Any) -> int:
    return next(
        (
            index
            for index, candidate in enumerate(collection)
            if _same_rna_data(candidate, socket)
        ),
        -1,
    )


def _matching_socket(source: Any, socket: Any, target: Any) -> Any | None:
    identifier = getattr(socket, "identifier", "")
    if identifier:
        match = next(
            (
                candidate
                for candidate in target
                if getattr(candidate, "identifier", "") == identifier
            ),
            None,
        )
        if match is not None:
            return match
    match = _socket(target, getattr(socket, "name", ""))
    if match is not None:
        return match
    index = _socket_index(source, socket)
    return target[index] if 0 <= index < len(target) else None


class _GoboImageTracer:
    def __init__(self, output: Any, max_depth: int) -> None:
        self.output = output
        self.max_depth = max(1, int(max_depth))
        self.visited: set[tuple[int, str, tuple[int, ...]]] = set()
        self.image_output_names: dict[int, str] = {}
        self.warned = False

    def trace(self) -> Any | None:
        surface = _socket(getattr(self.output, "inputs", ()), "Surface")
        links = list(getattr(surface, "links", ())) if surface is not None else []
        if not links:
            return None
        link = links[0]
        return self._output(link.from_node, link.from_socket, (), 0)

    def trace_input(self, socket: Any) -> Any | None:
        """Trace one authored input when its old output is disconnected."""
        return self._input(socket, (), 0)

    def trace_output(self, node: Any, socket: Any) -> Any | None:
        """Trace one authored group output outside the active output path."""
        return self._output(node, socket, (), 0)

    def output_name_for(self, image_node: Any) -> str:
        return self.image_output_names.get(_rna_identity(image_node), "Color")

    def _warn_depth(self) -> None:
        if self.warned:
            return
        self.warned = True
        log.warning(
            "Gobo traversal exceeded %d nodes from light output '%s'; "
            "the remaining cyclic or deeply nested branch was skipped",
            self.max_depth,
            getattr(self.output, "name", "Light Output"),
        )

    def _input(
        self,
        socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> Any | None:
        links = list(getattr(socket, "links", ()))
        if not links:
            return None
        link = links[0]
        return self._output(link.from_node, link.from_socket, contexts, depth + 1)

    def _output(
        self,
        node: Any,
        socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> Any | None:
        if depth > self.max_depth:
            self._warn_depth()
            return None
        node_type = getattr(node, "bl_idname", "")
        if _is_generated(node):
            return None
        if _is_image_node(node) and getattr(node, "image", None) is not None:
            self.image_output_names[_rna_identity(node)] = getattr(
                socket,
                "name",
                "Color",
            )
            return node
        if node_type == "NodeReroute" or getattr(node, "type", "") == "REROUTE":
            inputs = getattr(node, "inputs", ())
            return self._input(inputs[0], contexts, depth) if inputs else None

        key = (
            _rna_identity(node),
            getattr(socket, "name", ""),
            tuple(_rna_identity(context) for context in contexts),
        )
        if key in self.visited:
            return None
        self.visited.add(key)

        if _is_group_node(node):
            child_tree = getattr(node, "node_tree", None)
            outputs = [
                candidate
                for candidate in getattr(child_tree, "nodes", ())
                if getattr(candidate, "bl_idname", "") == "NodeGroupOutput"
                or getattr(candidate, "type", "") == "GROUP_OUTPUT"
            ]
            active = [
                candidate
                for candidate in outputs
                if bool(getattr(candidate, "is_active_output", False))
            ]
            for group_output in active or outputs:
                internal = _matching_socket(
                    getattr(node, "outputs", ()),
                    socket,
                    getattr(group_output, "inputs", ()),
                )
                if internal is None:
                    continue
                found = self._input(internal, (*contexts, node), depth)
                if found is not None:
                    return found
            return None

        if node_type == "NodeGroupInput" or getattr(node, "type", "") == "GROUP_INPUT":
            if not contexts:
                return None
            group_node = contexts[-1]
            external = _matching_socket(
                getattr(node, "outputs", ()),
                socket,
                getattr(group_node, "inputs", ()),
            )
            return (
                self._input(external, contexts[:-1], depth)
                if external is not None
                else None
            )

        inputs = list(getattr(node, "inputs", ()))
        if node_type == "ShaderNodeEmission":
            inputs.sort(
                key=lambda candidate: 0
                if getattr(candidate, "name", "") == "Color"
                else 1
            )
        for input_socket in inputs:
            found = self._input(input_socket, contexts, depth)
            if found is not None:
                return found
        return None


def _active_light_output(node_tree: Any) -> Any | None:
    outputs = [
        node
        for node in getattr(node_tree, "nodes", ())
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputLight"
        or getattr(node, "type", "") == "OUTPUT_LIGHT"
    ]
    return next(
        (node for node in outputs if bool(getattr(node, "is_active_output", False))),
        outputs[0] if outputs else None,
    )


def _find_authored_gobo_image(node_tree: Any, tracer: _GoboImageTracer) -> Any | None:
    """Find an image in a disconnected, non-Octane light shader branch."""
    for node in getattr(node_tree, "nodes", ()):
        if _is_generated(node):
            continue
        node_type = getattr(node, "bl_idname", "")
        if node_type == "ShaderNodeEmission":
            inputs = list(getattr(node, "inputs", ()))
            inputs.sort(
                key=lambda candidate: 0
                if getattr(candidate, "name", "") == "Color"
                else 1
            )
            for socket in inputs:
                found = tracer.trace_input(socket)
                if found is not None:
                    return found
        elif _is_group_node(node):
            for socket in getattr(node, "outputs", ()):
                found = tracer.trace_output(node, socket)
                if found is not None:
                    return found
    return None


def detect_light_gobo(
    light_obj: bpy.types.Object,
    max_depth: int = _MAX_GOBO_TRACE_DEPTH,
) -> GoboInfo | None:
    """Return an image gobo authored on an Area or Spot light, if present.

    Light Wrangler's Cycles ``Gobo Light`` groups and EEVEE stencil fallback
    are recognized explicitly.  Other light graphs are traced backward from
    Light Output, including nested shader groups, so ordinary image-driven
    Cycles gobos are handled without depending on Light Wrangler being loaded.
    """
    if light_obj is None or getattr(light_obj, "type", None) != "LIGHT":
        return None
    light_data = getattr(light_obj, "data", None)
    if getattr(light_data, "type", None) not in {"AREA", "SPOT"}:
        return None
    node_tree = getattr(light_data, "node_tree", None)
    if node_tree is not None:
        detected = _find_light_wrangler_gobo(node_tree, max_depth)
        if detected is not None:
            return detected
    detected = _find_light_wrangler_stencil(light_obj, max_depth)
    if detected is not None:
        return detected
    if not bool(getattr(light_data, "use_nodes", False)) or node_tree is None:
        return None
    output = _active_light_output(node_tree)
    tracer = _GoboImageTracer(output, max_depth)
    image_node = tracer.trace() if output is not None else None
    if image_node is None:
        # Octanify keeps authored Cycles nodes for non-destructive conversion.
        # Once a light has been converted its original Emission branch is no
        # longer connected to the active output, so inspect those authored
        # branches too.  This also upgrades pre-gobo Phase 3 light graphs.
        image_node = _find_authored_gobo_image(node_tree, tracer)
    if image_node is None:
        return None
    rotation, scale, translation = _generic_image_mapping(image_node)
    return _gobo_info(
        image_node,
        "CYCLES_IMAGE",
        channel=tracer.output_name_for(image_node),
        rotation_radians=rotation,
        mapping_scale=scale,
        mapping_translation=translation,
    )


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
        node[_GENERATED_NODE_TAG] = True
    except (AttributeError, TypeError):
        pass
    return node


def _is_generated(node: bpy.types.Node) -> bool:
    getter = getattr(node, "get", None)
    if callable(getter):
        try:
            return bool(getter(_GENERATED_NODE_TAG, False))
        except (AttributeError, TypeError):
            return False
    return False


def _remove_generated_nodes(
    node_tree: bpy.types.NodeTree,
    nodes: list[bpy.types.Node] | None = None,
) -> None:
    candidates = list(node_tree.nodes) if nodes is None else nodes
    for node in candidates:
        if not _is_generated(node):
            continue
        try:
            node_tree.nodes.remove(node)
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            pass


def _rollback_new_nodes(
    node_tree: bpy.types.NodeTree,
    existing_node_ids: set[int],
) -> None:
    for node in list(node_tree.nodes):
        if _rna_identity(node) not in existing_node_ids and _is_generated(node):
            node_tree.nodes.remove(node)


def _restore_input_links(
    node_tree: bpy.types.NodeTree,
    input_socket: Any,
    from_sockets: list[Any],
) -> None:
    """Restore a single-input socket after a transactional graph failure."""
    if input_socket is None:
        return
    for link in list(getattr(input_socket, "links", ())):
        try:
            node_tree.links.remove(link)
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    for from_socket in from_sockets:
        try:
            node_tree.links.new(from_socket, input_socket)
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            log.warning("Could not restore the authored Light Output link")
            break


def _notify_octane_tree(
    node_tree: bpy.types.NodeTree,
    owner: bpy.types.Light,
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
            log.debug("Octane light link validation deferred: %s", exc)
    try:
        node_tree.update_tag()
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        owner.update_tag()
    except (AttributeError, RuntimeError, TypeError):
        pass


def _validate_export_root(
    node_tree: bpy.types.NodeTree,
    owner: bpy.types.Light,
    output: bpy.types.Node,
    root: bpy.types.Node,
) -> None:
    """Confirm both Blender and Octane resolve the generated light root."""
    surface = _socket(output.inputs, "Surface")
    links = list(getattr(surface, "links", ())) if surface is not None else []
    if not any(_same_rna_data(link.from_node, root) for link in links):
        raise RuntimeError("Generated Octane light is not linked to Light Output")

    try:
        from octane.utils import utility
    except ImportError:
        return
    try:
        owner_type = utility.get_node_tree_owner_type(owner)
        active_output = utility.find_active_output_node(node_tree, owner_type)
    except (AttributeError, RuntimeError, TypeError) as exc:
        raise RuntimeError(
            "Octane could not resolve the converted light output"
        ) from exc
    if not _same_rna_data(active_output, output):
        raise RuntimeError("Octane did not select the converted Light Output")


def _surface_output(
    node_tree: bpy.types.NodeTree,
    _owner: bpy.types.Light,
) -> bpy.types.Node:
    """Create a renderer-targeted output without touching the Cycles output."""
    output = _new_node(
        node_tree,
        "ShaderNodeOutputLight",
        (620.0, 0.0),
    )
    try:
        output.target = "ALL"
        output.is_active_output = True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    surface = _socket(output.inputs, "Surface")
    if surface is None:
        raise RuntimeError("Light Output is missing its Surface input")
    return output


def _prepare_light_outputs(
    node_tree: bpy.types.NodeTree,
) -> list[tuple[bpy.types.Node, Any, Any]]:
    """Reserve authored outputs for Cycles and retire old Octane outputs."""
    state: list[tuple[bpy.types.Node, Any, Any]] = []
    for node in node_tree.nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeOutputLight":
            continue
        state.append((
            node,
            getattr(node, "target", None),
            getattr(node, "is_active_output", None),
        ))
        try:
            node.target = "CYCLES"
            if _is_generated(node):
                node.is_active_output = False
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    return state


def _restore_light_outputs(state: list[tuple[bpy.types.Node, Any, Any]]) -> None:
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


def _restore_legacy_cycles_light_link(
    node_tree: bpy.types.NodeTree,
    original_nodes: list[bpy.types.Node],
) -> None:
    """Reconnect source shaders left loose by older Octanify conversions."""
    candidates: list[tuple[int, Any]] = []
    for node in original_nodes:
        if getattr(node, "bl_idname", "") == "ShaderNodeOutputLight":
            continue
        for output in getattr(node, "outputs", ()):
            name = getattr(output, "name", "")
            socket_type = getattr(output, "type", "")
            if name not in {"Emission", "Shader", "BSDF"} and socket_type != "SHADER":
                continue
            if getattr(output, "links", ()):
                continue
            score = 100 if getattr(node, "bl_idname", "") == "ShaderNodeEmission" else 50
            if name == "Emission":
                score += 20
            candidates.append((score, output))
    if not candidates:
        return
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return
    root_socket = candidates[0][1]

    for node in original_nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeOutputLight":
            continue
        surface = _socket(node.inputs, "Surface")
        links = list(getattr(surface, "links", ())) if surface is not None else []
        if links and not _is_generated(links[0].from_node):
            continue
        try:
            for link in links:
                node_tree.links.remove(link)
            node_tree.links.new(root_socket, surface)
            log.info("Restored authored Cycles branch on '%s'", node.name)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass


def _finite_power_values(
    light_data: bpy.types.Light,
    light_type: str,
    effective_energy: float,
    radius: float,
) -> tuple[float, float, bool]:
    convention = POWER_CONVENTIONS[light_type]
    normalized = bool(getattr(light_data, "normalize", True))
    if normalized:
        return convention.factor, effective_energy * convention.factor, False

    # Cycles gives a zero-radius Point light a synthetic area of 4 so its
    # normalized point-light limit remains finite.  Octane treats a radius of
    # zero as a true point light, so transfer that area into total Power.
    if light_type == "POINT" and radius <= 0.0:
        factor = convention.factor * _CYCLES_ZERO_RADIUS_POINT_AREA
        return factor, effective_energy * factor, False

    factor = convention.factor * OCTANE_SURFACE_BRIGHTNESS_REFERENCE_AREA
    return factor, effective_energy * factor, True


def _sun_power_values(
    light_data: bpy.types.Light,
    effective_energy: float,
    angle: float,
) -> tuple[float, float]:
    convention = POWER_CONVENTIONS["SUN"]
    if bool(getattr(light_data, "normalize", True)):
        return convention.factor, effective_energy * convention.factor

    # Cycles normalizes a Sun by its apparent disc solid-angle factor.  An
    # Octane Directional light has no Normalize socket, so bake that factor
    # into Power when the Blender option is disabled.
    disc_factor = math.pi * math.sin(angle * 0.5) ** 2 if angle > 0.0 else 1.0
    factor = convention.factor * disc_factor
    return factor, effective_energy * factor


def _set_octane_light_mode(light_data: bpy.types.Light, light_type: str) -> None:
    octane = getattr(light_data, "octane", None)
    if octane is None:
        return
    try:
        if light_type == "POINT":
            octane.octane_point_light_type = "Sphere"
        elif light_type == "SUN":
            octane.octane_directional_light_type = "Directional"
        elif light_type == "AREA":
            # The plugin's native Area path generates a Quad/Disc primitive
            # directly from Blender's shape, size, and size_y.  Mesh-light
            # mode instead expects a separate mesh datablock.
            octane.used_as_octane_mesh_light = False
    except (AttributeError, TypeError, ValueError):
        pass


def _light_mode_snapshot(light_data: bpy.types.Light) -> dict[str, Any]:
    octane = getattr(light_data, "octane", None)
    if octane is None:
        return {}
    return {
        name: getattr(octane, name)
        for name in (
            "octane_point_light_type",
            "octane_directional_light_type",
            "used_as_octane_mesh_light",
        )
        if hasattr(octane, name)
    }


def _restore_light_mode(
    light_data: bpy.types.Light,
    snapshot: dict[str, Any],
) -> None:
    octane = getattr(light_data, "octane", None)
    if octane is None:
        return
    for name, value in snapshot.items():
        try:
            setattr(octane, name, value)
        except (AttributeError, TypeError, ValueError):
            pass


def _create_emission(
    node_tree: bpy.types.NodeTree,
    color: tuple[float, float, float],
    power: float,
    surface_brightness: bool,
    temperature: float,
) -> tuple[bpy.types.Node, bpy.types.Node]:
    color_texture = _new_node(node_tree, "OctaneRGBColor", (-210.0, 65.0))
    try:
        color_texture.a_value = color
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("Octane RGB Color cannot store the light color") from exc

    emission = _new_node(node_tree, "OctaneBlackBodyEmission", (30.0, 0.0))
    _set_input(emission, "Power", power)
    _set_input(emission, "Surface brightness", surface_brightness)
    _set_input(emission, "Temperature", temperature)
    _set_input(emission, "Normalize", True)
    _link(node_tree, color_texture, "Texture out", emission, "Texture")
    return color_texture, emission


def _set_optional_input(node: bpy.types.Node, name: str, value: Any) -> bool:
    socket = _socket(node.inputs, name)
    if socket is None or not hasattr(socket, "default_value"):
        return False
    try:
        socket.default_value = value
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return True


def _mark_gobo_node(node: bpy.types.Node) -> None:
    try:
        node[_GENERATED_GOBO_TAG] = True
    except (AttributeError, TypeError):
        pass


def _assign_gobo_image(node: bpy.types.Node, gobo: GoboInfo) -> None:
    assigned = False
    try:
        node.image = gobo.image
        assigned = True
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        pass
    if gobo.filepath and hasattr(node, "a_filename"):
        try:
            node.a_filename = gobo.filepath
            assigned = True
        except (AttributeError, RuntimeError, TypeError):
            pass
    if not assigned:
        raise RuntimeError(
            f"Octane image node cannot receive gobo '{gobo.image_name or '?'}'"
        )
    try:
        node.a_reload = True
    except (AttributeError, TypeError):
        pass

    image_user = getattr(gobo.source_node, "image_user", None)
    for name in (
        "frame_current",
        "frame_duration",
        "frame_offset",
        "frame_start",
        "use_auto_refresh",
        "use_cyclic",
    ):
        if image_user is None or not hasattr(image_user, name) or not hasattr(node, name):
            continue
        try:
            setattr(node, name, getattr(image_user, name))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    if gobo.animated and hasattr(node, "use_auto_refresh"):
        try:
            node.use_auto_refresh = True
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    if gobo.animated and abs(gobo.playback_speed - 1.0) > 1.0e-6:
        driver_add = getattr(node, "driver_add", None)
        if callable(driver_add):
            try:
                base_offset = int(getattr(node, "frame_offset", 0))
                driver = driver_add("frame_offset").driver
                driver.type = "SCRIPTED"
                driver.expression = (
                    f"frame * ({gobo.playback_speed:.9g} - 1.0) "
                    f"+ {base_offset}"
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                log.debug(
                    "Could not preserve playback speed for gobo '%s'",
                    gobo.image_name,
                )

    linear_names = {"Non-Color", "Linear", "Raw", "Utility - Raw"}
    gamma = (
        1.0
        if gobo.colorspace in linear_names
        or gobo.colorspace.casefold().startswith("linear")
        else 2.2
    )
    _set_optional_input(node, "Legacy gamma", gamma)
    _set_optional_input(node, "Invert", gobo.invert)
    _set_optional_input(node, "Linear sRGB invert", True)
    _set_optional_input(node, "Border mode (U)", "Black color")
    _set_optional_input(node, "Border mode (V)", "Black color")
    try:
        node.label = f"Gobo: {gobo.image_name}"
    except (AttributeError, TypeError):
        pass


def _vignette_hardness(vignette: float) -> float:
    normalized = max(0.0, min(1.0, vignette / 100.0))
    return 1.0 - normalized * 0.9


def _multiply_gobo_texture(
    node_tree: bpy.types.NodeTree,
    first: bpy.types.Node,
    second: bpy.types.Node,
    location: tuple[float, float],
) -> bpy.types.Node:
    multiply = _new_node(node_tree, "OctaneMultiplyTexture", location)
    _mark_gobo_node(multiply)
    _link(node_tree, first, "Texture out", multiply, "Texture 1")
    _link(node_tree, second, "Texture out", multiply, "Texture 2")
    return multiply


def convert_gobo_to_octane(
    node_tree: bpy.types.NodeTree,
    emission: bpy.types.Node,
    gobo: GoboInfo,
    *,
    light_type: str,
    base_distribution: bpy.types.Node | None = None,
    cone_degrees: float = 180.0,
) -> list[bpy.types.Node]:
    """Append an Octane perspective gobo distribution to a light graph.

    The source image, colorspace, inversion, animation controls, and authored
    rotation are copied.  Light Wrangler's Area-light Focus/Vignette behavior
    is represented by a Spotlight distribution multiplied with the projected
    image; Spot lights retain their normal cone and add vignette falloff.
    """
    if light_type not in {"AREA", "SPOT"}:
        raise ValueError(f"Gobos are unsupported on {light_type or '?'} lights")

    transform = _new_node(node_tree, "Octane3DTransformation", (-900.0, -490.0))
    projection = _new_node(node_tree, "OctanePerspective", (-650.0, -490.0))
    image_type = (
        "OctaneAlphaImage"
        if gobo.channel.casefold() == "alpha"
        else "OctaneRGBImage"
    )
    image = _new_node(node_tree, image_type, (-390.0, -400.0))
    for node in (transform, projection, image):
        _mark_gobo_node(node)

    _set_input(transform, "Rotation order", "XYZ")
    _set_input(
        transform,
        "Rotation",
        (0.0, 0.0, math.degrees(gobo.rotation_radians)),
    )
    _set_input(transform, "Scale", gobo.mapping_scale)
    _set_input(transform, "Translation", gobo.mapping_translation)
    _set_input(projection, "Coordinate space", "Object space")
    _set_input(projection, "Use rest attributes", False)
    _assign_gobo_image(image, gobo)
    _link(node_tree, transform, "Transform out", projection, "Plane transformation")
    _link(node_tree, projection, "Projection out", image, "Projection")

    created = [transform, projection, image]
    distribution = image
    if base_distribution is not None:
        distribution = _multiply_gobo_texture(
            node_tree,
            base_distribution,
            distribution,
            (-105.0, -310.0),
        )
        created.append(distribution)

    is_light_wrangler = gobo.source_kind.startswith("LIGHT_WRANGLER")
    if light_type == "AREA" and is_light_wrangler:
        focus_distribution = _new_node(
            node_tree,
            "OctaneSpotlight",
            (-390.0, -610.0),
        )
        _mark_gobo_node(focus_distribution)
        _set_input(focus_distribution, "Cone angle", cone_degrees)
        _set_input(
            focus_distribution,
            "Hardness",
            _vignette_hardness(gobo.vignette),
        )
        _set_input(focus_distribution, "Normalize power", False)
        distribution = _multiply_gobo_texture(
            node_tree,
            focus_distribution,
            distribution,
            (-105.0, -410.0),
        )
        created.extend((focus_distribution, distribution))
    elif light_type == "SPOT" and gobo.vignette > 0.0:
        vignette_distribution = _new_node(
            node_tree,
            "OctaneSpotlight",
            (-390.0, -610.0),
        )
        _mark_gobo_node(vignette_distribution)
        _set_input(vignette_distribution, "Cone angle", cone_degrees)
        _set_input(
            vignette_distribution,
            "Hardness",
            _vignette_hardness(gobo.vignette),
        )
        _set_input(vignette_distribution, "Normalize power", False)
        distribution = _multiply_gobo_texture(
            node_tree,
            vignette_distribution,
            distribution,
            (130.0, -330.0),
        )
        created.extend((vignette_distribution, distribution))

    _link(node_tree, distribution, "Texture out", emission, "Distribution")
    return created


def _create_mesh_emitter_graph(
    node_tree: bpy.types.NodeTree,
    output: bpy.types.Node,
    color_texture: bpy.types.Node,
    emission: bpy.types.Node,
) -> list[bpy.types.Node]:
    material = _new_node(node_tree, "OctaneDiffuseMaterial", (300.0, 0.0))
    _link(node_tree, emission, "Emission out", material, "Emission")
    _link(node_tree, material, "Material out", output, "Surface")
    return [color_texture, emission, material]


def _create_sun_graph(
    light_obj: bpy.types.Object,
    node_tree: bpy.types.NodeTree,
    output: bpy.types.Node,
    color_texture: bpy.types.Node,
    emission: bpy.types.Node,
    spread_degrees: float,
) -> list[bpy.types.Node]:
    directional = _new_node(node_tree, "OctaneDirectionalLight", (300.0, 0.0))
    transform = _new_node(node_tree, "OctaneObjectData", (25.0, -230.0))
    _set_input(directional, "Light sample spread angle", spread_degrees)
    try:
        transform.source_type = "Object"
        transform.object_ptr = light_obj
    except (AttributeError, TypeError):
        log.warning(
            "Directional light transform node could not reference '%s'",
            getattr(light_obj, "name", "?"),
        )
    _link(node_tree, emission, "Emission out", directional, "Emission")
    _link(node_tree, transform, "Transform out", directional, "Light transform")
    _link(node_tree, directional, "Geometry out", output, "Surface")
    return [color_texture, emission, transform, directional]


def _create_spot_graph(
    node_tree: bpy.types.NodeTree,
    output: bpy.types.Node,
    color_texture: bpy.types.Node,
    emission: bpy.types.Node,
    cone_degrees: float,
    hardness: float,
) -> list[bpy.types.Node]:
    volumetric = _new_node(node_tree, "OctaneVolumetricSpotlight", (520.0, 0.0))
    material = _new_node(node_tree, "OctaneDiffuseMaterial", (285.0, 0.0))
    distribution = _new_node(node_tree, "OctaneSpotlight", (20.0, -245.0))
    _set_input(distribution, "Cone angle", cone_degrees)
    _set_input(distribution, "Hardness", hardness)
    _set_input(distribution, "Normalize power", False)
    _set_input(volumetric, "Cone hardness", hardness)
    _link(node_tree, distribution, "Texture out", emission, "Distribution")
    _link(node_tree, emission, "Emission out", material, "Emission")
    _link(node_tree, material, "Material out", volumetric, "Emitter material")
    _link(node_tree, volumetric, "Geometry out", output, "Surface")
    return [color_texture, emission, distribution, material, volumetric]


def light_needs_octane_conversion(light_obj: bpy.types.Object) -> bool:
    """Return whether a supported light still has a non-Octane output root."""
    if light_obj is None or getattr(light_obj, "type", None) != "LIGHT":
        return False
    light_data = getattr(light_obj, "data", None)
    if getattr(light_data, "type", None) not in SUPPORTED_LIGHT_TYPES:
        return False
    if not bool(getattr(light_data, "use_nodes", False)):
        return True
    node_tree = getattr(light_data, "node_tree", None)
    if node_tree is None:
        return True
    generated_emissions = {
        getattr(node, "bl_idname", "")
        for node in node_tree.nodes
        if _is_generated(node)
        and getattr(node, "bl_idname", "")
        in {"OctaneTextureEmission", "OctaneBlackBodyEmission"}
    }
    if "OctaneTextureEmission" in generated_emissions:
        # Upgrade graphs produced by the earlier Phase 3 implementation.
        return True
    if "OctaneBlackBodyEmission" in generated_emissions:
        generated_output = next(
            (
                node for node in node_tree.nodes
                if _is_generated(node)
                and getattr(node, "bl_idname", "") == "ShaderNodeOutputLight"
            ),
            None,
        )
        surface = (
            _socket(generated_output.inputs, "Surface")
            if generated_output is not None
            else None
        )
        if not getattr(surface, "links", ()):
            # Upgrade the older implementation that replaced the authored
            # output link instead of creating a renderer-targeted output.
            return True
        gobo = detect_light_gobo(light_obj)
        generated_gobo = any(
            bool(_id_property(node, _GENERATED_GOBO_TAG, False))
            for node in node_tree.nodes
        )
        if gobo is not None:
            stored_signature = str(
                _id_property(light_data, _GOBO_SIGNATURE_TAG, "")
            )
            if not generated_gobo or stored_signature != gobo.signature:
                return True
        return False
    return True


def convert_light_to_octane(
    light_obj: bpy.types.Object,
    auto_arrange: bool = True,
    color_nodes: bool = True,
) -> dict[str, Any]:
    """Convert one Blender light object to the matching Octane light graph.

    The returned dictionary contains the source datablock values, the computed
    Octane values, and the node types written for reporting and tests.
    """
    if light_obj is None or getattr(light_obj, "type", None) != "LIGHT":
        raise ValueError("Expected a Blender light object")
    light_data = getattr(light_obj, "data", None)
    light_type = getattr(light_data, "type", "")
    if light_data is None or light_type not in SUPPORTED_LIGHT_TYPES:
        raise ValueError(f"Unsupported Blender light type: {light_type or '?'}")

    # Detect the authored graph before replacing the active Light Output link.
    # Light Wrangler groups remain in the tree after conversion, but ordinary
    # Cycles gobos may otherwise become unreachable from the active output.
    gobo = detect_light_gobo(light_obj)

    color = tuple(float(component) for component in light_data.color[:3])
    use_temperature = bool(getattr(light_data, "use_temperature", False))
    source_temperature = float(getattr(light_data, "temperature", 6500.0))
    octane_temperature = source_temperature if use_temperature else 6500.0
    energy = float(light_data.energy)
    exposure = float(getattr(light_data, "exposure", 0.0))
    effective_energy = energy * math.exp2(exposure)
    normalized = bool(getattr(light_data, "normalize", True))
    source_unit = POWER_CONVENTIONS[light_type].source_unit

    spot_size = float(getattr(light_data, "spot_size", 0.0))
    spot_blend = float(getattr(light_data, "spot_blend", 0.0))
    area_size = float(getattr(light_data, "size", 0.0))
    area_size_y = float(getattr(light_data, "size_y", area_size))
    sun_angle = float(getattr(light_data, "angle", 0.0))
    radius = float(getattr(light_data, "shadow_soft_size", 0.0))
    area_spread = float(getattr(light_data, "spread", math.pi))
    area_gobo_cone_degrees = math.degrees(area_spread)
    if (
        light_type == "AREA"
        and gobo is not None
        and gobo.source_kind.startswith("LIGHT_WRANGLER")
        and gobo.focus is not None
    ):
        clamped_focus = max(0.0, min(100.0, gobo.focus))
        area_gobo_cone_degrees = 10.0 - clamped_focus * 0.099

    if light_type == "SUN":
        power_factor, octane_power = _sun_power_values(
            light_data,
            effective_energy,
            sun_angle,
        )
        surface_brightness = False
    elif light_type == "SPOT":
        # Blender defines Spot power before cone clipping.  Octane's
        # Spotlight distribution with Normalize Power disabled has the same
        # convention; its Volumetric Spotlight exposes no emitter radius.
        power_factor = POWER_CONVENTIONS[light_type].factor
        octane_power = effective_energy * power_factor
        surface_brightness = False
    else:
        power_factor, octane_power, surface_brightness = _finite_power_values(
            light_data,
            light_type,
            effective_energy,
            radius,
        )

    previous_use_nodes = bool(getattr(light_data, "use_nodes", False))
    previous_light_mode = _light_mode_snapshot(light_data)
    _set_octane_light_mode(light_data, light_type)
    try:
        light_data.use_nodes = True
    except (AttributeError, RuntimeError, TypeError) as exc:
        _restore_light_mode(light_data, previous_light_mode)
        raise RuntimeError("The light datablock cannot create a node tree") from exc
    node_tree = getattr(light_data, "node_tree", None)
    if node_tree is None:
        _restore_light_mode(light_data, previous_light_mode)
        try:
            light_data.use_nodes = previous_use_nodes
        except (AttributeError, RuntimeError, TypeError):
            pass
        raise RuntimeError("The light datablock has no node tree")

    existing_node_ids = {_rna_identity(node) for node in node_tree.nodes}
    original_nodes = [
        node for node in node_tree.nodes if not _is_generated(node)
    ]
    previous_generated = [
        node
        for node in node_tree.nodes
        if _is_generated(node)
    ]
    output_state = _prepare_light_outputs(node_tree)
    output = None
    try:
        output = _surface_output(node_tree, light_data)
        color_texture, emission = _create_emission(
            node_tree,
            color,
            octane_power,
            surface_brightness,
            octane_temperature,
        )

        if light_type in {"POINT", "AREA"}:
            created = _create_mesh_emitter_graph(
                node_tree,
                output,
                color_texture,
                emission,
            )
        elif light_type == "SUN":
            created = _create_sun_graph(
                light_obj,
                node_tree,
                output,
                color_texture,
                emission,
                math.degrees(sun_angle),
            )
        else:
            hardness = max(0.0, min(1.0, 1.0 - spot_blend))
            created = _create_spot_graph(
                node_tree,
                output,
                color_texture,
                emission,
                math.degrees(spot_size),
                hardness,
            )
        root = created[-1]
        if gobo is not None:
            base_distribution = next(
                (
                    node
                    for node in created
                    if getattr(node, "bl_idname", "") == "OctaneSpotlight"
                ),
                None,
            )
            cone_degrees = (
                math.degrees(spot_size)
                if light_type == "SPOT"
                else area_gobo_cone_degrees
            )
            created.extend(
                convert_gobo_to_octane(
                    node_tree,
                    emission,
                    gobo,
                    light_type=light_type,
                    base_distribution=base_distribution,
                    cone_degrees=cone_degrees,
                )
            )
        _validate_export_root(node_tree, light_data, output, root)
        _restore_legacy_cycles_light_link(node_tree, original_nodes)
    except Exception:
        _rollback_new_nodes(node_tree, existing_node_ids)
        _restore_light_outputs(output_state)
        _restore_light_mode(light_data, previous_light_mode)
        try:
            light_data.use_nodes = previous_use_nodes
        except (AttributeError, RuntimeError, TypeError):
            pass
        raise

    # Keep a valid old graph until the replacement has been fully built and
    # linked.  This prevents a missing Octane node class from destroying the
    # source light setup when an operator returns CANCELLED.
    _remove_generated_nodes(node_tree, previous_generated)
    style_smart_graphs(
        node_tree,
        original_nodes,
        [output, *created],
        auto_arrange=auto_arrange,
        colorize=color_nodes,
    )

    try:
        light_data["octanify_converted"] = True
        light_data[_GOBO_SIGNATURE_TAG] = gobo.signature if gobo is not None else ""
    except (AttributeError, TypeError):
        pass
    _notify_octane_tree(node_tree, light_data)

    result: dict[str, Any] = {
        "object_name": getattr(light_obj, "name", ""),
        "light_name": getattr(light_data, "name", ""),
        "type": light_type,
        "source_energy": energy,
        "source_exposure": exposure,
        "effective_source_energy": effective_energy,
        "source_unit": source_unit,
        "source_color": color,
        "source_use_temperature": use_temperature,
        "source_temperature": source_temperature,
        "octane_temperature": octane_temperature,
        "blackbody_efficiency": OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        "source_normalize": normalized,
        "power_factor": power_factor,
        "octane_power": octane_power,
        "octane_unit": POWER_CONVENTIONS[light_type].octane_unit,
        "surface_brightness": surface_brightness,
        "node_types": [node.bl_idname for node in created],
        "radius": radius,
        "spot_size_radians": spot_size if light_type == "SPOT" else None,
        "spot_size_degrees": math.degrees(spot_size) if light_type == "SPOT" else None,
        "spot_blend": spot_blend if light_type == "SPOT" else None,
        "spot_hardness": (
            max(0.0, min(1.0, 1.0 - spot_blend))
            if light_type == "SPOT"
            else None
        ),
        "sun_angle_radians": sun_angle if light_type == "SUN" else None,
        "sun_spread_degrees": math.degrees(sun_angle) if light_type == "SUN" else None,
        "area_shape": getattr(light_data, "shape", None) if light_type == "AREA" else None,
        "area_size": area_size if light_type == "AREA" else None,
        "area_size_y": area_size_y if light_type == "AREA" else None,
        "area_size_scale": 1.0 if light_type == "AREA" else None,
        "area_spread_radians": area_spread if light_type == "AREA" else None,
        "gobo_detected": gobo is not None,
        "gobo_converted": gobo is not None,
        "gobo_source_kind": gobo.source_kind if gobo is not None else None,
        "gobo_image_name": gobo.image_name if gobo is not None else None,
        "gobo_filepath": gobo.filepath if gobo is not None else None,
        "gobo_channel": gobo.channel if gobo is not None else None,
        "gobo_rotation_radians": (
            gobo.rotation_radians if gobo is not None else None
        ),
        "gobo_rotation_degrees": (
            math.degrees(gobo.rotation_radians) if gobo is not None else None
        ),
        "gobo_mapping_scale": (
            gobo.mapping_scale if gobo is not None else None
        ),
        "gobo_mapping_translation": (
            gobo.mapping_translation if gobo is not None else None
        ),
        "gobo_focus": gobo.focus if gobo is not None else None,
        "gobo_vignette": gobo.vignette if gobo is not None else None,
        "gobo_invert": gobo.invert if gobo is not None else None,
        "gobo_animated": gobo.animated if gobo is not None else False,
        "gobo_playback_speed": (
            gobo.playback_speed if gobo is not None else None
        ),
        "gobo_projection": "Perspective" if gobo is not None else None,
        "gobo_cone_degrees": (
            math.degrees(spot_size)
            if gobo is not None and light_type == "SPOT"
            else area_gobo_cone_degrees
            if gobo is not None and light_type == "AREA"
            else None
        ),
        "conversion_verified": True,
    }
    log.info(
        "Converted %s light '%s': %.6g %s -> %.6g Octane power",
        light_type,
        result["object_name"] or result["light_name"],
        energy,
        source_unit,
        octane_power,
    )
    return result
