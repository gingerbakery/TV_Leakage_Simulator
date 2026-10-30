import { beforeEach, describe, expect, it } from 'vitest'

import { leakPreviewStore, snapshotLeakPreviewProjectState } from './leak-preview-store'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { isLeakPreviewPointHighlighted, createEscapeEnclosureReceivers, detectLeakPreviewCandidates } from './leak-preview-model'
import { workspaceStore } from '@/stores'

describe('leak preview Allowed Area groups', () => {
  it('deletes only the selected candidate and its points, including overlapping ROI bounds', () => {
    const scene = createSceneFixture()
    const result = createRayTraceResultFixture()
    result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
    result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [3, 1], bin_area_mm2: 1, flux_lumen: [[1, 0, 1]], hit_count: 2 }]
    result.stored_paths = []
    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.candidates).toHaveLength(2)
    expect(detection.points).toHaveLength(2)
    leakPreviewStore.getState().setDetection(result, detection.points, detection.candidates)
    const [first, second] = detection.candidates
    leakPreviewStore.getState().removeCandidate(first.id)
    expect(leakPreviewStore.getState().candidates.map((candidate) => candidate.id)).toEqual([second.id])
    expect(leakPreviewStore.getState().points.map((point) => point.candidateId)).toEqual([second.id])
    expect(leakPreviewStore.getState().selectedCandidateId).toBe(second.id)
    expect(leakPreviewStore.getState().result).toBe(result)
    leakPreviewStore.getState().removeCandidate(second.id)
    expect(leakPreviewStore.getState().points).toEqual([])
    expect(leakPreviewStore.getState().selectedCandidateId).toBeNull()
  })

  it('persists Ray Tracing Emitter selection and restores legacy projects with an empty selection', () => {
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'emitter.step', displayName: 'Emitter' })
    leakPreviewStore.getState().setSourceEmitters(['emitter_001', 'emitter_002', 'emitter_001'])
    const saved = snapshotLeakPreviewProjectState()
    expect(saved.sourceMode).toBe('emitters')
    expect(saved.sourceEmitterIds).toEqual(['emitter_001', 'emitter_002'])
    leakPreviewStore.getState().clear()
    leakPreviewStore.getState().restoreProjectState(saved)
    expect(leakPreviewStore.getState().sourceEmitterIds).toEqual(saved.sourceEmitterIds)
    const legacy = { ...saved, sourceMode: 'face' as const }
    delete legacy.sourceEmitterIds
    leakPreviewStore.getState().restoreProjectState(legacy)
    expect(leakPreviewStore.getState().sourceEmitterIds).toEqual([])
  })

  beforeEach(() => {
    workspaceStore.getState().actions.resetWorkspace()
    leakPreviewStore.getState().clear()
  })

  it('keeps multiple dragged regions in one list item until selection is finished', () => {
    const state = leakPreviewStore.getState()
    state.beginIgnoreAreaSelection()

    for (let index = 0; index < 4; index += 1) {
      leakPreviewStore.getState().addIgnoreAreaRegion({
        plane: 'xy',
        xMin: index * 10,
        xMax: index * 10 + 2,
        yMin: 0,
        yMax: 2,
        zMin: -1,
        zMax: 1,
      })
    }

    expect(leakPreviewStore.getState().ignoreAreas).toHaveLength(1)
    expect(leakPreviewStore.getState().ignoreAreas[0].regions).toHaveLength(4)
    expect(leakPreviewStore.getState().ignoreAreaSelectionArmed).toBe(true)

    leakPreviewStore.getState().finishIgnoreAreaSelection()
    expect(leakPreviewStore.getState().ignoreAreaSelectionArmed).toBe(false)
  })

  it('discards an empty group when selection is finished without a drag', () => {
    leakPreviewStore.getState().beginIgnoreAreaSelection()
    leakPreviewStore.getState().finishIgnoreAreaSelection()
    expect(leakPreviewStore.getState().ignoreAreas).toEqual([])
  })

  it('keeps exactly one exterior detection direction selected', () => {
    leakPreviewStore.getState().toggleDirection('neg_y')
    expect(leakPreviewStore.getState().directions).toEqual(['neg_y'])
    leakPreviewStore.getState().toggleDirection('pos_z')
    expect(leakPreviewStore.getState().directions).toEqual(['pos_z'])
  })

  it('stores a Body light source as its component and expanded Face ids', () => {
    leakPreviewStore.getState().setSourceBody([3], 3)
    expect(leakPreviewStore.getState()).toMatchObject({
      sourceMode: 'body',
      sourceComponentIds: [3],
      sourceFaceIds: [],
      sourceBodyFaceCount: 3,
    })
  })

  it('keeps preview setup when the same CAD is rebound to a refreshed server token', () => {
    leakPreviewStore.getState().ensureScene('scene-old')
    leakPreviewStore.getState().setSourceBody([3], 12)
    leakPreviewStore.getState().rebindSceneToken('scene-new')

    expect(leakPreviewStore.getState()).toMatchObject({
      sceneToken: 'scene-new',
      sourceMode: 'body',
      sourceComponentIds: [3],
      sourceBodyFaceCount: 12,
    })
  })

  it('keeps coordinate constraints across token refresh and ACC imports while resetting CAD selections', () => {
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'main.step', displayName: 'main.step' })
    const caseId = workspaceStore.getState().activeCadCaseId!
    leakPreviewStore.getState().ensureScene('scene-old')
    leakPreviewStore.getState().setSourceFaceIds([7])
    leakPreviewStore.getState().addBlocker({
      id: 'blocker-1', label: 'Main Board', enabled: true,
      baseCenter: [0, 0, 0], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
      widthMm: 10, heightMm: 10, offsetMm: 0, depthMm: 1.5, reverse: false,
    })

    leakPreviewStore.getState().ensureScene('scene-refreshed')
    expect(leakPreviewStore.getState().blockers).toHaveLength(1)
    expect(workspaceStore.getState().cadCases[0].leakPreviewState?.blockers)
      .toHaveLength(1)

    actions.addAccessoryCad(caseId, {
      path: 'board.step', displayName: 'board.step',
    })
    leakPreviewStore.getState().ensureScene('scene-composite')
    expect(leakPreviewStore.getState().blockers).toHaveLength(1)
    expect(leakPreviewStore.getState().sourceFaceIds).toEqual([])
    expect(workspaceStore.getState().cadCases[0].leakPreviewState?.blockers)
      .toHaveLength(1)
  })

  it('tracks blocker drag selection and clears it when the blocker is removed', () => {
    leakPreviewStore.getState().addBlocker({
      id: 'blocker-1', label: 'Main Board', enabled: true,
      baseCenter: [0, 0, 0], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
      widthMm: 10, heightMm: 10, offsetMm: 0, depthMm: 1.5, reverse: false,
    })
    leakPreviewStore.getState().beginBlockerAreaSelection('blocker-1')
    expect(leakPreviewStore.getState().blockerAreaSelectionId).toBe('blocker-1')
    leakPreviewStore.getState().removeBlocker('blocker-1')
    expect(leakPreviewStore.getState().blockerAreaSelectionId).toBeNull()
  })

  it('hides and restores preview markers without deleting detection data', () => {
    leakPreviewStore.getState().setVisualizationVisible(false)
    expect(leakPreviewStore.getState().visualizationVisible).toBe(false)
    leakPreviewStore.getState().setVisualizationVisible(true)
    expect(leakPreviewStore.getState().visualizationVisible).toBe(true)
  })

  it('hides Allowed Areas and Blockers without disabling their analysis rules', () => {
    leakPreviewStore.getState().setIgnoreAreasVisible(false)
    leakPreviewStore.getState().setBlockersVisible(false)
    expect(leakPreviewStore.getState()).toMatchObject({
      ignoreAreasVisible: false,
      blockersVisible: false,
    })

    leakPreviewStore.getState().setIgnoreAreasVisible(true)
    leakPreviewStore.getState().setBlockersVisible(true)
    expect(leakPreviewStore.getState()).toMatchObject({
      ignoreAreasVisible: true,
      blockersVisible: true,
    })
  })

  it('persists precision-preview inputs against the active Case', () => {
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = workspaceStore.getState().activeCadCaseId!

    leakPreviewStore.getState().addBlocker({
      id: 'saved-blocker', label: 'Saved blocker', enabled: true,
      baseCenter: [1, 2, 3], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
      widthMm: 12, heightMm: 8, offsetMm: 0.5, depthMm: 1.2, reverse: false,
    })
    leakPreviewStore.getState().beginIgnoreAreaSelection()
    leakPreviewStore.getState().addIgnoreAreaRegion({
      plane: 'xy', xMin: 0, xMax: 2, yMin: 4, yMax: 6, zMin: 8, zMax: 9,
    })
    leakPreviewStore.getState().finishIgnoreAreaSelection()

    const saved = workspaceStore.getState().cadCases.find(
      (item) => item.caseId === caseId,
    )?.leakPreviewState
    expect(saved?.blockers[0].id).toBe('saved-blocker')
    expect(saved?.ignoreAreas[0].regions).toHaveLength(1)

    leakPreviewStore.getState().restoreProjectState(saved)
    expect(leakPreviewStore.getState()).toMatchObject({
      sceneToken: null,
      blockers: [{ id: 'saved-blocker' }, { label: 'Block 2', regions: [{ baseCenter: [1, 5, 8], widthMm: 2, heightMm: 2, depthMm: 1 }] }],
      ignoreAreas: [],
      jobId: null,
      points: [],
      candidates: [],
    })
  })
})

it('highlights exact view angles and every matching angular bin, and removes deleted glow points', () => {
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
  result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [3, 1], bin_area_mm2: 1, flux_lumen: [[1, 0, 2]], hit_count: 2 }]
  const detection = detectLeakPreviewCandidates(scene, result)
  const [low, high] = [...detection.candidates].sort((a, b) => a.relativeStrength - b.relativeStrength)
  const points = detection.points.map((point) => ({ ...point, angleBins: [1, 0, 0, 2], observerAngles: [10, 20] as [number, number] }))
  leakPreviewStore.getState().setDetection(result, points, [low, high])
  expect(leakPreviewStore.getState().candidates.map((item) => item.id)).toEqual([high.id, low.id])
  expect(leakPreviewStore.getState().selectedCandidateId).toBe(high.id)
  leakPreviewStore.getState().setHighlight({ kind: 'escape', bin: 3 })
  expect(points.every((point) => isLeakPreviewPointHighlighted(point, leakPreviewStore.getState().highlight, null))).toBe(true)
  expect(isLeakPreviewPointHighlighted(points[0], { kind: 'escape', bin: 1 }, null)).toBe(false)
  expect(isLeakPreviewPointHighlighted(points[0], { kind: 'observer', horizontal: 10, vertical: 20 }, null)).toBe(true)
  expect(isLeakPreviewPointHighlighted(points[0], { kind: 'observer', horizontal: 10, vertical: 0 }, null)).toBe(false)
  leakPreviewStore.getState().removeCandidate(high.id)
  expect(leakPreviewStore.getState().points.every((point) => point.candidateId === low.id)).toBe(true)
  leakPreviewStore.getState().selectCandidate(low.id)
  expect(leakPreviewStore.getState().highlight).toBeNull()
})
