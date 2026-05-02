"""Octanify — Conversion cache.

Tracks converted materials and node mappings to prevent duplicate work.
"""

from __future__ import annotations

from typing import Any


class ConversionCache:
    """Session-scoped cache for material conversion de-duplication."""

    def __init__(self) -> None:
        # material name -> converted material name
        self._materials: dict[str, str] = {}
        # (material_name, node_name) -> converted node reference
        self._nodes: dict[tuple[str, str], Any] = {}

    # ----- material level -----

    def has_material(self, mat_name: str) -> bool:
        return mat_name in self._materials

    def register_material(self, original_name: str, converted_name: str) -> None:
        self._materials[original_name] = converted_name

    def get_converted_material_name(self, original_name: str) -> str | None:
        return self._materials.get(original_name)

    # ----- node level -----

    def has_node(self, mat_name: str, node_name: str) -> bool:
        return (mat_name, node_name) in self._nodes

    def register_node(self, mat_name: str, node_name: str, new_node: Any) -> None:
        self._nodes[(mat_name, node_name)] = new_node

    def get_node(self, mat_name: str, node_name: str) -> Any | None:
        return self._nodes.get((mat_name, node_name))

    # ----- session control -----

    def clear(self) -> None:
        self._materials.clear()
        self._nodes.clear()

    @property
    def converted_material_names(self) -> list[str]:
        return list(self._materials.values())
