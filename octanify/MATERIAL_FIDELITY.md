# Conversion fidelity and validation

This document records the conversion decisions that are easy to misread from a
node graph alone: target-material semantics, destination-aware texture handling,
procedural coordinates, displacement, volumes, and renderer-branch ownership.
For the node-by-node support classification, see `NODE_SUPPORT.md`.

## Finding

The glossy-plastic regression was caused by property transfer, not Octane node
initialization. Two Universal Material nodes created through the same
`nodes.new("OctaneUniversalMaterial")` call had identical sockets, writable RNA
properties, custom properties, and internal flags. Differences appeared only
after Octanify transferred Principled defaults.

The pre-fix A/B probe found these destructive changes on a completely default
Cycles Principled BSDF:

| Universal input | Fresh Octane | Pre-fix converted | Cause |
|---|---:|---:|---|
| Roughness | 0.0632 | 0.0 | Cycles Diffuse Roughness overwrote the already copied main Roughness |
| Specular | 1.0 | 0.5 | Cycles Specular IOR Level was copied without the required factor of two |
| Coating | black | white | Default Coat Tint overwrote zero Coat Weight on the same Octane colour socket |
| Sheen | black | white | Default Sheen Tint overwrote zero Sheen Weight on the same Octane colour socket |
| Coating roughness | 0.0632 | 0.03 | Inactive coat defaults were written unnecessarily |
| Sheen roughness | 0.2 | 0.5 | Inactive sheen defaults were written unnecessarily |
| Film IOR | 1.45 | 1.33 | Inactive thin-film defaults were written unnecessarily |

White Coating activates a full dielectric clear-coat layer. Combined with zero
main roughness, it overwhelms the intended differences between plastic, rubber,
foam, and fabric. Replacing only the Universal node fixed the render because the
fresh node restored black Coating/Sheen and Octane's valid specular defaults.

## Corrected mapping

- Main Roughness maps only from Cycles Roughness. Diffuse Roughness is never
  written into the Universal specular roughness channel.
- Universal Specular is `2 × Specular IOR Level`, matching the scale used by
  OTOY's installed Cycles converter. Linked values receive an explicit Octane
  Multiply Texture node.
- Universal Coating is `Coat Tint × Coat Weight`.
- Universal Sheen is `Sheen Tint × Sheen Weight`.
- Linked coat and sheen controls are composed with Multiply Texture nodes rather
  than competing for one destination socket.
- Coating roughness/IOR, sheen roughness, and thin-film IOR are written only when
  their corresponding layer is active.
- Converted Principled materials explicitly use `GGX`, matching the Cycles
  microfacet model and the installed OTOY converter's GGX path. The legacy
  `Octane` lobe and the brighter `GGX (energy preserving)` variant are not used.
- Transmission no longer blacks albedo or enables fake shadows heuristically.
  Non-zero unlinked transmission is materialized as a texture for Octane's
  link-only Transmission input.
- Unsafe direct mappings for Specular Tint, Diffuse Roughness, Tangent, and
  subsurface-only controls are reported instead of corrupting main specular,
  anisotropy, or IOR controls.

## Fidelity-first target selection

Standard Surface is now the recommended default for new Principled conversions.
It is structurally closer to Cycles Principled and therefore preserves independent
Base Weight, Diffuse Roughness, Metalness, Specular Weight/Color/Roughness/IOR,
Transmission Weight/Color, Coating, Sheen, subsurface, and thin-film controls.

The mapper is selected from the material node that Octane actually creates, not
only from the UI preference. This makes plugin-version fallback safe: if Standard
Surface is unavailable and creation falls back to Universal, the Universal mapper
is used automatically. Existing files that saved `Universal` remain on that
option; the default change applies to new settings.

Important semantic conversions are:

- Cycles Base Weight maps to Standard Surface Base weight. Older Principled
  versions without that socket use `1.0`, correcting Octane's `0.8` fresh-node
  default.
- Specular Weight is `2 × Specular IOR Level`, so the Cycles `0.5` dielectric
  default maps to Octane `1.0`.
- Coat and sheen weights and colors remain separate. Zero weight cannot be
  accidentally enabled by a white tint.
- Cycles Base Color is also used as the active transmission and subsurface tint,
  including one-to-many routing when Base Color is textured.
- Diffuse Roughness, anisotropy, SSS, and nanometer thin-film values use their
  dedicated Standard Surface sockets instead of unsafe Universal aliases.

Glossy Material is also an explicit selectable target. It maps the compatible
Principled diffuse, roughness, IOR, specular, anisotropy, rotation, sheen,
opacity, normal, displacement, and thin-film controls. Specular IOR Level uses
the same factor-of-two convention as the other Principled targets, and
nanometer thin-film thickness is converted to the micrometers expected by
Octane Glossy 31.9. Non-zero Diffuse Roughness selects Oren-Nayar because
Glossy has no continuous equivalent; Metallic, Transmission, Coat, Subsurface,
Emission, and other unsupported active lobes are reported rather than routed
to unrelated inputs. If a requested Glossy node class is unavailable, Octanify
does not silently create a different base material.

The optional **Auto-upgrade SSS materials to Standard Surface** setting is
disabled by default. When enabled, backward shading intent promotes a selected
Universal or Glossy target to Standard Surface only for materials whose
Principled subsurface path is active. The effective target is included in the
conversion report, and materials without active SSS retain the user's selected
target.

## Destination-aware texture fidelity

Texture gamma is selected from the shader destination rather than only from the
image's Blender colorspace declaration. A backward intent pass records roles per
`(node, output socket)` path, including paths that cross reroutes and nested node
groups. Base Color and Emission are treated as color; Roughness, Metallic,
Normal, Bump, and Alpha are treated as linear data.

Declaration/destination mismatches are surfaced in the conversion report while
the destination role remains authoritative. If one Image Texture participates
in both color and data paths, Octanify creates role-specific Octane texture
instances so the graph does not silently force one gamma treatment onto both
uses. Emission-color intent also allows a non-black or textured Emission input
to build the material emission graph when Cycles Emission Strength is zero.

## Procedural and mix fidelity

Color Mix and MixRGB nodes use Octane Composite Texture plus two native layer
nodes when those classes are available. Branch inputs, constants, factor/mask,
blend mode, and clamp state are assigned to the appropriate layer. Creation is
transactional: if any required layer or socket is missing, the partial
Composite graph is removed and the compatible legacy Mix path is used.

Noise, legacy Musgrave, and Voronoi prefer Octane Cinema 4D Noise. For Generated
coordinates, Octanify accounts for Cycles' local-bounding-box normalization;
Object coordinates use the logical inverse scale without bounding-box
normalization. The transform also applies the empirically validated `0.5`
Cinema 4D frequency correction for Blender 5.1 + Octane 31.9. Mapping
translation and scale are carried into the generated transform. Zero-length
bounds, zero procedural scale, and rotations on non-uniform bounds are reported
because they require manual verification.

Cinema 4D Noise is an intentional preference, not an unconditional dependency.
The installed Octane 31.9 node exposes the required noise families and a stable
UVW Transform input, which makes coordinate correction possible in one native
node. Its algorithms still differ from Cycles, so these mappings remain marked
as approximations. If the C4D node class is unavailable, Octanify continues
through the ordered generic-noise candidates instead of failing older materials.

## Displacement fidelity

The Displacement Mode preference is read during node creation. `Texture`
selects Octane Texture Displacement and applies the chosen Level of Detail;
`Vertex` selects Octane Vertex Displacement. Cycles Scale and Midlevel are
transferred to the created node, while a non-default panel Mid Level acts as an
explicit conversion-wide override. Blender Vector Displacement keeps its native
vertex-displacement path regardless of this scalar-displacement preference.

This setting was verified in Blender 5.1 + Octane 31.9 by asserting the exact
created node type in both modes. It does not remove Octane's normal requirement
for sufficient mesh subdivision when Vertex Displacement is used.

## Volume fidelity

Cycles homogeneous density is converted to Octane's 100-based medium scale.
Constant density values are multiplied during transfer, while linked density
chains receive an explicit Octane Multiply Texture stage so animation and
procedural control remain live.

A direct Add Shader combining Volume Absorption and Volume Scatter is rebuilt
as one native Octane Scattering medium with both absorption and scattering
colors. Octane exposes one shared density for that medium, so differing source
densities are reported. Volume-only materials receive a generated Null Material
with the converted medium attached instead of leaving a medium connected to a
surface socket. Principled Volume remains a best-effort mapping and is reported
as approximate.

## Renderer-branch preservation

Smart conversion keeps the authored Cycles graph and adds a renderer-targeted
Octane branch. The same preservation rule now applies to converted Light and
World node trees: source outputs are reserved for `CYCLES`, generated light
outputs use `ALL`, and Worlds use a dedicated Octane environment output.
Generated scene-domain graphs are built and validated before previous generated
nodes are retired, so missing Octane node classes do not destroy the source
setup.

## Validation

- The post-fix A/B probe differs from a fresh node only for intended Cycles
  values and the intentional `GGX` BSDF selection.
- 150 Python regression tests cover Standard Surface, Universal, and Glossy
  semantics; the smart SSS override; shading intent; nested groups; Geometry
  Nodes discovery; Composite layers; procedural scale matching; volume
  topology and density; safe Cycles-graph cleanup; lights; Worlds; gobos;
  layout; and live progress state.
- Blender 5.1 with Octane 31.9 converts ten fixture materials with zero failed
  links, including modern Standard Surface/Mix nodes, active multiple outputs,
  and split Projection/UV Transform.
- Blender 5.1 with Octane 31.9 runtime checks also verify Composite Texture
  layers, Cinema 4D procedural scale at 1 m, 2 m, and 5 m object sizes, exact
  Texture/Vertex displacement selection, Glossy mapping, SSS target selection,
  linked and constant medium density, volume-only Null Material routing, framed
  arrangement, disabled custom coloring, preserved Cycles Light/World branches,
  and Octane active-output resolution.
- Parameter assertions pass for hard plastic, rubber, fabric, and metal. Their
  roughness, specular, metallic, coating, and sheen responses remain distinct.

The machine-readable A/B probe is generated by
`tools/blender_probe_universal_material.py`; general integration results are
generated by `tools/blender_validate_conversion.py`. Phase-specific live checks
are in `tools/blender_validate_phase4.py`, `tools/blender_validate_phase5.py`,
and `tools/blender_validate_glossy_material.py`.

## Remaining visual validation

The automated tests validate physical parameters and graph semantics, not
pixel-level equivalence between two render engines. Final perceptual validation
on proprietary production assets requires those `.blend` files, their packed
textures, matched lighting/cameras, and approved reference renders.
