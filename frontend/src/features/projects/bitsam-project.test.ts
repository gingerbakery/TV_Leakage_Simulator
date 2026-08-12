import { describe, expect, it } from 'vitest'

import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  BitsamProjectError,
  bitsamDownloadFileName,
  bitsamFileExtension,
  compareBitsamProjectScene,
  createBitsamSettingsOnlyState,
  createBitsamProject,
  parseBitsamProject,
  serializeBitsamProject,
} from './bitsam-project'

function createProjectFixture() {
  const store = createWorkspaceStore()
  const activeCad = {
    path: 'C:\\company-secret\\tv-corner.step',
    displayName: 'tv-corner.step',
  }
  const actions = store.getState().actions
  actions.setActiveCad(activeCad)
  actions.renameComponent(1, 'Chassis Rear')
  actions.setHiddenComponentIds([2])
  actions.setRayTraceConfig({
    ...store.getState().rayTraceConfig,
    ray_count: 25_000,
    max_depth: 8,
  })
  actions.setSelectedFaceIds([3])
  actions.setSelectedComponentIds([1])
  actions.setActiveRayTraceJobId('temporary-job')

  return {
    activeCad,
    project: createBitsamProject(
      createSceneFixture(),
      store.getState(),
      new Date('2026-07-29T01:02:03.000Z'),
    ),
  }
}

describe('BITSAM project format', () => {
  it('round-trips persistent simulation state without local CAD paths', () => {
    const { project } = createProjectFixture()
    const serialized = serializeBitsamProject(project)
    const restored = parseBitsamProject(serialized)

    expect(restored).toEqual(project)
    expect(restored.schema_version).toBe('bitsam-project.v1')
    expect(restored.cad.display_name).toBe('tv-corner.step')
    expect(restored.workspace).toMatchObject({
      hiddenComponentIds: [2],
      componentNameOverrides: { 1: 'Chassis Rear' },
      rayTraceConfig: {
        ray_count: 25_000,
        max_depth: 8,
      },
    })
    expect(serialized).not.toContain('company-secret')
    expect(serialized).not.toContain('selectedFaceIds')
    expect(serialized).not.toContain('activeRayTraceJobId')
  })

  it('uses the custom .bitsam extension', () => {
    const { project } = createProjectFixture()

    expect(bitsamFileExtension).toBe('.bitsam')
    expect(bitsamDownloadFileName(project)).toBe(
      'tv-corner.bitsam',
    )
  })

  it('accepts the same geometry and warns about renamed CAD files', () => {
    const { project } = createProjectFixture()
    const compatibility = compareBitsamProjectScene(
      project,
      createSceneFixture(),
      {
        path: 'C:\\uploads\\renamed.step',
        displayName: 'renamed.step',
      },
    )

    expect(compatibility.compatible).toBe(true)
    expect(compatibility.reasons).toEqual([])
    expect(compatibility.warnings).toHaveLength(1)
  })

  it('rejects a project when the loaded CAD geometry differs', () => {
    const { activeCad, project } = createProjectFixture()
    const scene = createSceneFixture()
    scene.components[0].bbox_max = [61, 60, 10]

    const compatibility = compareBitsamProjectScene(
      project,
      scene,
      activeCad,
    )

    expect(compatibility.compatible).toBe(false)
    expect(compatibility.reasons).toContain(
      '부품 ID 또는 형상 경계 정보가 다릅니다.',
    )
  })

  it('restores geometry-independent settings for a different CAD', () => {
    const { project } = createProjectFixture()
    project.workspace.emitters = [
      {
        emitter_id: 'face-emitter',
        emitter_type: 'face',
      } as (typeof project.workspace.emitters)[number],
      {
        emitter_id: 'datum-emitter',
        emitter_type: 'datum_plane',
      } as (typeof project.workspace.emitters)[number],
    ]
    project.workspace.receivers = [
      {
        receiver_id: 'datum-receiver',
        placement_mode: 'datum_plane',
      } as (typeof project.workspace.receivers)[number],
      {
        receiver_id: 'view-receiver',
        placement_mode: 'current_view',
      } as (typeof project.workspace.receivers)[number],
    ]

    const restored = createBitsamSettingsOnlyState(project)

    expect(restored.workspace.rayTraceConfig.ray_count).toBe(25_000)
    expect(restored.workspace.emitters.map((item) => item.emitter_id)).toEqual([
      'datum-emitter',
    ])
    expect(restored.workspace.receivers.map((item) => item.receiver_id)).toEqual([
      'datum-receiver',
    ])
    expect(restored.workspace.hiddenComponentIds).toEqual([])
    expect(restored.workspace.componentNameOverrides).toEqual({})
    expect(restored.restoredDatumEmitters).toBe(1)
    expect(restored.restoredDatumReceivers).toBe(1)
    expect(restored.skippedGeometryItems).toBeGreaterThan(0)
  })

  it('reports malformed and unsupported project files', () => {
    expect(() => parseBitsamProject('{broken')).toThrow(
      BitsamProjectError,
    )

    const { project } = createProjectFixture()
    const unsupported = {
      ...project,
      schema_version: 'bitsam-project.v99',
    }
    expect(() =>
      parseBitsamProject(JSON.stringify(unsupported)),
    ).toThrow('지원하지 않는 BITSAM 버전')

    const damaged = {
      ...project,
      workspace: {
        ...project.workspace,
        emitters: [{}],
      },
    }
    expect(() =>
      parseBitsamProject(JSON.stringify(damaged)),
    ).toThrow('필수 데이터가 없거나 손상')
  })
})
