import { expect, it } from 'vitest'
import { exitGuideGeometry, leakPreviewExitGuides } from './leak-preview-exit-guides'
import { verifiedLeakPreviewSurfacePoints, type ObserverProbeSummary } from '@/features/leak-preview/leak-preview-model'

it('keeps an audited gap-visible endpoint without inventing an exit and rejects intermediate reflections', () => {
  const summary = { views: [{ horizontal_deg: 45, vertical_deg: 0 }], cells: [{ view_index: 0,
    path_validation: { status: 'valid', endpoint_kind: 'cad_surface' },
    representative_path: [[0,0,-5], [0,0,-3], [0,0,-1], [0,0,10]] }] } as ObserverProbeSummary
  const points = [-3, -1].map(z => ({ position: [0,0,z] as [number,number,number], observerAngles: [45,0] as [number,number], relativeStrength: 1 }))
  const visible = verifiedLeakPreviewSurfacePoints(points, summary)
  expect(visible.map(p => p.position)).toEqual([[0,0,-1]])
  expect(leakPreviewExitGuides(visible, summary, 4)).toEqual([])
  summary.cells[0].aperture = { contract: 'automatic_exit_estimate_v1', id: 'gap', position: [0,0,0], exterior_shell_certified: false }
  expect(leakPreviewExitGuides(visible, summary, 4)).toEqual([{ start: [0,0,0], end: [0,0,4], strength: 1, candidateId: undefined }])
  summary.cells[0].aperture.position = [1,0,0]
  expect(leakPreviewExitGuides(visible, summary, 4)).toEqual([])
  summary.cells[0].aperture.position = [0,0,20]
  expect(leakPreviewExitGuides(visible, summary, 4)).toEqual([])
  summary.cells[0].path_validation!.status = 'invalid'
  expect(verifiedLeakPreviewSurfacePoints(points, summary)).toEqual([])
})

it('fades guide alpha to zero at the end and edges without extending beyond the segment', () => {
  const geometry = exitGuideGeometry([0,0,0], [0,0,4], 1, .5)
  const position = geometry.getAttribute('position'), color = geometry.getAttribute('color')
  expect(color.itemSize).toBe(4)
  let bright = false
  for (let i = 0; i < position.count; i++) {
    expect(position.getZ(i)).toBeGreaterThanOrEqual(0)
    expect(position.getZ(i)).toBeLessThanOrEqual(4)
    if (position.getZ(i) === 4) expect(color.getW(i)).toBe(0)
    if (color.getW(i) > 0) bright = true
  }
  expect(bright).toBe(true)
  geometry.dispose()
})
