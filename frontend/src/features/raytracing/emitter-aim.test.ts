import { describe, expect, it } from 'vitest'
import { Raycaster, Vector3 } from 'three'

import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import {
  createBitsamProject,
  parseBitsamProject,
  serializeBitsamProject,
} from '@/features/projects/bitsam-project'
import { createEmitterAimOverlay } from '@/features/viewer/emitter-aim-overlay'
import { createEmitterAim, emitterAimBoundary } from './emitter-aim'
import { createDatumEmitter, planeAxesFromRotation } from './ray-tracing-model'

describe('Emitter Aim geometry and persistence', () => {
  it('starts disabled and constructs rectangle and circle boundaries in world coordinates', () => {
    const aim = createEmitterAim([10, 20, 30])
    expect(aim.enabled).toBe(false)
    expect(emitterAimBoundary(aim)).toHaveLength(4)
    const axes = planeAxesFromRotation([20, 40, 10])
    const circle = { ...aim, shape: 'circle' as const, radius_mm: 3, u_axis: axes.uAxis, v_axis: axes.vAxis }
    for (const point of emitterAimBoundary(circle)) {
      const offset = new Vector3(...point).sub(new Vector3(...aim.center))
      expect(offset.length()).toBeCloseTo(3, 10)
      expect(offset.dot(new Vector3(...axes.normal))).toBeCloseTo(0, 10)
    }
  })

  it('draws only the Target outline and does not intercept CAD picking', () => {
    const emitter = createDatumEmitter('aim', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true }
    const overlay = createEmitterAimOverlay(emitter, [0, 0, 0])!
    const boundary = overlay.getObjectByName('aim-target-boundary')!
    expect('geometry' in boundary).toBe(true)
    const positions = (boundary as import('three').LineSegments).geometry.getAttribute('position')
    expect(positions.count).toBe(8)
    const caster = new Raycaster(new Vector3(), new Vector3(0, 0, 1))
    expect(caster.intersectObject(overlay, true)).toEqual([])
    emitter.aim.show_in_viewer = false
    expect(createEmitterAimOverlay(emitter, [0, 0, 0])).toBeNull()
    emitter.aim.enabled = false
    expect(createEmitterAimOverlay(emitter, [0, 0, 0])).toBeNull()
  })

  it('round-trips enabled Aim and the original Gaussian settings through .bitsam', () => {
    const store = createWorkspaceStore()
    const emitter = createDatumEmitter('aim', [0, 0, 0], [0, 0, 0])
    emitter.direction_distribution = 'gaussian'
    emitter.aim = { ...createEmitterAim([1, 2, 30]), enabled: true, shape: 'circle' }
    store.getState().actions.setActiveCad({ path: 'aim.step', displayName: 'aim.step' })
    store.getState().actions.upsertEmitter(emitter)
    const project = createBitsamProject(createSceneFixture(), store.getState())
    const restored = parseBitsamProject(serializeBitsamProject(project))
    expect(restored.workspace.emitters[0].aim).toEqual(emitter.aim)
    expect(restored.workspace.emitters[0].direction_distribution).toBe('gaussian')
    const reopened = createWorkspaceStore()
    reopened.getState().actions.restoreProjectState(restored.workspace)
    expect(reopened.getState().emitters[0].aim).toEqual(emitter.aim)
    delete project.workspace.emitters[0].aim
    expect(parseBitsamProject(serializeBitsamProject(project)).workspace.emitters[0].aim).toBeUndefined()
  })

  it('rejects malformed Aim geometry when loading a project', () => {
    const store = createWorkspaceStore()
    const emitter = createDatumEmitter('aim', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true }
    store.getState().actions.setActiveCad({ path: 'aim.step', displayName: 'aim.step' })
    store.getState().actions.upsertEmitter(emitter)
    const project = createBitsamProject(createSceneFixture(), store.getState())
    for (const invalid of [{ radius_mm: 0 }, { center: [0, 0] }, { v_axis: [1, 0, 0] }, { shape: 'unknown' }]) {
      project.workspace.emitters[0].aim = { ...emitter.aim, ...invalid } as typeof emitter.aim
      expect(() => parseBitsamProject(JSON.stringify(project))).toThrow()
    }
  })
})
