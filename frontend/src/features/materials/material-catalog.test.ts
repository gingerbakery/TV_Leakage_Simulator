import { describe, expect, it } from 'vitest'

import {
  compileOpticalProfile,
  findSurfaceProperty,
} from './material-catalog'

describe('material catalog optical compilation', () => {
  it('keeps the existing base reflectance scale behavior', () => {
    const profile = compileOpticalProfile(
      'black_powder_coated_aluminum',
      'black_powder_coat_coarse',
    )

    expect(profile.reflectance).toBeCloseTo(0.12 * 1.33)
    expect(profile.scatterModel).toBe('gaussian')
  })

  it('provides a high-reflectance polished specular preset', () => {
    const surface = findSurfaceProperty('polished_mirror_high')
    const profile = compileOpticalProfile(
      'black_pc_resin',
      surface.id,
    )

    expect(surface.reflectanceOverride).toBe(0.85)
    expect(profile).toMatchObject({
      diffuseRatio: 0,
      reflectance: 0.85,
      scatterModel: 'specular',
      specularRatio: 1,
    })
  })

  it('provides a very-high-reflectance mirror reference preset', () => {
    const surface = findSurfaceProperty(
      'enhanced_mirror_very_high',
    )
    const profile = compileOpticalProfile(
      'black_pc_resin',
      surface.id,
    )

    expect(surface.reflectanceOverride).toBe(0.95)
    expect(profile).toMatchObject({
      diffuseRatio: 0,
      reflectance: 0.95,
      scatterModel: 'specular',
      specularRatio: 1,
    })
  })
})
