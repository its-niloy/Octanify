from __future__ import annotations

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
                "Operator",
                "Panel",
                "Scene",
                "UILayout",
            )
        }
    )
    bpy.props = SimpleNamespace(
        BoolProperty=lambda **_kwargs: None,
        EnumProperty=lambda **_kwargs: None,
        FloatProperty=lambda **_kwargs: None,
        IntProperty=lambda **_kwargs: None,
        StringProperty=lambda **_kwargs: None,
    )
    bpy.utils = SimpleNamespace(
        register_class=lambda _cls: None,
        unregister_class=lambda _cls: None,
    )
    bpy.context = SimpleNamespace(scene=SimpleNamespace())
    sys.modules["bpy"] = bpy


_install_bpy_stub()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from octanify.ui.panel import (  # noqa: E402
    OCTANIFY_PT_main_panel,
    OCTANIFY_PT_shader_panel,
    _draw_albedo_controls,
    _draw_conversion_console,
    _draw_last_report,
    _draw_node_tools,
    classes,
)
from octanify.core.report import report_data  # noqa: E402


class _RecordingLayout:
    """Minimal UILayout recorder shared by nested rows, columns, and boxes."""

    def __init__(self, events=None) -> None:
        self.events = [] if events is None else events
        self.enabled = True
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = True

    def _child(self, kind: str):
        self.events.append((kind,))
        return _RecordingLayout(self.events)

    def box(self):
        return self._child("box")

    def row(self, **_kwargs):
        return self._child("row")

    def column(self, **_kwargs):
        return self._child("column")

    def separator(self, **_kwargs) -> None:
        self.events.append(("separator",))

    def operator(self, operator_id: str, **kwargs):
        self.events.append(("operator", operator_id, kwargs))
        return SimpleNamespace()

    def prop(self, _owner, property_name: str, **kwargs) -> None:
        self.events.append(("prop", property_name, kwargs))

    def label(self, **kwargs) -> None:
        self.events.append(("label", kwargs))


def _scene(**overrides):
    values = {
        "octanify_batch_mode": "ACTIVE",
        "octanify_base_material": "STANDARD_SURFACE",
        "octanify_smart_material_override": False,
        "octanify_albedo_gamma": 2.2,
        "octanify_auto_arrange": True,
        "octanify_color_nodes": True,
        "octanify_progress": 0,
        "octanify_progress_label": "Ready",
        "octanify_progress_active": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PanelHierarchyTests(unittest.TestCase):
    def tearDown(self) -> None:
        report_data.clear()

    def test_primary_action_precedes_compact_conversion_choices(self) -> None:
        layout = _RecordingLayout()
        _draw_conversion_console(layout, SimpleNamespace(scene=_scene()))

        operator_index = next(
            index
            for index, event in enumerate(layout.events)
            if event[0] == "operator" and event[1] == "octanify.convert"
        )
        scope_index = next(
            index
            for index, event in enumerate(layout.events)
            if event[0] == "prop" and event[1] == "octanify_batch_mode"
        )
        material_index = next(
            index
            for index, event in enumerate(layout.events)
            if event[0] == "prop" and event[1] == "octanify_base_material"
        )

        self.assertLess(operator_index, scope_index)
        self.assertLess(scope_index, material_index)
        override_index = next(
            index
            for index, event in enumerate(layout.events)
            if event[0] == "prop"
            and event[1] == "octanify_smart_material_override"
        )
        self.assertGreater(override_index, material_index)
        self.assertTrue(layout.events[override_index][2].get("toggle"))
        self.assertTrue(
            any(
                event[0] == "prop"
                and event[1] == "octanify_auto_arrange"
                and event[2].get("toggle")
                for event in layout.events
            )
        )
        self.assertTrue(
            any(
                event[0] == "prop"
                and event[1] == "octanify_color_nodes"
                and event[2].get("toggle")
                for event in layout.events
            )
        )

        target_event = layout.events[scope_index]
        action_event = layout.events[operator_index]
        self.assertTrue(target_event[2].get("expand"))
        self.assertEqual(
            action_event[2].get("text"),
            "Convert to Octane",
        )
        self.assertTrue(
            any(
                event[0] == "label"
                and event[1].get("text") == "Selection + active object's children"
                for event in layout.events
            )
        )

    def test_glossy_material_choice_has_specific_workflow_hint(self) -> None:
        layout = _RecordingLayout()
        _draw_conversion_console(
            layout,
            SimpleNamespace(scene=_scene(octanify_base_material="GLOSSY")),
        )

        self.assertTrue(
            any(
                event[0] == "label"
                and event[1].get("text") == "Classic diffuse + glossy workflow"
                for event in layout.events
            )
        )

    def test_albedo_and_node_tools_expose_compact_action_groups(self) -> None:
        context = SimpleNamespace(scene=_scene())

        albedo_layout = _RecordingLayout()
        _draw_albedo_controls(albedo_layout, context)
        self.assertTrue(
            any(
                event[0] == "prop" and event[1] == "octanify_albedo_gamma"
                for event in albedo_layout.events
            )
        )
        self.assertEqual(
            [event[1] for event in albedo_layout.events if event[0] == "operator"],
            [
                "octanify.update_selected_gamma",
                "octanify.update_all_gamma",
            ],
        )

        tools_layout = _RecordingLayout()
        _draw_node_tools(tools_layout, context)
        self.assertEqual(
            [event[1] for event in tools_layout.events if event[0] == "operator"],
            [
                "octanify.preview_node_viewport",
                "octanify.arrange_node_tree",
                "octanify.create_basic_material",
                "octanify.auto_connect_textures",
                "octanify.delete_cycles_nodes",
            ],
        )

    def test_progress_bar_only_appears_while_conversion_is_active(self) -> None:
        complete_layout = _RecordingLayout()
        _draw_conversion_console(
            complete_layout,
            SimpleNamespace(scene=_scene(octanify_progress=100)),
        )
        self.assertFalse(
            any(
                event[0] == "prop" and event[1] == "octanify_progress"
                for event in complete_layout.events
            )
        )
        self.assertTrue(
            any(
                event[0] == "label"
                and event[1].get("text") == "Last conversion completed"
                for event in complete_layout.events
            )
        )

        active_layout = _RecordingLayout()
        _draw_conversion_console(
            active_layout,
            SimpleNamespace(
                scene=_scene(
                    octanify_progress=42,
                    octanify_progress_label="Converting Rubber",
                    octanify_progress_active=True,
                )
            ),
        )
        self.assertTrue(
            any(
                event[0] == "prop" and event[1] == "octanify_progress"
                for event in active_layout.events
            )
        )

    def test_secondary_panels_are_closed_and_mirrored_between_editors(self) -> None:
        secondary = [
            panel
            for panel in classes
            if panel not in (OCTANIFY_PT_main_panel, OCTANIFY_PT_shader_panel)
        ]
        self.assertEqual(len(secondary), 4)
        self.assertTrue(
            all(panel.bl_options == {"DEFAULT_CLOSED"} for panel in secondary)
        )
        self.assertEqual(
            sum(panel.bl_parent_id == "OCTANIFY_PT_main_panel" for panel in secondary),
            2,
        )
        self.assertEqual(
            sum(
                panel.bl_parent_id == "OCTANIFY_PT_shader_panel"
                for panel in secondary
            ),
            2,
        )
        labels = [panel.bl_label for panel in secondary]
        for label in (
            "Displacement Settings",
            "Conversion Report",
        ):
            self.assertEqual(labels.count(label), 2)

    def test_last_report_surfaces_intent_notices_and_warnings(self) -> None:
        report_data.add_notice(
            "[Mat] 'packed.png' used for both color and data roles"
        )
        report_data.add_warning(
            "[Mat] 'roughness.png' feeds Roughness but is set to sRGB"
        )
        layout = _RecordingLayout()

        _draw_last_report(layout, SimpleNamespace())

        labels = [
            event[1].get("text")
            for event in layout.events
            if event[0] == "label"
        ]
        self.assertIn("Notices (1)", labels)
        self.assertIn("Warnings (1)", labels)
        self.assertTrue(any("packed.png" in label for label in labels))
        self.assertTrue(any("roughness.png" in label for label in labels))


if __name__ == "__main__":
    unittest.main()
