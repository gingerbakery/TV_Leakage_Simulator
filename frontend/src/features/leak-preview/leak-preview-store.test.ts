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

  it('keeps at least one exterior detection direction enabled', () => {
    for (const direction of ['neg_z', 'pos_x', 'neg_x', 'pos_y', 'neg_y'] as const) {
      leakPreviewStore.getState().toggleDirection(direction)
    }
    expect(leakPreviewStore.getState().directions).toEqual(['pos_z'])
    leakPreviewStore.getState().toggleDirection('pos_z')
    expect(leakPreviewStore.getState().directions).toEqual(['pos_z'])
  })
})
