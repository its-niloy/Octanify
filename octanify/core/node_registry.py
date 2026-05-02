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
        "ShaderNodeOctVoronoiTex",
        "OctaneVoronoiTexture",
    ],
    "ShaderNodeTexWave": [
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
        "OctaneCyclesMixFloatNodeWrapper",
        "OctaneCyclesMixFloat3NodeWrapper",
        "ShaderNodeOctMixTex",
        "OctaneMixTexture",
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
        "ShaderNodeOctGammaCorrectionTex",
        "OctaneGammaCorrection",
    ],
    "ShaderNodeRGBCurves": [
        "NodeReroute"
    ],
    "ShaderNodeMath": [
        "ShaderNodeOctMathTex",
        "ShaderNodeOctMath",
        "ShaderNodeOctCyclesMath",
        "ShaderNodeOctFloatMathTex",
        "OctaneMath",
        "OctaneFloatMath",
    ],
    "ShaderNodeMapRange": [
        "ShaderNodeOctRangeTex",
        "OctaneRange",
    ],
    "ShaderNodeClamp": [
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
        "ShaderNodeOctDisplacementTex",
        "OctaneDisplacement",
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
        "ShaderNodeOctFresnelTex",
        "OctaneFresnel",
    ],
    "ShaderNodeLayerWeight": [
        "ShaderNodeOctFresnelTex",
        "OctaneFresnel",
    ],
    "ShaderNodeVertexColor": [
        "ShaderNodeOctVertexColorTex",
        "OctaneColorVertexAttribute",
    ],
    "ShaderNodeAttribute": [
        "ShaderNodeOctAttributeTex",
        "OctaneAttribute",
    ],
    "ShaderNodeAmbientOcclusion": [
        "ShaderNodeOctDirtTex",
        "OctaneDirtTexture",
    ],
    "ShaderNodeVectorMath": [
        "ShaderNodeOctVectorMathTex",
        "ShaderNodeOctVectorMath",
        "ShaderNodeOctCyclesVectorMath",
        "OctaneVectorMath",
        "ShaderNodeOctAddTex",
        "OctaneAddTexture",
    ],
    "ShaderNodeVectorRotate": [
        "ShaderNodeOct3DTransform",
        "OctaneTransform3D",
    ],
    "ShaderNodeVectorTransform": [
        "ShaderNodeOct3DTransform",
        "OctaneTransform3D",
    ],
    "ShaderNodeVectorDisplacement": [
        "ShaderNodeOctDisplacementTex",
        "OctaneDisplacement",
    ],
    "ShaderNodeNormal": [
        "ShaderNodeOctNormalMapTex",
        "OctaneNormalTexture",
    ],
    "ShaderNodeTangent": [
        "ShaderNodeOctNormalMapTex",
        "OctaneNormalTexture",
    ],
    "ShaderNodeObjectInfo": [
        "ShaderNodeOctInstanceColorTex",
        "OctaneInstanceColor",
    ],
    "ShaderNodeCameraData": [
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
        "ShaderNodeOctBevelTex",
        "OctaneBevelTexture",
    ],

    # ── Volume ───────────────────────────────────────────────────────────
    "ShaderNodeVolumeAbsorption": [
        "ShaderNodeOctAbsorptionMedium",
        "OctaneAbsorptionMedium",
    ],
    "ShaderNodeVolumeScatter": [
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
}

# Nodes that are passed through (logic handled inline, no 1:1 node creation)
PASSTHROUGH_TYPES: set[str] = {
    "ShaderNodeSeparateColor",
    "ShaderNodeSeparateRGB",
    "ShaderNodeSeparateXYZ",
    "ShaderNodeCombineColor",
    "ShaderNodeCombineRGB",
    "ShaderNodeCombineXYZ",
    "ShaderNodeNewGeometry",
    "ShaderNodeLightPath",
    "ShaderNodeOutputMaterial",
    "ShaderNodeGroup",
    "NodeReroute",
    "NodeFrame",
    "NodeGroupInput",
    "NodeGroupOutput",
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
        "Diffuse Roughness":    ["Roughness", "Roughness float", "Diffuse roughness"],
        "Specular IOR Level":   ["Specular", "Specular float"],
        "Specular Tint":        ["Specular tint", "Specular map", "Specular color"],
        "IOR":                  ["Dielectric IOR", "Index", "IOR", "Specular IOR"],
        "Transmission Weight":  ["Transmission", "Transmission float"],
        "Alpha":                ["Opacity", "Opacity float"],
        "Normal":               ["Normal", "Bump", "ShaderNormal"],
        "Tangent":              ["Anisotropy rotation", "Rotation"],
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
        "Subsurface Weight":    ["SSS", "Subsurface"],
        "Subsurface Radius":    ["Absorption", "Medium radius"],
        "Subsurface Scale":     ["Density", "Medium scale"],
        "Subsurface IOR":       ["Index", "IOR"],
        "Subsurface Anisotropy": ["Anisotropy", "Subsurface anisotropy"],
        "Anisotropic":          ["Anisotropy", "Anisotropy float"],
        "Anisotropic Rotation": ["Anisotropy rotation", "Rotation"],
        "Thin Film Thickness":  ["Film width", "Thin film thickness"],
        "Thin Film IOR":        ["Film IOR", "Thin film IOR"],
    },
    "ShaderNodeBsdfGlass": {
        "Color":     ["Reflection", "Specular", "Albedo color"],
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
        "Color":  ["Diffuse", "Albedo color", "Albedo"],
        "Normal": ["Normal", "Bump"],
    },
    "ShaderNodeBsdfRefraction": {
        "Color":     ["Reflection", "Specular", "Albedo color"],
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
        "Value": ["Input", "Value"],
        "Min":   ["Minimum", "Min"],
        "Max":   ["Maximum", "Max"],
    },
    "ShaderNodeFresnel": {
        "IOR":    ["IOR", "Index"],
        "Normal": ["Normal"],
    },
    "ShaderNodeLayerWeight": {
        "Blend":  ["IOR", "Index", "Power"],
        "Normal": ["Normal"],
    },
    "ShaderNodeAmbientOcclusion": {
        "Color":    ["Inclination color", "Bright color", "Color"],
        "Distance": ["Radius", "Distance"],
        "Normal":   ["Normal"],
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
        "Fac": ["Input", "Value", "Amount"],
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
        "Color": ["OutTex", "Texture out", "Output", "Emission"],
    },
    "ShaderNodeVolumePrincipled": {
        "Volume": ["OutMedium", "Medium out", "Output"],
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

    # Strategy 1: exact name match via INPUT_MAP candidates
    candidates = type_map.get(cycles_socket_name, [])
    for cand in candidates:
        sock = octane_node.inputs.get(cand)
        if sock is not None:
            return sock

    # Strategy 2: try identifier-based lookup (disambiguates MixShader etc.)
    if socket_identifier and socket_identifier != cycles_socket_name:
        id_candidates = type_map.get(socket_identifier, [])
        for cand in id_candidates:
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

    # Strategy 6: first output (texture/shader nodes usually have one main output)
    if octane_node.outputs:
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


def create_octane_node(node_tree, cycles_type: str, label: str = ""):
    """Try to create an Octane node using candidates list. Returns node or None."""
    from ..utils.logger import get_logger
    import bpy
    log = get_logger()

    candidates = NODE_TYPE_MAP.get(cycles_type, [])
    
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

    for idname in candidates:
        try:
            new_node = node_tree.nodes.new(type=idname)
            if label:
                new_node.label = label
            return new_node
        except (RuntimeError, TypeError, KeyError):
            continue

    log.warning("No Octane equivalent found for Cycles node type '%s'", cycles_type)
    return None
