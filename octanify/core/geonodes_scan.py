"""Octanify - Geometry Nodes material discovery.

Geometry Nodes can assign materials that never appear in an object's normal
material slots.  This module statically discovers those references without
evaluating or modifying the geometry graph.
"""

from __future__ import annotations

from typing import Any

import bpy

from ..utils.logger import get_logger

log = get_logger()


_SWITCH_NODE_TYPES = {
    "GeometryNodeSwitch",
    "GeometryNodeIndexSwitch",
    "GeometryNodeMenuSwitch",
}
_SWITCH_CONTROL_INPUTS = {"switch", "index", "menu"}
_GROUP_NODE_TYPES = {"GeometryNodeGroup", "ShaderNodeGroup"}


def _rna_identity(value: Any) -> int:
    """Return a stable identity for Blender RNA wrappers and test doubles."""
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _socket_get(collection: Any, name: str) -> Any | None:
    getter = getattr(collection, "get", None)
    if callable(getter):
        result = getter(name)
        if result is not None:
            return result
    return next(
        (socket for socket in collection if getattr(socket, "name", "") == name),
        None,
    )


def _socket_index(collection: Any, socket: Any) -> int:
    return next(
        (
            index
            for index, candidate in enumerate(collection)
            if _rna_identity(candidate) == _rna_identity(socket)
        ),
        -1,
    )


def _matching_socket(
    source_collection: Any,
    source_socket: Any,
    target_collection: Any,
) -> Any | None:
    """Match a group-boundary socket by identifier, name, then index."""
    identifier = getattr(source_socket, "identifier", "")
    if identifier:
        for candidate in target_collection:
            if getattr(candidate, "identifier", "") == identifier:
                return candidate
    by_name = _socket_get(
        target_collection, getattr(source_socket, "name", "")
    )
    if by_name is not None:
        return by_name
    index = _socket_index(source_collection, source_socket)
    if 0 <= index < len(target_collection):
        return target_collection[index]
    return None


def _is_material(value: Any) -> bool:
    try:
        return isinstance(value, bpy.types.Material)
    except (AttributeError, TypeError):
        return False


def _is_material_socket(socket: Any) -> bool:
    if _is_material(getattr(socket, "default_value", None)):
        return True
    return (
        getattr(socket, "bl_idname", "") == "NodeSocketMaterial"
        or getattr(socket, "type", "") == "MATERIAL"
    )


class _GeometryMaterialScanner:
    """Context-aware, bounded traversal of Geometry Nodes material values."""

    def __init__(self, obj: bpy.types.Object, max_depth: int) -> None:
        self.obj = obj
        self.max_depth = max_depth
        self.materials: list[bpy.types.Material] = []
        self._material_ids: set[int] = set()
        self._tree_contexts: set[tuple[int, tuple[int, ...]]] = set()
        self._active_tree_ids: set[int] = set()
        self._value_visited: set[tuple[int, str, tuple[int, ...]]] = set()
        self._depth_warning_emitted = False
        self._cycle_warning_emitted = False

    def scan(self) -> list[bpy.types.Material]:
        modifiers = getattr(self.obj, "modifiers", ())
        node_modifiers = [
            modifier
            for modifier in modifiers
            if getattr(modifier, "type", "") == "NODES"
        ]
        if not node_modifiers:
            return []

        for modifier in node_modifiers:
            node_group = getattr(modifier, "node_group", None)
            if node_group is not None:
                self._walk_tree(node_group, (), 0)
        return self.materials

    def _add_material(self, material: Any) -> None:
        if not _is_material(material):
            return
        identity = _rna_identity(material)
        if identity in self._material_ids:
            return
        self._material_ids.add(identity)
        self.materials.append(material)

    def _warn_depth(self) -> None:
        if self._depth_warning_emitted:
            return
        self._depth_warning_emitted = True
        log.warning(
            "Geometry Nodes material scan exceeded %d nodes on '%s'; "
            "the remaining deeply nested branch was skipped",
            self.max_depth,
            getattr(self.obj, "name", "Object"),
        )

    def _warn_cycle(self, node_tree: Any) -> None:
        if self._cycle_warning_emitted:
            return
        self._cycle_warning_emitted = True
        log.warning(
            "Geometry Nodes material scan found a recursive group on '%s' "
            "at '%s'; the cyclic branch was skipped",
            getattr(self.obj, "name", "Object"),
            getattr(node_tree, "name", "Geometry Nodes"),
        )

    def _walk_tree(
        self,
        node_tree: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return

        tree_id = _rna_identity(node_tree)
        context_ids = tuple(_rna_identity(context) for context in contexts)
        visit_key = tree_id, context_ids
        if visit_key in self._tree_contexts:
            return
        if tree_id in self._active_tree_ids:
            self._warn_cycle(node_tree)
            return

        self._tree_contexts.add(visit_key)
        self._active_tree_ids.add(tree_id)
        try:
            for node in getattr(node_tree, "nodes", ()):
                node_type = getattr(node, "bl_idname", "")
                if node_type == "GeometryNodeSetMaterial":
                    material_input = _socket_get(
                        getattr(node, "inputs", ()), "Material"
                    )
                    if material_input is not None:
                        self._trace_input(
                            material_input, contexts, depth + 1
                        )

                nested_tree = getattr(node, "node_tree", None)
                is_group = (
                    node_type in _GROUP_NODE_TYPES
                    or getattr(node, "type", "") == "GROUP"
                )
                if is_group and nested_tree is not None:
                    self._walk_tree(
                        nested_tree,
                        (*contexts, node),
                        depth + 1,
                    )
        finally:
            self._active_tree_ids.discard(tree_id)

    def _trace_input(
        self,
        input_socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return

        links = getattr(input_socket, "links", ())
        if not links:
            self._add_material(getattr(input_socket, "default_value", None))
            return
        for link in links:
            self._trace_output(
                link.from_node,
                link.from_socket,
                contexts,
                depth + 1,
            )

    def _trace_output(
        self,
        node: Any,
        output_socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return

        visit_key = (
            _rna_identity(node),
            getattr(output_socket, "identifier", "")
            or getattr(output_socket, "name", ""),
            tuple(_rna_identity(context) for context in contexts),
        )
        if visit_key in self._value_visited:
            return
        self._value_visited.add(visit_key)

        self._add_material(getattr(output_socket, "default_value", None))
        node_type = getattr(node, "bl_idname", "")

        if node_type == "NodeReroute":
            for input_socket in getattr(node, "inputs", ()):
                self._trace_input(input_socket, contexts, depth)
            return

        if node_type in _SWITCH_NODE_TYPES:
            for input_socket in getattr(node, "inputs", ()):
                name = getattr(input_socket, "name", "").casefold()
                if name in _SWITCH_CONTROL_INPUTS:
                    continue
                if (_is_material_socket(input_socket)
                        or getattr(input_socket, "links", ())):
                    self._trace_input(input_socket, contexts, depth)
            return

        nested_tree = getattr(node, "node_tree", None)
        is_group = (
            node_type in _GROUP_NODE_TYPES
            or getattr(node, "type", "") == "GROUP"
        )
        if is_group and nested_tree is not None:
            for group_output in self._group_outputs(nested_tree):
                internal_input = _matching_socket(
                    getattr(node, "outputs", ()),
                    output_socket,
                    getattr(group_output, "inputs", ()),
                )
                if internal_input is not None:
                    self._trace_input(
                        internal_input,
                        (*contexts, node),
                        depth,
                    )
            return

        if node_type == "NodeGroupInput" and contexts:
            group_node = contexts[-1]
            external_input = _matching_socket(
                getattr(node, "outputs", ()),
                output_socket,
                getattr(group_node, "inputs", ()),
            )
            if external_input is not None:
                self._trace_input(
                    external_input, contexts[:-1], depth
                )

    @staticmethod
    def _group_outputs(node_tree: Any) -> list[Any]:
        outputs = [
            node
            for node in getattr(node_tree, "nodes", ())
            if getattr(node, "bl_idname", "") == "NodeGroupOutput"
        ]
        active = [
            node
            for node in outputs
            if bool(getattr(node, "is_active_output", False))
        ]
        return active or outputs


def collect_geometry_node_materials(
    obj: bpy.types.Object,
    max_depth: int = 200,
) -> list[bpy.types.Material]:
    """Return Set Material references from an object's Geometry Nodes.

    Materials are deduplicated by datablock identity and retain deterministic
    first-encountered modifier/node/group order.  Switch branches are scanned
    without attempting to evaluate their active selection.
    """
    if obj is None:
        return []
    return _GeometryMaterialScanner(
        obj, max(1, int(max_depth))
    ).scan()
