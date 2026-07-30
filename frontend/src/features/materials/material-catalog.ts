export interface BaseMaterial {
  id: string
  name: string
  category: string
  reflectanceTotal: number
  defaultSurfaceId: string
}

export type ScatterModel =
  | 'none'
  | 'specular'
  | 'lambertian'
  | 'gaussian'
  | 'mixed'

export interface SurfaceProperty {
  id: string
  name: string
  scatterModel: ScatterModel
  reflectanceScale: number
  reflectanceOverride?: number
  specularRatio: number
  diffuseRatio: number
  roughness: number
  scatterSigmaDeg: number
}

export interface OpticalProfilePreset {
  id: string
  name: string
  baseMaterialId: string
  surfaceId: string
  bsdfAssetId: string
}

export interface CompiledOpticalProfile {
  reflectance: number
  loss: number
  specularRatio: number
  diffuseRatio: number
  scatterModel: ScatterModel
  roughness: number
  scatterSigmaDeg: number
}

export const baseMaterials: BaseMaterial[] = [
  {
    id: 'black_powder_coated_aluminum',
    name: 'Black powder coated aluminum',
    category: 'Metal',
    reflectanceTotal: 0.12,
    defaultSurfaceId: 'black_powder_coat_fine',
  },
  {
    id: 'black_pc_resin',
    name: 'Black PC resin',
    category: 'Resin',
    reflectanceTotal: 0.08,
    defaultSurfaceId: 'matte_black_resin',
  },
  {
    id: 'anodized_aluminum',
    name: 'Anodized aluminum',
    category: 'Metal',
    reflectanceTotal: 0.18,
    defaultSurfaceId: 'anodized_matte',
  },
  {
    id: 'matte_black_abs',
    name: 'Matte black ABS',
    category: 'Resin',
    reflectanceTotal: 0.08,
    defaultSurfaceId: 'matte_black_resin',
  },
  {
    id: 'black_tape_general',
    name: 'Black tape',
    category: 'Tape',
    reflectanceTotal: 0.05,
    defaultSurfaceId: 'tape_black_matte',
  },
  {
    id: 'foam_absorber_general',
    name: 'Foam absorber',
    category: 'Foam',
    reflectanceTotal: 0.03,
    defaultSurfaceId: 'foam_low_reflect',
  },
]

export const surfaceProperties: SurfaceProperty[] = [
  {
    id: 'black_powder_coat_fine',
    name: 'Black powder coat · fine',
    scatterModel: 'gaussian',
    reflectanceScale: 1,
    specularRatio: 0.15,
    diffuseRatio: 0.85,
    roughness: 0.7,
    scatterSigmaDeg: 18,
  },
  {
    id: 'black_powder_coat_coarse',
    name: 'Black powder coat · coarse',
    scatterModel: 'gaussian',
    reflectanceScale: 1.33,
    specularRatio: 0.05,
    diffuseRatio: 0.95,
    roughness: 0.82,
    scatterSigmaDeg: 28,
  },
  {
    id: 'matte_black_resin',
    name: 'Matte black resin',
    scatterModel: 'lambertian',
    reflectanceScale: 1,
    specularRatio: 0,
    diffuseRatio: 1,
    roughness: 0.88,
    scatterSigmaDeg: 32,
  },
  {
    id: 'semi_gloss_black_resin',
    name: 'Semi-gloss black resin',
    scatterModel: 'mixed',
    reflectanceScale: 1.25,
    specularRatio: 0.4,
    diffuseRatio: 0.6,
    roughness: 0.45,
    scatterSigmaDeg: 14,
  },
  {
    id: 'polished_mirror_high',
    name: 'Polished mirror · high reflectance (R 0.85)',
    scatterModel: 'specular',
    reflectanceScale: 1,
    reflectanceOverride: 0.85,
    specularRatio: 1,
    diffuseRatio: 0,
    roughness: 0.03,
    scatterSigmaDeg: 0.5,
  },
  {
    id: 'enhanced_mirror_very_high',
    name: 'Enhanced mirror · very high (R 0.95)',
    scatterModel: 'specular',
    reflectanceScale: 1,
    reflectanceOverride: 0.95,
    specularRatio: 1,
    diffuseRatio: 0,
    roughness: 0.01,
    scatterSigmaDeg: 0.2,
  },
  {
    id: 'anodized_matte',
    name: 'Anodized matte',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    specularRatio: 0.45,
    diffuseRatio: 0.55,
    roughness: 0.5,
    scatterSigmaDeg: 12,
  },
  {
    id: 'tape_black_matte',
    name: 'Black tape matte',
    scatterModel: 'lambertian',
    reflectanceScale: 1,
    specularRatio: 0,
    diffuseRatio: 1,
    roughness: 0.92,
    scatterSigmaDeg: 38,
  },
  {
    id: 'foam_low_reflect',
    name: 'Foam low reflect',
    scatterModel: 'lambertian',
    reflectanceScale: 1,
    specularRatio: 0,
    diffuseRatio: 1,
    roughness: 0.98,
    scatterSigmaDeg: 45,
  },
  {
    id: 'corrosion_light',
    name: 'Corrosion · light',
    scatterModel: 'gaussian',
    reflectanceScale: 1.17,
    specularRatio: 0.1,
    diffuseRatio: 0.9,
    roughness: 0.76,
    scatterSigmaDeg: 24,
  },
  {
    id: 'corrosion_medium',
    name: 'Corrosion · medium',
    scatterModel: 'gaussian',
    reflectanceScale: 1.5,
    specularRatio: 0.05,
    diffuseRatio: 0.95,
    roughness: 0.84,
    scatterSigmaDeg: 34,
  },
  {
    id: 'corrosion_heavy',
    name: 'Corrosion · heavy',
    scatterModel: 'gaussian',
    reflectanceScale: 1.83,
    specularRatio: 0.02,
    diffuseRatio: 0.98,
    roughness: 0.94,
    scatterSigmaDeg: 46,
  },
]

export const opticalProfilePresets: OpticalProfilePreset[] = [
  {
    id: 'profile_tv_black_default',
    name: 'TV black default',
    baseMaterialId: 'black_pc_resin',
    surfaceId: 'matte_black_resin',
    bsdfAssetId: '',
  },
  {
    id: 'profile_black_chassis_default',
    name: 'Black chassis default',
    baseMaterialId: 'black_powder_coated_aluminum',
    surfaceId: 'black_powder_coat_fine',
    bsdfAssetId: '',
  },
  {
    id: 'profile_corrosion_medium',
    name: 'Corrosion medium edge',
    baseMaterialId: 'black_powder_coated_aluminum',
    surfaceId: 'corrosion_medium',
    bsdfAssetId: '',
  },
  {
    id: 'profile_polished_mirror_high',
    name: 'Polished mirror · R 0.85 reference',
    baseMaterialId: 'anodized_aluminum',
    surfaceId: 'polished_mirror_high',
    bsdfAssetId: '',
  },
  {
    id: 'profile_enhanced_mirror_very_high',
    name: 'Enhanced mirror · R 0.95 reference',
    baseMaterialId: 'anodized_aluminum',
    surfaceId: 'enhanced_mirror_very_high',
    bsdfAssetId: '',
  },
]

export function findBaseMaterial(id: string): BaseMaterial {
  return baseMaterials.find((item) => item.id === id) ?? baseMaterials[0]
}

export function findSurfaceProperty(id: string): SurfaceProperty {
  return (
    surfaceProperties.find((item) => item.id === id) ??
    surfaceProperties[0]
  )
}

export function compileOpticalProfile(
  baseMaterialId: string,
  surfaceId: string,
): CompiledOpticalProfile {
  const base = findBaseMaterial(baseMaterialId)
  const surface = findSurfaceProperty(surfaceId)
  const reflectanceSource =
    surface.reflectanceOverride ??
    base.reflectanceTotal * surface.reflectanceScale
  const reflectance = Math.min(1, Math.max(0, reflectanceSource))
  const ratioTotal = surface.specularRatio + surface.diffuseRatio
  const specularRatio =
    ratioTotal > 0 ? surface.specularRatio / ratioTotal : 0
  const diffuseRatio =
    ratioTotal > 0 ? surface.diffuseRatio / ratioTotal : 1

  return {
    reflectance,
    loss: 1 - reflectance,
    specularRatio,
    diffuseRatio,
    scatterModel: surface.scatterModel,
    roughness: surface.roughness,
    scatterSigmaDeg: surface.scatterSigmaDeg,
  }
}
