<div align="center">

# 🧪 O C T A N I F Y

```text
   ____       __              _ ____
  / __ \_____/ /_____ _____  (_) __/_  __
 / / / / ___/ __/ __ `/ __ \/ / /_/ / / /
/ /_/ / /__/ /_/ /_/ / / / / / __/ /_/ /
\____/\___/\__/\__,_/_/ /_/_/_/  \__, /
                                 /____/
```

**One-click Cycles → Octane scene conversion for Blender**

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Version-1.4.0-orange?style=for-the-badge&logo=blender"></a>
  <a href="#"><img src="https://img.shields.io/badge/Release%20Size-138%20KB-FF8C00?style=for-the-badge&logo=files"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--or--later-blue?style=for-the-badge"></a>
  <br>
  <a href="#"><img src="https://img.shields.io/badge/Blender-4.2%2B-F5792A?style=for-the-badge&logo=blender"></a>
  <a href="#"><img src="https://img.shields.io/badge/Runtime%20Verified-Blender%205.1%20%2B%20Octane%2031.9-green?style=for-the-badge"></a>
</p>

[**Interface**](#the-interface) • [**Features**](#features) • [**How It Works**](#how-it-works) • [**Supported Nodes**](#supported-nodes) • [**Installation**](#installation)

</div>

---

**Octanify** is a production-grade Blender addon that intelligently translates complex Cycles materials, supported lights, and World environments into high-fidelity Octane equivalents. It preserves shader intent, texture chains, procedural structure, and the original Cycles renderer branches with a single click.

> Convert supported branches without routine rewiring, preserve the Cycles source, and inspect approximations or fallbacks in one structured report.

---

## 📑 Table of Contents

- [🖥️ The Interface](#the-interface)
- [📖 How to Use Octanify](#how-to-use-octanify)
- [✨ Features](#features)
- [🎨 Destination-Aware Texture Intent](#destination-aware-texture-intent)
- [🛠️ Workflow Utilities](#workflow-utilities)
- [⚙️ How It Works](#how-it-works)
- [🔌 Supported Nodes](#supported-nodes)
- [📁 Project Structure](#project-structure)
  - [Validation](#validation)
- [📥 Installation](#installation)
  - [Blender 4.2+](#blender-42)
  - [Requirements](#requirements)
  - [Runtime-verified configuration and compatibility note](#runtime-verified-configuration-and-compatibility-note)
- [🙏 Credits](#credits)
- [License](#license)

---

## <a id="the-interface"></a>🖥️ The Interface

Once installed, press `N` in the 3D Viewport or Shader Editor and open the **Octanify** tab. The interface uses a compact control-deck layout: conversion stays at the top, common material and node tools remain immediately accessible, and advanced details start collapsed.

```text
┌──────────────────────────────────────────┐
│ ▼ Octanify                               │
│ ┌──────────────────────────────────────┐ │
│ │       [ CONVERT TO OCTANE ]          │ │
│ │ Objects to Convert                   │ │
│ │ [ Active Object ] [ Entire Scene ]   │ │
│ │ Selection + active object's children │ │
│ │ Octane Material                      │ │
│ │ [ Standard Surface (Recommended)  ▼ ]│ │
│ │ Recommended - closest Principled match│ │
│ │ [ ] Auto-upgrade SSS to Standard     │ │
│ │ [ Arrange Nodes ] [ Color Nodes ]    │ │
│ │ ✓ Last conversion completed          │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌──────────── Albedo Gamma ────────────┐ │
│ │ [ gamma slider                      ]│ │
│ │ [ Selected Material ] [ All Materials]│ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌────────────── Node Tools ────────────┐ │
│ │ [ Preview Node in Viewport          ]│ │
│ │ [ Arrange Current Node Tree         ]│ │
│ │ [Create Material] [Connect Textures] │ │
│ │ [ Delete Cycles Nodes               ]│ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ▶ Displacement Settings                  │
│ ▶ Conversion Report                      │
└──────────────────────────────────────────┘
```

During conversion, the completion message is replaced by a live percentage bar and the current operation. Once complete, the bar collapses back to a quiet status line so it does not compete with the main action.

## 📖 How to Use Octanify

### 1️⃣ Conversion Console

The large **Convert to Octane** action is always the first control. The conversion choices sit directly beneath it:

*   **`Active Object`** converts materials discovered on selected objects plus every descendant of the active object. This includes materials assigned by nested Geometry Nodes `Set Material` nodes and supports parent/empty-based production assets whose root object has no material slots.
*   **`Entire Scene`** scans every object, material slot, and Geometry Nodes material assignment in the scene.
*   **`Octane Material`** chooses what each Cycles *Principled BSDF* becomes. `Standard Surface` is recommended because its separate base, specular, transmission, coat, sheen, and subsurface controls most closely match Principled semantics. `Universal Material` remains available for compatibility and uses the plain `GGX` BRDF model—not `Octane` or `GGX (energy preserving)`. `Glossy Material` provides the classic Octane diffuse/specular workflow and reports Principled lobes it cannot represent instead of wiring them into unrelated sockets.
*   **`Auto-upgrade SSS materials to Standard Surface`** is an optional, disabled-by-default safety override. When enabled, a Principled material with active subsurface intent uses Standard Surface even if Universal or Glossy was selected; every override appears in the conversion report.
*   **`Arrange Nodes`** controls automatic dependency-based layout after conversion, including nested frame contents and converted node groups.
*   **`Color Nodes`** independently controls the graphite/teal editor themes. Disable it to retain Blender's default node colors without disabling layout or graph tagging.

The same action also detects supported Cycles lights anywhere in the scene and the active World. Conversion preserves the authored Cycles material, light, and World branches while creating separate Octane outputs. Re-running conversion skips current generated light/World graphs and upgrades graphs made by older Octanify builds when possible.

### 2️⃣ Live Progress

The progress bar appears only while conversion is running. It shows both a real-time percentage and the current material/node operation. `Esc` stops between materials while keeping completed changes undoable. After conversion, a compact success line replaces the bar.

### 3️⃣ Albedo Gamma

The visible gamma control handles a common post-conversion adjustment without opening another panel. Change the slider, then choose **Selected Material** or **All Materials** to update existing Octane textures without reconverting.

### 4️⃣ Node Tools

Frequently used node actions stay together in a compact tool group:

*   **Preview Node in Viewport** temporarily routes the selected node through an emission preview.
*   **Arrange Current Node Tree** immediately arranges the material, World, light, or nested node-group tree currently open in the Node Editor.
*   **Create Material** creates a clean material using the selected Octane material type.
*   **Connect Textures** connects loose PBR image nodes by filename patterns.
*   **Delete Cycles Nodes** removes only Cycles nodes explicitly tagged by Octanify for the current `Active Object` or `Entire Scene` choice. Original Cycles nodes remain preserved by default, and cleanup asks for confirmation and supports Blender Undo.

### 5️⃣ Displacement Settings

Open this subpanel only when the defaults need adjustment:

*   **`Mode`** selects the node class created for a Cycles Displacement node: Octane Texture Displacement for image/procedural height fields or Octane Vertex Displacement for displacement evaluated on mesh vertices and subdivision.
*   **`Level of Detail`** is shown only in Texture mode and sets that node's displacement-map resolution limit.
*   **`Mid Level`** transfers from the Cycles node by default. Setting a non-default panel value applies it as the conversion-wide zero-point override.

This preference is consumed while nodes are created; it is not a display-only setting. The Blender 5.1 + Octane 31.9 runtime validator checks that the two modes produce `OctaneTextureDisplacement` and `OctaneVertexDisplacement`, respectively.

### 6️⃣ Conversion Report

Open this subpanel to inspect materials converted, nodes translated, links created, approximations, unsupported nodes, failed links, and warnings from the most recent conversion. It stays collapsed during normal use so detailed diagnostics never obscure the primary action.

## <a id="features"></a>✨ Features

| Feature | Description |
|---|---|
| 🎯 **Principled BSDF Targets** | Fidelity-first Standard Surface mapping, physically tuned Universal compatibility mapping, or a narrower classic Glossy Material workflow with explicit unsupported-lobe reporting |
| 🧬 **Smart SSS Override** | Optionally promotes only Principled materials with active subsurface intent to Standard Surface while leaving the selected target unchanged for other materials |
| 🧠 **Shading Intent** | Traces backward from destination sockets so Base Color/Emission stay color-managed and Roughness/Metallic/Normal/Alpha remain linear even when source image colorspaces are mislabeled |
| 🪆 **Node Groups** | Recursively converts nested Node Groups into cached Octane group datablocks without ungrouping or mutating the authored Cycles groups |
| 🎬 **Driver Preservation** | Automatically transfers `#frame` expressions and animation drivers |
| 🔗 **Link Reconstruction** | Rebuilds topology with 7-strategy socket matching, shared-node preservation, reroute flattening, and duplicate socket handling |
| 🧭 **Graph Engine** | Cycle-safe dependency scheduling preserves branching, muted nodes, material outputs, and nested group boundaries |
| 🪟 **Glass & Transmission** | Converts glass, refraction, alpha, and transmission paths without corrupting unrelated Universal Material defaults |
| 💡 **Emission** | Auto-inserts Texture Emission for materials with the existing x100 scale and uses Black Body Emission with type-specific power conversion for lights |
| 🌫️ **Volumetrics** | Applies Octane's 100-based medium-density scale, preserves linked density drivers, merges compatible Absorption + Scatter graphs, and supports volume-only materials through Null Material |
| 🗺️ **Normal, Bump & Displacement** | Preserves normal chains, folds bump height into material inputs, and creates Texture or Vertex Displacement according to the selected mode |
| 🧩 **UV Mapping** | Routes Texture Coordinate / UV Map to Octane Projection and Mapping to UV Transform, including radians-to-degrees rotation conversion |
| 🌀 **Procedural Scale Matching** | Matches Generated/Object-coordinate Noise, Musgrave, and Voronoi scale with object bounds and the validated Cinema 4D Noise frequency convention |
| 🧱 **Composite Texture Layers** | Rebuilds color Mix/MixRGB nodes as native Composite Texture layers when available, with transactional fallback to legacy Mix nodes |
| 🧬 **Geometry Nodes Materials** | Discovers `Set Material` references through nested Geometry Nodes groups and Switch branches, then deduplicates them against normal slots |
| 💡 **Scene Lights** | Converts Point, Sun, Spot, and Area lights with exposure, normalization, cone/size controls, and separate renderer-targeted outputs |
| 🌍 **World Environments** | Converts flat-color and HDRI Worlds with strength, gamma, and Mapping rotation while preserving the Cycles World branch |
| 🎭 **Gobos** | Converts generic image gobos and Light Wrangler setups, including Alpha, mapping, inversion, animation, focus, and vignette controls |
| 📦 **Batch Conversion** | Converts selected hierarchies or complete scenes with live progress plus automatic scene-light and active-World detection |
| 🔄 **Dual Renderer Graphs** | Keeps Cycles and Octane material, light, and World branches with renderer-targeted outputs, optional themes, automatic layout, and safe cleanup/migration |
| 🧹 **Node Arrangement** | Ranks dependencies, reduces crossings, packs disconnected components, handles cycles, and arranges nested frame contents without changing graph semantics |
| 🛡️ **Structured Fallbacks** | Unsupported or approximate conversions stay visible, produce warnings, and do not crash the conversion |
| 🧮 **Math & Mix Wrappers** | Uses Octane Cycles-compatible wrappers where available, with native fallbacks for plugin-version differences |

---

## 🎨 Destination-Aware Texture Intent

Octane requires color textures such as Base Color and Emission to use gamma `2.2`, while data textures such as Roughness, Metallic, Normal, Bump, and Alpha must remain linear (`1.0`). Octanify determines that treatment by tracing backward from the destination shader socket instead of trusting the image's declared Blender colorspace.

If the declaration conflicts with the destination, the destination wins and the conversion report asks you to verify the source asset. If one Image Texture output is shared by both color and data paths, Octanify creates separate Octane texture instances so each branch receives the correct treatment.

```text
      [ CYCLES TREE ]                  [ OCTANE TREE ]
  ┌───────────────────┐             ┌───────────────────┐
  │ Color Map (sRGB)  ├─┐         ┌─┤ Gamma: 2.2        │
  └───────────────────┘ │         │ └───────────────────┘
                        ▼         │
               ┌────────┴───┐     │        ┌────────────┐
               │ Principled │ ===>│        │ Standard   │
               │ BSDF       │     │        │ Surface    │
               └────────┬───┘     │        └────────────┘
                        ▲         │
  ┌───────────────────┐ │         │ ┌───────────────────┐
  │ Data Map (Linear) ├─┘         └─┤ Gamma: 1.0 (Auto) │
  └───────────────────┘             └───────────────────┘
```
*If a color texture needs artistic adjustment later, change the `Albedo Gamma` slider and click **Selected Material** to update the active Octane material without reconverting.*

---

## <a id="workflow-utilities"></a>🛠️ Workflow Utilities

These buttons (located at the bottom of the panel) speed up your daily shader work:

### 🔗 `[ Auto-Connect Textures ]`
Dropped a bunch of loose PBR images into the shader editor? Octanify will read the filenames and instantly connect them to the correct sockets based on smart word boundaries (`_col`, `_rough`, `_nrm`, `_disp`, etc).

```text
 ┌───────────────┐
 │ wood_col.png  ├──────────┐     ┌──────────────┐
 └───────────────┘          └───► │ Albedo Color │
                                  │              │
 ┌───────────────┐          ┌───► │ Roughness    │
 │ wood_rgh.png  ├──────────┘     │              │
 └───────────────┘                │ Universal    │
                                  │ Material     │
 ┌───────────────┐          ┌───► │ Normal       │
 │ wood_nrm.png  ├──────────┘     │              │
 └───────────────┘                └──────────────┘
```

### 👁️ `[ Preview Node in Viewport ]`
Trying to figure out what a complex Math node or ColorRamp is doing? Select it and click Preview. Octanify isolates the node by temporarily routing it through an Emission setup so you can see it glowing in the live Octane viewport.

```text
 ┌───────────────┐             ┌─────────────┐
 │ Complex       │    [👁️]    │ Temporary   │
 │ Noise Pattern ├───►CLICK───►│ Emission    │──► (VIEWPORT)
 └───────────────┘             └─────────────┘
```

### 🆕 `[ Create Basic Material ]`
Instantly wipes the default Cycles Principled BSDF and gives you a fresh, properly-wired Octane `Standard Surface` material (recommended), `Universal` material, or `Glossy` material connected to the Material Output.

### 🧹 `[ Arrange Current Node Tree ]`
Arranges the tree currently open in the Node Editor, including nested group-edit contexts. Converted dual-renderer trees keep their Cycles and Octane branches separated; framed graphs are arranged from the deepest frame outward. This action changes editor positions only—it never inserts, removes, ungroups, or rewires shader nodes.

---

## <a id="how-it-works"></a>⚙️ How It Works

```text
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Analyze    │────▶│    Create    │────▶│   Transfer   │
│  Cycles Tree │     │ Octane Nodes │     │  Properties  │
└──────────────┘     └──────────────┘     └──────────────┘
                                                │
┌──────────────┐     ┌──────────────┐           ▼
│   Apply      │◀────│    Post-     │◀────┌──────────────┐
│   Gamma      │     │   Process    │     │   Rebuild    │
└──────────────┘     └──────────────┘     │    Links     │
                                          └──────────────┘
```

1. **Discover** — Collect normal material slots and recursively discover Geometry Nodes `Set Material` references. The same operator detects supported scene lights and the active World.
2. **Trace intent** — Walk backward from terminal shader inputs through reroutes and nested groups, recording destination roles per node output path before conversion begins.
3. **Analyze** — Snapshot the Cycles node tree, normalize reroutes, preserve shared branches, and record properties, links, muted state, outputs, and group boundaries.
4. **Schedule and create** — Use cycle-safe dependency ordering and runtime-resolved `bl_idname` candidates to create compatible Octane nodes without ungrouping source groups.
5. **Transfer** — Target-aware handlers map Cycles values into actual Octane material semantics. Standard Surface receives independent PBR layer weights and colors, Universal receives its required scaled and composed equivalents, and Glossy receives only its compatible classic diffuse/specular controls. The optional SSS override is resolved per material before creation.
6. **Rebuild** — Seven-strategy socket resolution reconnects duplicate socket identities, output indices, one-to-many channel picker bindings, and split Projection / UV Transform paths.
7. **Post-process** — Fix Mix Shader order, expand supported color mixes into Composite Texture layers, insert emission nodes, apply destination-aware gamma, handle opacity/Alpha, Normal/Bump, displacement, 100-based medium density and volume topology, mixed-role texture duplication, and object-aware procedural scale matching.
8. **Convert scene domains** — Build renderer-targeted Octane light and World branches, including supported gobos, only after complete replacement graphs validate successfully.
9. **Style and report** — Optionally color and arrange both renderer graphs, report live progress and approximations, and leave recoverable failures visible for manual review.

---

## <a id="supported-nodes"></a>🔌 Supported Nodes

The full support matrix lives in [`octanify/NODE_SUPPORT.md`](octanify/NODE_SUPPORT.md). It currently tracks **99 Cycles node entries** plus dedicated scene-domain conversion:

- **84 supported or preserved**: 44 direct, 36 approximate, 3 version-dependent, and 1 layout-only.
- **15 unsupported**: 13 unsupported and 2 unsupported in the current Octane plugin.
- Section coverage: Input 13/20, Output 3/4, Shader 23/24, Texture 13/15, Color 6/7, Vector 8/9, Converter 14/15, Script/Group 4/5.
- Scene domains: Point/Sun/Spot/Area lights, flat/HDRI Worlds, generic gobos, and Light Wrangler gobos.

<details>
<summary><strong>Shaders (23 supported / 24 tracked)</strong></summary>

- Principled BSDF → Standard Surface by default / Universal or Glossy Material option, with an optional per-material SSS upgrade to Standard Surface
- Glass BSDF → Specular Material
- Glossy BSDF → Glossy Material
- Diffuse BSDF → Diffuse Material
- Metallic BSDF → Metallic Material
- Toon BSDF → Toon Material
- Sheen BSDF → Universal Material
- Hair BSDF / Principled Hair → Hair Material
- Subsurface Scattering (standalone) → Universal Material
- Emission → Diffuse Material + Texture Emission, including colored/textured zero-strength intent detection
- Transparent BSDF → Null Material
- Translucent BSDF → Diffuse Material
- Refraction BSDF → Specular Material
- Holdout / Ray Portal / Background → Support mapped
- Mix Shader / Add Shader → Mix Material (with auto slot swap)
</details>

<details>
<summary><strong>Textures (13 supported / 15 tracked)</strong></summary>

- Image Texture → Octane Image Texture (with strict colorspace/packed-file handling)
- Environment / Sky Texture → Octane Image / Daylight Env
- Noise / Musgrave → Cinema 4D Noise when available, including Generated/Object-coordinate scale matching
- Voronoi → Cinema 4D Voronoi when available, including Generated/Object-coordinate scale matching
- White Noise → Octane Noise fallback
- Wave → Octane Wave
- Checker → Octane Checks
- Brick / Magic → Octane Marble
- Gradient → Octane Gradient
</details>

<details>
<summary><strong>Input / Vector (21 supported / 29 tracked)</strong></summary>

- Mapping → UV Transform; Vector Rotate / Transform → approximate 3D Transform
- Texture Coordinate / UV Map → Mesh UV Projection
- Normal Map → direct material Normal routing; Normal / Tangent → approximate normal-texture candidates
- Bump → material Bump plus Bump Height, while preserving a chained Normal input
- Displacement → selected Octane Texture or Vertex Displacement; Vector Displacement → Octane Vertex Displacement
- RGB / Wavelength → Octane RGB Color
- Value → Octane Float Value
- Fresnel / Layer Weight → Octane Fresnel
- Vertex Color → Octane Color Vertex Attribute
- Attribute → Octane Attribute
- Ambient Occlusion → Octane Dirt Texture
- Object Info → compatible instance-color use; Camera Data → exact Octane Camera Data outputs
</details>

<details>
<summary><strong>Color & Math (20 supported / 22 tracked)</strong></summary>

- Math / Vector Math → Mapped to specific Add/Multiply/Math nodes based on inner operation
- Map Range → Octane Range
- Clamp → Octane Clamp
- Invert / Hue Saturation / Brightness Contrast / Gamma / RGB Curves → Octane Color Correction & Gamma nodes
- Blackbody → Black Body Emission; RGBToBW and ShaderToRGB use reported approximations
- Color Mix / MixRGB → native Composite Texture + two layers when available; legacy Mix fallback otherwise
</details>

<details>
<summary><strong>Volumetrics (3 supported / 4 tracked)</strong></summary>

- Volume Absorption → Octane Absorption Medium with Cycles density scaled by 100
- Volume Scatter → Octane Scattering Medium with Cycles density scaled by 100
- Direct Absorption + Scatter additions → one native Scattering Medium when compatible
- Linked density chains → Octane Multiply Texture scale stage; volume-only graphs → Null Material + Medium
- Volume Principled → best-effort Octane volume/standard-medium mapping
</details>

<details>
<summary><strong>Passthrough & Logic (4 supported / 5 tracked)</strong></summary>

- Node Groups / Group Input / Group Output — deeply traversed and rebuilt as converted groups without ungrouping
- Separate Color / RGB / XYZ — handled inline
- Combine Color / RGB / XYZ — handled inline
</details>

---

## 📁 Project Structure

```text
Octanify/
├── README.md                            # User guide and architecture overview
├── LICENSE                              # GPL-3.0-or-later license
└── octanify/                            # Blender add-on package
    ├── __init__.py                      # Registration, bl_info, and scene properties
    ├── blender_manifest.toml            # Blender 4.2+ extension metadata
    ├── core/                            # Conversion backend
    │   ├── conversion_engine.py         # Transactional conversion orchestration and link rebuild
    │   ├── shader_detection.py          # Tree analysis, outputs, reroutes, and graph boundaries
    │   ├── graph_engine.py              # Cycle-safe dependency scheduling and node creation
    │   ├── shading_intent.py            # Destination-role tracing across paths and nested groups
    │   ├── geonodes_scan.py             # Recursive Geometry Nodes material discovery
    │   ├── node_registry.py             # 99 tracked Cycles node strategies and Octane candidates
    │   ├── property_mapper.py           # Values, enums, target-specific mappings, and fidelity-safe defaults
    │   ├── layout_engine.py             # Ranked layout, frame handling, graph spacing, and themes
    │   ├── gamma_system.py              # Intent-aware color/data gamma and texture duplication
    │   ├── light_converter.py           # Light power, Black Body emission, and gobo conversion
    │   ├── world_converter.py           # Flat/HDRI World environment conversion
    │   ├── volumetric_handler.py        # Cycles volume to Octane medium routing
    │   └── report.py                    # Structured statistics, warnings, and approximations
    ├── ui/                              # Blender interface layer
    │   ├── panel.py                     # Viewport and Shader Editor control-deck panels
    │   └── operators.py                 # Batch conversion, progress, cleanup, and material tools
    ├── utils/                           # Shared infrastructure
    │   ├── cache.py                     # Conversion and material deduplication cache
    │   └── logger.py                    # Structured console logging
    ├── NODE_SUPPORT.md                  # Cycles node support and fallback matrix
    ├── MATERIAL_FIDELITY.md             # Principled/Octane mapping and validation notes
    ├── tests/                           # Repository-only automated regression tests
    │   ├── test_graph_and_registry.py   # Graph, mapping, lifecycle, and regression coverage
    │   ├── test_light_and_world_conversion.py # Lights, Worlds, gobos, and rollback coverage
    │   └── test_panel_ui.py             # UI hierarchy and visibility regression coverage
    └── tools/                           # Repository-only Blender/Octane validation utilities
        ├── blender_fixture_scene.py     # Builds production-style validation materials
        ├── blender_validate_conversion.py # Runs integration and fidelity assertions
        ├── blender_validation_bootstrap.py # Loads the workspace add-on and Octane plugin
        ├── blender_probe_universal_material.py # Compares Universal Material initialization
        ├── blender_validate_phase4.py   # Composite layers and procedural-scale runtime checks
        ├── blender_validate_phase5.py   # Target selection, SSS, displacement, and volume checks
        ├── blender_validate_glossy_material.py # Glossy Material runtime checks
        ├── blender_validate_node_groups.py # Operator-level nested shader-group checks
        ├── blender_probe_bootstrap.py   # Bootstraps Octane material probes
        └── blender_inspect_octane_nodes.py # Inspects installed Octane node definitions
```

The clean release archive contains the license and runtime add-on files: `LICENSE`, `__init__.py`, `blender_manifest.toml`, `core/`, `ui/`, and `utils/`. Documentation, tests, validation tools, caches, and development metadata remain repository-only.

### Validation

The repository currently passes 151 Python regression tests. Node-RNA-sensitive paths have also been exercised in Blender 5.1 with OctaneRender for Blender 31.9, including Composite Texture layers, Cinema 4D procedural scale matching at 1 m, 2 m, and 5 m object sizes, exact Texture/Vertex displacement selection, Glossy target mapping, smart SSS overrides, medium-density/topology reconstruction, one-level and three-level shader groups through the real conversion operator, nested-frame arrangement, disabled custom coloring, renderer-targeted Light outputs, preserved World outputs, and Octane active-output resolution. These checks validate graph structure and parameter transfer; renderer-to-renderer pixel equivalence still requires matched reference renders, as described in [`octanify/MATERIAL_FIDELITY.md`](octanify/MATERIAL_FIDELITY.md).

---

## <a id="installation"></a>📥 Installation

### Blender 4.2+
1. Download `octanify.zip`
2. Open Blender → `Edit → Preferences → Add-ons`
3. Click the dropdown arrow → **Install from Disk**
4. Select `octanify.zip`
5. Enable **Octanify**

### Requirements
- **Blender** 4.2 or later
- **OctaneRender** plugin for Blender (required for Octane node creation)

### Runtime-verified configuration and compatibility note

The current node-RNA validation baseline is Blender 5.1 with OctaneRender for Blender 31.9. Other supported Blender/Octane combinations use ordered node-class fallbacks but should be checked against the conversion report.

Octane 31.9 may print an `active_output_name` callback traceback when Blender 5.1 activates a custom Octane World output. In validated conversions the property is still applied and Octane resolves the generated World output correctly. Treat it as a conversion failure only when the Octanify report also records a failed World conversion or the generated environment output is missing/unlinked.

---

## 🙏 Credits

- Architecture inspired by analysis of [cycles2octane](https://github.com/RodrigoGama1902/cycles2octane) by Rodrigo Gama
- Ranked layout and crossing-reduction behavior is adapted for Octanify from [node-arrange](https://github.com/Leonardo-Pike-Excell/node-arrange) by Leonardo Pike-Excell

## License

GPL-3.0-or-later — Compatible with Blender's licensing requirements.

---

<p align="center">
  <sub>Built with ☕ by <strong>Niloy Bhowmick</strong></sub>
</p>
