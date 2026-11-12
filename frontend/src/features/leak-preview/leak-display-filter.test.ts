import { expect, it } from 'vitest'
import type { Vec3 } from '@/api'
import { createSavedLeakPreviewFixture } from '@/test/leak-preview-fixture'
import { defaultObserverSettings, type ObserverProbeSummary, type LeakPreviewPoint } from './leak-preview-model'
import { defaultLeakDisplayFilter, filterLeakDisplay } from './leak-display-filter'

function fixture() {
  const base = createSavedLeakPreviewFixture().candidates[0]
  const candidates = [{...base,id:'a',observerEvidence:{axis:'pos_z' as const,stepDeg:1,horizontalDeg:0,verticalDeg:0,
    direction:[0,0,1] as Vec3,receiverCenter:[0,0,100] as Vec3,directCount:1,reflectedCount:1}}, {...base,id:'b'}]
  candidates[1] = {...candidates[0],id:'b'}
  const cells = [100,10,1].map((nit,i) => ({view_index:0,row:0,column:i,position:[i,0,0] as Vec3,
    direct_count:i === 1 ? 0 : 1,reflected_count:i === 1 ? 1 : 0,min_depth:i === 1 ? 2 : 0,max_depth:i === 1 ? 2 : 0,
    sample_count:4,luminance_nit:nit,path_validation:{status:'valid' as const,endpoint_kind:'cad_surface' as const},
    representative_path: (i === 1 ? [[1,0,-10],[1,1,-5],[1,0,0],[0,0,100]] : [[i,0,0],[0,0,100]]) as Vec3[] }))
  const summary: ObserverProbeSummary = {contract:'observer_radiance_parallel_v1',execution_backend:'cpu',measure:'luminance_cd_m2',
    settings:defaultObserverSettings,attempted_count:12,planned_count:12,direct_count:2,reflected_count:1,
    excluded_count:0,depth_limited_count:0,complete:true,stopped:false,runtime_sec:1,cells,
    views:[{horizontal_deg:0,vertical_deg:0,direction:[0,0,1],observer_center:[0,0,100],u_axis:[1,0,0],v_axis:[0,1,0],
      spacing_mm:[1,1],grid_resolution:[3,1],direct_count:2,reflected_count:1}]}
  const points: LeakPreviewPoint[] = cells.map((cell,i) => ({position:cell.representative_path.at(-2)!,relativeStrength:cell.luminance_nit/100,
    candidateId:i < 2 ? 'a' : 'b',observerAngles:[0,0]}))
  return {candidates,points,summary}
}

it('filters individual observations in a grouped candidate, includes thresholds, and keeps raw evidence intact', () => {
  const {candidates,points,summary} = fixture()
  const before = structuredClone({candidates,points,summary})
  const filtered = filterLeakDisplay(points,candidates,summary,{maxReflections:0,minNit:1,minBrightnessRatio:.01})
  expect(filtered.points.map(point => point.position)).toEqual([[0,0,0],[2,0,0]])
  expect(filtered.candidates.map(candidate => candidate.id)).toEqual(['a','b'])
  const brighter = filterLeakDisplay(points,candidates,summary,{maxReflections:2,minNit:10,minBrightnessRatio:.1})
  expect(brighter.points).toHaveLength(2)
  expect(brighter.candidates.map(candidate => candidate.id)).toEqual(['a'])
  expect({candidates,points,summary}).toEqual(before)
  const restored = filterLeakDisplay(points,candidates,summary,defaultLeakDisplayFilter)
  expect(restored.points).toBe(points)
  expect(restored.candidates).toBe(candidates)
})

it('uses the unfiltered brightness reference rather than rescaling after reflection filtering', () => {
  const {candidates,points,summary} = fixture()
  summary.cells[0].representative_path = [[0,0,-10],[0,1,-5],[0,0,0],[0,0,100]]
  expect(filterLeakDisplay(points,candidates,summary,{maxReflections:0,minNit:0,minBrightnessRatio:.5}).points).toEqual([])
})

it('keeps a surviving faint direct observation as representative instead of presenting the brighter reflection as the entire group', () => {
  const {candidates,points,summary} = fixture()
  summary.cells[0].luminance_nit = 1
  summary.cells[1].luminance_nit = 100
  const filtered = filterLeakDisplay(points,candidates,summary,{maxReflections:null,minNit:1,minBrightnessRatio:0})
  expect(filtered.candidates[0].representativePath).toHaveLength(2)
  expect(filtered.points.filter(point => point.candidateId === 'a')).toHaveLength(2)
  expect(summary.cells[1].luminance_nit).toBe(100)
})

it('never substitutes path counts for nit, and hides missing evidence only when a criterion needs it', () => {
  const {candidates,points,summary} = fixture()
  summary.measure = 'path_support_counts'
  expect(filterLeakDisplay(points,candidates,summary,{maxReflections:0,minNit:0,minBrightnessRatio:0}).points).toHaveLength(2)
  expect(filterLeakDisplay(points,candidates,summary,{maxReflections:null,minNit:1,minBrightnessRatio:0}).points).toEqual([])
  expect(filterLeakDisplay(points,candidates,null,{maxReflections:null,minNit:0,minBrightnessRatio:.1}).points).toEqual([])
})
