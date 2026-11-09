import { expect, it } from 'vitest'
import { pointListGroups } from './point-list-groups'
import { detectLeakPreviewCandidates } from './leak-preview-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import type { Vec3 } from '@/api'

it('distinguishes equal detection counts from similar physical routes without merging evidence', () => {
  const base = detectLeakPreviewCandidates(createSceneFixture(), createRayTraceResultFixture()).candidates[0]
  const make = (id: string, x: number, count: number) => ({ ...base, id, sampledPathCount: count,
    representativePath: [[x,0,0],[x,0,10]] as Vec3[] })
  const candidates = [make('a',0,1),make('b',100,1),make('c',1,2),{...make('missing',0,1),representativePath:undefined}]
  const before = structuredClone(candidates)
  expect(pointListGroups(candidates,'route',3).map(group => group.members.map(point => point.id))).toEqual([['a','c'],['b'],['missing']])
  expect(candidates).toEqual(before)
})

it('groups nearby routes with different numbers of small bends', () => {
  const base = detectLeakPreviewCandidates(createSceneFixture(), createRayTraceResultFixture()).candidates[0]
  const candidates = [
    { ...base, id: 'straight', representativePath: [[0,0,0],[0,0,20],[0,0,100]] as Vec3[] },
    { ...base, id: 'nearby', representativePath: [[1,0,0],[2,0,10],[1,0,20],[40,0,100]] as Vec3[] },
    { ...base, id: 'detour', representativePath: [[0,0,0],[30,0,10],[0,0,20],[0,0,100]] as Vec3[] },
  ]
  expect(pointListGroups(candidates, 'route', 3).map(group => group.members.map(point => point.id)))
    .toEqual([['straight', 'nearby'], ['detour']])
})
