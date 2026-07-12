"""Enable local addons for one background process and run validation."""

from __future__ import annotations

import sys
from pathlib import Path

import addon_utils


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

if not addon_utils.check("octane")[1]:
    addon_utils.enable("octane", default_set=False, persistent=False)

import octanify

octanify.register()

from octanify.tools.blender_fixture_scene import create_fixture_scene
from octanify.tools.blender_validate_conversion import main

create_fixture_scene()
main()
