import { describe, expect, it } from 'vitest'

import type { RayTraceRequest } from '@/api'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  attachRayTraceResultSourceContext,
  createRayTraceResultSourceContext,
  createRayTraceSceneMeshSignature,
  findPendingRayTraceResultSourceContext,
  mergeRayTraceResultSourceContexts,
  registerPendingRayTraceResultSourceContext,
  takePendingRayTraceResultSourceContext,
} from './ray-result-source-context'

function createRequest(): RayTraceRequest {
  const result = createRayTraceResultFixture()
  return {
    scene_token: 'fixture-token',
    project_name: 'fixture.step',
    emitters: structuredClone(result.emitters),
    receivers: structuredClone(result.receivers),
    optical_profiles: structuredClone(result.optical_profiles),
    optical_assignments: [],
    transform_rules: [],
    excluded_component_ids: [],
    config: structuredClone(result.config),
  }
}

describe('ray result source context', () => {
  it('fingerprints the complete mesh instead of only its bounding metadata', () => {
    const scene = createSceneFixture()
    const sameScene = structuredClone(scene)
    const changedScene = structuredClone(scene)
    changedScene.mesh.vertices[0][0] += 0.2

    expect(createRayTraceSceneMeshSignature(sameScene)).toBe(
      createRayTraceSceneMeshSignature(scene),
    )
    expect(createRayTraceSceneMeshSignature(changedScene)).not.toBe(
      createRayTraceSceneMeshSignature(scene),
    )
  })

  it('captures an immutable case, scene and transport request snapshot', () => {
    const scene = createSceneFixture()
    const request = createRequest()
    const sourceContext = createRayTraceResultSourceContext(
      scene,
      request,
      'case-fixture',
    )
    request.emitters[0].power_lumen = 999
    scene.mesh.vertices[0][0] += 99

    expect(sourceContext.cad_case_id).toBe('case-fixture')
    expect(sourceContext.cad_display_name).toBe('fixture.step')
    expect(sourceContext.scene.scene_token).toBe('fixture-token')
    expect(sourceContext.requests).toHaveLength(1)
    expect(sourceContext.requests[0].emitters[0].power_lumen).not.toBe(999)
  })

  it('preserves compatible requests and rejects scene or trace-geometry changes', () => {
    const scene = createSceneFixture()
    const firstRequest = createRequest()
    const secondRequest = createRequest()
    secondRequest.config.seed += 1
    const first = createRayTraceResultSourceContext(
      scene,
      firstRequest,
      'case-fixture',
    )
    const second = createRayTraceResultSourceContext(
      scene,
      secondRequest,
      'case-fixture',
    )
    const merged = mergeRayTraceResultSourceContexts(first, second)

    expect(merged?.requests.map((request) => request.config.seed)).toEqual([
      firstRequest.config.seed,
      secondRequest.config.seed,
    ])

    const incompatible = structuredClone(second)
    incompatible.scene.mesh_signature = 'mesh-fnv-pair-v1:changed'
    expect(() =>
      mergeRayTraceResultSourceContexts(first, incompatible),
    ).toThrow('Ray result source changed during convergence')

    const transformed = structuredClone(second)
    transformed.requests[0].transform_rules = [
      {
        rule_id: 'move-fixture',
        target_type: 'component',
        object_id: 1,
        label: 'move-fixture',
        enabled: true,
        move: { x: 1, y: 0, z: 0 },
        tilt: { x: 0, y: 0, z: 0 },
      },
    ]
    expect(() =>
      mergeRayTraceResultSourceContexts(first, transformed),
    ).toThrow('Ray result source changed during convergence')
  })

  it('attaches a cloned source context to the completed result', () => {
    const result = createRayTraceResultFixture()
    const sourceContext = createRayTraceResultSourceContext(
      createSceneFixture(),
      createRequest(),
      'case-fixture',
    )
    const attached = attachRayTraceResultSourceContext(result, sourceContext)
    sourceContext.requests[0].project_name = 'mutated.step'

    expect(attached.source_context?.cad_case_id).toBe('case-fixture')
    expect(attached.source_context?.requests[0].project_name).toBe(
      'fixture.step',
    )
  })

  it('keeps launch context available while the launching panel is unmounted', () => {
    const sourceContext = createRayTraceResultSourceContext(
      createSceneFixture(),
      createRequest(),
      'case-fixture',
    )
    registerPendingRayTraceResultSourceContext('job-fixture', sourceContext)
    sourceContext.cad_display_name = 'mutated.step'

    expect(
      findPendingRayTraceResultSourceContext('job-fixture')?.cad_display_name,
    ).toBe('fixture.step')
    expect(
      takePendingRayTraceResultSourceContext('job-fixture')?.cad_case_id,
    ).toBe('case-fixture')
    expect(findPendingRayTraceResultSourceContext('job-fixture')).toBeUndefined()
  })
})
