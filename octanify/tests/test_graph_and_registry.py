from __future__ import annotations

import sys
import types
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _install_bpy_stub() -> None:
    if "bpy" in sys.modules:
        return

    bpy = types.ModuleType("bpy")
    blender_types = {
        name: type(name, (), {})
        for name in (
            "Context",
            "Image",
            "Material",
            "Node",
            "NodeLink",
            "NodeSocket",
            "NodeTree",
            "Nodes",
            "Object",
            "Operator",
            "Panel",
        )
    }
    bpy.types = SimpleNamespace(**blender_types)
    bpy.props = SimpleNamespace(
        EnumProperty=lambda **_kwargs: None,
        FloatProperty=lambda **_kwargs: None,
    )
    bpy.utils = SimpleNamespace(
        register_class=lambda _cls: None,
        unregister_class=lambda _cls: None,
    )
    bpy.context = SimpleNamespace(scene=SimpleNamespace())
    sys.modules["bpy"] = bpy


_install_bpy_stub()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from octanify.core.graph_engine import GraphEngine
from octanify.core.gamma_system import apply_gamma
from octanify.core.layout_engine import arrange_nodes, style_smart_graphs
from octanify.core.conversion_engine import (
    _apply_scale_correction,
    _handle_alpha,
    _handle_emission_node_insertion,
    _handle_normal_map_fallback,
    _handle_principled_material_inputs,
    _rebuild_links,
    _route_original_outputs_to_cycles,
    collect_material_work_items,
    convert_material,
    convert_objects_materials,
    reset_cache,
)
from octanify.core.geonodes_scan import collect_geometry_node_materials
from octanify.core.node_registry import (
    NODE_TYPE_MAP,
    get_contextual_node_candidates,
    resolve_input_socket,
    resolve_output_socket,
)
from octanify.core.property_mapper import (
    _transfer_displacement,
    _transfer_glass,
    _transfer_image_texture,
    _transfer_mapping,
    _transfer_principled,
    _transfer_rgb_curve,
)
from octanify.core.report import report_data
from octanify.core.shading_intent import (
    Role,
    ShadingIntentMap,
    TextureTreatment,
    trace_shading_intent,
)
from octanify.core.shader_detection import analyze_tree
from octanify.core.volumetric_handler import handle_volumetrics
from octanify.ui.operators import (
    OCTANIFY_OT_arrange_node_tree,
    OCTANIFY_OT_convert,
    _delete_cycles_nodes_from_material,
    _find_preferred_material_node,
    _guess_texture_socket,
    _node_tree_from_context,
    _set_progress,
)
from octanify.utils.cache import ConversionCache
import bpy


class _Sockets(list):
    def get(self, name: str):
        return next((socket for socket in self if socket.name == name), None)


class _Socket:
    def __init__(self, name: str, default_value=None) -> None:
        self.name = name
        self.identifier = name
        self.default_value = default_value
        self.links = []


class _Node:
    def __init__(self, name: str, bl_idname: str, inputs=(), outputs=()) -> None:
        self.name = name
        self.label = ""
        self.bl_idname = bl_idname
        self.inputs = _Sockets(inputs)
        self.outputs = _Sockets(outputs)
        self.location = SimpleNamespace(x=0.0, y=0.0)
        self.parent = None
        self.width = 160.0
        self.dimensions = SimpleNamespace(y=140.0)


class _Links(list):
    def new(self, from_socket, to_socket):
        link = SimpleNamespace(
            from_node=from_socket.node,
            from_socket=from_socket,
            to_node=to_socket.node,
            to_socket=to_socket,
        )
        self.append(link)
        from_socket.links.append(link)
        to_socket.links.append(link)
        return link

    def remove(self, link) -> None:
        super().remove(link)
        link.from_socket.links.remove(link)
        link.to_socket.links.remove(link)


class _Nodes(list):
    def get(self, name: str):
        return next((node for node in self if node.name == name), None)

    def new(self, type: str):
        if type == "ShaderNodeOutputMaterial":
            node = _Node(
                "Material Output",
                type,
                inputs=[
                    _Socket("Surface", None),
                    _Socket("Volume", None),
                    _Socket("Displacement", None),
                ],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in ("ShaderNodeOctChannelPickerTex", "OctaneChannelPicker"):
            node = _Node(
                "Channel Picker",
                type,
                inputs=[_Socket("Input", None), _Socket("Channel", "Red")],
                outputs=[_Socket("OutTex")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in ("OctaneAlphaImage", "ShaderNodeOctAlphaImage"):
            node = _Node(
                "Alpha Image",
                type,
                inputs=[_Socket("Legacy gamma", 2.2)],
                outputs=[_Socket("OutTex")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in (
            "OctaneRGBImage",
            "ShaderNodeOctImageTex",
            "OctaneImageTexture",
            "OctaneGreyscaleImage",
            "ShaderNodeOctGreyscaleImage",
        ):
            node = _Node(
                "Image",
                type,
                inputs=[_Socket("Legacy gamma", 1.0)],
                outputs=[_Socket("Texture out")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"):
            node = _Node(
                "Multiply",
                type,
                inputs=[_Socket("Texture 1", None), _Socket("Texture 2", 1.0)],
                outputs=[_Socket("Texture out")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in (
            "OctaneSeparateColor",
            "ShaderNodeOctColorCorrectionTex",
            "OctaneColorCorrection",
        ):
            node = _Node(
                "Separate",
                type,
                inputs=[_Socket("Input", None), _Socket("Mask", 1.0)],
                outputs=[_Socket("OutTex")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in (
            "OctaneCyclesNodeMathNodeWrapper",
            "OctaneBinaryMathOperation",
        ):
            node = _Node(
                "Math",
                type,
                inputs=[_Socket("Value 1", None), _Socket("Value 2", 1.0)],
                outputs=[_Socket("Value")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type in (
            "OctaneStandardSurfaceMaterial",
            "ShaderNodeOctStandardSurfaceMat",
            "OctaneUniversalMaterial",
            "ShaderNodeOctUniversalMat",
        ):
            node = _Node(
                "Material",
                type,
                inputs=[
                    _Socket("Base color", None),
                    _Socket("Specular roughness", None),
                ],
                outputs=[_Socket("Material out")],
            )
            _attach_sockets(node)
            self.append(node)
            return node
        if type not in ("ShaderNodeOctTextureEmission", "OctaneTextureEmission"):
            raise RuntimeError(type)
        node = _Node(
            "Texture Emission",
            type,
            inputs=[
                _Socket("Texture", (0.0, 0.0, 0.0, 1.0)),
                _Socket("Power", 1.0),
                _Socket("Surface brightness", False),
            ],
            outputs=[_Socket("OutEmission")],
        )
        _attach_sockets(node)
        self.append(node)
        return node


def _attach_sockets(node: _Node) -> _Node:
    for socket in [*node.inputs, *node.outputs]:
        socket.node = node
    return node


def _node_info(bl_idname: str) -> SimpleNamespace:
    return SimpleNamespace(bl_idname=bl_idname)


def _link(source: str, target: str, **overrides) -> SimpleNamespace:
    values = {
        "from_node": source,
        "to_node": target,
        "from_socket": "Value",
        "to_socket": "Value",
        "from_socket_identifier": "Value",
        "to_socket_identifier": "Value",
        "to_socket_index": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class GraphScheduleTests(unittest.TestCase):
    def test_creates_all_target_output_for_octane(self) -> None:
        output_info = SimpleNamespace(
            bl_idname="ShaderNodeOutputMaterial",
            location=(320.0, 0.0),
            properties={},
        )
        analysis = SimpleNamespace(
            nodes=OrderedDict((("Material Output", output_info),)),
            links=[],
        )
        tree = SimpleNamespace(
            name="Output Test",
            nodes=_Nodes(),
            links=_Links(),
        )

        node_map = GraphEngine(
            analysis,
            reuse_output_nodes=False,
        ).create_nodes(tree)

        self.assertEqual(
            node_map["Material Output"].bl_idname,
            "ShaderNodeOutputMaterial",
        )
        self.assertEqual(node_map["Material Output"].target, "ALL")

    def test_smart_conversion_reserves_original_output_for_cycles(self) -> None:
        output = _Node("Material Output", "ShaderNodeOutputMaterial")
        output.target = "ALL"

        _route_original_outputs_to_cycles([output])

        self.assertEqual(output.target, "CYCLES")

    def test_only_active_duplicate_material_output_is_rebuilt(self) -> None:
        active = SimpleNamespace(
            bl_idname="ShaderNodeOutputMaterial",
            location=(320.0, 0.0),
            properties={"is_active_output": True},
        )
        inactive = SimpleNamespace(
            bl_idname="ShaderNodeOutputMaterial",
            location=(320.0, -240.0),
            properties={"is_active_output": False},
        )
        analysis = SimpleNamespace(
            nodes=OrderedDict((("Active", active), ("Inactive", inactive))),
            links=[],
        )
        tree = SimpleNamespace(name="Outputs", nodes=_Nodes(), links=_Links())

        engine = GraphEngine(analysis, reuse_output_nodes=False)
        node_map = engine.create_nodes(tree)

        self.assertIn("Active", node_map)
        self.assertNotIn("Inactive", node_map)
        self.assertTrue(engine.is_skipped_material_output("Inactive"))

    def test_explicit_cycles_output_wins_over_other_renderer_activity(self) -> None:
        cycles = SimpleNamespace(
            bl_idname="ShaderNodeOutputMaterial",
            location=(320.0, 0.0),
            properties={
                "is_active_output": False,
                "octanify_cycles_output": True,
            },
        )
        other_renderer = SimpleNamespace(
            bl_idname="ShaderNodeOutputMaterial",
            location=(320.0, -240.0),
            properties={"is_active_output": True},
        )
        analysis = SimpleNamespace(
            nodes=OrderedDict(
                (("Cycles Output", cycles), ("Other Output", other_renderer))
            ),
            links=[],
        )
        tree = SimpleNamespace(name="Outputs", nodes=_Nodes(), links=_Links())

        node_map = GraphEngine(analysis, reuse_output_nodes=False).create_nodes(tree)

        self.assertIn("Cycles Output", node_map)
        self.assertNotIn("Other Output", node_map)
        self.assertTrue(node_map["Cycles Output"].is_active_output)

    def test_link_to_inactive_duplicate_output_is_not_a_false_failure(self) -> None:
        shader = _attach_sockets(
            _Node("Shader", "OctaneStandardSurfaceMaterial", outputs=[_Socket("OutMat")])
        )
        analysis = SimpleNamespace(
            nodes=OrderedDict(
                (
                    ("Shader", _node_info("ShaderNodeBsdfPrincipled")),
                    (
                        "Active",
                        SimpleNamespace(
                            bl_idname="ShaderNodeOutputMaterial",
                            properties={"is_active_output": True},
                        ),
                    ),
                    (
                        "Inactive",
                        SimpleNamespace(
                            bl_idname="ShaderNodeOutputMaterial",
                            properties={"is_active_output": False},
                        ),
                    ),
                )
            ),
            links=[
                _link(
                    "Shader",
                    "Inactive",
                    from_socket="BSDF",
                    to_socket="Surface",
                )
            ],
        )
        engine = GraphEngine(analysis)
        tree = SimpleNamespace(name="Outputs", nodes=_Nodes(), links=_Links())
        report_data.clear()

        _rebuild_links(analysis, {"Shader": shader}, tree, engine)

        self.assertEqual(report_data.links_failed, 0)

    def test_preserves_disconnected_nodes(self) -> None:
        nodes = OrderedDict(
            (
                ("Texture", _node_info("ShaderNodeTexImage")),
                ("Shader", _node_info("ShaderNodeBsdfPrincipled")),
                ("Output", _node_info("ShaderNodeOutputMaterial")),
                ("Staged", _node_info("ShaderNodeValue")),
            )
        )
        analysis = SimpleNamespace(
            nodes=nodes,
            links=[_link("Texture", "Shader"), _link("Shader", "Output")],
        )

        schedule = GraphEngine(analysis).compute_schedule()

        self.assertEqual(schedule[:3], ["Texture", "Shader", "Output"])
        self.assertIn("Staged", schedule)
        self.assertEqual(len(schedule), len(nodes))

    def test_handles_deep_graph_without_recursion_error(self) -> None:
        count = 2_000
        nodes = OrderedDict(
            (f"Node{index}", _node_info("ShaderNodeMath"))
            for index in range(count)
        )
        nodes["Output"] = _node_info("ShaderNodeOutputMaterial")
        links = [
            _link(f"Node{index}", f"Node{index + 1}")
            for index in range(count - 1)
        ]
        links.append(_link(f"Node{count - 1}", "Output"))
        analysis = SimpleNamespace(nodes=nodes, links=links)

        schedule = GraphEngine(analysis).compute_schedule()

        self.assertEqual(schedule[0], "Node0")
        self.assertEqual(schedule[-1], "Output")
        self.assertEqual(len(schedule), count + 1)

    def test_cycle_terminates_and_schedules_each_node_once(self) -> None:
        nodes = OrderedDict(
            (name, _node_info("ShaderNodeMath")) for name in ("A", "B", "C")
        )
        analysis = SimpleNamespace(
            nodes=nodes,
            links=[_link("A", "B"), _link("B", "C"), _link("C", "A")],
        )

        schedule = GraphEngine(analysis).compute_schedule()

        self.assertCountEqual(schedule, nodes)
        self.assertEqual(len(schedule), len(set(schedule)))


class TreeAnalysisTests(unittest.TestCase):
    def test_geometry_source_is_reportable_instead_of_silently_flattened(self) -> None:
        geometry = _attach_sockets(
            _Node(
                "Geometry",
                "ShaderNodeNewGeometry",
                outputs=[_Socket("Position", (0.0, 0.0, 0.0))],
            )
        )
        mapping = _attach_sockets(
            _Node(
                "Mapping",
                "ShaderNodeMapping",
                inputs=[_Socket("Vector", (0.0, 0.0, 0.0))],
                outputs=[_Socket("Vector", (0.0, 0.0, 0.0))],
            )
        )
        tree = SimpleNamespace(nodes=[geometry, mapping], links=_Links())
        tree.links.new(geometry.outputs[0], mapping.inputs[0])

        analysis = analyze_tree(tree)

        self.assertIn("Geometry", analysis.nodes)
        self.assertEqual(len(analysis.links), 1)
        self.assertEqual(analysis.links[0].from_socket, "Position")

    def test_legacy_principled_emission_is_detected(self) -> None:
        principled = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[
                    _Socket("Emission", (0.2, 0.1, 0.0, 1.0)),
                    _Socket("Emission Strength", 2.0),
                ],
                outputs=[_Socket("BSDF")],
            )
        )
        tree = SimpleNamespace(nodes=[principled], links=_Links())

        analysis = analyze_tree(tree)

        self.assertTrue(analysis.has_emission)

    def test_material_output_activity_is_snapshotted(self) -> None:
        output = _attach_sockets(
            _Node("Material Output", "ShaderNodeOutputMaterial")
        )
        output.is_active_output = True
        tree = SimpleNamespace(nodes=[output], links=_Links())

        analysis = analyze_tree(tree)

        self.assertTrue(
            analysis.nodes["Material Output"].properties["is_active_output"]
        )

    def test_blender_cycles_output_resolution_is_snapshotted(self) -> None:
        cycles = _Node("Cycles Output", "ShaderNodeOutputMaterial")
        cycles.is_active_output = False
        other = _Node("Other Output", "ShaderNodeOutputMaterial")
        other.is_active_output = True
        tree = SimpleNamespace(
            nodes=[cycles, other],
            links=_Links(),
            get_output_node=lambda target: cycles if target == "CYCLES" else other,
        )

        analysis = analyze_tree(tree)

        self.assertTrue(
            analysis.nodes["Cycles Output"].properties["octanify_cycles_output"]
        )


class ShadingIntentTests(unittest.TestCase):
    @staticmethod
    def _material_output():
        return _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[
                    _Socket("Surface", None),
                    _Socket("Displacement", None),
                ],
            )
        )

    def test_roles_are_per_output_and_per_path(self) -> None:
        image = _attach_sockets(
            _Node(
                "Image",
                "ShaderNodeTexImage",
                outputs=[_Socket("Color"), _Socket("Alpha")],
            )
        )
        separate = _attach_sockets(
            _Node(
                "Separate",
                "ShaderNodeSeparateColor",
                inputs=[_Socket("Color", None)],
                outputs=[_Socket("Red")],
            )
        )
        math = _attach_sockets(
            _Node(
                "Math",
                "ShaderNodeMath",
                inputs=[_Socket("Value", 0.0)],
                outputs=[_Socket("Value")],
            )
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[
                    _Socket("Base Color", (0.8, 0.8, 0.8, 1.0)),
                    _Socket("Roughness", 0.5),
                ],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(image.outputs.get("Color"), shader.inputs.get("Base Color"))
        links.new(image.outputs.get("Color"), separate.inputs.get("Color"))
        links.new(separate.outputs.get("Red"), math.inputs.get("Value"))
        links.new(math.outputs.get("Value"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        intent = trace_shading_intent(output)

        self.assertEqual(
            intent.roles_for(image, "Color"),
            {Role.ALBEDO, Role.ROUGHNESS},
        )
        self.assertEqual(
            intent.treatments_for_link(
                image, "Color", shader, "Base Color"
            ),
            {TextureTreatment.COLOR},
        )
        self.assertEqual(
            intent.treatments_for_link(
                image, "Color", separate, "Color"
            ),
            {TextureTreatment.DATA},
        )

    def test_alpha_output_is_detected_only_on_alpha_destination_path(self) -> None:
        image = _attach_sockets(
            _Node(
                "Image",
                "ShaderNodeTexImage",
                outputs=[_Socket("Color"), _Socket("Alpha")],
            )
        )
        math = _attach_sockets(
            _Node(
                "Math",
                "ShaderNodeMath",
                inputs=[_Socket("Value", 0.0)],
                outputs=[_Socket("Value")],
            )
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[_Socket("Alpha", 1.0)],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(image.outputs.get("Alpha"), math.inputs.get("Value"))
        links.new(math.outputs.get("Value"), shader.inputs.get("Alpha"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        intent = trace_shading_intent(output)

        self.assertEqual(intent.roles_for(image, "Alpha"), {Role.ALPHA})
        self.assertNotIn((image, "Color"), intent)

    def test_reroute_is_transparent_and_records_flattened_edge_intent(self) -> None:
        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        reroute = _attach_sockets(
            _Node(
                "Reroute",
                "NodeReroute",
                inputs=[_Socket("Input", None)],
                outputs=[_Socket("Output")],
            )
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[_Socket("Roughness", 0.5)],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(image.outputs.get("Color"), reroute.inputs.get("Input"))
        links.new(reroute.outputs.get("Output"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        intent = trace_shading_intent(output)

        self.assertEqual(intent.roles_for(image, "Color"), {Role.ROUGHNESS})
        self.assertEqual(intent.roles_for(reroute), set())
        self.assertEqual(
            intent.treatments_for_link(
                image, "Color", shader, "Roughness"
            ),
            {TextureTreatment.DATA},
        )

    def test_three_nested_groups_cross_both_boundaries(self) -> None:
        def passthrough_tree(name: str, source_node=None):
            group_input = _attach_sockets(
                _Node(
                    f"{name} Input",
                    "NodeGroupInput",
                    outputs=[_Socket("In")],
                )
            )
            group_output = _attach_sockets(
                _Node(
                    f"{name} Output",
                    "NodeGroupOutput",
                    inputs=[_Socket("Out", None)],
                )
            )
            links = _Links()
            nodes = _Nodes([group_input, group_output])
            if source_node is None:
                links.new(group_input.outputs.get("In"), group_output.inputs.get("Out"))
            else:
                nodes.insert(1, source_node)
                links.new(group_input.outputs.get("In"), source_node.inputs.get("In"))
                links.new(source_node.outputs.get("Out"), group_output.inputs.get("Out"))
            return SimpleNamespace(name=name, nodes=nodes, links=links)

        group3 = _attach_sockets(
            _Node(
                "Group 3",
                "ShaderNodeGroup",
                inputs=[_Socket("In", None)],
                outputs=[_Socket("Out")],
            )
        )
        group3.node_tree = passthrough_tree("Level 3")
        group2 = _attach_sockets(
            _Node(
                "Group 2",
                "ShaderNodeGroup",
                inputs=[_Socket("In", None)],
                outputs=[_Socket("Out")],
            )
        )
        group2.node_tree = passthrough_tree("Level 2", group3)
        group1 = _attach_sockets(
            _Node(
                "Group 1",
                "ShaderNodeGroup",
                inputs=[_Socket("In", None)],
                outputs=[_Socket("Out")],
            )
        )
        group1.node_tree = passthrough_tree("Level 1", group2)

        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[_Socket("Base Color", (0.8, 0.8, 0.8, 1.0))],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(image.outputs.get("Color"), group1.inputs.get("In"))
        links.new(group1.outputs.get("Out"), shader.inputs.get("Base Color"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        intent = trace_shading_intent(output)

        self.assertEqual(intent.roles_for(image, "Color"), {Role.ALBEDO})
        for group in (group1, group2, group3):
            self.assertEqual(intent.roles_for(group, "Out"), {Role.ALBEDO})

    def test_non_black_zero_strength_emission_is_active(self) -> None:
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[
                    _Socket("Emission Color", (0.2, 0.1, 0.0, 1.0)),
                    _Socket("Emission Strength", 0.0),
                ],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        intent = trace_shading_intent(output)

        self.assertTrue(intent.has_active_emission())

    def test_role_queries_use_stable_rna_identity(self) -> None:
        original = _Node("Image", "ShaderNodeTexImage")
        wrapper = _Node("Image", "ShaderNodeTexImage")
        original.as_pointer = lambda: 4242
        wrapper.as_pointer = lambda: 4242
        intent = ShadingIntentMap()
        intent.add_output(
            original,
            "Color",
            Role.ALBEDO,
            TextureTreatment.COLOR,
        )

        self.assertEqual(intent.roles_for(wrapper), {Role.ALBEDO})
        self.assertEqual(
            intent.treatments_for(wrapper, "Color"),
            {TextureTreatment.COLOR},
        )

    def test_depth_cap_logs_once_and_stops_the_branch(self) -> None:
        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        math = _attach_sockets(
            _Node(
                "Math",
                "ShaderNodeMath",
                inputs=[_Socket("Value", 0.0)],
                outputs=[_Socket("Value")],
            )
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[_Socket("Roughness", 0.5)],
                outputs=[_Socket("BSDF")],
            )
        )
        output = self._material_output()
        links = _Links()
        links.new(image.outputs.get("Color"), math.inputs.get("Value"))
        links.new(math.outputs.get("Value"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))

        with patch("octanify.core.shading_intent.log.warning") as warning:
            intent = trace_shading_intent(output, max_depth=1)

        warning.assert_called_once()
        self.assertNotIn((image, "Color"), intent)


class GeometryNodesMaterialScanTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()
        reset_cache()

    @staticmethod
    def _material(name: str):
        material = bpy.types.Material()
        material.name = name
        return material

    @staticmethod
    def _set_material(name: str, material=None):
        return _attach_sockets(
            _Node(
                name,
                "GeometryNodeSetMaterial",
                inputs=[
                    _Socket("Selection", True),
                    _Socket("Material", material),
                ],
                outputs=[_Socket("Geometry")],
            )
        )

    @staticmethod
    def _object(node_tree=None, *, material_slots=()):
        modifiers = (
            [SimpleNamespace(type="NODES", node_group=node_tree)]
            if node_tree is not None
            else []
        )
        return SimpleNamespace(
            name="Geometry Object",
            modifiers=modifiers,
            material_slots=list(material_slots),
        )

    def test_direct_set_material_is_stable_and_deduplicated(self) -> None:
        material_a = self._material("Material A")
        material_b = self._material("Material B")
        tree = SimpleNamespace(
            name="Geometry",
            nodes=_Nodes([
                self._set_material("First", material_b),
                self._set_material("Duplicate", material_b),
                self._set_material("Last", material_a),
            ]),
        )

        materials = collect_geometry_node_materials(self._object(tree))

        self.assertEqual(materials, [material_b, material_a])

    def test_switch_collects_every_material_branch(self) -> None:
        material_a = self._material("Material A")
        material_b = self._material("Material B")
        false_socket = _Socket("False", material_a)
        false_socket.bl_idname = "NodeSocketMaterial"
        true_socket = _Socket("True", material_b)
        true_socket.bl_idname = "NodeSocketMaterial"
        switch = _attach_sockets(
            _Node(
                "Material Switch",
                "GeometryNodeSwitch",
                inputs=[
                    _Socket("Switch", False),
                    false_socket,
                    true_socket,
                ],
                outputs=[_Socket("Output")],
            )
        )
        set_material = self._set_material("Set Material")
        links = _Links()
        links.new(
            switch.outputs.get("Output"),
            set_material.inputs.get("Material"),
        )
        tree = SimpleNamespace(
            name="Switch Geometry",
            nodes=_Nodes([switch, set_material]),
            links=links,
        )

        materials = collect_geometry_node_materials(self._object(tree))

        self.assertEqual(materials, [material_a, material_b])

    def test_index_switch_collects_every_material_branch(self) -> None:
        material_a = self._material("Material A")
        material_b = self._material("Material B")
        first_socket = _Socket("0", material_a)
        first_socket.bl_idname = "NodeSocketMaterial"
        second_socket = _Socket("1", material_b)
        second_socket.bl_idname = "NodeSocketMaterial"
        switch = _attach_sockets(
            _Node(
                "Material Index Switch",
                "GeometryNodeIndexSwitch",
                inputs=[
                    _Socket("Index", 0),
                    first_socket,
                    second_socket,
                ],
                outputs=[_Socket("Output")],
            )
        )
        set_material = self._set_material("Set Material")
        links = _Links()
        links.new(
            switch.outputs.get("Output"),
            set_material.inputs.get("Material"),
        )
        tree = SimpleNamespace(
            name="Index Switch Geometry",
            nodes=_Nodes([switch, set_material]),
            links=links,
        )

        materials = collect_geometry_node_materials(self._object(tree))

        self.assertEqual(materials, [material_a, material_b])

    def test_nested_group_resolves_material_across_group_input(self) -> None:
        material = self._material("Nested Material")
        group_input = _attach_sockets(
            _Node(
                "Group Input",
                "NodeGroupInput",
                outputs=[_Socket("Material")],
            )
        )
        set_material = self._set_material("Nested Set Material")
        inner_links = _Links()
        inner_links.new(
            group_input.outputs.get("Material"),
            set_material.inputs.get("Material"),
        )
        inner_tree = SimpleNamespace(
            name="Inner Geometry",
            nodes=_Nodes([group_input, set_material]),
            links=inner_links,
        )
        inner_group = _attach_sockets(
            _Node(
                "Inner Group",
                "GeometryNodeGroup",
                inputs=[_Socket("Material", material)],
            )
        )
        inner_group.node_tree = inner_tree
        middle_tree = SimpleNamespace(
            name="Middle Geometry",
            nodes=_Nodes([inner_group]),
            links=_Links(),
        )
        outer_group = _attach_sockets(
            _Node("Outer Group", "GeometryNodeGroup")
        )
        outer_group.node_tree = middle_tree
        root_tree = SimpleNamespace(
            name="Root Geometry",
            nodes=_Nodes([outer_group]),
            links=_Links(),
        )

        materials = collect_geometry_node_materials(
            self._object(root_tree)
        )

        self.assertEqual(materials, [material])

    def test_no_geometry_nodes_or_set_material_is_empty(self) -> None:
        self.assertEqual(
            collect_geometry_node_materials(self._object()),
            [],
        )
        empty_tree = SimpleNamespace(
            name="Empty Geometry",
            nodes=_Nodes([_Node("Join", "GeometryNodeJoinGeometry")]),
            links=_Links(),
        )
        self.assertEqual(
            collect_geometry_node_materials(self._object(empty_tree)),
            [],
        )

    def test_recursive_group_cycle_logs_once_and_stops(self) -> None:
        recursive_group = _attach_sockets(
            _Node("Recursive Group", "GeometryNodeGroup")
        )
        tree = SimpleNamespace(
            name="Recursive Geometry",
            nodes=_Nodes([recursive_group]),
            links=_Links(),
        )
        recursive_group.node_tree = tree

        with patch(
            "octanify.core.geonodes_scan.log.warning"
        ) as warning:
            materials = collect_geometry_node_materials(self._object(tree))

        self.assertEqual(materials, [])
        warning.assert_called_once()
        self.assertIn("recursive group", warning.call_args.args[0])

    def test_depth_cap_logs_once_and_stops_deep_branch(self) -> None:
        material = self._material("Deep Material")
        leaf = SimpleNamespace(
            name="Leaf Geometry",
            nodes=_Nodes([self._set_material("Deep Set", material)]),
            links=_Links(),
        )
        middle_group = _attach_sockets(
            _Node("Middle Group", "GeometryNodeGroup")
        )
        middle_group.node_tree = leaf
        middle = SimpleNamespace(
            name="Middle Geometry",
            nodes=_Nodes([middle_group]),
            links=_Links(),
        )
        root_group = _attach_sockets(
            _Node("Root Group", "GeometryNodeGroup")
        )
        root_group.node_tree = middle
        root = SimpleNamespace(
            name="Root Geometry",
            nodes=_Nodes([root_group]),
            links=_Links(),
        )

        with patch(
            "octanify.core.geonodes_scan.log.warning"
        ) as warning:
            materials = collect_geometry_node_materials(
                self._object(root), max_depth=1
            )

        self.assertEqual(materials, [])
        warning.assert_called_once()
        self.assertIn("exceeded %d nodes", warning.call_args.args[0])

    def test_slot_and_geometry_reference_convert_once_via_cache(self) -> None:
        class _Material(dict):
            def __init__(self) -> None:
                super().__init__()
                self.name = "Shared Material"
                self.node_tree = SimpleNamespace(nodes=[])

        material = _Material()
        slot = SimpleNamespace(material=material)
        obj = self._object(material_slots=[slot])
        bpy.data = SimpleNamespace(
            materials=SimpleNamespace(
                get=lambda name: material if name == material.name else None
            )
        )

        with patch(
            "octanify.core.conversion_engine.collect_geometry_node_materials",
            return_value=[material],
        ), patch(
            "octanify.core.conversion_engine.analyze_tree",
            return_value=SimpleNamespace(nodes={}, links=[], has_emission=False),
        ), patch(
            "octanify.core.conversion_engine._populate_converted_material",
            return_value=[],
        ) as populate, patch(
            "octanify.core.conversion_engine.style_smart_graphs"
        ):
            converted = convert_objects_materials([obj])

        self.assertEqual(converted, [material])
        self.assertEqual(report_data.materials_converted, 1)
        populate.assert_called_once()
        self.assertTrue(
            any(
                "1 unique material(s) via Geometry Nodes vs 1 via normal slots"
                in notice
                for notice in report_data.notices
            )
        )

    def test_report_notice_counts_unique_materials_per_source(self) -> None:
        material_a = self._material("Material A")
        material_b = self._material("Material B")
        first = self._object(
            material_slots=[
                SimpleNamespace(material=material_a),
                SimpleNamespace(material=material_a),
            ]
        )
        second = self._object(
            material_slots=[SimpleNamespace(material=material_a)]
        )

        with patch(
            "octanify.core.conversion_engine.collect_geometry_node_materials",
            side_effect=[[material_a, material_b], [material_a, material_b]],
        ):
            work_items = collect_material_work_items([first, second])

        self.assertEqual(len(work_items), 7)
        self.assertTrue(
            any(
                "2 unique material(s) via Geometry Nodes vs 1 via normal slots"
                in notice
                for notice in report_data.notices
            )
        )


class OperatorUtilityTests(unittest.TestCase):
    def test_arrange_action_uses_currently_edited_nested_tree(self) -> None:
        source = _attach_sockets(
            _Node(
                "Source",
                "ShaderNodeValue",
                outputs=[_Socket("Value")],
            )
        )
        target = _attach_sockets(
            _Node(
                "Target",
                "ShaderNodeMath",
                inputs=[_Socket("Value")],
                outputs=[_Socket("Value")],
            )
        )
        links = _Links()
        links.new(source.outputs[0], target.inputs[0])
        nested_tree = SimpleNamespace(nodes=[source, target], links=links)
        material_tree = SimpleNamespace(nodes=[], links=[])
        context = SimpleNamespace(
            scene=SimpleNamespace(octanify_progress_active=False),
            space_data=SimpleNamespace(
                type="NODE_EDITOR",
                edit_tree=nested_tree,
                node_tree=material_tree,
            ),
            active_object=None,
        )
        operator = OCTANIFY_OT_arrange_node_tree()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertIs(_node_tree_from_context(context), nested_tree)
        self.assertTrue(operator.poll(context))
        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertLess(source.location[0], target.location[0])
        self.assertTrue(any("Arranged 2 node(s)" in message for _, message in reports))

    def test_texture_filename_inference_still_has_regex_runtime(self) -> None:
        self.assertEqual(
            _guess_texture_socket(
                "headphone_base_color.png", "OctaneStandardSurfaceMaterial"
            ),
            "Albedo color",
        )
        self.assertEqual(
            _guess_texture_socket("earpad_roughness.exr", "ShaderNodeBsdfPrincipled"),
            "Roughness",
        )

    def test_auto_connect_prefers_octane_in_smart_dual_graph(self) -> None:
        cycles = _Node("Principled", "ShaderNodeBsdfPrincipled")
        octane = _Node("Standard", "OctaneStandardSurfaceMaterial")

        self.assertIs(_find_preferred_material_node([cycles, octane]), octane)

    def test_delete_cycles_nodes_uses_graph_tags_and_activates_octane_output(self) -> None:
        class _TaggedNode(dict):
            def __init__(self, name: str, bl_idname: str, graph_kind: str) -> None:
                super().__init__(octanify_graph=graph_kind)
                self.name = name
                self.bl_idname = bl_idname
                self.target = "CYCLES" if graph_kind == "cycles" else "ALL"
                self.is_active_output = graph_kind == "cycles"

        cycles_shader = _TaggedNode(
            "Principled",
            "ShaderNodeBsdfPrincipled",
            "cycles",
        )
        cycles_output = _TaggedNode(
            "Cycles Output",
            "ShaderNodeOutputMaterial",
            "cycles",
        )
        octane_shader = _TaggedNode(
            "Standard Surface",
            "OctaneStandardSurfaceMaterial",
            "octane",
        )
        octane_output = _TaggedNode(
            "Octane Output",
            "ShaderNodeOutputMaterial",
            "octane",
        )
        nodes = _Nodes(
            [cycles_shader, cycles_output, octane_shader, octane_output]
        )
        material = SimpleNamespace(node_tree=SimpleNamespace(nodes=nodes))

        deleted = _delete_cycles_nodes_from_material(material)

        self.assertEqual(deleted, 2)
        self.assertEqual(list(nodes), [octane_shader, octane_output])
        self.assertTrue(octane_output.is_active_output)
        self.assertEqual(octane_output.target, "ALL")

    def test_progress_callback_updates_percentage_and_label(self) -> None:
        updates = []
        context = SimpleNamespace(
            window_manager=SimpleNamespace(
                progress_update=lambda value: updates.append(value)
            ),
            scene=SimpleNamespace(
                octanify_progress=0,
                octanify_progress_label="",
            ),
            workspace=SimpleNamespace(status_text_set=lambda **_kwargs: None),
            screen=SimpleNamespace(areas=[]),
            window=None,
        )

        _set_progress(context, 1, 4, "Creating nodes")

        self.assertEqual(updates, [25])
        self.assertEqual(context.scene.octanify_progress, 25)
        self.assertEqual(context.scene.octanify_progress_label, "Creating nodes")

    def test_modal_conversion_finishes_with_visible_100_percent_state(self) -> None:
        report_data.clear()
        progress_updates = []
        progress_ended = []
        reports = []
        material = SimpleNamespace(name="Modal Material")
        slot = SimpleNamespace(material=material)
        obj = SimpleNamespace(name="Modal Object")
        context = SimpleNamespace(
            window_manager=SimpleNamespace(
                progress_update=lambda value: progress_updates.append(value),
                progress_end=lambda: progress_ended.append(True),
                event_timer_remove=lambda _timer: None,
            ),
            scene=SimpleNamespace(
                octanify_progress=0,
                octanify_progress_label="",
                octanify_progress_active=True,
            ),
            workspace=SimpleNamespace(status_text_set=lambda **_kwargs: None),
            screen=SimpleNamespace(areas=[]),
            window=object(),
        )
        operator = OCTANIFY_OT_convert()
        operator.report = lambda level, message: reports.append((level, message))
        operator._batch_mode = "ACTIVE"
        operator._gamma = 2.2
        operator._objects = [obj]
        operator._work_items = [(obj, material, slot)]
        operator._work_index = 0
        operator._timer = None

        with patch(
            "octanify.ui.operators.convert_material",
            return_value=material,
        ) as convert_mock:
            operator._auto_arrange = False
            operator._color_nodes = False
            result = operator.modal(context, SimpleNamespace(type="TIMER"))

        self.assertEqual(result, {"FINISHED"})
        self.assertIn(100, progress_updates)
        self.assertEqual(context.scene.octanify_progress, 100)
        self.assertEqual(context.scene.octanify_progress_label, "Conversion complete")
        self.assertFalse(context.scene.octanify_progress_active)
        self.assertEqual(progress_ended, [True])
        self.assertFalse(convert_mock.call_args.kwargs["auto_arrange"])
        self.assertFalse(convert_mock.call_args.kwargs["color_nodes"])


class LayoutTests(unittest.TestCase):
    @staticmethod
    def _node(
        name: str,
        bl_idname: str,
        input_names: tuple[str, ...] = (),
        output_names: tuple[str, ...] = (),
        location: tuple[float, float] = (0.0, 0.0),
    ) -> _Node:
        node = _attach_sockets(
            _Node(
                name,
                bl_idname,
                inputs=[_Socket(socket_name) for socket_name in input_names],
                outputs=[_Socket(socket_name) for socket_name in output_names],
            )
        )
        node.location.x, node.location.y = location
        return node

    @staticmethod
    def _xy(node: _Node) -> tuple[float, float]:
        try:
            return float(node.location.x), float(node.location.y)
        except AttributeError:
            return float(node.location[0]), float(node.location[1])

    def test_branch_order_follows_destination_socket_order(self) -> None:
        first = self._node(
            "First", "ShaderNodeTexImage", output_names=("Color",),
            location=(0.0, 0.0),
        )
        second = self._node(
            "Second", "ShaderNodeTexImage", output_names=("Color",),
            location=(0.0, 300.0),
        )
        mix = self._node(
            "Mix", "ShaderNodeMix", input_names=("A", "B"),
            output_names=("Result",), location=(400.0, 0.0),
        )
        tree = SimpleNamespace(nodes=[first, second, mix], links=_Links())
        tree.links.new(first.outputs[0], mix.inputs[0])
        tree.links.new(second.outputs[0], mix.inputs[1])

        arrange_nodes(tree, tree.nodes, (0.0, 0.0))

        self.assertGreater(self._xy(first)[1], self._xy(second)[1])
        self.assertGreater(self._xy(mix)[0], self._xy(first)[0])

    def test_crossing_reduction_reorders_intermediate_branches(self) -> None:
        upper = self._node(
            "Upper", "ShaderNodeTexImage", output_names=("Color",),
            location=(0.0, 300.0),
        )
        lower = self._node(
            "Lower", "ShaderNodeTexImage", output_names=("Color",),
            location=(0.0, 0.0),
        )
        first_math = self._node(
            "First Math", "ShaderNodeMath", input_names=("Value",),
            output_names=("Value",), location=(400.0, 300.0),
        )
        second_math = self._node(
            "Second Math", "ShaderNodeMath", input_names=("Value",),
            output_names=("Value",), location=(400.0, 0.0),
        )
        shader = self._node(
            "Shader", "ShaderNodeBsdfPrincipled",
            input_names=("Roughness", "Metallic"),
            output_names=("BSDF",), location=(800.0, 0.0),
        )
        tree = SimpleNamespace(
            nodes=[upper, lower, first_math, second_math, shader],
            links=_Links(),
        )
        tree.links.new(upper.outputs[0], second_math.inputs[0])
        tree.links.new(lower.outputs[0], first_math.inputs[0])
        tree.links.new(first_math.outputs[0], shader.inputs[0])
        tree.links.new(second_math.outputs[0], shader.inputs[1])

        arrange_nodes(tree, tree.nodes, (0.0, 0.0))

        self.assertGreater(
            (
                self._xy(upper)[1] - self._xy(lower)[1]
            ) * (
                self._xy(second_math)[1] - self._xy(first_math)[1]
            ),
            0.0,
        )
        self.assertGreater(self._xy(shader)[0], self._xy(first_math)[0])

    def test_cycles_share_a_column_without_overlapping(self) -> None:
        first = self._node(
            "First", "ShaderNodeMath", input_names=("Value",),
            output_names=("Value",),
        )
        second = self._node(
            "Second", "ShaderNodeMath", input_names=("Value",),
            output_names=("Value",),
        )
        output = self._node(
            "Output", "ShaderNodeOutputMaterial", input_names=("Surface",),
        )
        tree = SimpleNamespace(nodes=[first, second, output], links=_Links())
        tree.links.new(first.outputs[0], second.inputs[0])
        tree.links.new(second.outputs[0], first.inputs[0])
        tree.links.new(second.outputs[0], output.inputs[0])

        arrange_nodes(tree, tree.nodes, (0.0, 0.0))

        self.assertEqual(self._xy(first)[0], self._xy(second)[0])
        self.assertNotEqual(self._xy(first)[1], self._xy(second)[1])
        self.assertGreater(self._xy(output)[0], self._xy(first)[0])

    def test_disconnected_components_are_packed_without_overlap(self) -> None:
        source = self._node(
            "Source", "ShaderNodeTexImage", output_names=("Color",),
        )
        target = self._node(
            "Target", "ShaderNodeBsdfPrincipled", input_names=("Base Color",),
        )
        isolated = self._node("Isolated", "ShaderNodeValue")
        tree = SimpleNamespace(nodes=[source, target, isolated], links=_Links())
        tree.links.new(source.outputs[0], target.inputs[0])

        arrange_nodes(tree, tree.nodes, (0.0, 0.0))

        self.assertLess(
            self._xy(isolated)[1],
            min(self._xy(source)[1], self._xy(target)[1]) - 140.0,
        )

    def test_frame_cluster_moves_without_changing_child_coordinates(self) -> None:
        frame = self._node(
            "Frame", "NodeFrame", location=(100.0, 300.0)
        )
        child = self._node(
            "Child", "ShaderNodeTexImage", output_names=("Color",),
            location=(40.0, -60.0),
        )
        child.parent = frame
        output = self._node(
            "Output", "ShaderNodeOutputMaterial", input_names=("Surface",),
            location=(700.0, 0.0),
        )
        tree = SimpleNamespace(nodes=[frame, child, output], links=_Links())
        tree.links.new(child.outputs[0], output.inputs[0])

        arrange_nodes(tree, tree.nodes, (0.0, 0.0))

        self.assertEqual(self._xy(child), (40.0, -60.0))
        self.assertNotEqual(self._xy(frame), (100.0, 300.0))
        self.assertGreater(self._xy(output)[0], self._xy(frame)[0])

    def test_layout_is_idempotent_for_the_same_origin(self) -> None:
        source = self._node(
            "Source", "ShaderNodeTexImage", output_names=("Color",),
        )
        target = self._node(
            "Target", "ShaderNodeBsdfPrincipled", input_names=("Base Color",),
        )
        tree = SimpleNamespace(nodes=[source, target], links=_Links())
        tree.links.new(source.outputs[0], target.inputs[0])

        arrange_nodes(tree, tree.nodes, (25.0, 75.0))
        first_layout = [self._xy(node) for node in tree.nodes]
        arrange_nodes(tree, tree.nodes, (25.0, 75.0))

        self.assertEqual(
            [self._xy(node) for node in tree.nodes], first_layout
        )

    def test_framed_authored_graph_arranges_frame_contents(self) -> None:
        frame = self._node("Frame", "NodeFrame", location=(100.0, 300.0))
        source = self._node(
            "Source",
            "ShaderNodeTexImage",
            output_names=("Color",),
            location=(500.0, -300.0),
        )
        target = self._node(
            "Target",
            "ShaderNodeBsdfPrincipled",
            input_names=("Base Color",),
            location=(-400.0, 250.0),
        )
        source.parent = frame
        target.parent = frame
        authored = self._node(
            "Authored", "ShaderNodeBsdfPrincipled", location=(700.0, 50.0)
        )
        converted = self._node(
            "Converted", "OctaneStandardSurfaceMaterial"
        )
        tree = SimpleNamespace(
            nodes=[frame, source, target, authored, converted],
            links=_Links(),
        )
        tree.links.new(source.outputs[0], target.inputs[0])

        style_smart_graphs(
            tree,
            [frame, source, target, authored],
            [converted],
            auto_arrange=True,
        )

        self.assertLess(self._xy(source)[0], self._xy(target)[0])
        self.assertGreater(
            self._xy(converted)[0],
            max(self._xy(frame)[0], self._xy(authored)[0]),
        )

    def test_graph_tags_remain_when_custom_colors_are_disabled(self) -> None:
        cycles = self._node("Cycles", "ShaderNodeBsdfPrincipled")
        octane = self._node("Octane", "OctaneStandardSurfaceMaterial")
        tree = SimpleNamespace(nodes=[cycles, octane], links=[])

        style_smart_graphs(
            tree,
            [cycles],
            [octane],
            auto_arrange=False,
            colorize=False,
        )

        self.assertFalse(cycles.use_custom_color)
        self.assertFalse(octane.use_custom_color)


class SocketResolutionTests(unittest.TestCase):
    def test_rgb_curve_factor_maps_to_color_correction_mask(self) -> None:
        node = _attach_sockets(
            _Node(
                "Color correction",
                "OctaneColorCorrection",
                inputs=[_Socket("Input"), _Socket("Mask", 1.0)],
                outputs=[_Socket("Texture out")],
            )
        )

        resolved_input = resolve_input_socket(
            "ShaderNodeRGBCurve",
            "Factor",
            node,
            socket_identifier="Factor",
            socket_index=0,
        )
        resolved_output = resolve_output_socket(
            "ShaderNodeRGBCurve",
            "Color",
            node,
            socket_identifier="Color",
        )

        self.assertIs(resolved_input, node.inputs.get("Mask"))
        self.assertIs(resolved_output, node.outputs.get("Texture out"))

    def test_unique_identifier_wins_for_duplicate_mix_shader_names(self) -> None:
        material_1 = SimpleNamespace(name="Material1")
        material_2 = SimpleNamespace(name="Material2")
        node = SimpleNamespace(
            name="Mix",
            bl_idname="OctaneMixMaterial",
            inputs=_Sockets([material_1, material_2]),
        )

        resolved = resolve_input_socket(
            "ShaderNodeMixShader",
            "Shader",
            node,
            socket_identifier="Shader_001",
            socket_index=2,
        )

        self.assertIs(resolved, material_2)

    def test_multi_output_node_does_not_silently_alias_first_output(self) -> None:
        node = SimpleNamespace(
            name="Channels",
            bl_idname="OctaneUnknownMultiOutput",
            outputs=_Sockets(
                [SimpleNamespace(name="First"), SimpleNamespace(name="Second")]
            ),
        )

        resolved = resolve_output_socket("UnknownCyclesNode", "Blue", node)

        self.assertIsNone(resolved)

    def test_mapping_and_coordinates_use_separate_octane_image_pins(self) -> None:
        coordinates = _attach_sockets(
            _Node(
                "Coordinates",
                "OctaneMeshUVProjection",
                outputs=[_Socket("OutProjection")],
            )
        )
        mapping = _attach_sockets(
            _Node(
                "Mapping",
                "Octane3DTransformation",
                inputs=[
                    _Socket("Rotation order", "YXZ"),
                    _Socket("Rotation", (0.0, 0.0, 0.0)),
                ],
                outputs=[_Socket("OutTransform")],
            )
        )
        image = _attach_sockets(
            _Node(
                "Image",
                "OctaneRGBImage",
                inputs=[_Socket("UV transform"), _Socket("Projection")],
                outputs=[_Socket("OutTex")],
            )
        )
        tree = SimpleNamespace(
            name="MappedMaterial",
            nodes=_Nodes([coordinates, mapping, image]),
            links=_Links(),
        )
        analysis = SimpleNamespace(
            nodes={
                "Coordinates": _node_info("ShaderNodeTexCoord"),
                "Mapping": _node_info("ShaderNodeMapping"),
                "Image": _node_info("ShaderNodeTexImage"),
            },
            links=[
                _link(
                    "Coordinates",
                    "Mapping",
                    from_socket="UV",
                    from_socket_identifier="UV",
                    to_socket="Vector",
                    to_socket_identifier="Vector",
                ),
                _link(
                    "Mapping",
                    "Image",
                    from_socket="Vector",
                    from_socket_identifier="Vector",
                    to_socket="Vector",
                    to_socket_identifier="Vector",
                ),
            ],
        )

        _rebuild_links(
            analysis,
            {"Coordinates": coordinates, "Mapping": mapping, "Image": image},
            tree,
        )

        self.assertIs(
            image.inputs.get("Projection").links[0].from_node,
            coordinates,
        )
        self.assertIs(
            image.inputs.get("UV transform").links[0].from_node,
            mapping,
        )
        self.assertEqual(len(mapping.inputs.get("Rotation order").links), 0)


class ModernOctaneNodeTests(unittest.TestCase):
    def test_modern_material_nodes_are_preferred_over_legacy_ids(self) -> None:
        self.assertEqual(
            NODE_TYPE_MAP["ShaderNodeBsdfPrincipled"][0],
            "OctaneUniversalMaterial",
        )
        self.assertEqual(
            NODE_TYPE_MAP["ShaderNodeMixShader"][0],
            "OctaneMixMaterial",
        )

    def test_rgb_curve_uses_blenders_singular_rna_identifier(self) -> None:
        self.assertEqual(
            NODE_TYPE_MAP["ShaderNodeRGBCurve"][0],
            "OctaneColorCorrection",
        )
        self.assertNotIn("ShaderNodeRGBCurves", NODE_TYPE_MAP)

    def test_rgb_curve_creates_color_correction_without_unsupported_fallback(self) -> None:
        info = SimpleNamespace(
            bl_idname="ShaderNodeRGBCurve",
            label="RGB Curves",
            location=(0.0, 0.0),
            properties={},
        )
        analysis = SimpleNamespace(
            nodes=OrderedDict((("RGB Curves", info),)),
            links=[],
        )
        tree = SimpleNamespace(
            name="RGB Curve Group",
            nodes=_Nodes(),
            links=_Links(),
        )
        report_data.clear()

        node_map = GraphEngine(analysis).create_nodes(tree)

        self.assertEqual(
            node_map["RGB Curves"].bl_idname,
            "OctaneColorCorrection",
        )
        self.assertEqual(report_data.nodes_unsupported, 0)
        self.assertTrue(
            any("cannot preserve arbitrary curves" in message
                for message in report_data.approximations)
        )


class ContextualImageTests(unittest.TestCase):
    def test_mixed_color_and_alpha_usage_prefers_rgb_image(self) -> None:
        analysis = SimpleNamespace(
            links=[
                _link(
                    "Image",
                    "Shader",
                    from_socket="Color",
                    to_socket="Base Color",
                ),
                _link(
                    "Image",
                    "Shader",
                    from_socket="Alpha",
                    to_socket="Alpha",
                ),
            ]
        )

        candidates = get_contextual_node_candidates(
            "ShaderNodeTexImage", analysis, "Image"
        )

        self.assertEqual(candidates[0], "OctaneRGBImage")

    def test_missing_rgb_alpha_output_creates_alpha_image_variant(self) -> None:
        report_data.clear()
        rgb_image = _attach_sockets(
            _Node("Image", "OctaneRGBImage", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "ShaderNodeOctUniversalMat",
                inputs=[_Socket("Opacity", 1.0)],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="AlphaMat_OCTANE",
            nodes=_Nodes([rgb_image, material]),
            links=_Links(),
        )
        tree.links.new(rgb_image.outputs[0], material.inputs.get("Opacity"))
        image_info = SimpleNamespace(
            bl_idname="ShaderNodeTexImage",
            label="Packed Image",
            properties={"colorspace": "sRGB", "image_user": {}},
        )
        analysis = SimpleNamespace(
            has_alpha=True,
            nodes={
                "Image": image_info,
                "Material": _node_info("ShaderNodeBsdfPrincipled"),
            },
            links=[
                _link(
                    "Image",
                    "Material",
                    from_socket="Alpha",
                    to_socket="Alpha",
                    from_socket_identifier="Alpha",
                    to_socket_identifier="Alpha",
                    to_socket_index=4,
                )
            ],
        )

        _handle_alpha(
            analysis,
            {"Image": rgb_image, "Material": material},
            tree,
        )

        alpha_image = tree.nodes[-1]
        self.assertIn(alpha_image.bl_idname, ("OctaneAlphaImage", "ShaderNodeOctAlphaImage"))
        self.assertEqual(alpha_image.inputs.get("Legacy gamma").default_value, 1.0)
        self.assertIs(material.inputs.get("Opacity").links[0].from_node, alpha_image)


class IntentGammaTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()

    @staticmethod
    def _source_graph(colorspace: str, include_color_branch: bool):
        image = _attach_sockets(
            _Node(
                "Image",
                "ShaderNodeTexImage",
                outputs=[_Socket("Color")],
            )
        )
        image.image = SimpleNamespace(
            name="packed.png",
            filepath="//textures/packed.png",
            colorspace_settings=SimpleNamespace(name=colorspace),
        )
        separate = _attach_sockets(
            _Node(
                "Separate",
                "ShaderNodeSeparateColor",
                inputs=[_Socket("Color", None)],
                outputs=[_Socket("Red")],
            )
        )
        shader_inputs = [_Socket("Roughness", 0.5)]
        if include_color_branch:
            shader_inputs.insert(0, _Socket("Base Color", (0.8, 0.8, 0.8, 1.0)))
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=shader_inputs,
                outputs=[_Socket("BSDF")],
            )
        )
        output = _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[_Socket("Surface", None), _Socket("Displacement", None)],
            )
        )
        links = _Links()
        if include_color_branch:
            links.new(image.outputs.get("Color"), shader.inputs.get("Base Color"))
        links.new(image.outputs.get("Color"), separate.inputs.get("Color"))
        links.new(separate.outputs.get("Red"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))
        tree = SimpleNamespace(
            name="Source",
            nodes=_Nodes([image, separate, shader, output]),
            links=links,
        )
        return tree, image, separate, shader, output

    @staticmethod
    def _image_analysis(colorspace: str):
        info = SimpleNamespace(
            name="Image",
            bl_idname="ShaderNodeTexImage",
            label="Packed",
            location=(0.0, 0.0),
            properties={
                "colorspace": colorspace,
                "image_name": "packed.png",
                "filepath": "//textures/packed.png",
            },
        )
        return SimpleNamespace(
            nodes=OrderedDict((("Image", info),)),
            links=[],
        )

    def test_srgb_color_data_conflict_creates_and_routes_two_instances(self) -> None:
        source_tree, _image, separate, shader, output = self._source_graph(
            "sRGB", True
        )
        intent = trace_shading_intent(output)
        analysis = self._image_analysis("sRGB")
        target_tree = SimpleNamespace(
            name="Material_OCTANE", nodes=_Nodes(), links=_Links()
        )
        engine = GraphEngine(
            analysis,
            intent_map=intent,
            source_tree=source_tree,
            report_context_name="Material",
        )
        node_map = engine.create_nodes(target_tree)

        color_link = _link(
            "Image",
            "Principled",
            from_socket="Color",
            to_socket="Base Color",
        )
        data_link = _link(
            "Image",
            "Separate",
            from_socket="Color",
            to_socket="Color",
        )
        color_node = engine.source_node_for(color_link, node_map)
        data_node = engine.source_node_for(data_link, node_map)

        self.assertIsNot(color_node, data_node)
        self.assertEqual(len(engine.created_nodes_for("Image", node_map)), 2)
        self.assertEqual(color_node.label, "Packed")
        self.assertEqual(data_node.label, "Packed [Data]")
        self.assertTrue(
            any("created 2 texture instances" in notice for notice in report_data.notices)
        )

        material = SimpleNamespace(name="Material", node_tree=target_tree)
        apply_gamma(
            material,
            2.2,
            analysis=analysis,
            node_map=node_map,
            graph_engine=engine,
        )

        self.assertEqual(color_node.inputs.get("Legacy gamma").default_value, 2.2)
        self.assertEqual(data_node.inputs.get("Legacy gamma").default_value, 1.0)
        self.assertTrue(
            any("feeds Roughness but is set to sRGB" in warning
                for warning in report_data.warnings)
        )

    def test_shared_channel_split_pairs_each_texture_treatment(self) -> None:
        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        image.image = SimpleNamespace(
            name="packed.png",
            filepath="//textures/packed.png",
            colorspace_settings=SimpleNamespace(name="sRGB"),
        )
        separate = _attach_sockets(
            _Node(
                "Separate",
                "ShaderNodeSeparateColor",
                inputs=[_Socket("Color", None)],
                outputs=[_Socket("Red"), _Socket("Green")],
            )
        )
        separate.mode = "RGB"
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[
                    _Socket("Base Color", (0.8, 0.8, 0.8, 1.0)),
                    _Socket("Roughness", 0.5),
                ],
                outputs=[_Socket("BSDF")],
            )
        )
        output = _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[_Socket("Surface", None), _Socket("Displacement", None)],
            )
        )
        links = _Links()
        links.new(image.outputs.get("Color"), separate.inputs.get("Color"))
        links.new(separate.outputs.get("Red"), shader.inputs.get("Base Color"))
        links.new(separate.outputs.get("Green"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))
        source_tree = SimpleNamespace(
            name="Shared Split",
            nodes=_Nodes([image, separate, shader, output]),
            links=links,
        )
        analysis = analyze_tree(source_tree)
        intent = trace_shading_intent(output)
        target_tree = SimpleNamespace(
            name="Shared Split OCTANE", nodes=_Nodes(), links=_Links()
        )
        engine = GraphEngine(
            analysis,
            intent_map=intent,
            source_tree=source_tree,
            report_context_name="Shared Split",
        )
        node_map = engine.create_nodes(target_tree)

        incoming = next(
            link for link in analysis.links
            if link.from_node == "Image" and link.to_node == "Separate"
        )
        red = next(
            link for link in analysis.links
            if link.from_node == "Separate" and link.from_socket == "Red"
        )
        green = next(
            link for link in analysis.links
            if link.from_node == "Separate" and link.from_socket == "Green"
        )
        pairs = engine.link_node_pairs(incoming, node_map)
        variants = {
            treatment: node
            for node, treatment in engine.image_variants_for("Image", node_map)
        }

        self.assertEqual(len(pairs), 2)
        self.assertIn(
            (variants[TextureTreatment.COLOR], engine.source_node_for(red, node_map)),
            pairs,
        )
        self.assertIn(
            (variants[TextureTreatment.DATA], engine.source_node_for(green, node_map)),
            pairs,
        )

    def test_shared_math_chain_is_duplicated_by_treatment(self) -> None:
        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        image.image = SimpleNamespace(
            name="packed.png",
            filepath="//textures/packed.png",
            colorspace_settings=SimpleNamespace(name="sRGB"),
        )
        math_node = _attach_sockets(
            _Node(
                "Math",
                "ShaderNodeMath",
                inputs=[_Socket("Value", 0.0), _Socket("Value", 1.0)],
                outputs=[_Socket("Value")],
            )
        )
        math_node.operation = "MULTIPLY"
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[
                    _Socket("Base Color", (0.8, 0.8, 0.8, 1.0)),
                    _Socket("Roughness", 0.5),
                ],
                outputs=[_Socket("BSDF")],
            )
        )
        output = _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[_Socket("Surface", None), _Socket("Displacement", None)],
            )
        )
        links = _Links()
        links.new(image.outputs.get("Color"), math_node.inputs[0])
        links.new(math_node.outputs.get("Value"), shader.inputs.get("Base Color"))
        links.new(math_node.outputs.get("Value"), shader.inputs.get("Roughness"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))
        source_tree = SimpleNamespace(
            name="Shared Math",
            nodes=_Nodes([image, math_node, shader, output]),
            links=links,
        )
        analysis = analyze_tree(source_tree)
        intent = trace_shading_intent(output)
        target_tree = SimpleNamespace(
            name="Shared Math OCTANE", nodes=_Nodes(), links=_Links()
        )
        engine = GraphEngine(
            analysis,
            intent_map=intent,
            source_tree=source_tree,
            report_context_name="Shared Math",
        )
        node_map = engine.create_nodes(target_tree)

        incoming = next(
            link for link in analysis.links
            if link.from_node == "Image" and link.to_node == "Math"
        )
        color_link = next(
            link for link in analysis.links
            if link.from_node == "Math" and link.to_socket == "Base Color"
        )
        data_link = next(
            link for link in analysis.links
            if link.from_node == "Math" and link.to_socket == "Roughness"
        )
        pairs = engine.link_node_pairs(incoming, node_map)

        self.assertEqual(len(pairs), 2)
        self.assertEqual(len(engine.created_nodes_for("Math", node_map)), 2)
        self.assertIsNot(
            engine.source_node_for(color_link, node_map),
            engine.source_node_for(data_link, node_map),
        )

    def test_correct_non_color_roughness_stays_linear_without_warning(self) -> None:
        source_tree, _image, _separate, _shader, output = self._source_graph(
            "Non-Color", False
        )
        intent = trace_shading_intent(output)
        analysis = self._image_analysis("Non-Color")
        target_tree = SimpleNamespace(
            name="Roughness_OCTANE", nodes=_Nodes(), links=_Links()
        )
        engine = GraphEngine(
            analysis,
            intent_map=intent,
            source_tree=source_tree,
            report_context_name="Roughness",
        )
        node_map = engine.create_nodes(target_tree)

        apply_gamma(
            SimpleNamespace(name="Roughness", node_tree=target_tree),
            2.2,
            analysis=analysis,
            node_map=node_map,
            graph_engine=engine,
        )

        node = node_map["Image"]
        self.assertEqual(node.inputs.get("Legacy gamma").default_value, 1.0)
        self.assertEqual(report_data.warnings, [])

    def test_non_color_albedo_uses_color_gamma_and_warns(self) -> None:
        image = _attach_sockets(
            _Node("Image", "ShaderNodeTexImage", outputs=[_Socket("Color")])
        )
        shader = _attach_sockets(
            _Node(
                "Principled",
                "ShaderNodeBsdfPrincipled",
                inputs=[_Socket("Base Color", (0.8, 0.8, 0.8, 1.0))],
                outputs=[_Socket("BSDF")],
            )
        )
        output = _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[_Socket("Surface", None), _Socket("Displacement", None)],
            )
        )
        links = _Links()
        links.new(image.outputs.get("Color"), shader.inputs.get("Base Color"))
        links.new(shader.outputs.get("BSDF"), output.inputs.get("Surface"))
        source_tree = SimpleNamespace(
            name="Source",
            nodes=_Nodes([image, shader, output]),
            links=links,
        )
        intent = trace_shading_intent(output)
        analysis = self._image_analysis("Non-Color")
        target_tree = SimpleNamespace(
            name="Albedo_OCTANE", nodes=_Nodes(), links=_Links()
        )
        engine = GraphEngine(
            analysis,
            intent_map=intent,
            source_tree=source_tree,
            report_context_name="Albedo",
        )
        node_map = engine.create_nodes(target_tree)

        apply_gamma(
            SimpleNamespace(name="Albedo", node_tree=target_tree),
            2.2,
            analysis=analysis,
            node_map=node_map,
            graph_engine=engine,
        )

        node = node_map["Image"]
        self.assertEqual(node.inputs.get("Legacy gamma").default_value, 2.2)
        self.assertTrue(
            any(
                "feeds Base Color but is set to Non-Color" in warning
                for warning in report_data.warnings
            )
        )


class ChannelSplitExpansionTests(unittest.TestCase):
    def test_rgb_outputs_expand_to_distinct_channel_picker_nodes(self) -> None:
        primary = _attach_sockets(
            _Node(
                "Fallback Split",
                "ShaderNodeOctColorCorrectionTex",
                inputs=[_Socket("Texture", None)],
                outputs=[_Socket("OutTex")],
            )
        )
        nodes = _Nodes([primary])
        tree = SimpleNamespace(name="Channels_OCTANE", nodes=nodes, links=_Links())
        outgoing = [
            _link(
                "Separate",
                f"Target {channel}",
                from_socket=channel,
                from_socket_identifier=channel,
            )
            for channel in ("Red", "Green", "Blue")
        ]
        analysis = SimpleNamespace(
            nodes={"Separate": _node_info("ShaderNodeSeparateColor")},
            links=outgoing,
        )
        info = SimpleNamespace(
            bl_idname="ShaderNodeSeparateColor",
            label="Separate RGB",
            location=(100.0, 200.0),
        )
        engine = GraphEngine(analysis)

        selected = engine._expand_channel_split(
            tree, "Separate", info, primary
        )
        node_map = {"Separate": selected}

        variants = engine.created_nodes_for("Separate", node_map)
        self.assertEqual(len(variants), 3)
        self.assertNotIn(primary, nodes)
        sources = [engine.source_node_for(link, node_map) for link in outgoing]
        self.assertEqual(len({id(source) for source in sources}), 3)
        self.assertEqual(
            [source.inputs.get("Channel").default_value for source in sources],
            ["1", "2", "3"],
        )


class EmissionReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()

    def test_standalone_emission_preserves_unlinked_color_and_strength(self) -> None:
        material_node = _attach_sockets(
            _Node(
                "Converted Emission",
                "ShaderNodeOctDiffuseMat",
                inputs=[
                    _Socket("Diffuse", (0.0, 0.0, 0.0, 1.0)),
                    _Socket("Emission", None),
                ],
                outputs=[_Socket("OutMat")],
            )
        )
        nodes = _Nodes([material_node])
        tree = SimpleNamespace(name="Material_OCTANE", nodes=nodes, links=_Links())
        info = SimpleNamespace(
            bl_idname="ShaderNodeEmission",
            inputs={"Color": (1.0, 0.25, 0.0, 1.0), "Strength": 2.0},
            input_identifiers={"Color": "Color", "Strength": "Strength"},
        )
        analysis = SimpleNamespace(
            has_emission=True,
            nodes={"Emission": info},
            links=[],
        )

        _handle_emission_node_insertion(
            analysis,
            {"Emission": material_node},
            tree,
        )

        emission_node = nodes[-1]
        self.assertEqual(
            emission_node.inputs.get("Texture").default_value,
            (1.0, 0.25, 0.0, 1.0),
        )
        self.assertEqual(emission_node.inputs.get("Power").default_value, 200.0)
        material_emission = material_node.inputs.get("Emission")
        self.assertEqual(len(material_emission.links), 1)
        self.assertIs(material_emission.links[0].from_node, emission_node)

    def test_legacy_principled_emission_uses_texture_emission_node(self) -> None:
        material_node = _attach_sockets(
            _Node(
                "Converted Principled",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Emission", None)],
                outputs=[_Socket("OutMat")],
            )
        )
        nodes = _Nodes([material_node])
        tree = SimpleNamespace(name="LegacyEmission", nodes=nodes, links=_Links())
        info = SimpleNamespace(
            bl_idname="ShaderNodeBsdfPrincipled",
            inputs={
                "Emission": (0.1, 0.3, 0.8, 1.0),
                "Emission Strength": 1.5,
            },
            input_identifiers={
                "Emission": "Emission",
                "Emission Strength": "Emission Strength",
            },
        )
        analysis = SimpleNamespace(
            has_emission=True,
            nodes={"Principled": info},
            links=[],
        )

        _handle_emission_node_insertion(
            analysis,
            {"Principled": material_node},
            tree,
        )

        emission_node = nodes[-1]
        self.assertEqual(
            emission_node.inputs.get("Texture").default_value,
            (0.1, 0.3, 0.8, 1.0),
        )
        self.assertEqual(emission_node.inputs.get("Power").default_value, 150.0)
        self.assertIs(
            material_node.inputs.get("Emission").links[0].from_node,
            emission_node,
        )

    def test_zero_strength_non_black_principled_still_builds_emission(self) -> None:
        material_node = _attach_sockets(
            _Node(
                "Converted Principled",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Emission", None)],
                outputs=[_Socket("OutMat")],
            )
        )
        nodes = _Nodes([material_node])
        tree = SimpleNamespace(name="ZeroEmission", nodes=nodes, links=_Links())
        info = SimpleNamespace(
            bl_idname="ShaderNodeBsdfPrincipled",
            inputs={
                "Emission Color": (0.2, 0.1, 0.0, 1.0),
                "Emission Strength": 0.0,
            },
            input_identifiers={
                "Emission Color": "Emission Color",
                "Emission Strength": "Emission Strength",
            },
        )
        analysis = SimpleNamespace(
            has_emission=True,
            nodes={"Principled": info},
            links=[],
        )

        _handle_emission_node_insertion(
            analysis,
            {"Principled": material_node},
            tree,
        )

        emission_node = nodes[-1]
        self.assertEqual(emission_node.inputs.get("Power").default_value, 0.0)
        self.assertTrue(
            emission_node.inputs.get("Surface brightness").default_value
        )
        self.assertIs(
            material_node.inputs.get("Emission").links[0].from_node,
            emission_node,
        )


class PropertyTransferTests(unittest.TestCase):
    def test_rgb_curve_factor_is_preserved_as_color_correction_mask(self) -> None:
        node = _attach_sockets(
            _Node(
                "Color correction",
                "OctaneColorCorrection",
                inputs=[_Socket("Mask", 1.0)],
            )
        )
        info = SimpleNamespace(
            inputs={"Factor": 0.35},
            input_identifiers={"Factor": "Factor"},
        )

        _transfer_rgb_curve(info, node)

        self.assertEqual(node.inputs.get("Mask").default_value, 0.35)

    def test_standard_surface_maps_principled_layers_without_enabling_them(self) -> None:
        node = _attach_sockets(
            _Node(
                "Standard Surface",
                "OctaneStandardSurfaceMaterial",
                inputs=[
                    _Socket("Base weight", 0.8),
                    _Socket("Base color", (1.0, 1.0, 1.0)),
                    _Socket("Diffuse roughness", 0.0),
                    _Socket("Metalness", 0.0),
                    _Socket("Specular weight", 1.0),
                    _Socket("Specular color", (1.0, 1.0, 1.0)),
                    _Socket("Specular roughness", 0.2),
                    _Socket("Specular IOR", 1.5),
                    _Socket("Specular anisotropy", 0.0),
                    _Socket("Specular rotation", 0.0),
                    _Socket("Transmission weight", 0.0),
                    _Socket("Transmission color", (1.0, 1.0, 1.0)),
                    _Socket("Coating weight", 0.0),
                    _Socket("Coating color", (1.0, 1.0, 1.0)),
                    _Socket("Coating roughness", 0.1),
                    _Socket("Coating IOR", 1.5),
                    _Socket("Sheen weight", 0.0),
                    _Socket("Sheen color", (1.0, 1.0, 1.0)),
                    _Socket("Sheen roughness", 0.3),
                    _Socket("Subsurface weight", 0.0),
                    _Socket("Subsurface color", (1.0, 1.0, 1.0)),
                    _Socket("Subsurface radius", (1.0, 0.2, 0.1)),
                    _Socket("Subsurface scale", 0.01),
                    _Socket("Subsurface anisotropy", 0.0),
                    _Socket("Film thickness (nm)", 0.0),
                    _Socket("Film IOR", 1.45),
                    _Socket("Opacity", 1.0),
                ],
            )
        )
        values = {
            "Base Weight": 1.0,
            "Base Color": (0.2, 0.4, 0.6, 1.0),
            "Diffuse Roughness": 0.15,
            "Metallic": 0.25,
            "Specular IOR Level": 0.35,
            "Specular Tint": (0.9, 0.8, 0.7, 1.0),
            "Roughness": 0.55,
            "IOR": 1.4,
            "Anisotropic": 0.2,
            "Anisotropic Rotation": 0.3,
            "Transmission Weight": 0.0,
            "Coat Weight": 0.0,
            "Coat Tint": (0.7, 0.8, 0.9, 1.0),
            "Coat Roughness": 0.12,
            "Coat IOR": 1.6,
            "Sheen Weight": 0.0,
            "Sheen Tint": (0.4, 0.5, 0.6, 1.0),
            "Sheen Roughness": 0.45,
            "Subsurface Weight": 0.0,
            "Subsurface Radius": (1.1, 0.4, 0.2),
            "Subsurface Scale": 0.02,
            "Subsurface Anisotropy": 0.1,
            "Thin Film Thickness": 180.0,
            "Thin Film IOR": 1.33,
            "Alpha": 0.85,
        }
        info = SimpleNamespace(
            inputs=values,
            input_identifiers={name: name for name in values},
            properties={},
        )

        _transfer_principled(info, node)

        self.assertEqual(node.inputs.get("Base weight").default_value, 1.0)
        self.assertEqual(node.inputs.get("Base color").default_value, (0.2, 0.4, 0.6))
        self.assertEqual(node.inputs.get("Diffuse roughness").default_value, 0.15)
        self.assertEqual(node.inputs.get("Specular weight").default_value, 0.7)
        self.assertEqual(node.inputs.get("Specular roughness").default_value, 0.55)
        self.assertEqual(node.inputs.get("Transmission weight").default_value, 0.0)
        self.assertEqual(node.inputs.get("Transmission color").default_value, (0.2, 0.4, 0.6))
        self.assertEqual(node.inputs.get("Coating weight").default_value, 0.0)
        self.assertEqual(node.inputs.get("Coating color").default_value, (0.7, 0.8, 0.9))
        self.assertEqual(node.inputs.get("Sheen weight").default_value, 0.0)
        self.assertEqual(node.inputs.get("Subsurface color").default_value, (0.2, 0.4, 0.6))
        self.assertEqual(node.inputs.get("Film thickness (nm)").default_value, 180.0)

    def test_standard_surface_uses_full_base_weight_for_legacy_principled(self) -> None:
        node = _attach_sockets(
            _Node(
                "Standard Surface",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Base weight", 0.8)],
            )
        )
        info = SimpleNamespace(inputs={}, input_identifiers={}, properties={})

        _transfer_principled(info, node)

        self.assertEqual(node.inputs.get("Base weight").default_value, 1.0)

    def test_default_principled_does_not_enable_coat_or_sheen(self) -> None:
        node = _attach_sockets(
            _Node(
                "Universal",
                "OctaneUniversalMaterial",
                inputs=[
                    _Socket("Albedo", (0.7, 0.7, 0.7)),
                    _Socket("Metallic", 0.0),
                    _Socket("Roughness", 0.0632),
                    _Socket("Specular", 1.0),
                    _Socket("Dielectric IOR", 1.5),
                    _Socket("Opacity", 1.0),
                    _Socket("Anisotropy", 0.0),
                    _Socket("Rotation", 0.0),
                    _Socket("Coating", (0.0, 0.0, 0.0)),
                    _Socket("Coating roughness", 0.0632),
                    _Socket("Coating IOR", 1.5),
                    _Socket("Sheen", (0.0, 0.0, 0.0)),
                    _Socket("Sheen roughness", 0.2),
                    _Socket("Film width", 0.0),
                    _Socket("Film IOR", 1.45),
                    _Socket("BSDF model", "Octane"),
                ],
            )
        )
        values = {
            "Base Color": (0.8, 0.8, 0.8, 1.0),
            "Metallic": 0.0,
            "Roughness": 0.5,
            "Diffuse Roughness": 0.0,
            "Specular IOR Level": 0.5,
            "IOR": 1.5,
            "Alpha": 1.0,
            "Anisotropic": 0.0,
            "Anisotropic Rotation": 0.0,
            "Coat Weight": 0.0,
            "Coat Roughness": 0.03,
            "Coat IOR": 1.5,
            "Coat Tint": (1.0, 1.0, 1.0, 1.0),
            "Sheen Weight": 0.0,
            "Sheen Roughness": 0.5,
            "Sheen Tint": (1.0, 1.0, 1.0, 1.0),
            "Thin Film Thickness": 0.0,
            "Thin Film IOR": 1.33,
            "Transmission Weight": 0.0,
        }
        info = SimpleNamespace(
            inputs=values,
            input_identifiers={name: name for name in values},
            properties={},
        )

        _transfer_principled(info, node)

        self.assertEqual(node.inputs.get("Roughness").default_value, 0.5)
        self.assertEqual(node.inputs.get("Specular").default_value, 1.0)
        self.assertEqual(node.inputs.get("Coating").default_value, (0.0, 0.0, 0.0))
        self.assertEqual(node.inputs.get("Sheen").default_value, (0.0, 0.0, 0.0))
        self.assertEqual(node.inputs.get("Coating roughness").default_value, 0.0632)
        self.assertEqual(node.inputs.get("Sheen roughness").default_value, 0.2)
        self.assertEqual(node.inputs.get("Film IOR").default_value, 1.45)
        self.assertEqual(
            node.inputs.get("BSDF model").default_value,
            "GGX",
        )

    def test_mapping_rotation_is_converted_to_octane_degrees_and_xyz_order(self) -> None:
        node = _attach_sockets(
            _Node(
                "Transform",
                "Octane3DTransformation",
                inputs=[
                    _Socket("Rotation order", "YXZ"),
                    _Socket("Rotation", (0.0, 0.0, 0.0)),
                    _Socket("Scale", (1.0, 1.0, 1.0)),
                    _Socket("Translation", (0.0, 0.0, 0.0)),
                ],
                outputs=[_Socket("Transform out")],
            )
        )
        info = SimpleNamespace(
            inputs={
                "Location": (0.25, 0.5, 0.0),
                "Rotation": (0.0, 0.0, 1.5707963267948966),
                "Scale": (2.0, 2.0, 1.0),
            },
            input_identifiers={
                "Location": "Location",
                "Rotation": "Rotation",
                "Scale": "Scale",
            },
        )

        _transfer_mapping(info, node)

        self.assertEqual(node.inputs.get("Rotation order").default_value, "XYZ")
        self.assertAlmostEqual(node.inputs.get("Rotation").default_value[2], 90.0)
        self.assertEqual(node.inputs.get("Scale").default_value, (2.0, 2.0, 1.0))

    def test_principled_layer_color_is_tint_times_weight(self) -> None:
        node = _attach_sockets(
            _Node(
                "Universal",
                "OctaneUniversalMaterial",
                inputs=[
                    _Socket("Coating", (0.0, 0.0, 0.0)),
                    _Socket("Coating roughness", 0.0632),
                    _Socket("Coating IOR", 1.5),
                    _Socket("Sheen", (0.0, 0.0, 0.0)),
                ],
            )
        )
        values = {
            "Coat Weight": 0.25,
            "Coat Tint": (0.8, 0.4, 0.2, 1.0),
            "Coat Roughness": 0.12,
            "Coat IOR": 1.6,
            "Sheen Weight": 0.0,
            "Sheen Tint": (1.0, 1.0, 1.0, 1.0),
            "Transmission Weight": 0.0,
        }
        info = SimpleNamespace(
            inputs=values,
            input_identifiers={name: name for name in values},
            properties={},
        )

        _transfer_principled(info, node)

        self.assertEqual(node.inputs.get("Coating").default_value, (0.2, 0.1, 0.05))
        self.assertEqual(node.inputs.get("Coating roughness").default_value, 0.12)
        self.assertEqual(node.inputs.get("Coating IOR").default_value, 1.6)

    def test_linked_coat_weight_is_not_connected_as_full_white_coat(self) -> None:
        weight = _attach_sockets(
            _Node("Weight", "OctaneFloatValue", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneUniversalMaterial",
                inputs=[_Socket("Coating", (0.0, 0.0, 0.0))],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="LayerMaterial",
            nodes=_Nodes([weight, material]),
            links=_Links(),
        )
        values = {
            "Coat Weight": 0.0,
            "Coat Tint": (1.0, 1.0, 1.0, 1.0),
            "Sheen Weight": 0.0,
            "Sheen Tint": (1.0, 1.0, 1.0, 1.0),
            "Transmission Weight": 0.0,
        }
        analysis = SimpleNamespace(
            nodes={
                "Weight": _node_info("ShaderNodeValue"),
                "Material": SimpleNamespace(
                    bl_idname="ShaderNodeBsdfPrincipled",
                    inputs=values,
                    input_identifiers={name: name for name in values},
                ),
            },
            links=[
                _link(
                    "Weight",
                    "Material",
                    from_socket="Value",
                    from_socket_identifier="Value",
                    to_socket="Coat Weight",
                    to_socket_identifier="Coat Weight",
                )
            ],
        )

        _handle_principled_material_inputs(
            analysis,
            {"Weight": weight, "Material": material},
            tree,
        )

        multiply = tree.nodes[-1]
        self.assertIn(multiply.bl_idname, ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"))
        self.assertIs(multiply.inputs.get("Texture 1").links[0].from_node, weight)
        self.assertEqual(multiply.inputs.get("Texture 2").default_value, 1.0)
        self.assertIs(material.inputs.get("Coating").links[0].from_node, multiply)

    def test_standard_surface_links_coat_weight_directly(self) -> None:
        weight = _attach_sockets(
            _Node("Weight", "OctaneFloatValue", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Coating weight", 0.0)],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="StandardLayerMaterial",
            nodes=_Nodes([weight, material]),
            links=_Links(),
        )
        analysis = SimpleNamespace(
            nodes={
                "Weight": _node_info("ShaderNodeValue"),
                "Material": _node_info("ShaderNodeBsdfPrincipled"),
            },
            links=[
                _link(
                    "Weight",
                    "Material",
                    from_socket="Value",
                    from_socket_identifier="Value",
                    to_socket="Coat Weight",
                    to_socket_identifier="Coat Weight",
                )
            ],
        )

        _rebuild_links(
            analysis,
            {"Weight": weight, "Material": material},
            tree,
        )

        self.assertEqual(len(tree.nodes), 2)
        self.assertIs(
            material.inputs.get("Coating weight").links[0].from_node,
            weight,
        )

    def test_standard_surface_scales_linked_specular_into_specular_weight(self) -> None:
        specular = _attach_sockets(
            _Node("Specular", "OctaneFloatValue", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Specular weight", 1.0)],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="StandardSpecularMaterial",
            nodes=_Nodes([specular, material]),
            links=_Links(),
        )
        values = {"Transmission Weight": 0.0, "Subsurface Weight": 0.0}
        analysis = SimpleNamespace(
            nodes={
                "Specular": _node_info("ShaderNodeValue"),
                "Material": SimpleNamespace(
                    bl_idname="ShaderNodeBsdfPrincipled",
                    inputs=values,
                    input_identifiers={name: name for name in values},
                ),
            },
            links=[
                _link(
                    "Specular",
                    "Material",
                    from_socket="Value",
                    from_socket_identifier="Value",
                    to_socket="Specular IOR Level",
                    to_socket_identifier="Specular IOR Level",
                )
            ],
        )

        _handle_principled_material_inputs(
            analysis,
            {"Specular": specular, "Material": material},
            tree,
        )

        multiply = tree.nodes[-1]
        self.assertIn(
            multiply.bl_idname,
            ("OctaneMultiplyTexture", "ShaderNodeOctMultiplyTex"),
        )
        self.assertEqual(multiply.inputs.get("Texture 2").default_value, 2.0)
        self.assertIs(
            material.inputs.get("Specular weight").links[0].from_node,
            multiply,
        )

    def test_legacy_linked_specular_uses_the_same_physical_scale(self) -> None:
        specular = _attach_sockets(
            _Node("Specular", "OctaneFloatValue", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneUniversalMaterial",
                inputs=[_Socket("Specular", 1.0)],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="LegacySpecularMaterial",
            nodes=_Nodes([specular, material]),
            links=_Links(),
        )
        values = {"Transmission": 0.0, "Subsurface": 0.0}
        analysis = SimpleNamespace(
            nodes={
                "Specular": _node_info("ShaderNodeValue"),
                "Material": SimpleNamespace(
                    bl_idname="ShaderNodeBsdfPrincipled",
                    inputs=values,
                    input_identifiers={name: name for name in values},
                ),
            },
            links=[
                _link(
                    "Specular",
                    "Material",
                    from_socket="Value",
                    from_socket_identifier="Value",
                    to_socket="Specular",
                    to_socket_identifier="Specular",
                )
            ],
        )

        _handle_principled_material_inputs(
            analysis,
            {"Specular": specular, "Material": material},
            tree,
        )

        multiply = tree.nodes[-1]
        self.assertEqual(multiply.inputs.get("Texture 2").default_value, 2.0)
        self.assertIs(material.inputs.get("Specular").links[0].from_node, multiply)

    def test_legacy_linked_subsurface_fans_base_color_to_standard_surface(self) -> None:
        base = _attach_sockets(
            _Node("Base", "OctaneRGBImage", outputs=[_Socket("OutTex")])
        )
        weight = _attach_sockets(
            _Node("Weight", "OctaneFloatValue", outputs=[_Socket("OutTex")])
        )
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneStandardSurfaceMaterial",
                inputs=[_Socket("Subsurface color", (1.0, 1.0, 1.0, 1.0))],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="LegacySSSMaterial",
            nodes=_Nodes([base, weight, material]),
            links=_Links(),
        )
        values = {"Transmission": 0.0, "Subsurface": 0.0}
        analysis = SimpleNamespace(
            nodes={
                "Base": _node_info("ShaderNodeTexImage"),
                "Weight": _node_info("ShaderNodeValue"),
                "Material": SimpleNamespace(
                    bl_idname="ShaderNodeBsdfPrincipled",
                    inputs=values,
                    input_identifiers={name: name for name in values},
                ),
            },
            links=[
                _link(
                    "Base",
                    "Material",
                    from_socket="Color",
                    from_socket_identifier="Color",
                    to_socket="Base Color",
                    to_socket_identifier="Base Color",
                ),
                _link(
                    "Weight",
                    "Material",
                    from_socket="Value",
                    from_socket_identifier="Value",
                    to_socket="Subsurface",
                    to_socket_identifier="Subsurface",
                ),
            ],
        )

        _handle_principled_material_inputs(
            analysis,
            {"Base": base, "Weight": weight, "Material": material},
            tree,
        )

        self.assertIs(
            material.inputs.get("Subsurface color").links[0].from_node,
            base,
        )

    def test_unlinked_legacy_principled_layers_map_to_universal(self) -> None:
        material = _attach_sockets(
            _Node(
                "Material",
                "OctaneUniversalMaterial",
                inputs=[
                    _Socket("BSDF model", "Octane"),
                    _Socket("Specular", 1.0),
                    _Socket("Coating", (0.0, 0.0, 0.0, 1.0)),
                    _Socket("Coating roughness", 0.0),
                    _Socket("Sheen", (0.0, 0.0, 0.0, 1.0)),
                ],
            )
        )
        info = SimpleNamespace(
            bl_idname="ShaderNodeBsdfPrincipled",
            inputs={
                "Specular": 0.3,
                "Clearcoat": 0.25,
                "Clearcoat Roughness": 0.2,
                "Sheen": 0.4,
                "Sheen Tint": (0.5, 0.25, 1.0, 1.0),
            },
            input_identifiers={},
            properties={},
        )

        _transfer_principled(info, material)

        self.assertEqual(material.inputs.get("BSDF model").default_value, "GGX")
        self.assertAlmostEqual(material.inputs.get("Specular").default_value, 0.6)
        self.assertEqual(
            material.inputs.get("Coating").default_value,
            (0.25, 0.25, 0.25, 1.0),
        )
        self.assertAlmostEqual(
            material.inputs.get("Coating roughness").default_value, 0.2
        )
        self.assertEqual(
            material.inputs.get("Sheen").default_value,
            (0.2, 0.1, 0.4, 1.0),
        )

    def test_glass_color_tints_transmission_not_reflection(self) -> None:
        node = _attach_sockets(
            _Node(
                "Glass",
                "ShaderNodeOctSpecularMat",
                inputs=[
                    _Socket("Reflection", (0.0, 0.0, 0.0, 1.0)),
                    _Socket("Transmission color", (1.0, 1.0, 1.0, 1.0)),
                    _Socket("Roughness", 0.0),
                    _Socket("Index", 1.0),
                ],
                outputs=[_Socket("OutMat")],
            )
        )
        tint = (0.2, 0.4, 0.8, 1.0)
        info = SimpleNamespace(
            inputs={"Color": tint, "Roughness": 0.1, "IOR": 1.5},
            input_identifiers={
                "Color": "Color",
                "Roughness": "Roughness",
                "IOR": "IOR",
            },
        )

        _transfer_glass(info, node)

        self.assertEqual(
            node.inputs.get("Reflection").default_value,
            (1.0, 1.0, 1.0, 1.0),
        )
        self.assertEqual(
            node.inputs.get("Transmission color").default_value,
            tint,
        )

    def test_image_user_animation_is_written_to_nested_image_user(self) -> None:
        node = _attach_sockets(
            _Node(
                "Image",
                "OctaneRGBImage",
                inputs=[_Socket("Legacy gamma", 1.0)],
                outputs=[_Socket("OutTex")],
            )
        )
        node.image_user = SimpleNamespace(
            frame_duration=1,
            frame_offset=0,
            frame_start=1,
            use_auto_refresh=False,
            use_cyclic=False,
        )
        info = SimpleNamespace(
            properties={
                "colorspace": "sRGB",
                "image_user": {
                    "frame_duration": 24,
                    "frame_offset": 3,
                    "frame_start": 10,
                    "use_auto_refresh": True,
                    "use_cyclic": True,
                },
            }
        )

        _transfer_image_texture(info, node)

        self.assertEqual(node.image_user.frame_duration, 24)
        self.assertEqual(node.image_user.frame_offset, 3)
        self.assertEqual(node.image_user.frame_start, 10)
        self.assertTrue(node.image_user.use_auto_refresh)
        self.assertTrue(node.image_user.use_cyclic)

    def test_current_displacement_lod_identifier_maps_to_ui_resolution(self) -> None:
        node = _attach_sockets(
            _Node(
                "Displacement",
                "OctaneTextureDisplacement",
                inputs=[
                    _Socket("Height", 1.0),
                    _Socket("Mid level", 0.5),
                    _Socket("Level of detail", "1024x1024"),
                ],
                outputs=[_Socket("OutTex")],
            )
        )
        bpy.context.scene = SimpleNamespace(
            octanify_disp_mode="TEXTURE",
            octanify_disp_level_of_detail="3",
            octanify_disp_mid_level=0.5,
        )
        info = SimpleNamespace(
            inputs={"Scale": 0.1, "Midlevel": 0.5},
            input_identifiers={"Scale": "Scale", "Midlevel": "Midlevel"},
        )

        _transfer_displacement(info, node)

        self.assertEqual(
            node.inputs.get("Level of detail").default_value,
            "2048x2048",
        )


class ConversionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()
        reset_cache()

    def test_cache_in_progress_guard_is_reentrant_safe(self) -> None:
        cache = ConversionCache()

        self.assertTrue(cache.begin("group"))
        self.assertFalse(cache.begin("group"))
        self.assertTrue(cache.is_in_progress("group"))
        cache.end("group")
        self.assertFalse(cache.is_in_progress("group"))
        self.assertTrue(cache.begin("group"))

    def test_material_failure_removes_partial_copy(self) -> None:
        class _Material(dict):
            def __init__(self, name: str) -> None:
                super().__init__()
                self.name = name
                self.node_tree = SimpleNamespace(nodes=[])
                self.use_nodes = True

            def copy(self):
                return _Material(f"{self.name} copy")

        original = _Material("Source")
        removed = []
        bpy.data = SimpleNamespace(
            materials=SimpleNamespace(
                get=lambda _name: None,
                remove=lambda material: removed.append(material),
            )
        )

        with patch(
            "octanify.core.conversion_engine.analyze_tree",
            return_value=SimpleNamespace(nodes={}, links=[]),
        ), patch(
            "octanify.core.conversion_engine._populate_converted_material",
            side_effect=RuntimeError("broken fixture"),
        ):
            converted = convert_material(original, smart_conversion=False)

        self.assertIsNone(converted)
        self.assertEqual(len(removed), 1)
        self.assertIn("rolled back", report_data.warnings[0])

    def test_smart_conversion_keeps_the_original_material_datablock(self) -> None:
        class _Material(dict):
            def __init__(self) -> None:
                super().__init__()
                self.name = "Smart"
                self.node_tree = SimpleNamespace(nodes=[], links=[])
                self.use_nodes = True

        original = _Material()
        with patch(
            "octanify.core.conversion_engine.analyze_tree",
            return_value=SimpleNamespace(nodes={}, links=[]),
        ), patch(
            "octanify.core.conversion_engine._populate_converted_material",
            return_value=[],
        ), patch(
            "octanify.core.conversion_engine.style_smart_graphs",
        ):
            converted = convert_material(original, smart_conversion=True)

        self.assertIs(converted, original)
        self.assertTrue(original["octanify_converted"])
        self.assertTrue(original["octanify_smart_conversion"])

    def test_smart_conversion_restores_authored_active_output(self) -> None:
        class _Material(dict):
            def __init__(self, tree) -> None:
                super().__init__()
                self.name = "SmartOutputs"
                self.node_tree = tree
                self.use_nodes = True

        authored_output = _Node("Cycles Output", "ShaderNodeOutputMaterial")
        authored_output.target = "ALL"
        authored_output.is_active_output = True
        tree = SimpleNamespace(nodes=_Nodes([authored_output]), links=_Links())
        original = _Material(tree)

        def _add_octane_output(*_args, **_kwargs):
            converted_output = _Node("Octane Output", "ShaderNodeOutputMaterial")
            converted_output.target = "ALL"
            converted_output.is_active_output = True
            authored_output.is_active_output = False
            tree.nodes.append(converted_output)
            return [converted_output]

        with patch(
            "octanify.core.conversion_engine.analyze_tree",
            return_value=SimpleNamespace(nodes={}, links=[]),
        ), patch(
            "octanify.core.conversion_engine._populate_converted_material",
            side_effect=_add_octane_output,
        ), patch(
            "octanify.core.conversion_engine.style_smart_graphs",
        ):
            converted = convert_material(original, smart_conversion=True)

        self.assertIs(converted, original)
        self.assertTrue(authored_output.is_active_output)

    def test_failed_smart_conversion_removes_only_new_nodes(self) -> None:
        class _Material(dict):
            def __init__(self, tree) -> None:
                super().__init__()
                self.name = "SmartFailure"
                self.node_tree = tree
                self.use_nodes = True

        authored = _attach_sockets(_Node("Principled", "ShaderNodeBsdfPrincipled"))
        authored_output = _attach_sockets(
            _Node("Material Output", "ShaderNodeOutputMaterial")
        )
        authored_output.target = "ALL"
        tree = SimpleNamespace(
            nodes=_Nodes([authored, authored_output]),
            links=_Links(),
        )
        original = _Material(tree)

        def _fail_after_add(*_args, **_kwargs):
            tree.nodes.append(_attach_sockets(_Node("Partial", "OctaneUniversalMaterial")))
            raise RuntimeError("in-place failure")

        with patch(
            "octanify.core.conversion_engine.analyze_tree",
            return_value=SimpleNamespace(nodes={}, links=[]),
        ), patch(
            "octanify.core.conversion_engine._populate_converted_material",
            side_effect=_fail_after_add,
        ):
            converted = convert_material(original, smart_conversion=True)

        self.assertIsNone(converted)
        self.assertEqual(list(tree.nodes), [authored, authored_output])
        self.assertEqual(authored_output.target, "ALL")
        self.assertNotIn("octanify_converted", original)


class VolumetricTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()

    def test_volume_is_attached_to_surface_from_same_material_output(self) -> None:
        material = _attach_sockets(
            _Node(
                "Surface",
                "ShaderNodeOctUniversalMat",
                inputs=[_Socket("Medium", None)],
                outputs=[_Socket("OutMat")],
            )
        )
        volume = _attach_sockets(
            _Node(
                "Absorption",
                "ShaderNodeOctAbsorptionMedium",
                inputs=[],
                outputs=[_Socket("OutMedium")],
            )
        )
        output = _attach_sockets(
            _Node(
                "Material Output",
                "ShaderNodeOutputMaterial",
                inputs=[_Socket("Surface", None), _Socket("Volume", None)],
                outputs=[],
            )
        )
        tree = SimpleNamespace(
            name="VolumeMat_OCTANE",
            nodes=_Nodes([material, volume, output]),
            links=_Links(),
        )
        tree.links.new(volume.outputs.get("OutMedium"), output.inputs.get("Volume"))
        analysis = SimpleNamespace(
            has_volume=True,
            nodes={
                "Surface": _node_info("ShaderNodeBsdfPrincipled"),
                "Absorption": _node_info("ShaderNodeVolumeAbsorption"),
                "Material Output": _node_info("ShaderNodeOutputMaterial"),
            },
            links=[
                _link(
                    "Surface",
                    "Material Output",
                    from_socket="BSDF",
                    to_socket="Surface",
                    from_socket_identifier="BSDF",
                    to_socket_identifier="Surface",
                ),
                _link(
                    "Absorption",
                    "Material Output",
                    from_socket="Volume",
                    to_socket="Volume",
                    from_socket_identifier="Volume",
                    to_socket_identifier="Volume",
                ),
            ],
        )

        handle_volumetrics(
            analysis,
            {
                "Surface": material,
                "Absorption": volume,
                "Material Output": output,
            },
            tree,
        )

        self.assertEqual(len(material.inputs.get("Medium").links), 1)
        self.assertIs(material.inputs.get("Medium").links[0].from_node, volume)
        self.assertEqual(len(output.inputs.get("Volume").links), 0)


class NormalFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        report_data.clear()

    def test_chained_normal_and_height_are_preserved_on_material(self) -> None:
        normal_image = _attach_sockets(
            _Node("Normal Image", "OctaneRGBImage", outputs=[_Socket("OutTex")])
        )
        height_image = _attach_sockets(
            _Node("Height Image", "OctaneGreyscaleImage", outputs=[_Socket("OutTex")])
        )
        fallback = _attach_sockets(
            _Node(
                "Bump",
                "ShaderNodeOctRGBColorTex",
                inputs=[_Socket("Input", 0.0)],
                outputs=[_Socket("OutTex")],
            )
        )
        fallback.label = "[UNSUPPORTED] Bump"
        material = _attach_sockets(
            _Node(
                "Material",
                "ShaderNodeOctUniversalMat",
                inputs=[
                    _Socket("Normal", None),
                    _Socket("Bump", None),
                    _Socket("Bump height", 0.0),
                ],
                outputs=[_Socket("OutMat")],
            )
        )
        tree = SimpleNamespace(
            name="NormalMat_OCTANE",
            nodes=_Nodes([normal_image, height_image, fallback, material]),
            links=_Links(),
        )
        tree.links.new(normal_image.outputs[0], fallback.inputs[0])
        tree.links.new(fallback.outputs[0], material.inputs.get("Normal"))

        bump_info = SimpleNamespace(
            bl_idname="ShaderNodeBump",
            inputs={"Strength": 0.5, "Distance": 0.1},
            input_identifiers={"Strength": "Strength", "Distance": "Distance"},
            properties={"invert": False},
        )
        analysis = SimpleNamespace(
            nodes={
                "Normal Image": _node_info("ShaderNodeTexImage"),
                "Height Image": _node_info("ShaderNodeTexImage"),
                "Bump": bump_info,
                "Material": _node_info("ShaderNodeBsdfPrincipled"),
            },
            links=[
                _link(
                    "Normal Image",
                    "Bump",
                    from_socket="Color",
                    to_socket="Normal",
                    from_socket_identifier="Color",
                    to_socket_identifier="Normal",
                ),
                _link(
                    "Height Image",
                    "Bump",
                    from_socket="Color",
                    to_socket="Height",
                    from_socket_identifier="Color",
                    to_socket_identifier="Height",
                ),
                _link(
                    "Bump",
                    "Material",
                    from_socket="Normal",
                    to_socket="Normal",
                    from_socket_identifier="Normal",
                    to_socket_identifier="Normal",
                ),
            ],
        )
        node_map = {
            "Normal Image": normal_image,
            "Height Image": height_image,
            "Bump": fallback,
            "Material": material,
        }

        _handle_normal_map_fallback(analysis, node_map, tree)

        self.assertIs(material.inputs.get("Normal").links[0].from_node, normal_image)
        self.assertIs(material.inputs.get("Bump").links[0].from_node, height_image)
        self.assertAlmostEqual(material.inputs.get("Bump height").default_value, 0.05)
        self.assertNotIn(fallback, tree.nodes)
        self.assertNotIn("Bump", node_map)


class ScaleCorrectionTests(unittest.TestCase):
    def test_uv_mapping_is_not_modified_by_object_scale(self) -> None:
        mapping = _attach_sockets(
            _Node(
                "Mapping",
                "ShaderNodeOct3DTransform",
                inputs=[_Socket("Scale", (1.0, 1.0, 1.0))],
                outputs=[_Socket("OutTransform")],
            )
        )
        analysis = SimpleNamespace(
            nodes={
                "Coordinates": _node_info("ShaderNodeTexCoord"),
                "Mapping": _node_info("ShaderNodeMapping"),
            },
            links=[
                _link(
                    "Coordinates",
                    "Mapping",
                    from_socket="UV",
                    to_socket="Vector",
                )
            ],
        )
        obj = SimpleNamespace(scale=SimpleNamespace(x=2.0, y=3.0, z=4.0))

        _apply_scale_correction(obj, {"Mapping": mapping}, analysis)

        self.assertEqual(
            mapping.inputs.get("Scale").default_value,
            (1.0, 1.0, 1.0),
        )


if __name__ == "__main__":
    unittest.main()
