import { expect, it } from 'vitest'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { createCandidateReceiver, defaultObserverSettings, detectLeakPreviewCandidates, observerProbeSummary } from './leak-preview-model'
import { candidateObservations, highestObservedLuminance, resolveObserverPoint } from './observer-point-selection'

it('binds an individual grouped observation to its own angle, path and Receiver without rewriting aggregate photometry', () => {
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  result.metrics._observer_probe_summary = {
    contract:'observer_radiance_parallel_v1',measure:'luminance_cd_m2',execution_backend:'cpu',calibration_state:'uncalibrated',limitations:['finite_sampling'],
    settings:{...defaultObserverSettings,resolution:8,min_luminance_nit:0},attempted_count:2,planned_count:2,direct_count:1,reflected_count:1,excluded_count:0,depth_limited_count:0,complete:true,stopped:false,runtime_sec:0,
    views:[{horizontal_deg:0,vertical_deg:0,direction:[0,0,1],observer_center:[0,0,10],u_axis:[1,0,0],v_axis:[0,1,0],spacing_mm:[1,1],direct_count:1,reflected_count:0},
      {horizontal_deg:45,vertical_deg:0,direction:[Math.SQRT1_2,0,Math.SQRT1_2],observer_center:[11,0,10],u_axis:[Math.SQRT1_2,0,-Math.SQRT1_2],v_axis:[0,1,0],spacing_mm:[1,1],direct_count:0,reflected_count:1}],
    cells:[{view_index:0,row:0,column:0,position:[0,0,-5],representative_path:[[0,0,-5],[0,0,10]],path_validation:{status:'valid'},luminance_nit:1,sample_count:1,direct_count:1,reflected_count:0,min_depth:0,max_depth:0,aperture:{contract:'bounded_planar_opening_v1',id:'same-hole',position:[0,0,0],exterior_shell_certified:false}},
      {view_index:1,row:0,column:0,position:[1,0,0],representative_path:[[1,0,-5],[1,0,0],[11,0,10]],path_validation:{status:'valid'},luminance_nit:20,sample_count:1,direct_count:0,reflected_count:1,min_depth:1,max_depth:1,aperture:{contract:'bounded_planar_opening_v1',id:'same-hole',position:[2,0,1],exterior_shell_certified:false}}],
  }
  const summary=observerProbeSummary(result)!, detection=detectLeakPreviewCandidates(scene,result)
  expect(detection.candidates).toHaveLength(1)
  const candidate=detection.candidates[0], before=structuredClone(detection)
  expect(candidate.observerEvidence?.horizontalDeg).toBe(0)
  const observations=candidateObservations(candidate.id,detection.points,summary)
  expect(observations).toHaveLength(2)
  const chosen=highestObservedLuminance(candidate.id,detection.points,summary)!
  expect(chosen.angles).toEqual([45,0])
  const resolved=resolveObserverPoint(candidate,chosen,detection.points,summary)!
  expect(resolved.representativePath).toEqual(summary.cells[1].representative_path)
  expect(resolved.observerEvidence?.horizontalDeg).toBe(45)
  const receiver=createCandidateReceiver(resolved,1,scene)
  expect(receiver.view_angles?.horizontal_deg).toBe(45)
  expect(receiver.observer_reference_path).toEqual(summary.cells[1].representative_path)
  expect(detection).toEqual(before)
  expect(resolveObserverPoint(candidate,{position:[999,0,0],angles:[45,0]},detection.points,summary)).toBe(candidate)
  summary.cells[1].path_validation={status:'invalid'}
  expect(highestObservedLuminance(candidate.id,detection.points,summary)?.angles).toEqual([0,0])
  expect(highestObservedLuminance(candidate.id,[],summary)).toBeNull()
})
