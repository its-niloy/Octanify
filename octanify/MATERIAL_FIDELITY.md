# Principled material fidelity investigation

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

## Validation

- The post-fix A/B probe differs from a fresh node only for intended Cycles
  values and the intentional `GGX` BSDF selection.
- 49 Python regression tests cover Standard Surface and Universal semantics,
  safe Cycles-graph cleanup, and live progress state.
- Blender 5.1 with Octane 31.9 converts ten fixture materials with zero failed
  links, including modern Standard Surface/Mix nodes, active multiple outputs,
  and split Projection/UV Transform.
- Parameter assertions pass for hard plastic, rubber, fabric, and metal. Their
  roughness, specular, metallic, coating, and sheen responses remain distinct.

The machine-readable A/B probe is generated by
`tools/blender_probe_universal_material.py`; the integration result is generated
by `tools/blender_validate_conversion.py`.

## Remaining visual validation

The automated tests validate physical parameters and graph semantics, not
pixel-level equivalence between two render engines. Final perceptual validation
on proprietary production assets requires those `.blend` files, their packed
textures, matched lighting/cameras, and approved reference renders.
