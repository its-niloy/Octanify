# Cycles node support matrix

This matrix targets Blender 4.2+ and the OctaneRender for Blender node set.
Octane node `bl_idname` values have changed between plugin releases, so
Octanify tries ordered runtime candidates. **Direct** means the graph and core
parameters have a usable Octane representation; it does not claim pixel-level
identity between two different renderers. **Approximate** conversions always
appear in the conversion report. **Unsupported** nodes remain visible as red
fallbacks and produce warnings instead of being silently removed.

Primary references:

- [Blender 4.5 shader node index](https://docs.blender.org/manual/en/latest/render/shader_nodes/index.html)
- [OctaneRender 2026 for Blender node index](https://docs.otoy.com/blender/OCTANE_BLENDER.html)
- [Octane texture operators](https://docs.otoy.com/blender/Operators1.html)
- [Octane channel picker](https://docs.otoy.com/blender/Channelpicker.html)
- [Octane emission](https://docs.otoy.com/blender/Emission.html)
- [Octane media](https://docs.otoy.com/blender/Medium.html)

## Input nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Ambient Occlusion | Approximate | Dirt Texture; AO and Color outputs are not identical |
| Attribute | Approximate | Attribute texture; Color/Vector/Fac share one source |
| Bevel | Direct | Bevel Texture candidates |
| Camera Data | Direct | Octane Camera Data with exact View Vector/Z Depth/Distance outputs |
| Fresnel | Direct | Fresnel Texture |
| Geometry | Unsupported | Multi-output geometry data requires output-specific Octane nodes |
| Curves Info | Unsupported in current Octane | No current Hair Data source node exposes equivalent outputs |
| Layer Weight | Approximate | Fresnel Texture; Facing and Fresnel are not independent |
| Light Path | Unsupported | Octane Ray Switch is selector-based, not a 1:1 boolean-output node |
| Object Info | Approximate | Instance Color-compatible uses only |
| Particle Info | Unsupported | Octane instance/object data is not equivalent to particle lifetime/velocity data |
| Point Info | Unsupported | No safe generic material mapping |
| RGB | Direct | RGB Color Texture |
| Tangent | Approximate | Normal/Tangent texture candidates; verify coordinate mode |
| Texture Coordinate | Approximate | Mesh UV Projection; non-UV outputs differ |
| UV Map | Direct | Mesh UV Projection; non-default layer names are reported for index verification |
| Value | Direct | Float Value |
| Color Attribute | Direct | Color Vertex Attribute; alpha may require manual setup |
| Volume Info | Unsupported | No current material texture exposes equivalent density/flame/temperature outputs |
| Wireframe | Unsupported in current Octane | Octane exposes Wireframe AOVs, not a material texture equivalent |

## Output nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Material Output | Direct | Preserves authored outputs, resolves Blender's explicit Cycles branch, and creates one `ALL` output selected by Octane |
| AOV Output | Unsupported | Requires explicit Octane custom-AOV semantics |
| Light Output | Out of scope | Add-on converts material node trees |
| World Output | Out of scope | World/environment conversion is not yet implemented |

## Shader nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Add Shader | Approximate | 50/50 Mix Material; additive closure energy is not identical |
| Background | Approximate | Diffuse/emission material behavior, not world conversion |
| Diffuse BSDF | Direct | Diffuse Material |
| Emission | Direct | Diffuse Material plus generated Texture Emission node; linked/default color and strength preserved |
| Glass BSDF | Direct | Specular Material; color routed to transmission |
| Glossy BSDF | Direct | Glossy Material |
| Hair BSDF | Approximate | Hair Material/Universal fallback; parameter models differ |
| Holdout | Approximate | Null Material |
| Mix Shader | Direct | Modern Octane Mix Material with duplicate socket identity and branch order preserved |
| Metallic BSDF | Direct | Metallic Material/Universal fallback |
| Principled BSDF | Direct | Standard Surface by default with separate base/specular/transmission/coat/sheen/SSS layers; optional Universal uses GGX, scaled specular, and coat/sheen tint × weight composition |
| Principled Hair BSDF | Approximate | Hair Material/Universal fallback |
| Principled Volume | Approximate | Volume/Standard Medium candidates |
| Ray Portal BSDF | Version-dependent | Portal Material when available; Null fallback |
| Refraction BSDF | Direct | Specular Material; color routed to transmission |
| Specular BSDF | Direct | Specular Material candidates |
| Subsurface Scattering | Approximate | Universal Material medium/SSS inputs |
| Toon BSDF | Direct | Toon Material |
| Translucent BSDF | Direct | Diffuse Material transmission channel |
| Transparent BSDF | Direct | Null Material |
| Sheen BSDF | Approximate | Universal Material sheen-compatible inputs |
| Volume Absorption | Direct | Absorption Medium, topology-routed to the corresponding material |
| Volume Scatter | Direct | Scattering Medium, topology-routed to the corresponding material |
| Volume Coefficients | Unsupported | No validated socket-level mapping yet |

## Texture nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Brick Texture | Approximate | Marble Texture fallback |
| Checker Texture | Approximate | Checks Texture; factor/color output behavior may differ |
| Environment Texture | Direct | Octane Image Texture; world routing remains out of scope |
| Gabor Texture | Approximate | Noise Texture |
| Gradient Texture | Approximate | Gradient Texture; mode differences are reported |
| IES Texture | Unsupported | Requires validated Octane IES-light context |
| Image Texture | Direct | Contextual RGB/Greyscale/Alpha nodes; mixed Color+Alpha creates a dedicated alpha variant |
| Magic Texture | Approximate | Marble Texture fallback |
| Musgrave Texture (legacy) | Approximate | Noise Texture |
| Noise Texture | Approximate | Noise algorithms and multi-outputs differ |
| Point Density Texture | Unsupported | Requires baking or a point-data pipeline |
| Sky Texture | Approximate | Daylight Environment candidates |
| Voronoi Texture | Approximate | Voronoi feature/distance modes differ |
| Wave Texture | Approximate | Wave modes differ |
| White Noise Texture | Approximate | Noise Texture |

## Color nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Brightness/Contrast | Direct | Color Correction |
| Gamma | Direct | Gamma Correction |
| Hue/Saturation/Value | Approximate | Color Correction parameter ranges differ by plugin version |
| Invert | Direct | Invert Texture |
| Light Falloff | Unsupported | Cycles light-energy falloff outputs are not equivalent to Octane surface Falloff Map |
| Mix Color | Direct | Official Octane Cycles Mix wrapper, with generic Mix fallback |
| RGB Curves | Approximate | Color Correction cannot preserve arbitrary curve control points |

## Vector nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Bump | Direct | Folded into material Bump plus Bump Height; chained Normal is preserved separately |
| Displacement | Direct | Texture or Vertex Displacement according to settings |
| Mapping | Direct | Mapping drives UV Transform while Texture Coordinate/UV Map drives Projection; rotation is converted from radians/XYZ to Octane degrees/XYZ |
| Normal | Approximate | Normal Texture; verify mode/space |
| Normal Map | Direct | RGB image routed directly to material Normal when no native node exists |
| Vector Curves | Unsupported | No validated arbitrary-curve equivalent |
| Vector Displacement | Direct | Vertex Displacement candidates |
| Vector Rotate | Approximate | 3D Transform |
| Vector Transform | Approximate | 3D Transform |

## Converter nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Blackbody | Direct | Black Body Emission |
| Clamp | Direct | Clamp Texture |
| Color Ramp | Approximate | Full stops when the Octane API exposes elements; endpoint fallback otherwise |
| Combine Color / RGB | Version-dependent | Channel Merger/Combine wrapper; Add fallback is reported |
| Combine XYZ | Approximate | Native wrapper when available; Add fallback is reported |
| Float Curve | Unsupported | No validated arbitrary-curve equivalent |
| Map Range | Direct | Range Texture |
| Math | Direct | Official Octane Cycles Math wrapper; native math fallback |
| Mix | Direct | Official Float/Float3/Color Cycles wrappers |
| RGB to BW | Approximate | Desaturated Color Correction |
| Separate Color / RGB | Version-dependent | Native wrapper or one Channel Picker per used R/G/B output |
| Separate XYZ | Approximate | Native wrapper when available; fallback is reported |
| Shader to RGB | Approximate | No physically equivalent Octane operation |
| Vector Math | Direct | Official Octane Cycles Vector Math wrapper |
| Wavelength | Approximate | Static RGB fallback |

## Script and group nodes

| Cycles node | Status | Octane strategy / limitation |
|---|---|---|
| Script (OSL) | Unsupported | Cycles OSL source and Octane OSL execution are not assumed portable |
| Node Group | Direct | Recursive copy with interface preservation, caching, driver transfer, and recursion guard |
| Group Input / Output | Direct | Reused from copied group interface |
| Reroute | Direct | Flattened at link analysis while preserving branches |
| Frame | Layout-only | Not copied; node positions remain |

## Required manual review

- Procedural texture algorithms are renderer-specific even when names match.
- Cycles closure addition, Shader to RGB, Light Path, and multi-output geometry
  data do not have general physically equivalent Octane translations.
- Texture Displacement accepts image textures; procedural displacement may
  require baking or Vertex Displacement.
- Non-default UV layers are labeled and reported because some Octane versions
  expose UV indices rather than Blender layer names.
