import { describe, expect, it } from 'vitest'

import {
  compileOpticalProfile,
  findSurfaceProperty,
} from './material-catalog'

describe('material catalog optical compilation', () => {
  it('keeps the existing base reflectance scale behavior', () => {
    const profile = compileOpticalProfile('pc_gray', 'semi_gloss_black_resin')

    expect(profile.reflectance).toBeCloseTo(0.3 * 1)
    expect(profile.scatterModel).toBe('mixed')
  })

  it('changes total resin reflectance with the selected finish', () => {
    const matte = compileOpticalProfile('pc_black', 'matte_black_resin')
    const normal = compileOpticalProfile(
      'pc_black',
      'semi_gloss_black_resin',
    )
    const gloss = compileOpticalProfile('pc_black', 'high_gloss_resin')

    expect(matte.reflectance).toBeCloseTo(0.08 * 0.72)
    expect(normal.reflectance).toBeCloseTo(0.08)
    expect(gloss.reflectance).toBeCloseTo(0.08 * 1.35)
    expect(matte.reflectance).toBeLessThan(normal.reflectance)
    expect(normal.reflectance).toBeLessThan(gloss.reflectance)
  })

  it('uses the measured 640 nm reflectance for PC White', () => {
    const profile = compileOpticalProfile(
      'pc_white',
      'semi_gloss_black_resin',
    )

    expect(profile.reflectance).toBeCloseTo(0.92)
  })

  it('provides a high-reflectance polished specular preset', () => {
    const surface = findSurfaceProperty('polished_mirror_high')
    const profile = compileOpticalProfile(
      'pc_black',
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
})
