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
from octanify.core.layout_engine import style_smart_graphs
from octanify.core.conversion_engine import (
    _apply_scale_correction,
    _handle_alpha,
    _handle_emission_node_insertion,
    _handle_normal_map_fallback,
    _handle_principled_material_inputs,
    _rebuild_links,
    _route_original_outputs_to_cycles,
    convert_material,
    reset_cache,
)
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
)
from octanify.core.report import report_data
from octanify.core.shader_detection import analyze_tree
from octanify.core.volumetric_handler import handle_volumetrics
from octanify.ui.operators import (
    OCTANIFY_OT_convert,
    _delete_cycles_nodes_from_material,
    _find_preferred_material_node,
    _guess_texture_socket,
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
        if type not in ("ShaderNodeOctTextureEmission", "OctaneTextureEmission"):
            raise RuntimeError(type)
        node = _Node(
            "Texture Emission",
            type,
            inputs=[
                _Socket("Texture", (0.0, 0.0, 0.0, 1.0)),
                _Socket("Power", 1.0),
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


class OperatorUtilityTests(unittest.TestCase):
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
        operator._work_items = [(obj, slot)]
        operator._work_index = 0
        operator._timer = None

        with patch(
            "octanify.ui.operators.convert_material",
            return_value=material,
        ):
            result = operator.modal(context, SimpleNamespace(type="TIMER"))

        self.assertEqual(result, {"FINISHED"})
        self.assertIn(100, progress_updates)
        self.assertEqual(context.scene.octanify_progress, 100)
        self.assertEqual(context.scene.octanify_progress_label, "Conversion complete")
        self.assertFalse(context.scene.octanify_progress_active)
        self.assertEqual(progress_ended, [True])


class LayoutTests(unittest.TestCase):
    def test_framed_authored_graph_is_not_partially_rearranged(self) -> None:
        frame = _Node("Frame", "NodeFrame")
        frame.location.x = 100.0
        frame.location.y = 300.0
        framed = _Node("Framed", "ShaderNodeTexImage")
        framed.parent = frame
        framed.location.x = 40.0
        framed.location.y = -60.0
        authored = _Node("Authored", "ShaderNodeBsdfPrincipled")
        authored.location.x = 700.0
        authored.location.y = 50.0
        converted = _Node("Converted", "OctaneStandardSurfaceMaterial")
        tree = SimpleNamespace(nodes=[frame, framed, authored, converted], links=[])

        style_smart_graphs(
            tree,
            [frame, framed, authored],
            [converted],
            auto_arrange=True,
        )

        self.assertEqual((frame.location.x, frame.location.y), (100.0, 300.0))
        self.assertEqual((framed.location.x, framed.location.y), (40.0, -60.0))
        self.assertEqual((authored.location.x, authored.location.y), (700.0, 50.0))
        self.assertGreaterEqual(converted.location[0], 1860.0)


class SocketResolutionTests(unittest.TestCase):
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


class PropertyTransferTests(unittest.TestCase):
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
