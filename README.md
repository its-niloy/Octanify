<p align="center">
  <h1 align="center">Octanify</h1>
  <p align="center">
    <strong>One-click Cycles → Octane material conversion for Blender</strong>
  </p>
  <p align="center">
    <a href="#the-interface">Interface</a> •
    <a href="#features">Features</a> •
    <a href="#workflow-utilities">Utilities</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#supported-nodes">Supported Nodes</a> •
    <a href="#installation">Installation</a>
  </p>
</p>

---

**Octanify** is a production-grade Blender addon that intelligently translates complex Cycles material trees into high-fidelity Octane equivalents — preserving shader intent, texture chains, and procedural structure with a single click.

> No manual node rewiring. No broken links. Just convert and render.

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

*   **`Active Object`** converts selected objects plus every descendant of the active object. The explanation beneath the choice always states what will be included. This supports parent/empty-based production assets whose root object has no material slots.
*   **`Entire Scene`** scans the scene and converts every material used by its objects.
*   **`Octane Material`** chooses what each Cycles *Principled BSDF* becomes. `Standard Surface` is recommended because its separate base, specular, transmission, coat, sheen, and subsurface controls most closely match Principled semantics. `Universal Material` remains available for compatibility and uses the plain `GGX` BRDF model—not `Octane` or `GGX (energy preserving)`.

Conversion preserves the authored Cycles graph, creates and auto-arranges a separate Octane graph, and gives both graphs distinct renderer outputs and editor themes.

### 2️⃣ Live Progress

The progress bar appears only while conversion is running. It shows both a real-time percentage and the current material/node operation. `Esc` stops between materials while keeping completed changes undoable. After conversion, a compact success line replaces the bar.

### 3️⃣ Albedo Gamma

The visible gamma control handles a common post-conversion adjustment without opening another panel. Change the slider, then choose **Selected Material** or **All Materials** to update existing Octane textures without reconverting.

### 4️⃣ Node Tools

Frequently used node actions stay together in a compact tool group:

*   **Preview Node in Viewport** temporarily routes the selected node through an emission preview.
*   **Create Material** creates a clean material using the selected Octane material type.
*   **Connect Textures** connects loose PBR image nodes by filename patterns.
*   **Delete Cycles Nodes** removes only Cycles nodes explicitly tagged by Octanify for the current `Active Object` or `Entire Scene` choice. Original Cycles nodes remain preserved by default, and cleanup asks for confirmation and supports Blender Undo.

### 5️⃣ Displacement Settings

Open this subpanel only when the defaults need adjustment:

*   **`Mode`** selects `Texture` (standard image-based displacement) or `Vertex` (memory-efficient mesh displacement).
*   **`Level of Detail`** controls the texture displacement resolution limit.
*   **`Mid Level`** sets the zero point of the height map.

### 6️⃣ Conversion Report

Open this subpanel to inspect materials converted, nodes translated, links created, approximations, unsupported nodes, failed links, and warnings from the most recent conversion. It stays collapsed during normal use so detailed diagnostics never obscure the primary action.

## <a id="features"></a>✨ Features

| Feature | Description |
|---|---|
| 🎯 **Principled BSDF** | Fidelity-first Standard Surface mapping with direct PBR layers; physically tuned Universal compatibility mapping remains optional |
| 🪆 **Node Groups** | Recursively converts and preserves nested Node Groups with interface caching and recursion guards |
| 🎬 **Driver Preservation** | Automatically transfers `#frame` expressions and animation drivers |
| 🔗 **Link Reconstruction** | Rebuilds topology with 7-strategy socket matching, shared-node preservation, reroute flattening, and duplicate socket handling |
| 🧭 **Graph Engine** | Cycle-safe dependency scheduling preserves branching, muted nodes, material outputs, and nested group boundaries |
| 🪟 **Glass & Transmission** | Converts glass, refraction, alpha, and transmission paths without corrupting unrelated Universal Material defaults |
| 💡 **Emission** | Auto-inserts Octane TextureEmission nodes and perfectly scales Power (x100) |
| 🌫️ **Volumetrics** | Maps Volume Absorption/Scatter directly to Octane Medium nodes |
| 🗺️ **Normal & Bump** | Preserves normal chains, folds bump height into material inputs, and routes displacement according to user settings |
| 🧩 **UV Mapping** | Routes Texture Coordinate / UV Map to Octane Projection and Mapping to UV Transform, including radians-to-degrees rotation conversion |
| 📦 **Batch Conversion** | Converts selected hierarchies or complete scenes with a repainting live percentage bar and operation label |
| 🔄 **Dual Renderer Graphs** | Always keeps Cycles and Octane graphs in one material with renderer-targeted outputs, graphite/teal themes, automatic layout, and optional safe Cycles cleanup |
| 🛡️ **Structured Fallbacks** | Unsupported or approximate conversions stay visible, produce warnings, and do not crash the conversion |
| 🧮 **Math & Mix Wrappers** | Uses Octane Cycles-compatible wrappers where available, with native fallbacks for plugin-version differences |

---

## 🎨 Smart Albedo Gamma

Octane requires specific gamma curves. Albedo/Color maps need `2.2`, while data maps (Roughness, Normal, Metallic) must remain linear (`1.0`). Octanify auto-detects linear data nodes and skips them, applying gamma correction only where needed!

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
*If things look washed out later, adjust the `Albedo Gamma` slider and click **`[ Update Selected ]`** to fix the active material instantly without re-converting.*

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
Instantly wipes the default Cycles Principled BSDF and gives you a fresh, properly-wired Octane `Standard Surface` material (recommended) or optional `Universal` material connected to the Material Output.

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

1. **Analyze** — Snapshot the Cycles node tree, normalize reroutes, preserve shared branches, and record properties, links, muted state, and output intent.
2. **Schedule** — Cycle-safe dependency ordering creates upstream nodes first while keeping disconnected and fallback nodes visible.
3. **Create** — Instantiate Octane equivalents using runtime-resolved `bl_idname` candidates for compatibility across Octane plugin versions.
4. **Transfer** — Target-aware handlers map Cycles values into the actual Octane material semantics. Standard Surface receives independent PBR layer weights and colors; Universal receives its required scaled and composed equivalents.
5. **Rebuild** — 7-strategy socket resolution reconnects links, duplicate socket identities, output indices, one-to-many channel picker bindings, and split Projection / UV Transform paths.
6. **Post-process** — Fix MixShader order, insert emission nodes, scale linked specular controls, compose Universal-only coat/sheen helpers, handle alpha/opacity, Normal/Bump, displacement, and volumetrics.
7. **Style, Report & Gamma** — Apply gamma correction, create distinct Cycles/Octane graph themes, auto-arrange with a safe gap, report live progress, and keep recoverable failures visible for manual review.

---

## <a id="supported-nodes"></a>🔌 Supported Nodes

The full support matrix lives in [`octanify/NODE_SUPPORT.md`](octanify/NODE_SUPPORT.md). It currently tracks **99 Cycles node entries**:

- **82 supported or preserved**: 42 direct, 36 approximate, 3 version-dependent, and 1 layout-only.
- **17 unsupported or out of scope**: 13 unsupported, 2 unsupported in current Octane, and 2 out of scope.
- Section coverage: Input 13/20, Output 1/4, Shader 23/24, Texture 13/15, Color 6/7, Vector 8/9, Converter 14/15, Script/Group 4/5.

<details>
<summary><strong>Shaders (23 supported / 24 tracked)</strong></summary>

- Principled BSDF → Standard Surface by default / Universal Material option
- Glass BSDF → Specular Material
- Glossy BSDF → Glossy Material
- Diffuse BSDF → Diffuse Material
- Metallic BSDF → Metallic Material
- Toon BSDF → Toon Material
- Sheen BSDF → Universal Material
- Hair BSDF / Principled Hair → Hair Material
- Subsurface Scattering (standalone) → Universal Material
- Emission → Diffuse Material + TextureEmission
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
- Noise / Musgrave / White Noise → Octane Noise
- Voronoi → Octane Voronoi
- Wave → Octane Wave
- Checker → Octane Checks
- Brick / Magic → Octane Marble
- Gradient → Octane Gradient
</details>

<details>
<summary><strong>Input / Vector (21 supported / 29 tracked)</strong></summary>

- Mapping / Vector Rotate / Transform → 3D Transform
- Texture Coordinate / UV Map → Mesh UV Projection
- Normal Map / Normal / Tangent → direct connection to Normal input
- Bump → Octane Bump Texture
- Displacement / Vector Displacement → Octane Displacement
- RGB / Wavelength → Octane RGB Color
- Value → Octane Float Value
- Fresnel / Layer Weight → Octane Fresnel
- Vertex Color → Octane Color Vertex Attribute
- Attribute → Octane Attribute
- Ambient Occlusion → Octane Dirt Texture
- Object / Camera / Hair / Particle Info → Float/Instance Data values
</details>

<details>
<summary><strong>Color & Math (20 supported / 22 tracked)</strong></summary>

- Math / Vector Math → Mapped to specific Add/Multiply/Math nodes based on inner operation
- Map Range → Octane Range
- Clamp → Octane Clamp
- Invert / Hue Saturation / Brightness Contrast / Gamma / RGB Curves → Octane Color Correction & Gamma nodes
- RGBToBW / Blackbody / ShaderToRGB → Fully mapped
</details>

<details>
<summary><strong>Volumetrics (3 supported / 4 tracked)</strong></summary>

- Volume Absorption → Octane Absorption Medium
- Volume Scatter → Octane Scattering Medium
- Volume Principled → Octane Volume Medium
</details>

<details>
<summary><strong>Passthrough & Logic (4 supported / 5 tracked)</strong></summary>

- Node Groups / Group Input / Group Output — deeply traversed, flattened, and rebuilt
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
    │   ├── node_registry.py             # 99 tracked Cycles node strategies and Octane candidates
    │   ├── property_mapper.py           # Values, enums, transforms, and fidelity-safe defaults
    │   ├── layout_engine.py             # Dual-graph spacing, themes, and automatic arrangement
    │   ├── gamma_system.py              # Albedo/data texture gamma classification and updates
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
    │   └── test_panel_ui.py             # UI hierarchy and visibility regression coverage
    └── tools/                           # Repository-only Blender/Octane validation utilities
        ├── blender_fixture_scene.py     # Builds production-style validation materials
        ├── blender_validate_conversion.py # Runs integration and fidelity assertions
        ├── blender_validation_bootstrap.py # Loads the workspace add-on and Octane plugin
        ├── blender_probe_universal_material.py # Compares Universal Material initialization
        ├── blender_probe_bootstrap.py   # Bootstraps Octane material probes
        └── blender_inspect_octane_nodes.py # Inspects installed Octane node definitions
```

The clean release archive contains the license and runtime add-on files: `LICENSE`, `__init__.py`, `blender_manifest.toml`, `core/`, `ui/`, and `utils/`. Documentation, tests, validation tools, caches, and development metadata remain repository-only.

---

## <a id="installation"></a>📥 Installation

### Blender 4.2+ / 5.1+
1. Download `octanify.zip`
2. Open Blender → `Edit → Preferences → Add-ons`
3. Click the dropdown arrow → **Install from Disk**
4. Select `octanify.zip`
5. Enable **Octanify**

### Requirements
- **Blender** 4.2 or later
- **OctaneRender** plugin for Blender (required for Octane node creation)

---

## 🙏 Credits

- Architecture inspired by analysis of [cycles2octane](https://github.com/RodrigoGama1902/cycles2octane) by Rodrigo Gama

## License

GPL-3.0-or-later — Compatible with Blender's licensing requirements.

---

<p align="center">
  <sub>Built with ☕ by <strong>Niloy Bhowmick</strong></sub>
</p>
