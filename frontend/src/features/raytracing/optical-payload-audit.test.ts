/// <reference types="node" />
import { writeFileSync } from 'node:fs'
import { afterAll, describe, expect, it } from 'vitest'

import { baseMaterials, compileOpticalProfile, surfaceProperties } from '@/features/materials/material-catalog'
import { defaultRayTraceConfig, type MaterialAssignment } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import { buildRayTraceRequest, createDatumEmitter, createDatumReceiver } from './ray-tracing-model'

function requestFor(assignments: MaterialAssignment[]) {
  return buildRayTraceRequest({
    scene: createSceneFixture(), projectName: 'optical-audit',
    emitters: [createDatumEmitter('source', [0, 0, 0], [0, 0, 0])],
    receivers: [createDatumReceiver('receiver', [0, 0, -2], [0, 0, 0])],
    materialAssignments: assignments, transformRules: [], excludedComponentIds: [],
    deletedComponentIds: [], roiScopes: [], config: defaultRayTraceConfig,
  })
}

function assignment(baseMaterialId: string, surfaceId: string): MaterialAssignment {
  return {
    assignmentId: 'audit-face', componentId: 8, targetType: 'faces', faceIds: [1],
    baseMaterialId, surfaceId, profileId: '', bsdfAssetId: '', enabled: true,
  }
}

const exported: unknown[] = []

describe('surface settings to optical request audit', () => {
  for (const base of baseMaterials) {
    for (const surface of surfaceProperties.filter((item) => item.compatibleCategories.includes(base.category))) {
      it(`preserves ${base.id} / ${surface.id} including JSON restore`, () => {
        const assignments = [assignment(base.id, surface.id)]
        const request = requestFor(assignments)
        const expectedReflectance = Math.min(1, Math.max(0, surface.reflectanceOverride ?? base.reflectanceTotal * surface.reflectanceScale))
        expect(request.optical_profiles[0]).toMatchObject({
          reflectance: expectedReflectance, absorption: 1 - expectedReflectance,
          scatter_model: surface.scatterModel, roughness: surface.roughness,
          gaussian_sigma_deg: surface.scatterSigmaDeg,
          specular_ratio: surface.specularRatio / (surface.specularRatio + surface.diffuseRatio),
          diffuse_ratio: surface.diffuseRatio / (surface.specularRatio + surface.diffuseRatio),
        })
        expect(request.optical_assignments[0].face_indices).toEqual([1])
        expect(requestFor(JSON.parse(JSON.stringify(assignments)))).toEqual(request)
        exported.push({ base: base.id, surface: surface.id, expected_reflectance: expectedReflectance,
          profile: request.optical_profiles[0], assignment: request.optical_assignments[0] })
      })
    }
  }

  it('preserves a user-entered 60 percent reflectance and lobe weights', () => {
    const custom = assignment('pc_black', 'high_gloss_resin')
    custom.opticalOverride = { reflectance: 0.6, loss: 0.4, specularRatio: 0.25, diffuseRatio: 0.75 }
    const request = requestFor([custom])
    expect(request.optical_profiles[0]).toMatchObject({ reflectance: 0.6, absorption: 0.4,
      specular_ratio: 0.25, diffuse_ratio: 0.75, scatter_model: 'mixed', gaussian_sigma_deg: 5 })
    exported.push({ base: 'pc_black', surface: 'custom_60_percent', expected_reflectance: 0.6,
      profile: request.optical_profiles[0], assignment: request.optical_assignments[0] })
  })

  it('inherits the current part substrate without replacing the face finish', () => {
    const face = assignment('pc_black', 'matte_black_resin')
    const part = { ...assignment('pc_white', 'semi_gloss_black_resin'), assignmentId: 'part',
      targetType: 'part' as const, faceIds: [] }
    const request = requestFor([part, face])
    expect(request.optical_profiles[1].reflectance).toBeCloseTo(0.92 * 0.72)
    expect(request.optical_profiles[1].scatter_model).toBe('lambertian')
    part.enabled = false
    expect(requestFor([part, face]).optical_profiles[0].reflectance).toBeCloseTo(0.08 * 0.72)
  })

  it('uses the catalog default finish for each substrate', () => {
    for (const base of baseMaterials) {
      const request = requestFor([assignment(base.id, base.defaultSurfaceId)])
      const compiled = compileOpticalProfile(base.id, base.defaultSurfaceId)
      expect(request.optical_profiles[0].reflectance).toBe(compiled.reflectance)
      expect(request.optical_profiles[0].scatter_model).toBe(compiled.scatterModel)
    }
  })
})

afterAll(() => {
  const destination = process.env.BITSAM_OPTICAL_AUDIT_PAYLOAD
  if (destination) writeFileSync(destination, JSON.stringify(exported, null, 2))
})
