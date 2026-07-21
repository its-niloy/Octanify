"""Octanify - destination-driven shading intent analysis.

The conversion graph is authored from source nodes toward a material output,
but texture interpretation is determined by the destination.  This module
walks the graph in the opposite direction and records the destination role for
every contributing node output.  The traversal is scene-side-effect free so
it can be exercised with lightweight node-tree test doubles.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from ..utils.logger import get_logger

log = get_logger()


class Role(Enum):
    """Semantic destination of a value in a material shading graph."""

    ALBEDO = "albedo"
    ROUGHNESS = "roughness"
    METALLIC = "metallic"
    NORMAL = "normal"
    BUMP = "bump"
    ALPHA = "alpha"
    EMISSION = "emission"
    SUBSURFACE = "subsurface"
    COAT = "coat"
    SHEEN = "sheen"
    TRANSMISSION = "transmission"
    DISPLACEMENT = "displacement"


class TextureTreatment(Enum):
    """Color-management treatment implied by a particular destination pin."""

    COLOR = "color"
    DATA = "data"


class CoordinateSource(Enum):
    """Texture-coordinate domain feeding a traced procedural node."""

    GENERATED = "Generated"
    OBJECT = "Object"
    UV = "UV"
    CAMERA = "Camera"
    WINDOW = "Window"
    NORMAL = "Normal"
    REFLECTION = "Reflection"


OutputKey = tuple[Any, str]
LinkKey = tuple[Any, str, Any, str]
TerminalInputKey = tuple[Any, str]


def _rna_identity(value: Any) -> int:
    """Return a stable identity for Blender RNA wrappers and test doubles."""
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(value)


def _same_rna(left: Any, right: Any) -> bool:
    return _rna_identity(left) == _rna_identity(right)


class ShadingIntentMap(dict[OutputKey, set[Role]]):
    """Per-output role mapping with path metadata used during conversion.

    The dictionary itself is the public ``(node, output name) -> roles`` map.
    Link and terminal-input metadata are retained separately so callers can
    route multi-role image variants and inspect unlinked terminal constants
    without weakening the public mapping shape.
    """

    def __init__(self) -> None:
        super().__init__()
        self.output_treatments: dict[OutputKey, set[TextureTreatment]] = {}
        self.link_roles: dict[LinkKey, set[Role]] = {}
        self.link_treatments: dict[LinkKey, set[TextureTreatment]] = {}
        self.terminal_inputs: dict[TerminalInputKey, set[Role]] = {}
        self.terminal_treatments: dict[
            TerminalInputKey, set[TextureTreatment]
        ] = {}
        self.coordinate_sources: dict[Any, set[CoordinateSource]] = {}

    @staticmethod
    def _output_key(node: Any, socket_name: str) -> OutputKey:
        return node, socket_name

    @staticmethod
    def _link_key(link: Any) -> LinkKey:
        return (
            link.from_node,
            link.from_socket.name,
            link.to_node,
            link.to_socket.name,
        )

    def add_output(
        self,
        node: Any,
        socket_name: str,
        role: Role,
        treatment: TextureTreatment | None,
    ) -> None:
        key = self._output_key(node, socket_name)
        self.setdefault(key, set()).add(role)
        if treatment is not None:
            self.output_treatments.setdefault(key, set()).add(treatment)

    def add_link(
        self,
        link: Any,
        role: Role,
        treatment: TextureTreatment | None,
    ) -> None:
        key = self._link_key(link)
        self.link_roles.setdefault(key, set()).add(role)
        if treatment is not None:
            self.link_treatments.setdefault(key, set()).add(treatment)

    def add_link_nodes(
        self,
        from_node: Any,
        from_socket: str,
        to_node: Any,
        to_socket: str,
        role: Role,
        treatment: TextureTreatment | None,
    ) -> None:
        """Record a concrete or analyzer-flattened edge."""
        key = from_node, from_socket, to_node, to_socket
        self.link_roles.setdefault(key, set()).add(role)
        if treatment is not None:
            self.link_treatments.setdefault(key, set()).add(treatment)

    def add_terminal_input(
        self,
        node: Any,
        socket_name: str,
        role: Role,
        treatment: TextureTreatment | None,
    ) -> None:
        key = node, socket_name
        self.terminal_inputs.setdefault(key, set()).add(role)
        if treatment is not None:
            self.terminal_treatments.setdefault(key, set()).add(treatment)

    def add_coordinate_source(
        self,
        node: Any,
        source: CoordinateSource,
    ) -> None:
        self.coordinate_sources.setdefault(node, set()).add(source)

    def coordinate_sources_for(self, node: Any) -> set[CoordinateSource]:
        sources = self.coordinate_sources.get(node)
        if sources is not None:
            return set(sources)
        for candidate, candidate_sources in self.coordinate_sources.items():
            if _same_rna(candidate, node):
                return set(candidate_sources)
        return set()

    def roles_for(self, node: Any, output_socket_name: str | None = None) -> set[Role]:
        """Return roles for one output, or the union across a node's outputs."""
        if output_socket_name is not None:
            roles = self.get((node, output_socket_name))
            if roles is not None:
                return set(roles)
            for (candidate, socket_name), candidate_roles in self.items():
                if (socket_name == output_socket_name
                        and _same_rna(candidate, node)):
                    return set(candidate_roles)
            return set()
        roles: set[Role] = set()
        for (candidate, _socket_name), candidate_roles in self.items():
            if _same_rna(candidate, node):
                roles.update(candidate_roles)
        return roles

    def treatments_for(
        self,
        node: Any,
        output_socket_name: str | None = None,
    ) -> set[TextureTreatment]:
        """Return implied treatments for one output or an entire node."""
        if output_socket_name is not None:
            treatments = self.output_treatments.get(
                (node, output_socket_name)
            )
            if treatments is not None:
                return set(treatments)
            for (candidate, socket_name), values in self.output_treatments.items():
                if (socket_name == output_socket_name
                        and _same_rna(candidate, node)):
                    return set(values)
            return set()
        treatments: set[TextureTreatment] = set()
        for (candidate, _socket_name), values in self.output_treatments.items():
            if _same_rna(candidate, node):
                treatments.update(values)
        return treatments

    def roles_for_link(self, from_node: Any, from_socket: str,
                       to_node: Any, to_socket: str) -> set[Role]:
        """Return the roles carried by one concrete graph edge."""
        roles = self.link_roles.get(
            (from_node, from_socket, to_node, to_socket)
        )
        if roles is not None:
            return set(roles)
        for key, candidate_roles in self.link_roles.items():
            candidate_from, candidate_socket, candidate_to, candidate_input = key
            if (candidate_socket == from_socket
                    and candidate_input == to_socket
                    and _same_rna(candidate_from, from_node)
                    and _same_rna(candidate_to, to_node)):
                return set(candidate_roles)
        return set()

    def treatments_for_link(self, from_node: Any, from_socket: str,
                            to_node: Any, to_socket: str) -> set[TextureTreatment]:
        """Return the treatments carried by one concrete graph edge."""
        treatments = self.link_treatments.get(
            (from_node, from_socket, to_node, to_socket)
        )
        if treatments is not None:
            return set(treatments)
        for key, values in self.link_treatments.items():
            candidate_from, candidate_socket, candidate_to, candidate_input = key
            if (candidate_socket == from_socket
                    and candidate_input == to_socket
                    and _same_rna(candidate_from, from_node)
                    and _same_rna(candidate_to, to_node)):
                return set(values)
        return set()

    def has_active_emission(self) -> bool:
        """Return whether a terminal emission color is linked or non-black."""
        for (node, socket_name), roles in self.terminal_inputs.items():
            if Role.EMISSION not in roles:
                continue
            if TextureTreatment.COLOR not in self.terminal_treatments.get(
                (node, socket_name), set()
            ):
                continue
            socket = _socket_get(getattr(node, "inputs", ()), socket_name)
            if socket is None:
                continue
            if getattr(socket, "links", None):
                return True
            value = getattr(socket, "default_value", None)
            if _is_non_black(value):
                return True
        return False

    def has_active_principled_subsurface(self) -> bool:
        """Return whether a traced Principled uses a non-zero SSS weight.

        The terminal-input table is populated by the Phase 1 traversal and
        includes Principled nodes reached through nested shader groups.  A
        linked non-constant weight is treated as potentially active; a direct
        Value node remains statically testable so an authored zero does not
        trigger a material-target override.
        """
        nodes_with_albedo = {
            node
            for (node, _socket_name), roles in self.terminal_inputs.items()
            if getattr(node, "bl_idname", "") == "ShaderNodeBsdfPrincipled"
            and Role.ALBEDO in roles
        }
        for node in nodes_with_albedo:
            for socket_name in ("Subsurface Weight", "Subsurface"):
                roles = self.terminal_inputs.get((node, socket_name), set())
                if Role.SUBSURFACE not in roles:
                    continue
                socket = _socket_get(getattr(node, "inputs", ()), socket_name)
                if socket is not None and _socket_effectively_nonzero(socket):
                    return True
        return False


# Socket name -> (semantic role, color-management treatment).  Treatments
# distinguish color pins from scalar/data pins inside broad roles such as Coat
# and Subsurface without adding implementation-only members to Role.
_PRINCIPLED_INPUTS: dict[str, tuple[Role, TextureTreatment]] = {
    "Base Color": (Role.ALBEDO, TextureTreatment.COLOR),
    "Metallic": (Role.METALLIC, TextureTreatment.DATA),
    "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
    "Diffuse Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
    "Normal": (Role.NORMAL, TextureTreatment.DATA),
    "Alpha": (Role.ALPHA, TextureTreatment.DATA),
    "Emission": (Role.EMISSION, TextureTreatment.COLOR),
    "Emission Color": (Role.EMISSION, TextureTreatment.COLOR),
    "Emission Strength": (Role.EMISSION, TextureTreatment.DATA),
    "Subsurface": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Subsurface Weight": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Subsurface Color": (Role.SUBSURFACE, TextureTreatment.COLOR),
    "Subsurface Radius": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Subsurface Scale": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Subsurface IOR": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Subsurface Anisotropy": (Role.SUBSURFACE, TextureTreatment.DATA),
    "Clearcoat": (Role.COAT, TextureTreatment.DATA),
    "Coat Weight": (Role.COAT, TextureTreatment.DATA),
    "Coat Tint": (Role.COAT, TextureTreatment.COLOR),
    "Clearcoat Roughness": (Role.COAT, TextureTreatment.DATA),
    "Coat Roughness": (Role.COAT, TextureTreatment.DATA),
    "Clearcoat Normal": (Role.COAT, TextureTreatment.DATA),
    "Coat Normal": (Role.COAT, TextureTreatment.DATA),
    "Coat IOR": (Role.COAT, TextureTreatment.DATA),
    "Sheen": (Role.SHEEN, TextureTreatment.DATA),
    "Sheen Weight": (Role.SHEEN, TextureTreatment.DATA),
    "Sheen Tint": (Role.SHEEN, TextureTreatment.COLOR),
    "Sheen Roughness": (Role.SHEEN, TextureTreatment.DATA),
    "Transmission": (Role.TRANSMISSION, TextureTreatment.DATA),
    "Transmission Weight": (Role.TRANSMISSION, TextureTreatment.DATA),
    "Transmission Color": (Role.TRANSMISSION, TextureTreatment.COLOR),
    "Transmission Roughness": (Role.TRANSMISSION, TextureTreatment.DATA),
}

_TERMINAL_INPUTS: dict[
    str, dict[str, tuple[Role, TextureTreatment]]
] = {
    "ShaderNodeBsdfPrincipled": _PRINCIPLED_INPUTS,
    "ShaderNodeBsdfDiffuse": {
        "Color": (Role.ALBEDO, TextureTreatment.COLOR),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfGlossy": {
        "Color": (Role.ALBEDO, TextureTreatment.COLOR),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfGlass": {
        "Color": (Role.TRANSMISSION, TextureTreatment.COLOR),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfRefraction": {
        "Color": (Role.TRANSMISSION, TextureTreatment.COLOR),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeEmission": {
        "Color": (Role.EMISSION, TextureTreatment.COLOR),
        "Strength": (Role.EMISSION, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfMetallic": {
        "Base Color": (Role.ALBEDO, TextureTreatment.COLOR),
        "Edge Tint": (Role.ALBEDO, TextureTreatment.COLOR),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfSheen": {
        "Color": (Role.SHEEN, TextureTreatment.COLOR),
        "Roughness": (Role.SHEEN, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeBsdfToon": {
        "Color": (Role.ALBEDO, TextureTreatment.COLOR),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
    "ShaderNodeSubsurfaceScattering": {
        "Color": (Role.SUBSURFACE, TextureTreatment.COLOR),
        "Scale": (Role.SUBSURFACE, TextureTreatment.DATA),
        "Radius": (Role.SUBSURFACE, TextureTreatment.DATA),
        "Roughness": (Role.ROUGHNESS, TextureTreatment.DATA),
        "Normal": (Role.NORMAL, TextureTreatment.DATA),
    },
}

_MIX_SHADER_TYPES = {"ShaderNodeMixShader", "ShaderNodeAddShader"}

_TERMINAL_PRODUCER_TYPES = {
    "ShaderNodeTexImage",
    "ShaderNodeRGB",
    "ShaderNodeValue",
    "ShaderNodeAttribute",
    "ShaderNodeVertexColor",
    "ShaderNodeTexNoise",
    "ShaderNodeTexVoronoi",
    "ShaderNodeTexWave",
    "ShaderNodeTexMusgrave",
    "ShaderNodeTexChecker",
    "ShaderNodeTexBrick",
    "ShaderNodeTexGradient",
    "ShaderNodeTexMagic",
    "ShaderNodeTexWhiteNoise",
    "ShaderNodeTexGabor",
    "ShaderNodeTexEnvironment",
    "ShaderNodeObjectInfo",
    "ShaderNodeNewGeometry",
    "ShaderNodeCameraData",
    "ShaderNodeParticleInfo",
    "ShaderNodeHairInfo",
    "ShaderNodeLightPath",
    "ShaderNodeTexCoord",
    "ShaderNodeUVMap",
}

_SCALE_MATCHED_PROCEDURAL_TYPES = {
    "ShaderNodeTexNoise",
    "ShaderNodeTexVoronoi",
    "ShaderNodeTexMusgrave",
}

_COORDINATE_OUTPUTS = {
    source.value: source
    for source in CoordinateSource
}


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
            if _same_rna(candidate, socket)
        ),
        -1,
    )


def _matching_socket(source_collection: Any, source_socket: Any,
                     target_collection: Any) -> Any | None:
    identifier = getattr(source_socket, "identifier", "")
    if identifier:
        for candidate in target_collection:
            if getattr(candidate, "identifier", "") == identifier:
                return candidate
    by_name = _socket_get(target_collection, getattr(source_socket, "name", ""))
    if by_name is not None:
        return by_name
    index = _socket_index(source_collection, source_socket)
    if 0 <= index < len(target_collection):
        return target_collection[index]
    return None


def _is_non_black(value: Any) -> bool:
    if value is None or not hasattr(value, "__len__"):
        return False
    try:
        return any(float(channel) > 0.0 for channel in tuple(value)[:3])
    except (TypeError, ValueError):
        return False


def _socket_effectively_nonzero(socket: Any) -> bool:
    """Conservatively evaluate whether a scalar socket can contribute."""
    links = getattr(socket, "links", ())
    if links:
        source_node = links[0].from_node
        source_type = getattr(source_node, "bl_idname", "")
        if source_type == "ShaderNodeValue":
            value = getattr(links[0].from_socket, "default_value", None)
            try:
                return abs(float(value)) > 1e-8
            except (TypeError, ValueError):
                return True
        return True
    value = getattr(socket, "default_value", None)
    try:
        return abs(float(value)) > 1e-8
    except (TypeError, ValueError):
        return False


class _IntentTracer:
    def __init__(self, material_output_node: Any, max_depth: int) -> None:
        self.material_output_node = material_output_node
        self.max_depth = max_depth
        self.result = ShadingIntentMap()
        self._value_visited: set[tuple[int, str, Role, tuple[int, ...]]] = set()
        self._shader_visited: set[tuple[int, str, tuple[int, ...]]] = set()
        self._coordinate_visited: set[
            tuple[int, int, str, tuple[int, ...]]
        ] = set()
        self._depth_warning_emitted = False

    def trace(self) -> ShadingIntentMap:
        surface = _socket_get(
            getattr(self.material_output_node, "inputs", ()), "Surface"
        )
        if surface is not None and getattr(surface, "links", None):
            link = surface.links[0]
            self._walk_shader_output(
                link.from_node, link.from_socket, (), 0
            )

        displacement = _socket_get(
            getattr(self.material_output_node, "inputs", ()), "Displacement"
        )
        if displacement is not None:
            self.result.add_terminal_input(
                self.material_output_node,
                displacement.name,
                Role.DISPLACEMENT,
                TextureTreatment.DATA,
            )
            self._trace_input(
                displacement,
                Role.DISPLACEMENT,
                TextureTreatment.DATA,
                (),
                0,
            )
        return self.result

    def _warn_depth(self) -> None:
        if self._depth_warning_emitted:
            return
        self._depth_warning_emitted = True
        output_name = getattr(self.material_output_node, "name", "Material Output")
        log.warning(
            "Shading intent traversal exceeded %d nodes from '%s'; "
            "the remaining cyclic or deeply nested branch was skipped",
            self.max_depth,
            output_name,
        )

    def _trace_input(
        self,
        input_socket: Any,
        role: Role,
        treatment: TextureTreatment | None,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        links = getattr(input_socket, "links", ())
        if not links:
            return
        link = links[0]
        self.result.add_link(link, role, treatment)
        resolved_node = link.from_node
        resolved_socket = link.from_socket
        reroute_seen: set[int] = set()
        while (getattr(resolved_node, "bl_idname", "") == "NodeReroute"
               and _rna_identity(resolved_node) not in reroute_seen):
            reroute_seen.add(_rna_identity(resolved_node))
            reroute_inputs = getattr(resolved_node, "inputs", ())
            if not reroute_inputs or not getattr(reroute_inputs[0], "links", None):
                break
            upstream = reroute_inputs[0].links[0]
            resolved_node = upstream.from_node
            resolved_socket = upstream.from_socket
        if not _same_rna(resolved_node, link.from_node):
            self.result.add_link_nodes(
                resolved_node,
                getattr(resolved_socket, "name", ""),
                link.to_node,
                link.to_socket.name,
                role,
                treatment,
            )
        self._trace_output(
            link.from_node,
            link.from_socket,
            role,
            treatment,
            contexts,
            depth + 1,
        )

    def _trace_output(
        self,
        node: Any,
        output_socket: Any,
        role: Role,
        treatment: TextureTreatment | None,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return

        node_type = getattr(node, "bl_idname", "")
        if node_type == "NodeReroute":
            inputs = getattr(node, "inputs", ())
            if inputs:
                self._trace_input(inputs[0], role, treatment, contexts, depth)
            return

        visit_key = (
            _rna_identity(node),
            getattr(output_socket, "name", ""),
            role,
            tuple(_rna_identity(context) for context in contexts),
        )
        if visit_key in self._value_visited:
            return
        self._value_visited.add(visit_key)

        output_name = getattr(output_socket, "name", "")
        self.result.add_output(node, output_name, role, treatment)

        if node_type == "ShaderNodeGroup":
            self._enter_group_value(
                node, output_socket, role, treatment, contexts, depth
            )
            return

        if node_type == "NodeGroupInput":
            self._leave_group_value(
                node, output_socket, role, treatment, contexts, depth
            )
            return

        if node_type in _SCALE_MATCHED_PROCEDURAL_TYPES:
            self._trace_coordinate_source(node, contexts, depth)

        if node_type in _TERMINAL_PRODUCER_TYPES:
            return

        inputs = getattr(node, "inputs", ())
        if node_type == "ShaderNodeBump":
            for input_socket in inputs:
                name = getattr(input_socket, "name", "")
                upstream_role = Role.NORMAL if name == "Normal" else Role.BUMP
                self._trace_input(
                    input_socket,
                    upstream_role,
                    TextureTreatment.DATA,
                    contexts,
                    depth,
                )
            return

        if node_type == "ShaderNodeNormalMap":
            for input_socket in inputs:
                self._trace_input(
                    input_socket,
                    Role.NORMAL,
                    TextureTreatment.DATA,
                    contexts,
                    depth,
                )
            return

        # Mix, MixRGB, and Math nodes deliberately fan the same role into all
        # connected branches.  Applying that rule to other value processors is
        # both conservative and correct: every linked input contributes to the
        # output being traced.
        for input_socket in inputs:
            self._trace_input(
                input_socket, role, treatment, contexts, depth
            )

    def _trace_coordinate_source(
        self,
        procedural_node: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        vector = _socket_get(
            getattr(procedural_node, "inputs", ()), "Vector"
        )
        if vector is None or not getattr(vector, "links", None):
            # Cycles procedural textures use Generated coordinates when their
            # vector input is unconnected.
            self.result.add_coordinate_source(
                procedural_node, CoordinateSource.GENERATED
            )
            return
        self._walk_coordinate_input(
            procedural_node, vector, contexts, depth + 1
        )

    def _walk_coordinate_input(
        self,
        procedural_node: Any,
        input_socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        for link in getattr(input_socket, "links", ()):
            self._walk_coordinate_output(
                procedural_node,
                link.from_node,
                link.from_socket,
                contexts,
                depth + 1,
            )

    def _walk_coordinate_output(
        self,
        procedural_node: Any,
        node: Any,
        output_socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return

        visit_key = (
            _rna_identity(procedural_node),
            _rna_identity(node),
            getattr(output_socket, "name", ""),
            tuple(_rna_identity(context) for context in contexts),
        )
        if visit_key in self._coordinate_visited:
            return
        self._coordinate_visited.add(visit_key)

        node_type = getattr(node, "bl_idname", "")
        if node_type == "ShaderNodeTexCoord":
            source = _COORDINATE_OUTPUTS.get(
                getattr(output_socket, "name", "")
            )
            if source is not None:
                self.result.add_coordinate_source(procedural_node, source)
            return
        if node_type == "ShaderNodeUVMap":
            self.result.add_coordinate_source(
                procedural_node, CoordinateSource.UV
            )
            return
        if node_type == "NodeReroute":
            inputs = getattr(node, "inputs", ())
            if inputs:
                self._walk_coordinate_input(
                    procedural_node, inputs[0], contexts, depth
                )
            return
        if node_type == "ShaderNodeGroup":
            for group_output in self._group_outputs(node):
                internal_input = _matching_socket(
                    node.outputs, output_socket, group_output.inputs
                )
                if internal_input is not None:
                    self._walk_coordinate_input(
                        procedural_node,
                        internal_input,
                        (*contexts, node),
                        depth,
                    )
            return
        if node_type == "NodeGroupInput":
            if not contexts:
                return
            group_node = contexts[-1]
            external_input = _matching_socket(
                node.outputs, output_socket, group_node.inputs
            )
            if external_input is not None:
                self._walk_coordinate_input(
                    procedural_node,
                    external_input,
                    contexts[:-1],
                    depth,
                )
            return

        # Mapping and vector-processing nodes remain transparent to source
        # classification.  This shares the same socket/group context rules as
        # destination-intent tracing instead of maintaining a converter-side
        # link walk.
        for candidate in getattr(node, "inputs", ()):
            if getattr(candidate, "links", None):
                self._walk_coordinate_input(
                    procedural_node, candidate, contexts, depth
                )

    def _group_outputs(self, group_node: Any) -> list[Any]:
        node_tree = getattr(group_node, "node_tree", None)
        if node_tree is None:
            return []
        outputs = [
            node for node in getattr(node_tree, "nodes", ())
            if getattr(node, "bl_idname", "") == "NodeGroupOutput"
        ]
        active = [
            node for node in outputs if bool(getattr(node, "is_active_output", False))
        ]
        return active or outputs

    def _enter_group_value(
        self,
        group_node: Any,
        output_socket: Any,
        role: Role,
        treatment: TextureTreatment | None,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        for group_output in self._group_outputs(group_node):
            internal_input = _matching_socket(
                group_node.outputs, output_socket, group_output.inputs
            )
            if internal_input is not None:
                self._trace_input(
                    internal_input,
                    role,
                    treatment,
                    (*contexts, group_node),
                    depth,
                )

    def _leave_group_value(
        self,
        group_input: Any,
        output_socket: Any,
        role: Role,
        treatment: TextureTreatment | None,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if not contexts:
            return
        group_node = contexts[-1]
        external_input = _matching_socket(
            group_input.outputs, output_socket, group_node.inputs
        )
        if external_input is not None:
            self._trace_input(
                external_input,
                role,
                treatment,
                contexts[:-1],
                depth,
            )

    def _walk_shader_output(
        self,
        node: Any,
        output_socket: Any,
        contexts: tuple[Any, ...],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            self._warn_depth()
            return
        node_type = getattr(node, "bl_idname", "")
        if node_type == "NodeReroute":
            inputs = getattr(node, "inputs", ())
            if inputs and getattr(inputs[0], "links", None):
                link = inputs[0].links[0]
                self._walk_shader_output(
                    link.from_node, link.from_socket, contexts, depth + 1
                )
            return

        visit_key = (
            _rna_identity(node),
            getattr(output_socket, "name", ""),
            tuple(_rna_identity(context) for context in contexts),
        )
        if visit_key in self._shader_visited:
            return
        self._shader_visited.add(visit_key)

        if node_type == "ShaderNodeGroup":
            for group_output in self._group_outputs(node):
                internal_input = _matching_socket(
                    node.outputs, output_socket, group_output.inputs
                )
                if internal_input is None or not getattr(internal_input, "links", None):
                    continue
                link = internal_input.links[0]
                self._walk_shader_output(
                    link.from_node,
                    link.from_socket,
                    (*contexts, node),
                    depth + 1,
                )
            return

        if node_type == "NodeGroupInput":
            if not contexts:
                return
            group_node = contexts[-1]
            external_input = _matching_socket(
                node.outputs, output_socket, group_node.inputs
            )
            if external_input is not None and getattr(external_input, "links", None):
                link = external_input.links[0]
                self._walk_shader_output(
                    link.from_node,
                    link.from_socket,
                    contexts[:-1],
                    depth + 1,
                )
            return

        if node_type in _MIX_SHADER_TYPES:
            for input_socket in getattr(node, "inputs", ()):
                if getattr(input_socket, "name", "") in {"Fac", "Factor"}:
                    continue
                if not getattr(input_socket, "links", None):
                    continue
                link = input_socket.links[0]
                self._walk_shader_output(
                    link.from_node, link.from_socket, contexts, depth + 1
                )
            return

        terminal_inputs = _TERMINAL_INPUTS.get(node_type)
        if terminal_inputs is None:
            return
        for input_socket in getattr(node, "inputs", ()):
            intent = terminal_inputs.get(getattr(input_socket, "name", ""))
            if intent is None:
                continue
            role, treatment = intent
            self.result.add_terminal_input(
                node, input_socket.name, role, treatment
            )
            self._trace_input(
                input_socket, role, treatment, contexts, depth
            )


def trace_shading_intent(
    material_output_node: Any,
    max_depth: int = 200,
) -> ShadingIntentMap:
    """Trace destination roles backward from a Material Output node.

    Reroutes are transparent, nested node groups retain their caller context,
    and every connected input of a value-processing node is followed.  Cycles
    and pathological group recursion are bounded by ``max_depth`` and a
    role-aware visited set.
    """
    if material_output_node is None:
        return ShadingIntentMap()
    return _IntentTracer(material_output_node, max(1, int(max_depth))).trace()
