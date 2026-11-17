import { expect, it } from 'vitest'
import type { SensorComparisonReport } from '@/api/client'
import { linkedPathEvidence, linkedEvidenceAdvice } from './linked-path-evidence'
import { comparisonValidationAdvice } from './validation-guidance'

function fixture(): SensorComparisonReport {
  return { complete:true, gpu_execution_proven:true, verdict:'inconclusive', reason:'zero_samples_not_zero_light_proof',
    sensor:{receiver_id:'r'},options:{receiver_id:'r',batches:4,forward_samples:32,reverse_samples:32,tolerance_percent:5},
    forward_hits:4,forward_effective_samples:1,reverse_positive_samples:0,reverse_effective_samples:0,
    batches:Array.from({length:4},(_,i)=>({index:i+1,reverse_sample_count:32,forward_execution:{},reverse_execution:{}})),
    path_evidence:Array.from({length:4},(_,i)=>({index:i+1,detected_paths:{recorded_count:1,actual_hit_count:1,complete:true,truncated:false,
      samples:[{ray_id:`ray${i}`,reflection_count:2,selected_point_radius_mm:1,selected_point_min_reflection_distance_mm:2,selected_point_min_segment_distance_mm:2}]},
      policy_pair:{checked_count:1,records:[{ray_id:`ray${i}`,state:'verified',reason:'energy_cutoff'}]}})),
    reverse_diagnostics:Array.from({length:4},(_,i)=>({index:i+1,stages:{available:true,sample_count:32,missing_records:0,
      counts:{positive:0,source_not_encountered:32,source_emission_zero:0,contribution_zero:0,camera_roi:0}}})),
  }
}
it('connects verified ray stops to a bounded next action without prescribing more samples',()=>{
  const report=fixture()
  expect(linkedPathEvidence(report)).toMatchObject({valid:true,recorded:4,near:0,checked:4,stopped:4,reverseValid:true,samples:128})
  const advice=comparisonValidationAdvice(report,true)
  expect(advice.title).toBe('확인된 광선 종료 지점 확인')
  expect(advice.samples).toBeUndefined()
  expect(advice.steps.join(' ')).toContain('확인한 광선에 한정')
})
it('does not turn unverified pairs or unmatched ray IDs into an energy cause',()=>{
  const report=fixture()
  report.path_evidence!.forEach(row=>row.policy_pair!.records[0].state='unverified')
  expect(linkedPathEvidence(report).stopped).toBe(0)
  expect(linkedEvidenceAdvice(report)?.title).toBe('검출 경로와 선택점 연결 확인')
  report.path_evidence!.forEach(row=>{row.policy_pair!.records[0].state='verified';row.policy_pair!.records[0].ray_id='other'})
  expect(linkedPathEvidence(report).stopped).toBe(0)
})
it('rejects duplicate batches, inconsistent hit totals and missing reverse categories',()=>{
  const report=fixture()
  report.path_evidence![1].index=1
  expect(linkedPathEvidence(report).valid).toBe(false)
  report.reverse_diagnostics![0].stages.counts!.source_not_encountered=0
  expect(linkedPathEvidence(report).reverseValid).toBe(false)
  const other=fixture();other.forward_hits=5
  expect(linkedPathEvidence(other).valid).toBe(false)
})
it('separates missing selected-point distances from no nearby reflection',()=>{
  const report=fixture()
  report.path_evidence!.forEach(row=>{row.policy_pair=undefined;row.detected_paths.samples[0].selected_point_radius_mm=null})
  expect(linkedPathEvidence(report).pointChecked).toBe(0)
  expect(linkedEvidenceAdvice(report)?.title).toBe('역추적 광원 연결 경로 확인')
})
it('distinguishes source emission zero and prevents stale/incomplete guidance',()=>{
  const report=fixture()
  report.path_evidence=[]
  report.reverse_diagnostics!.forEach(row=>{row.stages.counts!.source_not_encountered=0;row.stages.counts!.source_emission_zero=32})
  expect(linkedEvidenceAdvice(report)?.title).toBe('광원 도달 후 발광 조건 확인')
  report.complete=false
  expect(linkedEvidenceAdvice(report)).toBeNull()
  expect(comparisonValidationAdvice(report,false).title).not.toBe('광원 도달 후 발광 조건 확인')
})

it('does not mistake a segment near the point for a missing connection when no reflection is near',()=>{
  const report=fixture()
  report.path_evidence!.forEach(row=>{row.policy_pair=undefined;row.detected_paths.samples[0].selected_point_min_segment_distance_mm=.5})
  expect(linkedPathEvidence(report)).toMatchObject({near:0,nearSegment:4})
  expect(linkedEvidenceAdvice(report)?.title).toBe('역추적 광원 연결 경로 확인')
})
