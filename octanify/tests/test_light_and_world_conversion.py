from __future__ import annotations

import math
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


def _install_bpy_stub() -> None:
    if "bpy" in sys.modules:
        return
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(
        **{
            name: type(name, (), {})
            for name in (
                "Context",
                "Image",
                "Light",
                "Node",
                "NodeSocket",
                "NodeTree",
                "Object",
                "Operator",
                "Panel",
                "Scene",
                "World",
            )
        }
    )
    bpy.props = SimpleNamespace(
        EnumProperty=lambda **_kwargs: None,
        FloatProperty=lambda **_kwargs: None,
    )
    bpy.utils = SimpleNamespace(
        register_class=lambda _cls: None,
        unregister_class=lambda _cls: None,
    )
    bpy.context = SimpleNamespace(scene=SimpleNamespace())
    bpy.path = SimpleNamespace(abspath=lambda path: f"ABS:{path}")
    sys.modules["bpy"] = bpy


_install_bpy_stub()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import bpy  # noqa: E402
if not hasattr(bpy, "path"):
    bpy.path = SimpleNamespace(abspath=lambda path: f"ABS:{path}")
from octanify.core.light_converter import (  # noqa: E402
    OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
    OCTANE_SURFACE_BRIGHTNESS_REFERENCE_AREA,
    convert_light_to_octane,
    detect_light_gobo,
    light_needs_octane_conversion,
)
from octanify.core.world_converter import (  # noqa: E402
    convert_world_to_octane,
    world_needs_octane_conversion,
)
from octanify.ui.operators import (  # noqa: E402
    OCTANIFY_OT_convert,
    _scene_light_objects,
)


class _Sockets(list):
    def get(self, name: str):
        return next((socket for socket in self if socket.name == name), None)


class _Socket:
    def __init__(self, name: str, default_value=None) -> None:
        self.name = name
        self.default_value = default_value
        self.links = []
        self.node = None


class _Node(dict):
    def __init__(
        self,
        bl_idname: str,
        inputs: tuple[tuple[str, object], ...] = (),
        outputs: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.bl_idname = bl_idname
        self.name = bl_idname
        self.label = ""
        self.inputs = _Sockets(_Socket(name, value) for name, value in inputs)
        self.outputs = _Sockets(_Socket(name) for name in outputs)
        self.location = (0.0, 0.0)
        self.is_active_output = False
        self.target = "ALL"
        for socket in (*self.inputs, *self.outputs):
            socket.node = self


def _node_for_type(bl_idname: str) -> _Node:
    definitions = {
        "ShaderNodeOutputLight": (("Surface", None),),
        "ShaderNodeEmission": (
            ("Color", (1.0, 1.0, 1.0, 1.0)),
            ("Strength", 1.0),
        ),
        "ShaderNodeTexImage": (("Vector", None),),
        "OctaneBlackBodyEmission": (
            ("Texture", 0.025),
            ("Power", 100.0),
            ("Surface brightness", False),
            ("Temperature", 6500.0),
            ("Normalize", True),
            ("Distribution", 1.0),
        ),
        "OctaneTextureEmission": (
            ("Texture", (0.025, 0.025, 0.025)),
            ("Power", 100.0),
            ("Surface brightness", False),
            ("Distribution", 1.0),
        ),
        "OctaneDiffuseMaterial": (("Emission", None),),
        "OctaneDirectionalLight": (
            ("Emission", None),
            ("Light transform", None),
            ("Light sample spread angle", 0.5),
        ),
        "OctaneVolumetricSpotlight": (
            ("Cone hardness", 0.7),
            ("Emitter material", None),
        ),
        "OctaneSpotlight": (
            ("Cone angle", 60.0),
            ("Hardness", 0.75),
            ("Normalize power", False),
        ),
        "ShaderNodeOutputWorld": (("Surface", None),),
        "ShaderNodeBackground": (
            ("Color", (0.05, 0.05, 0.05, 1.0)),
            ("Strength", 1.0),
        ),
        "ShaderNodeRGB": (),
        "ShaderNodeTexEnvironment": (("Vector", None),),
        "ShaderNodeMapping": (
            ("Vector", None),
            ("Location", (0.0, 0.0, 0.0)),
            ("Rotation", (0.0, 0.0, 0.0)),
            ("Scale", (1.0, 1.0, 1.0)),
        ),
        "ShaderNodeTexCoord": (),
        "OctaneEditorWorldOutputNode": (
            ("Environment", None),
            ("Visible Environment", None),
        ),
        "OctaneTextureEnvironment": (
            ("Texture", (1.0, 1.0, 1.0)),
            ("Power", 1.0),
        ),
        "OctaneRGBImage": (
            ("Power", 1.0),
            ("Color space", "sRGB"),
            ("Legacy gamma", 2.2),
            ("Invert", False),
            ("Linear sRGB invert", True),
            ("UV transform", None),
            ("Projection", None),
            ("Border mode (U)", "Wrap around"),
            ("Border mode (V)", "Wrap around"),
        ),
        "OctaneAlphaImage": (
            ("Power", 1.0),
            ("Color space", "sRGB"),
            ("Legacy gamma", 2.2),
            ("Invert", False),
            ("Linear sRGB invert", True),
            ("UV transform", None),
            ("Projection", None),
            ("Border mode (U)", "Wrap around"),
            ("Border mode (V)", "Wrap around"),
        ),
        "OctanePerspective": (
            ("Plane transformation", None),
            ("Coordinate space", "Object space"),
            ("Use rest attributes", False),
        ),
        "OctaneMultiplyTexture": (
            ("Texture 1", None),
            ("Texture 2", 1.0),
        ),
        "OctaneSpherical": (("Sphere transformation", None),),
        "Octane3DTransformation": (
            ("Rotation order", "YXZ"),
            ("Rotation", (0.0, 0.0, 0.0)),
            ("Scale", (1.0, 1.0, 1.0)),
            ("Translation", (0.0, 0.0, 0.0)),
        ),
        "OctaneObjectData": (),
    }
    outputs = {
        "OctaneBlackBodyEmission": ("Emission out",),
        "OctaneTextureEmission": ("Emission out",),
        "OctaneRGBColor": ("Texture out",),
        "OctaneDiffuseMaterial": ("Material out",),
        "OctaneDirectionalLight": ("Geometry out",),
        "OctaneVolumetricSpotlight": ("Geometry out",),
        "OctaneSpotlight": ("Texture out",),
        "ShaderNodeTexImage": ("Color", "Alpha"),
        "ShaderNodeBackground": ("Background",),
        "ShaderNodeRGB": ("Color",),
        "ShaderNodeTexEnvironment": ("Color",),
        "ShaderNodeMapping": ("Vector",),
        "ShaderNodeTexCoord": ("Generated",),
        "OctaneTextureEnvironment": ("Environment out",),
        "OctaneRGBImage": ("Texture out",),
        "OctaneAlphaImage": ("Texture out",),
        "OctanePerspective": ("Projection out",),
        "OctaneMultiplyTexture": ("Texture out",),
        "OctaneSpherical": ("Projection out",),
        "Octane3DTransformation": ("Transform out",),
        "OctaneObjectData": ("Transform out",),
        "ShaderNodeEmission": ("Emission",),
    }
    node = _Node(bl_idname, definitions.get(bl_idname, ()), outputs.get(bl_idname, ()))
    if bl_idname in {"OctaneRGBImage", "OctaneAlphaImage"}:
        node.image = None
        node.a_filename = ""
        node.a_reload = False
        node.frame_current = 0
        node.frame_duration = 0
        node.frame_offset = 0
        node.frame_start = 0
        node.use_auto_refresh = False
        node.use_cyclic = False
    if bl_idname == "ShaderNodeTexImage":
        node.image = None
        node.image_user = SimpleNamespace(
            frame_current=0,
            frame_duration=0,
            frame_offset=0,
            frame_start=1,
            use_auto_refresh=False,
            use_cyclic=False,
        )
    if bl_idname == "ShaderNodeGroup":
        node.node_tree = None
    if bl_idname == "OctaneRGBColor":
        node.a_value = (0.7, 0.7, 0.7)
    if bl_idname == "OctaneEditorWorldOutputNode":
        node.active = False
    if bl_idname == "OctaneObjectData":
        node.source_type = ""
        node.object_ptr = None
    return node


class _Nodes(list):
    def __init__(self) -> None:
        super().__init__()
        self.fail_on = None

    def new(self, bl_idname: str) -> _Node:
        if bl_idname == self.fail_on:
            raise RuntimeError(f"Unavailable node: {bl_idname}")
        node = _node_for_type(bl_idname)
        self.append(node)
        return node

    def remove(self, node: _Node) -> None:
        for socket in (*node.inputs, *node.outputs):
            for link in list(socket.links):
                link.tree.links.remove(link)
        super().remove(node)


class _Links(list):
    def __init__(self, tree) -> None:
        super().__init__()
        self.tree = tree

    def new(self, from_socket: _Socket, to_socket: _Socket):
        # Blender input sockets accept a single link and replace the previous
        # connection when a new one is created.
        for existing in list(to_socket.links):
            self.remove(existing)
        link = SimpleNamespace(
            from_node=from_socket.node,
            from_socket=from_socket,
            to_node=to_socket.node,
            to_socket=to_socket,
            tree=self.tree,
        )
        self.append(link)
        from_socket.links.append(link)
        to_socket.links.append(link)
        return link

    def remove(self, link) -> None:
        if link in self:
            super().remove(link)
        if link in link.from_socket.links:
            link.from_socket.links.remove(link)
        if link in link.to_socket.links:
            link.to_socket.links.remove(link)


class _Tree:
    def __init__(self) -> None:
        self.name = "NodeTree"
        self.nodes = _Nodes()
        self.links = _Links(self)


class _LightData(dict):
    def __init__(self, light_type: str) -> None:
        super().__init__()
        self.name = f"{light_type} Data"
        self.type = light_type
        self.energy = 12.0
        self.exposure = 0.0
        self.color = (0.2, 0.4, 0.8)
        self.normalize = True
        self.spot_size = math.radians(50.0)
        self.spot_blend = 0.35
        self.size = 2.5
        self.size_y = 1.25
        self.shape = "RECTANGLE"
        self.spread = math.radians(180.0)
        self.angle = math.radians(0.53)
        self.shadow_soft_size = 0.2
        self.use_temperature = False
        self.temperature = 6500.0
        self.octane = SimpleNamespace(
            octane_point_light_type="Toon Point",
            octane_directional_light_type="Toon Directional",
            used_as_octane_mesh_light=True,
        )
        self.node_tree = _Tree()
        self.node_tree.nodes.new("ShaderNodeOutputLight")
        self.use_nodes = False


class _World(dict):
    def __init__(self, tree: _Tree) -> None:
        super().__init__()
        self.name = "World"
        self.node_tree = tree
        self.use_nodes = True


def _light_object(light_type: str):
    return SimpleNamespace(
        name=f"{light_type} Object",
        type="LIGHT",
        data=_LightData(light_type),
    )


def _nodes(tree: _Tree, bl_idname: str) -> list[_Node]:
    return [node for node in tree.nodes if node.bl_idname == bl_idname]


def _gobo_image(name: str = "window.png", source: str = "FILE"):
    return SimpleNamespace(
        name=name,
        filepath=f"//gobos/{name}",
        filepath_raw="",
        source=source,
        colorspace_settings=SimpleNamespace(name="sRGB"),
    )


def _add_cycles_image_gobo(obj, name: str = "window.png"):
    obj.data.use_nodes = True
    tree = obj.data.node_tree
    output = _nodes(tree, "ShaderNodeOutputLight")[0]
    emission = tree.nodes.new("ShaderNodeEmission")
    image = tree.nodes.new("ShaderNodeTexImage")
    image.image = _gobo_image(name)
    tree.links.new(image.outputs.get("Color"), emission.inputs.get("Color"))
    tree.links.new(emission.outputs.get("Emission"), output.inputs.get("Surface"))
    return image, emission


def _add_light_wrangler_gobo(
    obj,
    name: str = "branches.mp4",
    *,
    animated: bool = True,
):
    obj.data.use_nodes = True
    tree = obj.data.node_tree
    output = _nodes(tree, "ShaderNodeOutputLight")[0]
    group_tree = _Tree()
    group_tree.name = "Gobo Light v2.001"
    image = group_tree.nodes.new("ShaderNodeTexImage")
    image.image = _gobo_image(name, "MOVIE" if animated else "FILE")
    image.image_user.frame_duration = 120
    image.image_user.frame_start = 3
    image.image_user.frame_offset = 7
    image.image_user.use_auto_refresh = animated
    image.image_user.use_cyclic = animated
    group = _Node(
        "ShaderNodeGroup",
        inputs=(
            ("Focus", 75),
            ("Vignette", 40),
            ("Rotation", math.radians(30.0)),
            ("Invert Gobo", True),
            ("Playback Speed", 1.0),
        ),
        outputs=("Emission",),
    )
    group.node_tree = group_tree
    tree.nodes.append(group)
    tree.links.new(group.outputs.get("Emission"), output.inputs.get("Surface"))
    return group, image


class LightConversionTests(unittest.TestCase):
    def test_smart_detection_skips_an_already_converted_light(self) -> None:
        obj = _light_object("AREA")
        self.assertTrue(light_needs_octane_conversion(obj))

        convert_light_to_octane(obj)

        self.assertFalse(light_needs_octane_conversion(obj))

    def test_smart_detection_upgrades_generated_texture_emission(self) -> None:
        obj = _light_object("AREA")
        obj.data.use_nodes = True
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]
        material = tree.nodes.new("OctaneDiffuseMaterial")
        emission = tree.nodes.new("OctaneTextureEmission")
        material["octanify_light_conversion"] = True
        emission["octanify_light_conversion"] = True
        tree.links.new(
            emission.outputs.get("Emission out"),
            material.inputs.get("Emission"),
        )
        tree.links.new(
            material.outputs.get("Material out"),
            output.inputs.get("Surface"),
        )

        self.assertTrue(light_needs_octane_conversion(obj))

    def test_upgrade_restores_cycles_link_replaced_by_legacy_converter(self) -> None:
        obj = _light_object("AREA")
        obj.data.use_nodes = True
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]
        cycles_emission = tree.nodes.new("ShaderNodeEmission")
        old_material = tree.nodes.new("OctaneDiffuseMaterial")
        old_emission = tree.nodes.new("OctaneBlackBodyEmission")
        old_material["octanify_light_conversion"] = True
        old_emission["octanify_light_conversion"] = True
        tree.links.new(
            old_emission.outputs.get("Emission out"),
            old_material.inputs.get("Emission"),
        )
        tree.links.new(
            old_material.outputs.get("Material out"),
            output.inputs.get("Surface"),
        )

        convert_light_to_octane(obj)

        self.assertEqual(output.target, "CYCLES")
        self.assertIs(
            output.inputs.get("Surface").links[0].from_node,
            cycles_emission,
        )

    def test_exposure_is_folded_into_effective_power(self) -> None:
        obj = _light_object("POINT")
        obj.data.exposure = 2.0
        result = convert_light_to_octane(obj)

        self.assertEqual(result["source_energy"], 12.0)
        self.assertEqual(result["source_exposure"], 2.0)
        self.assertEqual(result["effective_source_energy"], 48.0)
        self.assertEqual(
            result["octane_power"],
            48.0 * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        )

    def test_spot_carries_cone_blend_and_unclipped_power(self) -> None:
        obj = _light_object("SPOT")
        result = convert_light_to_octane(obj)

        self.assertAlmostEqual(
            result["octane_power"],
            12.0 * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        )
        self.assertEqual(result["source_unit"], "W (before cone clipping)")
        self.assertAlmostEqual(result["spot_size_degrees"], 50.0)
        self.assertAlmostEqual(result["spot_hardness"], 0.65)
        self.assertAlmostEqual(obj.data.spot_blend, 0.35)
        distribution = _nodes(obj.data.node_tree, "OctaneSpotlight")[0]
        self.assertAlmostEqual(distribution.inputs.get("Cone angle").default_value, 50.0)
        self.assertAlmostEqual(distribution.inputs.get("Hardness").default_value, 0.65)
        volumetric = _nodes(obj.data.node_tree, "OctaneVolumetricSpotlight")[0]
        self.assertAlmostEqual(
            volumetric.inputs.get("Cone hardness").default_value,
            0.65,
        )

    def test_area_preserves_size_and_translates_unnormalized_power(self) -> None:
        obj = _light_object("AREA")
        obj.data.normalize = False
        result = convert_light_to_octane(obj)

        self.assertAlmostEqual(
            result["power_factor"],
            OCTANE_BLACKBODY_DEFAULT_EFFICIENCY
            * OCTANE_SURFACE_BRIGHTNESS_REFERENCE_AREA,
        )
        self.assertAlmostEqual(
            result["octane_power"],
            12.0
            * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY
            * OCTANE_SURFACE_BRIGHTNESS_REFERENCE_AREA,
        )
        self.assertTrue(result["surface_brightness"])
        self.assertFalse(obj.data.octane.used_as_octane_mesh_light)
        self.assertEqual(result["area_shape"], "RECTANGLE")
        self.assertEqual((result["area_size"], result["area_size_y"]), (2.5, 1.25))

    def test_zero_radius_unnormalized_point_uses_cycles_reference_area(self) -> None:
        obj = _light_object("POINT")
        obj.data.normalize = False
        obj.data.shadow_soft_size = 0.0
        result = convert_light_to_octane(obj)

        self.assertEqual(
            result["power_factor"],
            4.0 * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        )
        self.assertEqual(
            result["octane_power"],
            48.0 * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        )
        self.assertFalse(result["surface_brightness"])

    def test_sun_uses_irradiance_path_and_object_transform(self) -> None:
        obj = _light_object("SUN")
        result = convert_light_to_octane(obj)

        self.assertEqual(result["source_unit"], "W/m²")
        self.assertAlmostEqual(
            result["octane_power"],
            12.0 * OCTANE_BLACKBODY_DEFAULT_EFFICIENCY,
        )
        self.assertFalse(result["surface_brightness"])
        self.assertAlmostEqual(result["sun_spread_degrees"], 0.53)
        self.assertEqual(obj.data.octane.octane_directional_light_type, "Directional")
        transform = _nodes(obj.data.node_tree, "OctaneObjectData")[0]
        self.assertIs(transform.object_ptr, obj)

    def test_unnormalized_sun_bakes_cycles_disc_factor_into_power(self) -> None:
        obj = _light_object("SUN")
        obj.data.normalize = False
        result = convert_light_to_octane(obj)
        expected_factor = (
            OCTANE_BLACKBODY_DEFAULT_EFFICIENCY
            * math.pi
            * math.sin(obj.data.angle * 0.5) ** 2
        )

        self.assertAlmostEqual(result["power_factor"], expected_factor)
        self.assertAlmostEqual(result["octane_power"], 12.0 * expected_factor)

    def test_reconversion_replaces_generated_nodes(self) -> None:
        obj = _light_object("POINT")
        convert_light_to_octane(obj)
        first_count = sum(
            bool(node.get("octanify_light_conversion"))
            for node in obj.data.node_tree.nodes
        )
        convert_light_to_octane(obj)
        second_count = sum(
            bool(node.get("octanify_light_conversion"))
            for node in obj.data.node_tree.nodes
        )
        self.assertEqual(first_count, second_count)
        self.assertEqual(
            len(_nodes(obj.data.node_tree, "OctaneBlackBodyEmission")),
            1,
        )

    def test_blackbody_emission_preserves_color_and_temperature(self) -> None:
        obj = _light_object("AREA")
        obj.data.energy = 20.0
        obj.data.use_temperature = True
        obj.data.temperature = 3200.0

        result = convert_light_to_octane(obj)

        self.assertAlmostEqual(result["octane_power"], 0.5)
        self.assertEqual(result["octane_temperature"], 3200.0)
        emission = _nodes(obj.data.node_tree, "OctaneBlackBodyEmission")[0]
        color = _nodes(obj.data.node_tree, "OctaneRGBColor")[0]
        self.assertEqual(color.a_value, obj.data.color)
        self.assertIs(
            emission.inputs.get("Texture").links[0].from_node,
            color,
        )
        self.assertEqual(emission.inputs.get("Temperature").default_value, 3200.0)
        self.assertTrue(emission.inputs.get("Normalize").default_value)

    def test_existing_surface_link_is_preserved_on_cycles_output(self) -> None:
        obj = _light_object("AREA")
        tree = obj.data.node_tree
        first_output = _nodes(tree, "ShaderNodeOutputLight")[0]
        second_output = tree.nodes.new("ShaderNodeOutputLight")
        second_output.is_active_output = True
        cycles_emission = tree.nodes.new("ShaderNodeEmission")
        tree.links.new(
            cycles_emission.outputs.get("Emission"),
            second_output.inputs.get("Surface"),
        )

        convert_light_to_octane(obj)

        self.assertFalse(first_output.inputs.get("Surface").links)
        self.assertEqual(second_output.target, "CYCLES")
        self.assertIs(
            second_output.inputs.get("Surface").links[0].from_node,
            cycles_emission,
        )
        converted_outputs = [
            node for node in _nodes(tree, "ShaderNodeOutputLight")
            if node.get("octanify_light_conversion")
        ]
        self.assertEqual(len(converted_outputs), 1)
        self.assertEqual(converted_outputs[0].target, "ALL")
        root = converted_outputs[0].inputs.get("Surface").links[0].from_node
        self.assertEqual(root.bl_idname, "OctaneDiffuseMaterial")

    def test_light_color_toggle_keeps_default_node_colors(self) -> None:
        obj = _light_object("AREA")

        convert_light_to_octane(obj, color_nodes=False)

        self.assertTrue(
            all(
                not node.use_custom_color
                for node in obj.data.node_tree.nodes
            )
        )

    def test_scene_collection_deduplicates_shared_light_data(self) -> None:
        first = _light_object("AREA")
        second = SimpleNamespace(name="Instance", type="LIGHT", data=first.data)
        scene = SimpleNamespace(objects=[first, second, _light_object("SUN")])
        self.assertEqual(_scene_light_objects(scene), [first, scene.objects[2]])

    def test_main_operator_detects_scene_light_without_active_object(self) -> None:
        light = _light_object("AREA")
        context = SimpleNamespace(
            active_object=None,
            selected_objects=[],
            scene=SimpleNamespace(
                objects=[light],
                world=None,
                octanify_batch_mode="ACTIVE",
                octanify_albedo_gamma=2.2,
                octanify_progress_active=False,
            ),
        )
        operator = OCTANIFY_OT_convert()
        operator.report = lambda *_args: None

        self.assertTrue(operator.poll(context))
        self.assertTrue(operator._prepare_job(context))
        self.assertEqual(operator._light_objects, [light])
        self.assertEqual(operator._work_items, [])

    def test_failed_conversion_keeps_existing_light_graph_and_mode(self) -> None:
        obj = _light_object("POINT")
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]
        emission = tree.nodes.new("ShaderNodeEmission")
        tree.links.new(emission.outputs.get("Emission"), output.inputs.get("Surface"))
        tree.nodes.fail_on = "OctaneBlackBodyEmission"

        with self.assertRaises(RuntimeError):
            convert_light_to_octane(obj)

        self.assertEqual(output.inputs.get("Surface").links[0].from_node, emission)
        self.assertEqual(obj.data.octane.octane_point_light_type, "Toon Point")
        self.assertFalse(obj.data.use_nodes)

    def test_normal_cycles_image_gobo_converts_to_perspective_distribution(self) -> None:
        obj = _light_object("AREA")
        source_image, _source_emission = _add_cycles_image_gobo(obj)

        detected = detect_light_gobo(obj)
        self.assertIsNotNone(detected)
        self.assertEqual(detected.source_kind, "CYCLES_IMAGE")

        result = convert_light_to_octane(obj)

        self.assertTrue(result["gobo_converted"])
        self.assertEqual(result["gobo_source_kind"], "CYCLES_IMAGE")
        image = _nodes(obj.data.node_tree, "OctaneRGBImage")[0]
        self.assertIs(image.image, source_image.image)
        self.assertEqual(image.a_filename, "ABS://gobos/window.png")
        self.assertEqual(
            image.inputs.get("Border mode (U)").default_value,
            "Black color",
        )
        projection = _nodes(obj.data.node_tree, "OctanePerspective")[0]
        self.assertIs(
            image.inputs.get("Projection").links[0].from_node,
            projection,
        )
        emission = _nodes(obj.data.node_tree, "OctaneBlackBodyEmission")[0]
        self.assertIs(
            emission.inputs.get("Distribution").links[0].from_node,
            image,
        )
        self.assertFalse(light_needs_octane_conversion(obj))

    def test_normal_alpha_gobo_preserves_mapping_controls(self) -> None:
        obj = _light_object("AREA")
        source_image, source_emission = _add_cycles_image_gobo(
            obj,
            "alpha-window.png",
        )
        tree = obj.data.node_tree
        tree.links.remove(source_image.outputs.get("Color").links[0])
        tree.links.new(
            source_image.outputs.get("Alpha"),
            source_emission.inputs.get("Color"),
        )
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs.get("Location").default_value = (0.1, -0.2, 0.0)
        mapping.inputs.get("Rotation").default_value = (
            0.0,
            0.0,
            math.radians(18.0),
        )
        mapping.inputs.get("Scale").default_value = (1.5, 0.75, 1.0)
        tree.links.new(
            mapping.outputs.get("Vector"),
            source_image.inputs.get("Vector"),
        )

        detected = detect_light_gobo(obj)
        self.assertEqual(detected.channel, "Alpha")
        self.assertAlmostEqual(math.degrees(detected.rotation_radians), 18.0)
        self.assertEqual(detected.mapping_scale, (1.5, 0.75, 1.0))

        result = convert_light_to_octane(obj)

        self.assertEqual(result["gobo_channel"], "Alpha")
        self.assertEqual(len(_nodes(tree, "OctaneAlphaImage")), 1)
        transform = _nodes(tree, "Octane3DTransformation")[0]
        self.assertEqual(
            transform.inputs.get("Scale").default_value,
            (1.5, 0.75, 1.0),
        )
        self.assertEqual(
            transform.inputs.get("Translation").default_value,
            (0.1, -0.2, 0.0),
        )

    def test_generic_nested_group_gobo_is_traced_without_addon_metadata(self) -> None:
        obj = _light_object("AREA")
        obj.data.use_nodes = True
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]

        group_tree = _Tree()
        group_tree.name = "User Projector Group"
        image = group_tree.nodes.new("ShaderNodeTexImage")
        image.image = _gobo_image("leaf-breakup.png")
        emission = group_tree.nodes.new("ShaderNodeEmission")
        group_output = _Node(
            "NodeGroupOutput",
            inputs=(("Emission", None),),
        )
        group_tree.nodes.append(group_output)
        group_tree.links.new(image.outputs.get("Color"), emission.inputs.get("Color"))
        group_tree.links.new(
            emission.outputs.get("Emission"),
            group_output.inputs.get("Emission"),
        )
        group = _Node("ShaderNodeGroup", outputs=("Emission",))
        group.node_tree = group_tree
        tree.nodes.append(group)
        tree.links.new(group.outputs.get("Emission"), output.inputs.get("Surface"))

        result = convert_light_to_octane(obj)

        self.assertEqual(result["gobo_source_kind"], "CYCLES_IMAGE")
        self.assertEqual(result["gobo_image_name"], "leaf-breakup.png")

    def test_light_wrangler_gobo_preserves_controls_and_animation(self) -> None:
        obj = _light_object("SPOT")
        _group, source_image = _add_light_wrangler_gobo(obj)

        detected = detect_light_gobo(obj)
        self.assertIsNotNone(detected)
        self.assertEqual(detected.source_kind, "LIGHT_WRANGLER")
        self.assertEqual(detected.focus, 75.0)
        self.assertEqual(detected.vignette, 40.0)
        self.assertTrue(detected.invert)
        self.assertTrue(detected.animated)

        result = convert_light_to_octane(obj)

        self.assertEqual(result["gobo_source_kind"], "LIGHT_WRANGLER")
        self.assertAlmostEqual(result["gobo_rotation_degrees"], 30.0)
        self.assertEqual(len(_nodes(obj.data.node_tree, "OctaneSpotlight")), 2)
        self.assertEqual(len(_nodes(obj.data.node_tree, "OctaneMultiplyTexture")), 2)
        transform = _nodes(obj.data.node_tree, "Octane3DTransformation")[0]
        self.assertAlmostEqual(
            transform.inputs.get("Rotation").default_value[2],
            30.0,
        )
        image = _nodes(obj.data.node_tree, "OctaneRGBImage")[0]
        self.assertIs(image.image, source_image.image)
        self.assertTrue(image.inputs.get("Invert").default_value)
        self.assertEqual(image.frame_duration, 120)
        self.assertEqual(image.frame_start, 3)
        self.assertEqual(image.frame_offset, 7)
        self.assertTrue(image.use_auto_refresh)
        self.assertTrue(image.use_cyclic)
        emission = _nodes(obj.data.node_tree, "OctaneBlackBodyEmission")[0]
        self.assertEqual(
            emission.inputs.get("Distribution").links[0].from_node.bl_idname,
            "OctaneMultiplyTexture",
        )
        self.assertFalse(light_needs_octane_conversion(obj))

    def test_light_wrangler_area_focus_sets_projector_cone(self) -> None:
        obj = _light_object("AREA")
        _add_light_wrangler_gobo(obj, animated=False)

        result = convert_light_to_octane(obj)

        self.assertAlmostEqual(result["gobo_cone_degrees"], 2.575)
        focus = _nodes(obj.data.node_tree, "OctaneSpotlight")[0]
        self.assertAlmostEqual(focus.inputs.get("Cone angle").default_value, 2.575)
        self.assertAlmostEqual(focus.inputs.get("Hardness").default_value, 0.64)

    def test_old_converted_graph_with_disconnected_normal_gobo_is_upgraded(self) -> None:
        obj = _light_object("AREA")
        _add_cycles_image_gobo(obj, "upgrade.png")
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]
        material = tree.nodes.new("OctaneDiffuseMaterial")
        emission = tree.nodes.new("OctaneBlackBodyEmission")
        material["octanify_light_conversion"] = True
        emission["octanify_light_conversion"] = True
        tree.links.new(
            emission.outputs.get("Emission out"),
            material.inputs.get("Emission"),
        )
        tree.links.new(
            material.outputs.get("Material out"),
            output.inputs.get("Surface"),
        )

        self.assertTrue(light_needs_octane_conversion(obj))

    def test_gobo_node_failure_rolls_back_without_breaking_cycles_light(self) -> None:
        obj = _light_object("AREA")
        _source_image, source_emission = _add_cycles_image_gobo(obj)
        tree = obj.data.node_tree
        output = _nodes(tree, "ShaderNodeOutputLight")[0]
        tree.nodes.fail_on = "OctanePerspective"

        with self.assertRaises(RuntimeError):
            convert_light_to_octane(obj)

        self.assertIs(
            output.inputs.get("Surface").links[0].from_node,
            source_emission,
        )
        self.assertFalse(
            any(node.get("octanify_light_conversion") for node in tree.nodes)
        )


class WorldConversionTests(unittest.TestCase):
    def _flat_world(self) -> _World:
        tree = _Tree()
        output = tree.nodes.new("ShaderNodeOutputWorld")
        output.is_active_output = True
        background = tree.nodes.new("ShaderNodeBackground")
        background.inputs.get("Color").default_value = (0.1, 0.2, 0.3, 1.0)
        background.inputs.get("Strength").default_value = 2.5
        tree.links.new(background.outputs.get("Background"), output.inputs.get("Surface"))
        return _World(tree)

    def test_flat_color_world_builds_texture_environment(self) -> None:
        world = self._flat_world()
        self.assertTrue(world_needs_octane_conversion(world))
        result = convert_world_to_octane(world)

        self.assertEqual(result["source_kind"], "FLAT")
        self.assertEqual(result["source_color"], (0.1, 0.2, 0.3))
        self.assertEqual(result["source_strength"], 2.5)
        environment = _nodes(world.node_tree, "OctaneTextureEnvironment")[0]
        self.assertEqual(environment.inputs.get("Texture").default_value, (0.1, 0.2, 0.3))
        self.assertEqual(environment.inputs.get("Power").default_value, 2.5)
        cycles_output = _nodes(world.node_tree, "ShaderNodeOutputWorld")[0]
        cycles_background = _nodes(world.node_tree, "ShaderNodeBackground")[0]
        self.assertEqual(cycles_output.target, "CYCLES")
        self.assertIs(
            cycles_output.inputs.get("Surface").links[0].from_node,
            cycles_background,
        )
        self.assertEqual(len(_nodes(world.node_tree, "OctaneEditorWorldOutputNode")), 1)
        self.assertFalse(world_needs_octane_conversion(world))

    def test_world_color_toggle_keeps_default_node_colors(self) -> None:
        world = self._flat_world()

        convert_world_to_octane(world, color_nodes=False)

        self.assertTrue(
            all(not node.use_custom_color for node in world.node_tree.nodes)
        )

    def test_connected_rgb_is_treated_as_a_flat_world_color(self) -> None:
        world = self._flat_world()
        tree = world.node_tree
        background = _nodes(tree, "ShaderNodeBackground")[0]
        rgb = tree.nodes.new("ShaderNodeRGB")
        rgb.outputs.get("Color").default_value = (0.8, 0.4, 0.2, 1.0)
        tree.links.new(rgb.outputs.get("Color"), background.inputs.get("Color"))

        result = convert_world_to_octane(world)

        self.assertEqual(result["source_kind"], "FLAT")
        self.assertEqual(result["source_color"], (0.8, 0.4, 0.2))

    def test_hdri_world_carries_strength_image_and_z_rotation(self) -> None:
        world = self._flat_world()
        tree = world.node_tree
        background = _nodes(tree, "ShaderNodeBackground")[0]
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs.get("Rotation").default_value = (0.1, 0.2, math.radians(90.0))
        environment = tree.nodes.new("ShaderNodeTexEnvironment")
        environment.image = SimpleNamespace(
            name="studio.exr",
            filepath_raw="//studio.exr",
            colorspace_settings=SimpleNamespace(name="Linear"),
        )
        tree.links.new(mapping.outputs.get("Vector"), environment.inputs.get("Vector"))
        tree.links.new(environment.outputs.get("Color"), background.inputs.get("Color"))

        self.assertIs(
            background.inputs.get("Color").links[0].from_node,
            environment,
        )
        self.assertIsNotNone(environment.image)

        result = convert_world_to_octane(world)

        self.assertEqual(result["source_kind"], "HDRI")
        self.assertEqual(result["image_name"], "studio.exr")
        self.assertEqual(result["image_path"], "ABS://studio.exr")
        self.assertAlmostEqual(result["mapping_z_rotation_degrees"], 90.0)
        self.assertEqual(result["octane_rotation_degrees"], (0.0, 90.0, 0.0))
        image_node = _nodes(tree, "OctaneRGBImage")[0]
        self.assertIs(image_node.image, environment.image)
        self.assertEqual(image_node.a_filename, "ABS://studio.exr")
        self.assertEqual(image_node.inputs.get("Legacy gamma").default_value, 1.0)
        transform = _nodes(tree, "Octane3DTransformation")[0]
        self.assertEqual(transform.inputs.get("Rotation").default_value, (0.0, 90.0, 0.0))

    def test_world_reconversion_is_idempotent(self) -> None:
        world = self._flat_world()
        convert_world_to_octane(world)
        convert_world_to_octane(world)
        self.assertEqual(len(_nodes(world.node_tree, "OctaneTextureEnvironment")), 1)
        self.assertEqual(len(_nodes(world.node_tree, "OctaneEditorWorldOutputNode")), 1)

    def test_upgrade_restores_world_output_removed_by_legacy_converter(self) -> None:
        world = self._flat_world()
        convert_world_to_octane(world)
        tree = world.node_tree
        tree.nodes.remove(_nodes(tree, "ShaderNodeOutputWorld")[0])
        world.pop("octanify_world_source_signature", None)

        self.assertTrue(world_needs_octane_conversion(world))
        convert_world_to_octane(world)

        output = _nodes(tree, "ShaderNodeOutputWorld")[0]
        background = _nodes(tree, "ShaderNodeBackground")[0]
        self.assertEqual(output.target, "CYCLES")
        self.assertIs(
            output.inputs.get("Surface").links[0].from_node,
            background,
        )

    def test_failed_conversion_keeps_blender_world_output(self) -> None:
        world = self._flat_world()
        world.node_tree.nodes.fail_on = "OctaneTextureEnvironment"

        with self.assertRaises(RuntimeError):
            convert_world_to_octane(world)

        self.assertEqual(len(_nodes(world.node_tree, "ShaderNodeOutputWorld")), 1)
        self.assertFalse(_nodes(world.node_tree, "OctaneEditorWorldOutputNode"))


if __name__ == "__main__":
    unittest.main()
