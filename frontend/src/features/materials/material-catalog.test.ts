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
