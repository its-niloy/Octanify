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

## 🖥️ The Interface

Once installed, press `N` in the 3D Viewport or Shader Editor to open the sidebar and find the **Octanify** tab. It matches this layout perfectly:

```text
┌──────────────────────────────────────────────┐
│ ▼ Octanify                                   │
├──────────────────────────────────────────────┤
│ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ │
│ █            CONVERT TO OCTANE             █ │
│ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ │
│   [ ↺ Revert to Cycles                     ] │
│                                              │
│ ▼ Batch Object Conversion:                   │
│   [ ◉ Active Object ]    [ ○ All Objects ]   │
│                                              │
│ ▼ Albedo Gamma & Update Tools:               │
│   Albedo Gamma    <────────[ 2.20 ]────────> │
│   [ 🖌️ Update Selected Material            ] │
│   [ 🌍 Update All Materials                ] │
│                                              │
│ ▼ Conversion Settings:                       │
│   Target Material [ Universal Material   ▼ ] │
│   Displacement:                              │
│   [ ◉ Texture ]          [ ○ Vertex ]        │
│   Level of Detail [ 2048x2048            ▼ ] │
│   Mid Level       <────────[ 0.50 ]────────> │
│                                              │
│ ▼ Utilities:                                 │
│   [ 👁️ Preview Node in Viewport           ] │
│   [ 🆕 Create Basic Material              ] │
│   [ 🔗 Auto-Connect Textures              ] │
│                                              │
│ ▼ Last Conversion Report:                    │
│   [ ℹ️ Materials Converted: 10             ] │
│   [ 🗃️ Nodes Translated: 40               ] │
│   Warnings:                                  │
│   • [MatName] Unsupported: NodeName          │
└──────────────────────────────────────────────┘
```

## 📖 How to Use Octanify

A quick breakdown of every setting in the panel so you know exactly what to click.

### 1️⃣ The Big Button
```text
  █▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ 
  █            CONVERT TO OCTANE             █ 
  █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ 
```
*   **What it does:** This is the magic button. It safely duplicates your Cycles materials, appends `_OCTANE` to the name, and intelligently translates the entire node tree.

### 2️⃣ Batch Object Conversion
```text
  ▼ Batch Object Conversion:                   
    [ ◉ Active Object ]    [ ○ All Objects ]   
```
*   **`Active Object`**: Converts materials *only* on the currently selected object.
*   **`All Objects`**: Scans your *entire scene* and converts every material it finds. (Grab a coffee for large scenes!)

### 3️⃣ Albedo Gamma Control
```text
  ▼ Albedo Gamma Control:                      
    Albedo Gamma    <────────[ 2.20 ]────────> 
```
*   **Why it matters:** Octane expects sRGB color textures (like Albedo/Diffuse) to have a gamma of `2.2`. Data textures (like Roughness or Normal) must remain linear (`1.0`).
*   **What it does:** Sets the target gamma curve for color textures. *Don't worry about data textures—Octanify automatically detects linear maps and skips them!*

### 4️⃣ Conversion Settings
```text
  ▼ Conversion Settings:                       
    Target Material [ Universal Material   ▼ ] 
    Displacement:                              
    [ ◉ Texture ]          [ ○ Vertex ]        
    Level of Detail [ 2048x2048            ▼ ] 
    Mid Level       <────────[ 0.50 ]────────> 
```
*   **`Target Material`**: Choose what the Cycles *Principled BSDF* turns into. Use `Universal` for maximum flexibility, or `Standard Surface` for strict industry-standard PBR workflows.
*   **`Displacement`**: 
    *   **Mode**: `Texture` (Standard image-based) or `Vertex` (Memory-efficient mesh displacement).
    *   **Level of Detail (LOD)**: The resolution limit for texture displacement mapping.
    *   **Mid Level**: The zero-point shift for your height maps.

### 5️⃣ Material Update Tools
```text
  ▼ Albedo Gamma & Update Tools:               
    Albedo Gamma    <────────[ 2.20 ]────────> 
    [ 🖌️ Update Selected Material            ] 
    [ 🌍 Update All Materials                ] 
```
*   **What it does:** If you realize your materials look washed out *after* converting, you don't need to click Convert again. Just change the `Albedo Gamma` slider, and click one of these buttons to instantly push the new gamma value to your existing Octane textures. `Update All Materials` will update *every* converted material in your entire Blender scene!

### 6️⃣ Revert to Cycles
```text
    [ ↺ Revert to Cycles                     ]
```
*   **What it does:** Accidentally converted a material or want to go back? This instantly swaps your active object (or all objects, depending on your Batch Mode) back to the original Cycles materials, keeping your workflow totally non-destructive.

### 7️⃣ Last Conversion Report
```text
  ▼ Last Conversion Report:                    
    [ ℹ️ Materials Converted: 10             ] 
    [ 🗃️ Nodes Translated: 40               ] 
    Warnings:                                  
    • [MatName] Unsupported: NodeName          
```
*   **What it does:** Shows a summary of the most recent conversion. It tracks exactly how many materials were converted and how many nodes were translated. If any nodes were skipped or required a fallback, it lists them concisely by material and node type (e.g., `[Wood] Unsupported: RGBCurves`) so you can quickly find and fix them in the Shader Editor.

## ✨ Features

| Feature | Description |
|---|---|
| 🎯 **Principled BSDF** | Full 20+ input mapping to Universal or Standard Surface Material |
| 🪆 **Node Groups** | Recursively converts and preserves nested Node Groups |
| 🎬 **Driver Preservation** | Automatically transfers `#frame` expressions and animation drivers |
| 🔗 **Link Reconstruction** | Rebuilds all node connections with 7-strategy socket matching |
| 🪟 **Glass & Transmission** | Auto-detects transmission > 0.5 and configures specular mode |
| 💡 **Emission** | Auto-inserts Octane TextureEmission nodes and perfectly scales Power (x100) |
| 🌫️ **Volumetrics** | Maps Volume Absorption/Scatter directly to Octane Medium nodes |
| 🗺️ **Normal & Bump** | Direct-connects normal map textures and translates bump heights |
| 📦 **Batch Conversion** | Convert entire scenes with one click (now with native Progress Bars!) |
| 🔄 **Safe Revert** | Non-destructive — instantly swap back to the original Cycles setup |
| 🧮 **OSL Math Precision** | Flawless translation of Math/VectorMath/Mix nodes via hidden Octane OSL Wrappers |

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
               │ Principled │ ===>│        │ Universal  │
               │ BSDF       │     │        │ Material   │
               └────────┬───┘     │        └────────────┘
                        ▲         │
  ┌───────────────────┐ │         │ ┌───────────────────┐
  │ Data Map (Linear) ├─┘         └─┤ Gamma: 1.0 (Auto) │
  └───────────────────┘             └───────────────────┘
```
*If things look washed out later, adjust the `Albedo Gamma` slider and click **`[ Update Selected ]`** to fix the active material instantly without re-converting.*

---

## 🛠️ Workflow Utilities

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
Instantly wipes the default Cycles Principled BSDF and gives you a fresh, properly-wired Octane `Universal` or `Standard Surface` material connected to the Material Output.

---

## ⚙️ How It Works

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

1. **Analyze** — Snapshot the Cycles node tree (nodes, links, properties, patterns).
2. **Schedule** — Topological sort ensures dependencies are created first.
3. **Create** — Instantiate Octane equivalents using runtime-resolved `bl_idname` candidates.
4. **Transfer** — 30+ per-type handlers map Cycles values → Octane parameters.
5. **Rebuild** — 7-strategy socket resolution reconnects all links.
6. **Post-process** — Fix MixShader order, insert emission nodes, handle Normal/Bump fallbacks, alpha/opacity routing, and volumetrics.
7. **Gamma** — Apply albedo gamma correction (skips non-color textures).

---

## 🔌 Supported Nodes

<details>
<summary><strong>Shaders (15+ types)</strong></summary>

- Principled BSDF → Universal Material / Standard Surface
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
<summary><strong>Textures (11 types)</strong></summary>

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
<summary><strong>Input / Vector (15+ types)</strong></summary>

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
<summary><strong>Color & Math (12+ types)</strong></summary>

- Math / Vector Math → Mapped to specific Add/Multiply/Math nodes based on inner operation
- Map Range → Octane Range
- Clamp → Octane Clamp
- Invert / Hue Saturation / Brightness Contrast / Gamma / RGB Curves → Octane Color Correction & Gamma nodes
- RGBToBW / Blackbody / ShaderToRGB → Fully mapped
</details>

<details>
<summary><strong>Volumetrics</strong></summary>

- Volume Absorption → Octane Absorption Medium
- Volume Scatter → Octane Scattering Medium
- Volume Principled → Octane Volume Medium
</details>

<details>
<summary><strong>Passthrough & Logic</strong></summary>

- Node Groups / Group Input / Group Output — deeply traversed, flattened, and rebuilt
- Separate Color / RGB / XYZ — handled inline
- Combine Color / RGB / XYZ — handled inline
</details>

---

## 📁 Project Structure

```text
octanify/
├── __init__.py                 # Entry point, bl_info, scene properties
├── blender_manifest.toml       # Blender 4.2+ extension manifest
├── core/
│   ├── node_registry.py        # 40+ Cycles → Octane node mappings
│   ├── shader_detection.py     # Tree analysis, reroute & transparent flattening
│   ├── graph_engine.py         # Dependency scheduling & node creation
│   ├── property_mapper.py      # 30+ per-type value transfer handlers
│   ├── conversion_engine.py    # Main orchestrator pipeline
│   ├── gamma_system.py         # Albedo gamma correction
│   └── volumetric_handler.py   # Volume → Octane medium handling
├── ui/
│   ├── panel.py                # N-Panel (3D Viewport + Shader Editor)
│   └── operators.py            # Convert & gamma update operators
└── utils/
    ├── logger.py               # Console logging
    └── cache.py                # Material dedup cache
```

---

## 📥 Installation

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
