"""Octanify — Node registry.

Central mapping tables from Cycles bl_idname to Octane candidates,
plus socket-level input/output maps for link reconstruction.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Node type map: Cycles bl_idname → list of Octane bl_idname candidates
# The first candidate that succeeds at runtime is used.
# ---------------------------------------------------------------------------

NODE_TYPE_MAP: dict[str, list[str]] = {
    # ── Shaders ──────────────────────────────────────────────────────────
    "ShaderNodeBsdfPrincipled": [
        "ShaderNodeOctUniversalMat",
        "OctaneUniversalMaterial",
    ],
    "ShaderNodeBsdfGlass": [
        "ShaderNodeOctSpecularMat",
        "OctaneSpecularMaterial",
    ],
    "ShaderNodeBsdfGlossy": [
        "ShaderNodeOctGlossyMat",
        "OctaneGlossyMaterial",
    ],
    "ShaderNodeBsdfDiffuse": [
        "ShaderNodeOctDiffuseMat",
        "OctaneDiffuseMaterial",
    ],
    "ShaderNodeEmission": [
        "ShaderNodeOctDiffuseMat",
        "OctaneDiffuseMaterial",
    ],
    "ShaderNodeBsdfTransparent": [
        "ShaderNodeOctNullMat",
        "OctaneNullMaterial",
    ],
    "ShaderNodeBsdfTranslucent": [
        "ShaderNodeOctDiffuseMat",
        "OctaneDiffuseMaterial",
    ],
    "ShaderNodeBsdfRefraction": [
        "ShaderNodeOctSpecularMat",
        "OctaneSpecularMaterial",
    ],
    "ShaderNodeMixShader": [
        "ShaderNodeOctMixMat",
        "OctaneMixMaterial",
    ],
    "ShaderNodeAddShader": [
        "ShaderNodeOctMixMat",
        "OctaneMixMaterial",
    ],
    "ShaderNodeBsdfMetallic": [
        "ShaderNodeOctMetallicMat",
        "OctaneMetallicMaterial",
        "ShaderNodeOctUniversalMat",
    ],
    "ShaderNodeBsdfSheen": [
        "ShaderNodeOctUniversalMat",
        "OctaneUniversalMaterial",
    ],
    "ShaderNodeBsdfToon": [
        "ShaderNodeOctToonMat",
        "OctaneToonMaterial",
    ],
    "ShaderNodeBsdfHair": [
        "ShaderNodeOctHairMat",
        "OctaneHairMaterial",
        "ShaderNodeOctUniversalMat",
    ],
    "ShaderNodeBsdfHairPrincipled": [
        "ShaderNodeOctHairMat",
        "OctaneHairMaterial",
        "ShaderNodeOctUniversalMat",
    ],
    "ShaderNodeBsdfRayPortal": [
        "ShaderNodeOctPortalMat",
        "OctanePortalMaterial",
        "ShaderNodeOctNullMat",
        "OctaneNullMaterial",
    ],
    "ShaderNodeSubsurfaceScattering": [
        "ShaderNodeOctUniversalMat",
        "OctaneUniversalMaterial",
    ],
    "ShaderNodeBackground": [
        "ShaderNodeOctDiffuseMat",
        "OctaneDiffuseMaterial",
    ],
    "ShaderNodeHoldout": [
        "ShaderNodeOctNullMat",
        "OctaneNullMaterial",
    ],
    "ShaderNodeEeveeSpecular": [
        "ShaderNodeOctSpecularMat",
        "OctaneSpecularMaterial",
    ],

    # ── Textures ─────────────────────────────────────────────────────────
    "ShaderNodeTexImage": [
        "ShaderNodeOctImageTex",
        "OctaneImageTexture",
        "OctaneRGBImage",
    ],
    "ShaderNodeTexNoise": [
        "ShaderNodeOctNoiseTex",
        "OctaneNoiseTexture",
    ],
    "ShaderNodeTexVoronoi": [
        "OctaneSmoothVoronoiContours",
        "OctaneCellNoise",
        "ShaderNodeOctVoronoiTex",
        "OctaneVoronoiTexture",
    ],
    "ShaderNodeTexWave": [
        "OctaneWavePattern",
        "ShaderNodeOctWaveTex",
        "OctaneWaveTexture",
    ],
    "ShaderNodeTexMusgrave": [
        "ShaderNodeOctNoiseTex",
        "OctaneNoiseTexture",
    ],
    "ShaderNodeTexChecker": [
        "ShaderNodeOctChecksTex",
        "OctaneChecksTexture",
    ],
    "ShaderNodeTexBrick": [
        "ShaderNodeOctMarbleTex",
        "OctaneMarbleTexture",
    ],
    "ShaderNodeTexGradient": [
        "OctaneGradientGenerator",
        "ShaderNodeOctGradientTex",
        "OctaneGradientTexture",
    ],
    "ShaderNodeTexEnvironment": [
        "ShaderNodeOctImageTex",
        "OctaneImageTexture",
        "OctaneRGBImage",
    ],
    "ShaderNodeTexMagic": [
        "ShaderNodeOctMarbleTex",
        "OctaneMarbleTexture",
    ],
    "ShaderNodeTexSky": [
        "ShaderNodeOctDaylightEnv",
        "OctaneDaylightEnvironment",
    ],
    "ShaderNodeTexWhiteNoise": [
        "ShaderNodeOctNoiseTex",
        "OctaneNoiseTexture",
    ],
    "ShaderNodeTexGabor": [
        "ShaderNodeOctNoiseTex",
        "OctaneNoiseTexture",
    ],

    # ── Color / Math ─────────────────────────────────────────────────────
    "ShaderNodeValToRGB": [
        "OctaneGradientMap",
        "ShaderNodeOctGradientTex",
        "OctaneGradientTexture",
    ],
    "ShaderNodeMixRGB": [
        "OctaneCyclesMixColorNodeWrapper",
        "ShaderNodeOctMixTex",
        "OctaneMixTexture",
    ],
    "ShaderNodeMix": [
        "OctaneCyclesMixColorNodeWrapper",
        "ShaderNodeOctMixTex",
        "OctaneMixTexture",
    ],
    "ShaderNodeMixFloat": [
        "OctaneCyclesMixFloatNodeWrapper",
    ],
    "ShaderNodeMixFloat3": [
        "OctaneCyclesMixFloat3NodeWrapper",
    ],
    "ShaderNodeInvert": [
        "ShaderNodeOctInvertTex",
        "OctaneInvertTexture",
    ],
    "ShaderNodeHueSaturation": [
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeBrightContrast": [
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeGamma": [
        "OctaneColorCorrection",
        "ShaderNodeOctGammaCorrectionTex",
        "OctaneGammaCorrection",
    ],
    "ShaderNodeRGBCurves": [
        "OctaneColorCorrection",
        "ShaderNodeOctColorCorrectionTex",
    ],
    "ShaderNodeMath": [
        "OctaneCyclesNodeMathNodeWrapper",
        "OctaneBinaryMathOperation",
    ],
    "ShaderNodeMapRange": [
        "ShaderNodeOctRangeTex",
        "OctaneRange",
    ],
    "ShaderNodeClamp": [
        "OctaneClampTexture",
        "ShaderNodeOctClampTex",
        "OctaneClamp",
    ],
    "ShaderNodeRGBToBW": [
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeBlackbody": [
        "ShaderNodeOctBlackBodyEmission",
        "OctaneBlackBodyEmission",
    ],
    "ShaderNodeWavelength": [
        "ShaderNodeOctRGBColorTex",
        "OctaneRGBColor",
    ],
    "ShaderNodeShaderToRGB": [
        "ShaderNodeOctMixTex",
        "OctaneMixTexture",
    ],

    # ── Input / Vector ───────────────────────────────────────────────────
    "ShaderNodeMapping": [
        "Octane3DTransformation",
        "ShaderNodeOct3DTransform",
        "ShaderNodeOctFullTransform",
        "OctaneTransform3D",
    ],
    "ShaderNodeTexCoord": [
        "ShaderNodeOctMeshUVProjection",
        "OctaneMeshUVProjection",
    ],
    "ShaderNodeUVMap": [
        "ShaderNodeOctMeshUVProjection",
        "OctaneMeshUVProjection",
    ],
    "ShaderNodeNormalMap": [
        "ShaderNodeOctNormalMapTex",
        "OctaneNormalTexture",
        "OctaneNormalMap",
        "ShaderNodeOctNormalMap",
        "OctaneImageTexture",
    ],
    "ShaderNodeBump": [
        "ShaderNodeOctBumpTex",
        "OctaneBumpTexture",
        "OctaneBumpMap",
        "ShaderNodeOctBumpMap",
    ],
    "ShaderNodeDisplacement": [
        "OctaneTextureDisplacement",
        "ShaderNodeOctDisplacementTex",
    ],
    "ShaderNodeRGB": [
        "ShaderNodeOctRGBColorTex",
        "OctaneRGBColor",
    ],
    "ShaderNodeValue": [
        "ShaderNodeOctFloatTex",
        "OctaneFloatValue",
    ],
    "ShaderNodeFresnel": [
        "OctaneFalloffMap",
        "ShaderNodeOctFresnelTex",
        "OctaneFresnel",
    ],
    "ShaderNodeLayerWeight": [
        "OctaneFalloffMap",
        "ShaderNodeOctFresnelTex",
        "OctaneFresnel",
    ],
    "ShaderNodeVertexColor": [
        "ShaderNodeOctVertexColorTex",
        "OctaneColorVertexAttribute",
    ],
    "ShaderNodeAttribute": [
        "OctaneColorVertexAttribute",
        "ShaderNodeOctAttributeTex",
        "OctaneAttribute",
    ],
    "ShaderNodeAmbientOcclusion": [
        "ShaderNodeOctDirtTex",
        "OctaneDirtTexture",
    ],
    "ShaderNodeVectorMath": [
        "OctaneCyclesNodeVectorMathNodeWrapper",
    ],
    "ShaderNodeVectorRotate": [
        "Octane3DTransformation",
        "ShaderNodeOct3DTransform",
        "OctaneTransform3D",
    ],
    "ShaderNodeVectorTransform": [
        "Octane3DTransformation",
        "ShaderNodeOct3DTransform",
        "OctaneTransform3D",
    ],
    "ShaderNodeVectorDisplacement": [
        "OctaneVertexDisplacement",
        "ShaderNodeOctVertexDisplacementTex",
    ],
    "ShaderNodeNormal": [
        "OctaneNormal",
        "ShaderNodeOctNormalMapTex",
        "OctaneNormalTexture",
    ],
    "ShaderNodeTangent": [
        "OctaneSurfaceTangentDPdu",
        "ShaderNodeOctNormalMapTex",
        "OctaneNormalTexture",
    ],
    "ShaderNodeObjectInfo": [
        "ShaderNodeOctInstanceColorTex",
        "OctaneInstanceColor",
    ],
    "ShaderNodeCameraData": [
        "OctaneCameraData",
        "ShaderNodeOctFloatTex",
    ],
    "ShaderNodeParticleInfo": [
        "ShaderNodeOctFloatTex",
    ],
    "ShaderNodeHairInfo": [
        "ShaderNodeOctHairDataTex",
        "OctaneHairData",
    ],
    "ShaderNodeLightFalloff": [
        "ShaderNodeOctFalloffTex",
        "OctaneFalloffTexture",
    ],
    "ShaderNodeWireframe": [
        "ShaderNodeOctWireframeTex",
        "OctaneWireframe",
    ],
    "ShaderNodeBevel": [
        "OctaneRoundEdges",
        "ShaderNodeOctBevelTex",
        "OctaneBevelTexture",
    ],

    # ── Volume ───────────────────────────────────────────────────────────
    "ShaderNodeVolumeAbsorption": [
        "OctaneAbsorption",
        "ShaderNodeOctAbsorptionMedium",
        "OctaneAbsorptionMedium",
    ],
    "ShaderNodeVolumeScatter": [
        "OctaneScattering",
        "ShaderNodeOctScatterMedium",
        "OctaneScatteringMedium",
    ],
    "ShaderNodeVolumePrincipled": [
        "ShaderNodeOctVolumeMedium",
        "OctaneVolumeMedium",
        "ShaderNodeOctAbsorptionMedium",
    ],
    "ShaderNodeVolumeInfo": [
        "ShaderNodeOctFloatTex",
    ],

    # ── Channel Split / Combine ──────────────────────────────────────────
    # BUG 1 FIX: These were previously transparent/passthrough, which broke
    # packed texture channels. Now mapped to Octane equivalents or ColorCorrection fallback.
    "ShaderNodeSeparateColor": [
        "OctaneSeparateColor",
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeSeparateRGB": [
        "OctaneSeparateColor",
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeSeparateXYZ": [
        "OctaneSeparateXYZ",
        "ShaderNodeOctColorCorrectionTex",
        "OctaneColorCorrection",
    ],
    "ShaderNodeCombineColor": [
        "ShaderNodeOctChannelMergerTex",
        "OctaneChannelMerger",
        "OctaneCombineColor",
        "ShaderNodeOctAddTex",
        "OctaneAddTexture",
    ],
    "ShaderNodeCombineRGB": [
        "ShaderNodeOctChannelMergerTex",
        "OctaneChannelMerger",
        "OctaneCombineColor",
        "ShaderNodeOctAddTex",
        "OctaneAddTexture",
    ],
    "ShaderNodeCombineXYZ": [
        "OctaneCombineXYZ",
        "ShaderNodeOctAddTex",
        "OctaneAddTexture",
    ],
}

# Nodes that are passed through (logic handled inline, no 1:1 node creation)
# BUG 1 FIX: Separate*/Combine* removed — they now get proper Octane nodes
PASSTHROUGH_TYPES: set[str] = {
    "ShaderNodeOutputMaterial",
    "ShaderNodeGroup",
    "NodeReroute",
    "NodeFrame",
    "NodeGroupInput",
    "NodeGroupOutput",
}

# Mappings that intentionally preserve only an approximation of Cycles
# semantics.  A node can be creatable and still be lossy; surfacing that
# distinction prevents the UI and support report from claiming false parity.
APPROXIMATION_NOTES: dict[str, str] = {
    "ShaderNodeAddShader": "Add Shader is approximated by a 50/50 Mix Material",
    "ShaderNodeBsdfSheen": "Sheen BSDF is approximated with Universal Material",
    "ShaderNodeBsdfHair": "Hair BSDF parameters vary across Octane versions",
    "ShaderNodeBsdfHairPrincipled": "Principled Hair uses a best-effort Hair/Universal mapping",
    "ShaderNodeSubsurfaceScattering": "Standalone SSS is approximated with Universal Material inputs",
    "ShaderNodeBackground": "Background is material-scoped; world environment conversion is not automatic",
    "ShaderNodeHoldout": "Holdout is approximated with Null Material",
    "ShaderNodeEeveeSpecular": "Specular BSDF is approximated with Octane Specular Material",
    "ShaderNodeTexMusgrave": "Musgrave is approximated with Octane Noise",
    "ShaderNodeTexNoise": "Cycles and Octane Noise algorithms and multi-outputs differ",
    "ShaderNodeTexVoronoi": "Voronoi feature/distance modes require manual verification",
    "ShaderNodeTexWave": "Cycles and Octane Wave modes are not one-to-one",
    "ShaderNodeTexChecker": "Checker factor and color outputs may share one Octane texture output",
    "ShaderNodeTexGradient": "Gradient modes differ between Cycles and Octane",
    "ShaderNodeTexBrick": "Brick is approximated with Octane Marble",
    "ShaderNodeTexMagic": "Magic is approximated with Octane Marble",
    "ShaderNodeTexWhiteNoise": "White Noise is approximated with Octane Noise",
    "ShaderNodeTexGabor": "Gabor is approximated with Octane Noise",
    "ShaderNodeRGBCurves": "RGB Curves uses Color Correction and cannot preserve arbitrary curves",
    "ShaderNodeValToRGB": "Gradient transfer depends on Octane version and may preserve only endpoints",
    "ShaderNodeWavelength": "Wavelength is approximated by a static RGB value",
    "ShaderNodeShaderToRGB": "Shader to RGB has no physically equivalent Octane operation",
    "ShaderNodeVectorRotate": "Vector Rotate is approximated with a 3D Transform",
    "ShaderNodeVectorTransform": "Vector Transform is approximated with a 3D Transform",
    "ShaderNodeNormal": "Cycles Normal output modes require manual verification",
    "ShaderNodeTangent": "Cycles Tangent output modes require manual verification",
    "ShaderNodeTexCoord": "Only compatible projection outputs are preserved; coordinate modes differ",
    "ShaderNodeLayerWeight": "Facing and Fresnel outputs are approximated with one Fresnel texture",
    "ShaderNodeVertexColor": "Vertex color alpha may require a separate attribute setup",
    "ShaderNodeAttribute": "Attribute color/vector/factor outputs share an Octane attribute source",
    "ShaderNodeAmbientOcclusion": "AO color and factor outputs use Octane Dirt approximation",
    "ShaderNodeObjectInfo": "Only Octane instance-color-compatible Object Info uses are preserved",
    "ShaderNodeParticleInfo": "Particle Info outputs are not one-to-one with a Float value",
    "ShaderNodeVolumeInfo": "Volume Info outputs are not one-to-one with a Float value",
    "ShaderNodeVolumePrincipled": "Principled Volume uses a best-effort Octane medium mapping",
    "ShaderNodeSeparateColor": "Channel splitting depends on the installed Octane channel utility node",
    "ShaderNodeSeparateRGB": "Channel splitting depends on the installed Octane channel utility node",
    "ShaderNodeSeparateXYZ": "Vector channel splitting is approximate when no native wrapper is available",
    "ShaderNodeCombineColor": "Channel combining is approximate when no native merger is available",
    "ShaderNodeCombineRGB": "Channel combining is approximate when no native merger is available",
    "ShaderNodeCombineXYZ": "Vector combining is approximate when no native merger is available",
}

# Nodes to completely skip (decorative / layout only)
SKIP_TYPES: set[str] = {
    "NodeFrame",
}

# ---------------------------------------------------------------------------
# MixRGB blend_type → specialised Octane node
# When a MixRGB/Mix node has one of these blend types, we replace the generic
# OctaneMixTexture with a dedicated math node for accuracy.
# ---------------------------------------------------------------------------

BLEND_TYPE_MAP: dict[str, list[str]] = {
    "MULTIPLY": ["ShaderNodeOctMultiplyTex", "OctaneMultiplyTexture"],
    "ADD": ["ShaderNodeOctAddTex", "OctaneAddTexture"],
    "SUBTRACT": ["ShaderNodeOctSubtractTex", "OctaneSubtractTexture"],
    "SCREEN": ["ShaderNodeOctMixTex", "OctaneMixTexture"],
    "OVERLAY": ["ShaderNodeOctMixTex", "OctaneMixTexture"],
    "DIFFERENCE": ["ShaderNodeOctSubtractTex", "OctaneSubtractTexture"],
}

# ---------------------------------------------------------------------------
# Vector Math node operation → Octane specialized texture node (when mapped to VectorMath)
# If Octane doesn't have a direct vector math node, we map ADD to AddTex, MUL to MultiplyTex, etc.
# ---------------------------------------------------------------------------

VECTOR_MATH_OPERATION_MAP: dict[str, list[str]] = {
    "ADD": ["ShaderNodeOctAddTex", "OctaneAddTexture"],
    "SUBTRACT": ["ShaderNodeOctSubtractTex", "OctaneSubtractTexture"],
    "MULTIPLY": ["ShaderNodeOctMultiplyTex", "OctaneMultiplyTexture"],
    "DIVIDE": ["ShaderNodeOctMixTex", "OctaneMixTexture"],
    "CROSS_PRODUCT": ["ShaderNodeOctMixTex"],
    "PROJECT": ["ShaderNodeOctMixTex"],
    "REFLECT": ["ShaderNodeOctMixTex"],
    "DOT_PRODUCT": ["ShaderNodeOctFloatMathTex", "OctaneFloatMath"],
    "DISTANCE": ["ShaderNodeOctFloatMathTex", "OctaneFloatMath"],
    "LENGTH": ["ShaderNodeOctFloatMathTex", "OctaneFloatMath"],
    "SCALE": ["ShaderNodeOctMultiplyTex", "OctaneMultiplyTexture"],
    "NORMALIZE": ["ShaderNodeOctMixTex"],
    "ABSOLUTE": ["ShaderNodeOctMixTex"],
    "MINIMUM": ["ShaderNodeOctMixTex"],
    "MAXIMUM": ["ShaderNodeOctMixTex"],
    "FLOOR": ["ShaderNodeOctMixTex"],
    "CEIL": ["ShaderNodeOctMixTex"],
    "FRACTION": ["ShaderNodeOctMixTex"],
    "MODULO": ["ShaderNodeOctMixTex"],
    "WRAP": ["ShaderNodeOctMixTex"],
    "SNAP": ["ShaderNodeOctMixTex"],
    "SINE": ["ShaderNodeOctMixTex"],
    "COSINE": ["ShaderNodeOctMixTex"],
    "TANGENT": ["ShaderNodeOctMixTex"],
}

# ---------------------------------------------------------------------------
# Math node operation → Octane float math operation enum value
# ---------------------------------------------------------------------------

MATH_OPERATION_MAP: dict[str, str] = {
    "ADD": "ADD",
    "SUBTRACT": "SUBTRACT",
    "MULTIPLY": "MULTIPLY",
    "DIVIDE": "DIVIDE",
    "POWER": "POWER",
    "LOGARITHM": "LOGARITHM",
    "SQRT": "SQRT",
    "ABSOLUTE": "ABSOLUTE",
    "MINIMUM": "MINIMUM",
    "MAXIMUM": "MAXIMUM",
    "LESS_THAN": "LESS_THAN",
    "GREATER_THAN": "GREATER_THAN",
    "MODULO": "MODULO",
    "SINE": "SIN",
    "COSINE": "COS",
    "TANGENT": "TAN",
    "ARCSINE": "ASIN",
    "ARCCOSINE": "ACOS",
    "ARCTANGENT": "ATAN",
    "ROUND": "ROUND",
    "FLOOR": "FLOOR",
    "CEIL": "CEIL",
    "FRACT": "FRACT",
}


# ---------------------------------------------------------------------------
# Socket mapping tables
#
# INPUT_MAP:  { Cycles bl_idname: { cycles_socket_name: [octane_candidates] } }
# OUTPUT_MAP: { Cycles bl_idname: { cycles_socket_name: [octane_candidates] } }
#
# During link reconstruction we try each candidate in order until one exists
# on the actual Octane node.
# ---------------------------------------------------------------------------

INPUT_MAP: dict[str, dict[str, list[str]]] = {
    "ShaderNodeBsdfPrincipled": {
        "Base Color":           ["Albedo color", "Albedo", "Diffuse", "Base color"],
        "Metallic":             ["Metallic", "Metallic float", "Metalness"],
        "Roughness":            ["Roughness", "Roughness float", "Specular roughness"],
        "Specular IOR Level":   ["Specular", "Specular float"],
        "IOR":                  ["Dielectric IOR", "Index", "IOR", "Specular IOR"],
        "Transmission Weight":  ["Transmission", "Transmission float"],
        "Alpha":                ["Opacity", "Opacity float"],
        "Normal":               ["Normal", "Bump", "ShaderNormal"],
        # Octane's Coating and Sheen sockets are layer colours rather than
        # scalar weights.  Weight × tint is assembled by a dedicated graph
        # pass; mapping either input directly enables the layer at full power.
        "Coat Weight":          ["Coating", "Coating float"],
        "Coat Roughness":       ["Coating roughness", "Coating roughness float"],
        "Coat Normal":          ["Coating normal", "Coating bump"],
        "Coat IOR":             ["Coating IOR"],
        "Coat Tint":            ["Coating", "Coating color"],
        "Sheen Weight":         ["Sheen", "Sheen float"],
        "Sheen Roughness":      ["Sheen roughness", "Sheen roughness float"],
        "Sheen Tint":           ["Sheen", "Sheen color", "Sheen tint"],
        "Emission Color":       ["Emission", "Emission color"],
        "Emission Strength":    ["Emission power", "Emission weight"],
        "Anisotropic":          ["Anisotropy", "Anisotropy float"],
        "Anisotropic Rotation": ["Anisotropy rotation", "Rotation"],
        "Thin Film Thickness":  ["Film width", "Thin film thickness"],
        "Thin Film IOR":        ["Film IOR", "Thin film IOR"],
    },
    "ShaderNodeBsdfGlass": {
        "Color":     ["Transmission color", "Transmission", "Reflection"],
        "Roughness": ["Roughness", "Roughness float"],
        "IOR":       ["Index", "IOR", "Dielectric IOR"],
        "Normal":    ["Normal", "Bump"],
    },
    "ShaderNodeBsdfGlossy": {
        "Color":     ["Specular", "Reflection", "Albedo color"],
        "Roughness": ["Roughness", "Roughness float"],
        "Normal":    ["Normal", "Bump"],
    },
    "ShaderNodeBsdfDiffuse": {
        "Color":     ["Diffuse", "Albedo color", "Albedo"],
        "Roughness": ["Roughness", "Roughness float"],
        "Normal":    ["Normal", "Bump"],
    },
    "ShaderNodeEmission": {
        "Color":    ["Diffuse", "Emission", "Albedo color"],
        "Strength": ["Emission power", "Power"],
    },
    "ShaderNodeBsdfTranslucent": {
        "Color":  ["Transmission", "Transmission color", "Diffuse"],
        "Normal": ["Normal", "Bump"],
    },
    "ShaderNodeBsdfRefraction": {
        "Color":     ["Transmission color", "Transmission", "Reflection"],
        "Roughness": ["Roughness", "Roughness float"],
        "IOR":       ["Index", "IOR", "Dielectric IOR"],
        "Normal":    ["Normal", "Bump"],
    },
    "ShaderNodeMixShader": {
        "Fac":    ["Amount", "Factor"],
        # Octane swaps shader order: Cycles slot 1 → Octane slot 2
        "Shader": ["Material1", "Shader1"],
        "Shader_001": ["Material2", "Shader2"],
    },
    "ShaderNodeAddShader": {
        "Shader":     ["Material1", "Shader1"],
        "Shader_001": ["Material2", "Shader2"],
    },
    "ShaderNodeTexImage": {
        "Vector": ["Transform", "Projection", "UV", "UVTransform"],
    },
    "ShaderNodeTexNoise": {
        "Vector": ["Transform", "UVTransform"],
        "Scale":  ["Omega", "W", "Scale"],
        "Detail": ["Octaves", "Detail"],
        "Roughness": ["Lacunarity", "Roughness"],
        "Distortion": ["Distortion"],
    },
    "ShaderNodeTexVoronoi": {
        "Vector": ["Transform", "UVTransform"],
        "Scale":  ["Scale"],
        "Randomness": ["Randomness"],
    },
    "ShaderNodeTexWave": {
        "Vector": ["Transform", "UVTransform"],
        "Scale":  ["Scale"],
    },
    "ShaderNodeTexMusgrave": {
        "Vector": ["Transform", "UVTransform"],
        "Scale":  ["Omega", "W", "Scale"],
        "Detail": ["Octaves", "Detail"],
    },
    "ShaderNodeTexChecker": {
        "Vector": ["Transform", "UVTransform"],
        "Color1": ["Color1", "Checks color 1"],
        "Color2": ["Color2", "Checks color 2"],
        "Scale":  ["Scale"],
    },
    "ShaderNodeTexGradient": {
        "Vector": ["Transform", "UVTransform"],
    },
    "ShaderNodeMapping": {
        "Vector": ["Input", "Coordinates"],
    },
    "ShaderNodeNormalMap": {
        "Color":    ["Texture", "Input", "Normal"],
        "Strength": ["Strength", "Bump strength"],
    },
    "ShaderNodeBump": {
        "Strength": ["Strength", "Height"],
        "Height":   ["Texture", "Input"],
        "Normal":   ["Normal", "Input normal"],
    },
    "ShaderNodeDisplacement": {
        "Height": ["Texture", "Input"],
        "Scale":  ["Amount", "Height"],
        "Normal": ["Normal"],
    },
    "ShaderNodeMixRGB": {
        "Fac":    ["Amount", "Factor"],
        "Color1": ["Texture1", "Color1", "Input1"],
        "Color2": ["Texture2", "Color2", "Input2"],
    },
    "ShaderNodeMix": {
        "Factor":  ["Amount", "Factor"],
        "A":       ["Texture1", "Color1", "Input1"],
        "B":       ["Texture2", "Color2", "Input2"],
    },
    "ShaderNodeMixFloat": {
        "Factor":  ["Amount", "Factor"],
        "A":       ["Texture1", "Color1", "Input1"],
        "B":       ["Texture2", "Color2", "Input2"],
    },
    "ShaderNodeMixFloat3": {
        "Factor":  ["Amount", "Factor"],
        "A":       ["Texture1", "Color1", "Input1"],
        "B":       ["Texture2", "Color2", "Input2"],
    },
    "ShaderNodeInvert": {
        "Fac":   ["Amount", "Factor"],
        "Color": ["Texture", "Input"],
    },
    "ShaderNodeHueSaturation": {
        "Hue":        ["Hue", "HueShift"],
        "Saturation": ["Saturation"],
        "Value":      ["Brightness", "Value"],
        "Fac":        ["Amount", "Factor"],
        "Color":      ["Texture", "Input"],
    },
    "ShaderNodeBrightContrast": {
        "Color":    ["Texture", "Input"],
        "Bright":   ["Brightness"],
        "Contrast": ["Contrast"],
    },
    "ShaderNodeGamma": {
        "Color": ["Texture", "Input"],
        "Gamma": ["Gamma", "Power"],
    },
    "ShaderNodeRGBCurves": {
        "Color": ["Texture", "Input"],
        "Fac":   ["Amount", "Factor"],
    },
    "ShaderNodeMath": {
        "Value":     ["Input1", "Value1", "Value 1", "Input", "Value", "A"],
        "Value_001": ["Input2", "Value2", "Value 2", "Value2", "B"],
    },
    "ShaderNodeMapRange": {
        "Value":    ["Input", "Value"],
        "From Min": ["Input min", "FromMin"],
        "From Max": ["Input max", "FromMax"],
        "To Min":   ["Output min", "ToMin"],
        "To Max":   ["Output max", "ToMax"],
    },
    "ShaderNodeClamp": {
        "Value": ["Input texture", "Input", "Value"],
        "Min":   ["Minimum", "Min"],
        "Max":   ["Maximum", "Max"],
    },
    "ShaderNodeFresnel": {
        "IOR":    ["IOR", "Index", "Falloff skew factor"],
        "Normal": ["Normal"],
    },
    "ShaderNodeLayerWeight": {
        "Blend":  ["IOR", "Index", "Power", "Falloff skew factor"],
        "Normal": ["Normal"],
    },
    "ShaderNodeAmbientOcclusion": {
        "Color":    ["Inclination color", "Bright color", "Color"],
        "Distance": ["Radius", "Distance"],
        "Normal":   ["Normal"],
    },
    "ShaderNodeBevel": {
        "Radius": ["Radius"],
        "Samples": ["Samples"],
    },
    "ShaderNodeVolumeAbsorption": {
        "Color":   ["Absorption", "Color"],
        "Density": ["Density", "Density float"],
    },
    "ShaderNodeVolumeScatter": {
        "Color":      ["Scattering", "Color"],
        "Density":    ["Density", "Density float"],
        "Anisotropy": ["Phase", "Anisotropy"],
    },
    "ShaderNodeValToRGB": {
        "Fac": ["Input texture", "Input", "Value", "Amount"],
    },
    "ShaderNodeOutputMaterial": {
        "Surface":      ["Surface", "Shader", "Material"],
        "Volume":       ["Volume", "Medium"],
        "Displacement": ["Displacement", "Height"],
    },

    # ── New Nodes ────────────────────────────────────────────────────────
    "ShaderNodeBsdfMetallic": {
        "Base Color": ["Albedo color", "Albedo", "Diffuse"],
        "Edge Tint":  ["Specular", "Specular color", "Specular map"],
        "Roughness":  ["Roughness", "Roughness float"],
        "Anisotropy": ["Anisotropy", "Anisotropy float"],
        "Rotation":   ["Anisotropy rotation", "Rotation"],
        "Normal":     ["Normal", "Bump", "ShaderNormal"],
        "Tangent":    ["Anisotropy rotation", "Rotation"],
    },
    "ShaderNodeBsdfSheen": {
        "Color":      ["Albedo color", "Albedo", "Diffuse"],
        "Roughness":  ["Roughness", "Roughness float"],
        "Normal":     ["Normal", "Bump", "ShaderNormal"],
    },
    "ShaderNodeBsdfToon": {
        "Color":      ["Albedo color", "Albedo", "Diffuse"],
        "Size":       ["Roughness", "Roughness float"],
        "Smooth":     ["Roughness", "Roughness float"],
        "Normal":     ["Normal", "Bump", "ShaderNormal"],
    },
    "ShaderNodeSubsurfaceScattering": {
        "Color":      ["Albedo color", "Albedo", "Diffuse", "Absorption"],
        "Scale":      ["Density", "Medium scale"],
        "Radius":     ["Absorption", "Medium radius"],
        "IOR":        ["Index", "IOR"],
        "Roughness":  ["Roughness", "Roughness float"],
        "Anisotropy": ["Anisotropy", "Anisotropy float"],
        "Normal":     ["Normal", "Bump", "ShaderNormal"],
    },
    "ShaderNodeBackground": {
        "Color":    ["Diffuse", "Emission", "Albedo color"],
        "Strength": ["Emission power", "Power"],
    },
    "ShaderNodeTexEnvironment": {
        "Vector": ["Transform", "Projection", "UV", "UVTransform"],
    },
    "ShaderNodeVectorMath": {
        "Vector":    ["Texture1", "Color1", "Input1", "A"],
        "Vector_001": ["Texture2", "Color2", "Input2", "B"],
        "Scale":     ["Amount", "Factor", "Value2", "B"],
    },
    "ShaderNodeBlackbody": {
        "Temperature": ["Temperature"],
    },
    "ShaderNodeVolumePrincipled": {
        "Color":            ["Absorption", "Color"],
        "Density":          ["Density", "Density float"],
        "Anisotropy":       ["Phase", "Anisotropy"],
        "Emission Color":   ["Emission", "Emission color"],
        "Emission Strength":["Emission power", "Power"],
    },

    # ── Channel Split / Combine (BUG 1 FIX) ─────────────────────────────
    "ShaderNodeSeparateColor": {
        "Color": ["Texture", "Input", "Color"],
    },
    "ShaderNodeSeparateRGB": {
        "Image": ["Texture", "Input", "Color"],
    },
    "ShaderNodeSeparateXYZ": {
        "Vector": ["Texture", "Input", "Color"],
    },
    "ShaderNodeCombineColor": {
        "Red":   ["First channel", "Texture1", "Input1", "Color1", "R"],
        "Green": ["Second channel", "Texture2", "Input2", "Color2", "G"],
        "Blue":  ["Third channel", "Texture3", "Input3", "B"],
    },
    "ShaderNodeCombineRGB": {
        "R": ["First channel", "Texture1", "Input1", "Color1", "R"],
        "G": ["Second channel", "Texture2", "Input2", "Color2", "G"],
        "B": ["Third channel", "Texture3", "Input3", "B"],
    },
    "ShaderNodeCombineXYZ": {
        "X": ["Texture1", "Input1", "Color1", "X"],
        "Y": ["Texture2", "Input2", "Color2", "Y"],
        "Z": ["Texture3", "Input3", "Z"],
    },
}

OUTPUT_MAP: dict[str, dict[str, list[str]]] = {
    "ShaderNodeBsdfPrincipled": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfGlass": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfGlossy": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfDiffuse": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeEmission": {
        "Emission": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfTransparent": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfTranslucent": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfRefraction": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeMixShader": {
        "Shader": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeAddShader": {
        "Shader": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeTexImage": {
        "Color": ["OutTex", "Texture out", "Output"],
        "Alpha": ["Alpha", "OutTex", "Output"],
    },
    "ShaderNodeTexNoise": {
        "Fac":   ["OutTex", "Texture out", "Output"],
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeTexVoronoi": {
        "Distance": ["OutTex", "Texture out", "Output"],
        "Color":    ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeTexWave": {
        "Fac":   ["OutTex", "Texture out", "Output"],
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeTexMusgrave": {
        "Fac": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeTexChecker": {
        "Color": ["OutTex", "Texture out", "Output"],
        "Fac":   ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeTexGradient": {
        "Color": ["OutTex", "Texture out", "Output"],
        "Fac":   ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeValToRGB": {
        "Color": ["OutTex", "Texture out", "Output"],
        "Alpha": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMixRGB": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMix": {
        "Result": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMixFloat": {
        "Result": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMixFloat3": {
        "Result": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeInvert": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeHueSaturation": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeBrightContrast": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeGamma": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeRGBCurves": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMath": {
        "Value": ["OutTex", "Texture out", "Output", "Value", "Result"],
    },
    "ShaderNodeMapRange": {
        "Result": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeClamp": {
        "Result": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeMapping": {
        "Vector": ["OutTransform", "Transform out",  "Output"],
    },
    "ShaderNodeTexCoord": {
        "UV":       ["OutProjection", "Projection out", "Output"],
        "Object":   ["OutProjection", "Projection out", "Output"],
        "Camera":   ["OutProjection", "Projection out", "Output"],
        "Window":   ["OutProjection", "Projection out", "Output"],
        "Normal":   ["OutProjection", "Projection out", "Output"],
        "Reflection": ["OutProjection", "Projection out", "Output"],
        "Generated": ["OutProjection", "Projection out", "Output"],
    },
    "ShaderNodeUVMap": {
        "UV": ["OutProjection", "Projection out", "Output"],
    },
    "ShaderNodeNormalMap": {
        "Normal": ["OutTex", "Texture out", "Normal", "Output"],
    },
    "ShaderNodeBump": {
        "Normal": ["OutTex", "Texture out", "Normal", "Output"],
    },
    "ShaderNodeDisplacement": {
        "Displacement": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeVectorDisplacement": {
        "Displacement": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeRGB": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeValue": {
        "Value": ["OutTex", "Texture out", "Output", "Value"],
    },
    "ShaderNodeFresnel": {
        "Fac": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeLayerWeight": {
        "Fresnel": ["OutTex", "Texture out", "Output"],
        "Facing":  ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeCameraData": {
        "View Vector": ["View Vector"],
        "View Z Depth": ["View Z Depth"],
        "View Distance": ["View Distance"],
    },
    "ShaderNodeVertexColor": {
        "Color": ["OutTex", "Texture out", "Output"],
        "Alpha": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeAttribute": {
        "Color":  ["OutTex", "Texture out", "Output"],
        "Fac":    ["OutTex", "Texture out", "Output"],
        "Vector": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeAmbientOcclusion": {
        "Color": ["OutTex", "Texture out", "Output"],
        "AO":    ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeBevel": {
        "Normal": ["Round edges out", "Output"],
    },
    "ShaderNodeVolumeAbsorption": {
        "Volume": ["OutMedium", "Medium out", "Output"],
    },
    "ShaderNodeVolumeScatter": {
        "Volume": ["OutMedium", "Medium out", "Output"],
    },
    
    # ── New Nodes ────────────────────────────────────────────────────────
    "ShaderNodeBsdfMetallic": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfSheen": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfToon": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfHair": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfHairPrincipled": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBsdfRayPortal": {
        "BSDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeSubsurfaceScattering": {
        "BSSRDF": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeBackground": {
        "Background": ["OutMat", "Material out", "Output", "Emission"],
    },
    "ShaderNodeHoldout": {
        "Holdout": ["OutMat", "Material out", "Output"],
    },
    "ShaderNodeTexEnvironment": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeVectorMath": {
        "Vector": ["OutTex", "Texture out", "Output", "Value"],
        "Value": ["OutTex", "Texture out", "Output", "Value"],
    },
    "ShaderNodeBlackbody": {
        "Color": ["Emission out", "OutEmission", "OutTex", "Texture out", "Output", "Emission"],
    },
    "ShaderNodeVolumePrincipled": {
        "Volume": ["OutMedium", "Medium out", "Output"],
    },

    # ── Channel Split / Combine (BUG 1 FIX) ─────────────────────────────
    "ShaderNodeSeparateColor": {
        "Red":   ["OutTex", "Texture out", "Output", "R"],
        "Green": ["OutTex", "Texture out", "Output", "G"],
        "Blue":  ["OutTex", "Texture out", "Output", "B"],
    },
    "ShaderNodeSeparateRGB": {
        "R": ["OutTex", "Texture out", "Output", "R"],
        "G": ["OutTex", "Texture out", "Output", "G"],
        "B": ["OutTex", "Texture out", "Output", "B"],
    },
    "ShaderNodeSeparateXYZ": {
        "X": ["OutTex", "Texture out", "Output", "X"],
        "Y": ["OutTex", "Texture out", "Output", "Y"],
        "Z": ["OutTex", "Texture out", "Output", "Z"],
    },
    "ShaderNodeCombineColor": {
        "Color": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeCombineRGB": {
        "Image": ["OutTex", "Texture out", "Output"],
    },
    "ShaderNodeCombineXYZ": {
        "Vector": ["OutTex", "Texture out", "Output"],
    },
}


# ---------------------------------------------------------------------------
# Helper: resolve a socket name through the mapping table
#
# Multi-strategy resolution:
#   1. INPUT_MAP/OUTPUT_MAP candidates (exact match)
#   2. INPUT_MAP lookup by socket identifier (for duplicate-name disambiguation)
#   3. Literal Cycles socket name (exact match)
#   4. Case-insensitive match against all sockets
#   5. Substring match against all sockets
#   6. Index-based fallback (for inputs with known index)
# ---------------------------------------------------------------------------

def _find_socket_case_insensitive(
    collection, name: str
) -> "bpy.types.NodeSocket | None":
    """Case-insensitive search in a socket collection."""
    target = name.lower()
    for sock in collection:
        if sock.name.lower() == target:
            return sock
    return None


def _find_socket_substring(
    collection, name: str
) -> "bpy.types.NodeSocket | None":
    """Substring search: return the first socket whose name contains or is
    contained in the target name (case-insensitive)."""
    target = name.lower()
    for sock in collection:
        sock_lower = sock.name.lower()
        if target in sock_lower or sock_lower in target:
            return sock
    return None


def resolve_input_socket(
    cycles_type: str,
    cycles_socket_name: str,
    octane_node,
    socket_identifier: str = "",
    socket_index: int = -1,
) -> "bpy.types.NodeSocket | None":
    """Find the matching Octane input socket for a Cycles input socket.

    Uses a multi-strategy approach to maximise connection success.
    """
    from ..utils.logger import get_logger
    log = get_logger()

    type_map = INPUT_MAP.get(cycles_type, {})

    # Strategy 1: prefer the unique identifier when it differs from the
    # display name.  Blender gives duplicate sockets (for example the two
    # Mix Shader inputs) the same display name but distinct identifiers.  If
    # display-name candidates are tried first, both links resolve to the same
    # Octane input and one branch is silently overwritten.
    if socket_identifier and socket_identifier != cycles_socket_name:
        id_candidates = type_map.get(socket_identifier, [])
        for cand in id_candidates:
            sock = octane_node.inputs.get(cand)
            if sock is not None:
                return sock

    # Strategy 2: exact name match via INPUT_MAP candidates
    candidates = type_map.get(cycles_socket_name, [])
    for cand in candidates:
        sock = octane_node.inputs.get(cand)
        if sock is not None:
            return sock

    # Strategy 3: literal Cycles socket name
    sock = octane_node.inputs.get(cycles_socket_name)
    if sock is not None:
        return sock

    # Strategy 4: case-insensitive match on candidates
    for cand in candidates:
        sock = _find_socket_case_insensitive(octane_node.inputs, cand)
        if sock is not None:
            return sock

    # Strategy 5: case-insensitive match on Cycles socket name
    sock = _find_socket_case_insensitive(octane_node.inputs, cycles_socket_name)
    if sock is not None:
        return sock

    # Strategy 6: substring match on Cycles socket name
    sock = _find_socket_substring(octane_node.inputs, cycles_socket_name)
    if sock is not None:
        return sock

    # Strategy 7: index-based fallback
    if 0 <= socket_index < len(octane_node.inputs):
        return octane_node.inputs[socket_index]

    log.warning(
        "Cannot resolve input socket '%s' (id='%s', idx=%d) on Octane node '%s' (%s). "
        "Available inputs: %s",
        cycles_socket_name,
        socket_identifier,
        socket_index,
        octane_node.name,
        octane_node.bl_idname,
        [s.name for s in octane_node.inputs],
    )
    return None


def resolve_output_socket(
    cycles_type: str,
    cycles_socket_name: str,
    octane_node,
    socket_identifier: str = "",
) -> "bpy.types.NodeSocket | None":
    """Find the matching Octane output socket for a Cycles output socket.

    Uses a multi-strategy approach to maximise connection success.
    """
    from ..utils.logger import get_logger
    log = get_logger()

    type_map = OUTPUT_MAP.get(cycles_type, {})

    # Strategy 1: exact name match via OUTPUT_MAP candidates
    candidates = type_map.get(cycles_socket_name, [])
    for cand in candidates:
        sock = octane_node.outputs.get(cand)
        if sock is not None:
            return sock

    # Strategy 2: try identifier
    if socket_identifier and socket_identifier != cycles_socket_name:
        id_candidates = type_map.get(socket_identifier, [])
        for cand in id_candidates:
            sock = octane_node.outputs.get(cand)
            if sock is not None:
                return sock

    # Strategy 3: literal Cycles socket name
    sock = octane_node.outputs.get(cycles_socket_name)
    if sock is not None:
        return sock

    # Strategy 4: case-insensitive match
    sock = _find_socket_case_insensitive(octane_node.outputs, cycles_socket_name)
    if sock is not None:
        return sock
    for cand in candidates:
        sock = _find_socket_case_insensitive(octane_node.outputs, cand)
        if sock is not None:
            return sock

    # Strategy 5: substring match
    sock = _find_socket_substring(octane_node.outputs, cycles_socket_name)
    if sock is not None:
        return sock

    # Strategy 6: first output only when it is unambiguous.  Returning output
    # zero from a multi-output node silently aliases channels such as R/G/B.
    if len(octane_node.outputs) == 1:
        return octane_node.outputs[0]

    log.warning(
        "Cannot resolve output socket '%s' on Octane node '%s' (%s). "
        "Available outputs: %s",
        cycles_socket_name,
        octane_node.name,
        octane_node.bl_idname,
        [s.name for s in octane_node.outputs],
    )
    return None


def get_contextual_node_candidates(
    cycles_type: str,
    analysis,
    node_name: str,
    outgoing_links=None,
) -> list[str]:
    """Return preferred Octane node candidates when link context matters.

    Some Cycles node types are too broad to map correctly from the node type
    alone. Image Texture is the important case: Octane has separate RGB,
    greyscale, and alpha image nodes, and using the wrong one can break data
    texture chains such as roughness, metallic, opacity, and displacement.
    """
    if cycles_type != "ShaderNodeTexImage" or analysis is None:
        return []

    outgoing = (
        list(outgoing_links)
        if outgoing_links is not None
        else [link for link in analysis.links if link.from_node == node_name]
    )
    if not outgoing:
        return []

    alpha_inputs = {
        "Alpha",
        "Opacity",
        "Opacity float",
    }
    data_inputs = {
        "Metallic",
        "Metallic float",
        "Metalness",
        "Roughness",
        "Roughness float",
        "Specular roughness",
        "Displacement",
        "Height",
        "Midlevel",
        "Mid level",
        "Scale",
        "Strength",
        "Bump",
        "Density",
        "Amount",
        "Factor",
        "Fac",
        "Value",
        "Emission Strength",
        "Emission power",
        "Power",
        "IOR",
        "Dielectric IOR",
        "Transmission",
        "Transmission Weight",
        "Transmission float",
    }
    color_inputs = {
        "Base Color",
        "Base color",
        "Albedo",
        "Albedo color",
        "Diffuse",
        "Color",
        "Emission",
        "Emission Color",
        "Emission color",
        "Reflection",
        "Specular",
        "Specular color",
        "Texture",
    }

    feeds_alpha = False
    feeds_data = False
    feeds_color = False

    for link in outgoing:
        if link.from_socket == "Alpha" or link.to_socket in alpha_inputs:
            feeds_alpha = True
        if link.to_socket in data_inputs:
            feeds_data = True
        if link.to_socket in color_inputs:
            feeds_color = True

    # A single Cycles image can feed both Color and Alpha.  Prefer the RGB
    # node in that case: selecting an alpha-only node would destroy every
    # color connection.  Alpha extraction is handled separately by the
    # conversion pipeline when the RGB node lacks a dedicated Alpha output.
    if feeds_alpha and feeds_color:
        return ["OctaneRGBImage", "ShaderNodeOctImageTex", "OctaneImageTexture"]
    if feeds_alpha:
        return ["OctaneAlphaImage", "ShaderNodeOctAlphaImage", "OctaneRGBImage"]
    if feeds_data and not feeds_color:
        return ["OctaneGreyscaleImage", "ShaderNodeOctGreyscaleImage", "OctaneRGBImage"]
    return ["OctaneRGBImage", "ShaderNodeOctImageTex", "OctaneImageTexture"]


def create_octane_node(
    node_tree,
    cycles_type: str,
    label: str = "",
    preferred_candidates: list[str] | None = None,
):
    """Try to create an Octane node using candidates list. Returns node or None."""
    from ..utils.logger import get_logger
    import bpy
    log = get_logger()

    base_candidates = NODE_TYPE_MAP.get(cycles_type, [])
    preferred_candidates = preferred_candidates or []
    candidates = []
    for idname in [*preferred_candidates, *base_candidates]:
        if idname not in candidates:
            candidates.append(idname)
    
    # ── User Preferences Interception ──
    try:
        scene = bpy.context.scene
        if cycles_type == "ShaderNodeBsdfPrincipled":
            if getattr(scene, "octanify_base_material", "UNIVERSAL") == "STANDARD_SURFACE":
                candidates = [
                    "ShaderNodeOctStandardSurfaceMat",
                    "OctaneStandardSurfaceMaterial"
                ] + candidates

        elif cycles_type == "ShaderNodeDisplacement":
            if getattr(scene, "octanify_disp_mode", "TEXTURE") == "VERTEX":
                candidates = [
                    "ShaderNodeOctVertexDisplacement",
                    "OctaneVertexDisplacement"
                ] + candidates
    except Exception:
        pass

    return create_node_from_candidates(node_tree, candidates, label=label)


def create_node_from_candidates(
    node_tree,
    candidates: list[str] | tuple[str, ...],
    label: str = "",
):
    """Create the first available node from an explicit candidate list."""
    from ..utils.logger import get_logger
    log = get_logger()

    for idname in candidates:
        try:
            new_node = node_tree.nodes.new(type=idname)
            if label:
                new_node.label = label
            return new_node
        except (RuntimeError, TypeError, KeyError):
            continue

    log.warning("No available node found from candidates: %s", list(candidates))
    return None
