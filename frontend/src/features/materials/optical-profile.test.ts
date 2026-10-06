import { writeFileSync } from 'node:fs'
import { afterAll, describe, expect, it } from 'vitest'
import { buildRayTraceRequest } from '@/features/raytracing/ray-tracing-model'
import { defaultRayTraceConfig, type MaterialAssignment } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { isCustomOpticalValid, resolveAssignmentProfile, resolveOpticalProfile } from './optical-profile'

const part: MaterialAssignment = {
  assignmentId: 'part', componentId: 1, targetType: 'part', faceIds: [],
  baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: 'shared-saved-profile', bsdfAssetId: '', enabled: true,
}

function request(assignments: MaterialAssignment[]) {
  return buildRayTraceRequest({ scene: createSceneFixture(), projectName: 'custom optics', emitters: [], receivers: [],
    materialAssignments: assignments, transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: defaultRayTraceConfig })
}

const transportPayloads: ReturnType<typeof request>[] = []
afterAll(() => {
  if (process.env.BITSAM_SURFACE_EDIT_PAYLOAD) writeFileSync(process.env.BITSAM_SURFACE_EDIT_PAYLOAD, JSON.stringify(transportPayloads))
})

describe('shared effective optical profile', () => {
  it('retains the model of legacy custom values instead of silently changing saved physics', () => {
    const custom = { reflectance: 0.6, loss: 0.4, specularRatio: 1, diffuseRatio: 0 }
    expect(resolveOpticalProfile('pc_black', 'matte_black_resin', custom)).toMatchObject({ reflectance: 0.6, scatterModel: 'lambertian', scatterSigmaDeg: 32 })
  })

  for (const scatterModel of ['specular', 'lambertian', 'gaussian', 'mixed'] as const) {
    it(`sends the same ${scatterModel} custom values as the preview`, () => {
      const assignment = { ...part, opticalOverride: { reflectance: 0.6134567, loss: 0.3865433,
        specularRatio: 0.25, diffuseRatio: 0.75, scatterModel, roughness: 0.13, scatterSigmaDeg: 7.321 } }
      const preview = resolveAssignmentProfile(assignment, [assignment])
      expect(request([assignment]).optical_profiles[0]).toMatchObject({ reflectance: preview.reflectance,
        absorption: preview.loss, specular_ratio: preview.specularRatio, diffuse_ratio: preview.diffuseRatio,
        scatter_model: preview.scatterModel, roughness: preview.roughness, gaussian_sigma_deg: preview.scatterSigmaDeg })
      transportPayloads.push(request([assignment]))
    })
  }

  it('does not alias two assignments that share a saved profile name', () => {
    const face = { ...part, assignmentId: 'face', targetType: 'faces' as const, faceIds: [0],
      opticalOverride: { reflectance: 0.6, loss: 0.4, specularRatio: 1, diffuseRatio: 0, scatterModel: 'specular' as const } }
    const payload = request([part, face])
    expect(payload.optical_profiles).toHaveLength(2)
    expect(payload.optical_assignments[0].profile_id).not.toBe(payload.optical_assignments[1].profile_id)
    expect(payload.optical_profiles[0].reflectance).toBeCloseTo(0.0576)
    expect(payload.optical_profiles[1].reflectance).toBe(0.6)
  })

  it('keeps independent face values after substrate changes', () => {
    const face = { ...part, assignmentId: 'face', targetType: 'faces' as const, faceIds: [0],
      opticalOverride: { reflectance: 0.6, loss: 0.4, specularRatio: 0.4, diffuseRatio: 0.6, scatterModel: 'mixed' as const, scatterSigmaDeg: 3.25 } }
    expect(resolveAssignmentProfile(face, [{ ...part, baseMaterialId: 'pc_white' }, face])).toMatchObject({ reflectance: 0.6, scatterSigmaDeg: 3.25 })
  })

  it('rejects invalid custom numbers without silently clamping them for Apply', () => {
    const valid = { reflectance: 0.6, loss: 0.4, specularRatio: 0.2, diffuseRatio: 0.8, scatterModel: 'mixed' as const, roughness: 0.2, scatterSigmaDeg: 5 }
    expect(isCustomOpticalValid(valid)).toBe(true)
    for (const invalid of [{ reflectance: NaN }, { reflectance: 1.2 }, { loss: 0.5 }, { specularRatio: 0.8 }, { roughness: -1 }, { scatterSigmaDeg: 0 }, { scatterSigmaDeg: Infinity }]) {
      expect(isCustomOpticalValid({ ...valid, ...invalid })).toBe(false)
    }
  })
})
