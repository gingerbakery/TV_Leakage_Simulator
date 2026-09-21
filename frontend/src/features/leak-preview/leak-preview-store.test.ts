import { beforeEach, describe, expect, it } from 'vitest'

import { leakPreviewStore } from './leak-preview-store'

describe('leak preview Allowed Area groups', () => {
  beforeEach(() => leakPreviewStore.getState().clear())

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
})
