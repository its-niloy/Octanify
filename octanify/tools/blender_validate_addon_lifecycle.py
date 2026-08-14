"""Validate Octanify registration lifecycle in Blender 5.2 + Octane 31.10."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import addon_utils
import bpy


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ADDON = (REPOSITORY_ROOT / "octanify" / "__init__.py").resolve()


def main() -> None:
    octane_root = os.environ.get("OCTANE_ADDON_ROOT", "")
    if octane_root and octane_root not in sys.path:
        sys.path.insert(0, octane_root)
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))

    if not addon_utils.check("octane")[1]:
        addon_utils.enable("octane", default_set=True, persistent=False)
    if not addon_utils.check("octane")[1]:
        raise RuntimeError("Octane failed to register")

    import octanify

    if Path(octanify.__file__).resolve() != LOCAL_ADDON:
        raise RuntimeError(
            f"Validation imported {octanify.__file__}, expected {LOCAL_ADDON}"
        )

    output_property_preexisting = hasattr(
        bpy.types.ShaderNodeTree,
        "active_output_name",
    )
    passes = []
    for pass_index in range(2):
        octanify.register()
        if not hasattr(bpy.types.Scene, "octanify_keep_cycles_nodes"):
            raise AssertionError("Keep Cycles property was not registered")
        if not bool(bpy.context.scene.octanify_keep_cycles_nodes):
            raise AssertionError("Keep Cycles property did not default to checked")
        if not hasattr(bpy.types.ShaderNodeTree, "active_output_name"):
            raise AssertionError("Octane output compatibility property is missing")

        octanify.unregister()
        if hasattr(bpy.types.Scene, "octanify_keep_cycles_nodes"):
            raise AssertionError("Keep Cycles property leaked after unregister")
        if (
            hasattr(bpy.types.ShaderNodeTree, "active_output_name")
            != output_property_preexisting
        ):
            raise AssertionError(
                "Octane output compatibility ownership changed after unregister"
            )
        passes.append(pass_index + 1)

    octanify.register()
    payload = {
        "blender_version": ".".join(map(str, bpy.app.version)),
        "octane_enabled": addon_utils.check("octane")[1],
        "registration_passes": passes,
        "keep_cycles_default": bool(
            bpy.context.scene.octanify_keep_cycles_nodes
        ),
        "output_compatibility_registered": hasattr(
            bpy.types.ShaderNodeTree,
            "active_output_name",
        ),
    }
    print("OCTANIFY_LIFECYCLE_VALIDATION_BEGIN")
    print(json.dumps(payload, indent=2))
    print("OCTANIFY_LIFECYCLE_VALIDATION_END")


if __name__ == "__main__":
    main()
    bpy.ops.wm.quit_blender()
