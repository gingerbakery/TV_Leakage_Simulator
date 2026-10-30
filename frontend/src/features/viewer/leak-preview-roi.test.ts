import { expect, it } from 'vitest'
import type { RoiScope } from '@/stores'
import type { LeakPreviewPoint } from '@/features/leak-preview/leak-preview-model'
import { leakPreviewPointsInRoi } from './leak-preview-roi'

const scope = (id: string, xMin: number, xMax: number, active = true): RoiScope => ({
  id, scopeId: id, active, source: 'box', view: 'coordinate', components: [],
  clipBox: { plane: 'xy', zUnbounded: true, xMin, xMax, yMin: 0, yMax: 1, zMin: 0, zMax: 1 },
})
const points: LeakPreviewPoint[] = [
  { candidateId: 'a', position: [0, 0, -1e30], relativeStrength: 1, observerAngles: [0, 10] },
  { candidateId: 'b', position: [1, 1, 1e30], relativeStrength: .8, angleBins: [1, 0, 2, 0] },
  { candidateId: 'gap', position: [2, .5, 0], relativeStrength: .4 },
  { candidateId: 'c', position: [3.5, .5, 0], relativeStrength: .2 },
  { candidateId: 'outside-y', position: [.5, 2, 0], relativeStrength: .1 },
]

it('displays only points in the active ROI union and ignores both Z limits for Preview ROI', () => {
  const before = structuredClone(points)
  const visible = leakPreviewPointsInRoi(points, [scope('a', 0, 1), scope('b', 3, 4)])
  expect(visible.map((point) => point.candidateId)).toEqual(['a', 'b', 'c'])
  expect(visible[0]).toBe(points[0])
  expect(visible[1].angleBins).toEqual([1, 0, 2, 0])
  expect(points).toEqual(before)
})

it('updates the view when ROI activation changes and restores all points with no active ROI', () => {
  expect(leakPreviewPointsInRoi(points, [scope('a', 0, 1), scope('b', 3, 4, false)]).map((point) => point.candidateId)).toEqual(['a', 'b'])
  expect(leakPreviewPointsInRoi(points, [scope('a', 0, 1, false), scope('b', 3, 4)]).map((point) => point.candidateId)).toEqual(['c'])
  expect(leakPreviewPointsInRoi(points, [scope('a', 0, 1, false)])).toBe(points)
  expect(leakPreviewPointsInRoi(points, [])).toBe(points)
  expect(leakPreviewPointsInRoi(points, [scope('empty', 10, 11)])).toEqual([])
})

it('retains finite Z limits for a manually defined XYZ ROI', () => {
  const roi = scope('finite', 0, 1)
  roi.clipBox = { ...roi.clipBox!, plane: 'xyz', zUnbounded: false }
  expect(leakPreviewPointsInRoi(points, [roi])).toEqual([])
  expect(leakPreviewPointsInRoi([{ position: [.5, .5, .5], relativeStrength: 1 }], [roi])).toHaveLength(1)
})
