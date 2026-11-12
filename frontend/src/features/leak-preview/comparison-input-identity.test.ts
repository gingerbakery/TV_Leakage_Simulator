import { expect,it } from 'vitest'
import type { RayTraceRequest } from '@/api'
import { sameComparisonInput } from './comparison-input-identity'
import { buildRayTraceRequest,createDatumEmitter,createDatumReceiver } from '@/features/raytracing/ray-tracing-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { defaultRayTraceConfig } from '@/stores/workspace-store'

const request=()=>buildRayTraceRequest({scene:createSceneFixture(),projectName:'comparison',emitters:[createDatumEmitter('e',[0,0,0],[0,0,0])],
  receivers:[createDatumReceiver('r',[0,0,10],[0,0,0])],materialAssignments:[],transformRules:[],excludedComponentIds:[],deletedComponentIds:[],roiScopes:[],config:defaultRayTraceConfig})
it('requires a full frozen request and ignores only the project display name',()=>{
  const input=request(), saved=structuredClone(input)
  expect(sameComparisonInput(undefined,input)).toBe(false)
  expect(sameComparisonInput(saved,input)).toBe(true)
  saved.project_name='renamed'
  expect(sameComparisonInput(saved,input)).toBe(true)
  const reordered=Object.fromEntries(Object.entries(saved).reverse()) as RayTraceRequest
  expect(sameComparisonInput(reordered,input)).toBe(true)
})
it('rejects changed pose, normal flip, source power, ROI, geometry and termination with unchanged sensor dimensions',()=>{
  const input=request()
  const changes:Array<(r:RayTraceRequest)=>void>=[
    r=>{r.receivers[0].center[0]+=1},r=>{r.receivers[0].normal[0]=1},r=>{r.receivers[0].normal_flip=!r.receivers[0].normal_flip},
    r=>{r.emitters[0].power_lumen+=1},r=>{r.config.max_depth+=1},r=>{r.scene_token+='-changed'},
    r=>{r.excluded_component_ids=[1]},r=>{r.analysis_roi={face_indices:[1],clip_boxes:[]}},
  ]
  for(const change of changes){const saved=structuredClone(input);change(saved);expect(sameComparisonInput(saved,input)).toBe(false)}
})
