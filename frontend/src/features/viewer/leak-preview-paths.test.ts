import { expect, it } from 'vitest'
import { leakPreviewArrowSegments } from './leak-preview-paths'
import { detectLeakPreviewCandidates } from '@/features/leak-preview/leak-preview-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

it('draws actual source-to-bounce-to-observer arrows and a normalized observer direction', () => {
  const base = detectLeakPreviewCandidates(createSceneFixture(), createRayTraceResultFixture()).candidates[0]
  const representativePath: [number,number,number][] = [[0,0,-5],[2,0,0],[2,0,-10]]
  const arrows = leakPreviewArrowSegments({ ...base, representativePath }, 20)
  expect(arrows).toEqual([
    { start: [0,0,-5], end: [2,0,0], kind: 'ray' },
    { start: [2,0,0], end: [2,0,-10], kind: 'ray' },
    { start: [2,0,-10], end: [2,0,-30], kind: 'observer' },
  ])
  const old = leakPreviewArrowSegments({ ...base, representativePath: undefined }, 20)
  expect(old).toHaveLength(0)
})
