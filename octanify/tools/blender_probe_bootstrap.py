"""Enable local add-ons and run the Universal Material A/B probe."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import addon_utils


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ADDON = (REPOSITORY_ROOT / "octanify" / "__init__.py").resolve()


def _disable_non_workspace_octanify() -> None:
    """Prevent an installed extension copy from contaminating validation."""
    for module in addon_utils.modules():
        module_file = getattr(module, "__file__", "")
        try:
            resolved = Path(module_file).resolve()
        except (OSError, TypeError, ValueError):
            continue
        if module.__name__.split(".")[-1] != "octanify" or resolved == LOCAL_ADDON:
            continue
        if addon_utils.check(module.__name__)[1]:
            addon_utils.disable(module.__name__, default_set=False)


_disable_non_workspace_octanify()
octane_addon_root = os.environ.get("OCTANE_ADDON_ROOT", "")
if octane_addon_root and octane_addon_root not in sys.path:
    sys.path.insert(0, octane_addon_root)
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

if not addon_utils.check("octane")[1]:
    addon_utils.enable("octane", default_set=True, persistent=False)
if not addon_utils.check("octane")[1]:
    raise RuntimeError(
        "Octane failed to register; set OCTANE_ADDON_ROOT to the directory "
        "that contains the octane package"
    )

import octanify

if Path(octanify.__file__).resolve() != LOCAL_ADDON:
    raise RuntimeError(
        f"Validation imported {octanify.__file__}, expected {LOCAL_ADDON}"
    )
print(f"OCTANIFY_WORKSPACE_ADDON {LOCAL_ADDON}")
octanify.register()

from octanify.tools.blender_probe_universal_material import main

main()
