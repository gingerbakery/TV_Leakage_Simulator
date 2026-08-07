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
  /** Base-material categories this finish is physically plausible for
   *  (e.g. a mirror polish only makes sense on Metal) - drives which
   *  Surface property options the editor offers once a Base material is
   *  picked. */
  compatibleCategories: string[]
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

// Reflectance values below are industry-typical placeholders (no measured
// spec sheet backing them yet) - refine per part once real data is
// available. Color (Black/Gray/White) is the dominant driver of a resin's
// reflectance, so it is modeled as its own base-material axis; the resin
// family (PC/ABS/HIPS) mainly affects mechanical properties and is kept
// distinct here for BOM traceability even though their default optical
// finish is currently shared (`semi_gloss_black_resin`, see below - an
// as-molded part with no special mold treatment defaults to SPI B-2,
// a semi-gloss finish, not matte; matte requires an intentionally
// textured/EDM'd mold).
export const baseMaterials: BaseMaterial[] = [
  // Metal
  {
    id: 'aluminum_bare',
    name: 'Aluminum (bare)',
    category: 'Metal',
    reflectanceTotal: 0.55,
    defaultSurfaceId: 'metal_satin',
  },
  {
    id: 'secc_bare',
    name: 'SECC (bare)',
    category: 'Metal',
    reflectanceTotal: 0.45,
    defaultSurfaceId: 'metal_satin',
  },
  {
    id: 'anodized_aluminum_black',
    name: 'Anodized aluminum · Black',
    category: 'Metal',
    reflectanceTotal: 0.1,
    defaultSurfaceId: 'metal_satin',
  },
  {
    id: 'anodized_aluminum_silver',
    name: 'Anodized aluminum · Silver',
    category: 'Metal',
    reflectanceTotal: 0.3,
    defaultSurfaceId: 'metal_satin',
  },
  {
    id: 'powder_coated_secc_black',
    name: 'Powder coated SECC · Black',
    category: 'Metal',
    reflectanceTotal: 0.12,
    defaultSurfaceId: 'metal_low_gloss',
  },
  {
    id: 'powder_coated_secc_silver',
    name: 'Powder coated SECC · Silver',
    category: 'Metal',
    reflectanceTotal: 0.35,
    defaultSurfaceId: 'metal_low_gloss',
  },
  // Resin - PC
  {
    id: 'pc_black',
    name: 'PC · Black',
    category: 'Resin',
    reflectanceTotal: 0.08,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'pc_gray',
    name: 'PC · Gray',
    category: 'Resin',
    reflectanceTotal: 0.3,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'pc_white',
    name: 'PC · White',
    category: 'Resin',
    reflectanceTotal: 0.85,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  // Resin - ABS
  {
    id: 'abs_black',
    name: 'ABS · Black',
    category: 'Resin',
    reflectanceTotal: 0.08,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'abs_gray',
    name: 'ABS · Gray',
    category: 'Resin',
    reflectanceTotal: 0.3,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'abs_white',
    name: 'ABS · White',
    category: 'Resin',
    reflectanceTotal: 0.85,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  // Resin - HIPS
  {
    id: 'hips_black',
    name: 'HIPS · Black',
    category: 'Resin',
    reflectanceTotal: 0.08,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'hips_gray',
    name: 'HIPS · Gray',
    category: 'Resin',
    reflectanceTotal: 0.3,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  {
    id: 'hips_white',
    name: 'HIPS · White',
    category: 'Resin',
    reflectanceTotal: 0.85,
    defaultSurfaceId: 'semi_gloss_black_resin',
  },
  // Tape / Foam
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

// Metal finish tiers (`metal_*`) are keyed to typical 60° Gloss Unit (GU)
// bands rather than the process that produced them - anodizing, powder
// coating, and bare/brushed metal are usually spec'd by gloss level in
// practice, so one shared GU scale covers all of them (per-substrate look
// still comes from the Base material's own reflectance). Bands follow the
// common gloss-meter convention: low gloss <10GU, semi-gloss 10-70GU,
// high gloss >70GU (60°) - see Konica Minolta / Qualitest gloss references.
export const surfaceProperties: SurfaceProperty[] = [
  {
    id: 'metal_low_gloss',
    name: 'Low gloss',
    scatterModel: 'gaussian',
    reflectanceScale: 1,
    specularRatio: 0.1,
    diffuseRatio: 0.9,
    roughness: 0.85,
    scatterSigmaDeg: 30,
    compatibleCategories: ['Metal'],
  },
  {
    id: 'metal_satin',
    name: 'Normal',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    specularRatio: 0.35,
    diffuseRatio: 0.65,
    roughness: 0.5,
    scatterSigmaDeg: 15,
    compatibleCategories: ['Metal'],
  },
  {
    id: 'metal_gloss',
    name: 'Gloss',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    specularRatio: 0.65,
    diffuseRatio: 0.35,
    roughness: 0.25,
    scatterSigmaDeg: 6,
    compatibleCategories: ['Metal'],
  },
  // Resin finish tiers mirror the Metal GU bands, anchored to SPI mold
  // finish grades: SPI B-2 (sandpaper-polished) is the *default* mold
  // finish used when nothing special is called out, and lands mid
  // semi-gloss (~50GU) - matte (SPI C/D, textured/EDM mold) and high-gloss
  // (SPI A, diamond-buffed mold) are both deliberate, non-default choices.
  {
    id: 'matte_black_resin',
    name: 'Matte',
    scatterModel: 'lambertian',
    reflectanceScale: 1,
    specularRatio: 0,
    diffuseRatio: 1,
    roughness: 0.88,
    scatterSigmaDeg: 32,
    compatibleCategories: ['Resin'],
  },
  {
    id: 'semi_gloss_black_resin',
    name: 'Normal',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    specularRatio: 0.4,
    diffuseRatio: 0.6,
    roughness: 0.45,
    scatterSigmaDeg: 14,
    compatibleCategories: ['Resin'],
  },
  {
    id: 'high_gloss_resin',
    name: 'High-gloss',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    specularRatio: 0.75,
    diffuseRatio: 0.25,
    roughness: 0.15,
    scatterSigmaDeg: 5,
    compatibleCategories: ['Resin'],
  },
  {
    id: 'polished_mirror_high',
    name: 'Polished mirror',
    scatterModel: 'specular',
    reflectanceScale: 1,
    reflectanceOverride: 0.85,
    specularRatio: 1,
    diffuseRatio: 0,
    roughness: 0.03,
    scatterSigmaDeg: 0.5,
    compatibleCategories: ['Metal'],
  },
  {
    id: 'tape_black_matte',
    name: 'Black tape · matte',
    scatterModel: 'lambertian',
    reflectanceScale: 1,
    specularRatio: 0,
    diffuseRatio: 1,
    roughness: 0.92,
    scatterSigmaDeg: 38,
    compatibleCategories: ['Tape'],
  },
  {
    id: 'tape_black_glossy',
    name: 'Black tape · glossy',
    scatterModel: 'mixed',
    reflectanceScale: 1,
    reflectanceOverride: 0.12,
    specularRatio: 0.5,
    diffuseRatio: 0.5,
    roughness: 0.25,
    scatterSigmaDeg: 8,
    compatibleCategories: ['Tape'],
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
    compatibleCategories: ['Foam'],
  },
]

export function surfacePropertiesForCategory(
  category: string,
): SurfaceProperty[] {
  return surfaceProperties.filter((surface) =>
    surface.compatibleCategories.includes(category),
  )
}

export const opticalProfilePresets: OpticalProfilePreset[] = [
  {
    id: 'profile_tv_black_default',
    name: 'TV black default',
    baseMaterialId: 'pc_black',
    surfaceId: 'matte_black_resin',
    bsdfAssetId: '',
  },
  {
    id: 'profile_black_chassis_default',
    name: 'Black chassis default',
    baseMaterialId: 'powder_coated_secc_black',
    surfaceId: 'metal_low_gloss',
    bsdfAssetId: '',
  },
  {
    id: 'profile_polished_mirror_high',
    name: 'Polished mirror',
    baseMaterialId: 'anodized_aluminum_silver',
    surfaceId: 'polished_mirror_high',
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
