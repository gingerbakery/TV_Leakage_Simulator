import { expect, it } from 'vitest'
import type { SensorComparisonReport } from '@/api/client'
import { comparisonEvidence, selectedPointEvidence } from './comparison-evidence'
import { comparisonConclusion, comparisonValidationAdvice } from './validation-guidance'

function fixture(): SensorComparisonReport {
  const report: SensorComparisonReport = { sensor:{receiver_id:'sensor'},
    options:{receiver_id:'sensor',batches:8,forward_samples:16384,reverse_samples:1024,tolerance_percent:5},
    verdict:'inconclusive',reason:'insufficient_effective_samples',gpu_execution_proven:true,
    forward_hits:35,reverse_positive_samples:1,forward_effective_samples:1,reverse_effective_samples:1 }
  const hits=[7,7,2,2,5,6,3,3]
  report.forward_diagnostics=hits.map((count,index) => ({index:index+1,report:{
    contract:'forward_receiver_segments_v1',complete:true,primary_ray_count:16384,
    escaped_segments:415,escaped_segment_flux_lumen:4e-4,termination:{},receivers:[{
      receiver_id:'sensor',segments_tested:1000,actual_receiver_hits:count,actual_receiver_flux_lumen:1e-7,
      arrived_count:count,consistent:true,
      counts:{parallel:0,behind_origin:500,outside_area:500-count,occluded:0,other_receiver_first:0,back_face:0,outside_acceptance:0,outside_roi:0,accepted:count},
      segment_flux_lumen:{parallel:0,behind_origin:1,outside_area:1,occluded:0,other_receiver_first:0,back_face:0,outside_acceptance:0,outside_roi:0,accepted:1e-7},
      reference_sampling:{contract:'reference_origin_neighborhood_v1',point:[0,0,0],radius_mm:1,segments_started:0,min_origin_distance_mm:2.854,
        counts:{parallel:0,behind_origin:0,outside_area:0,occluded:0,other_receiver_first:0,back_face:0,outside_acceptance:0,outside_roi:0,accepted:0},examples:{}}
    }]}}))
  return report
}
function row(report: SensorComparisonReport, index=0) {
  return (report.forward_diagnostics![index].report as {receivers:Array<{
    receiver_id:string;consistent:boolean;segments_tested:number;counts:Record<string,number>;reference_sampling:{point:number[];segments_started:number;counts:Record<string,number>}
  }>}).receivers[0]
}

it('identifies both effective-sample shortages in the reported 35/1 case and aggregates all eight selected-point records', () => {
  const report=fixture()
  const findings=comparisonEvidence(report,true)
  expect(findings[0].status).toBe('유효표본 부족')
  expect(findings[1].status).toBe('양수·유효표본 부족')
  expect(findings[2].status).toBe('주변 출발 표본 없음')
  expect(selectedPointEvidence(report)).toMatchObject({complete:true,checked:8,started:0,nearest:2.854})
  expect(comparisonConclusion(report,true)).toBe('미확정 · 양방향 유효 표본 부족')
  const advice=comparisonValidationAdvice(report,true)
  expect(advice.title).toBe('반사 적은 Point에서 기본 검출 확인')
  expect(advice.samples).toBeUndefined()
  expect(advice.steps.join(' ')).toContain('전체 8회')
})

it.each(['missing','duplicate','inconsistent','different_point','different_sensor','different_total'] as const)('withholds selected-point diagnosis for %s evidence', mode => {
  const report=fixture()
  if (mode==='missing') report.forward_diagnostics!.pop()
  if (mode==='duplicate') report.forward_diagnostics![7].index=1
  if (mode==='inconsistent') row(report).consistent=false
  if (mode==='different_point') row(report).reference_sampling.point=[1,0,0]
  if (mode==='different_sensor') row(report).receiver_id='other'
  if (mode==='different_total') report.forward_hits=36
  expect(selectedPointEvidence(report).complete).toBe(false)
  expect(comparisonEvidence(report,true)[2].status).toBe('판정 보류')
  expect(comparisonValidationAdvice(report,true).steps.join(' ')).not.toContain('전체 8회에서')
})

it('uses selected-point sensor exclusions, not global occlusion totals, for the next action', () => {
  const report=fixture(), first=row(report)
  first.counts.outside_roi=2
  first.counts.occluded=1171
  first.segments_tested+=1173
  first.reference_sampling.counts.outside_roi=2
  first.reference_sampling.segments_started=2
  expect(comparisonEvidence(report,true)[2].status).toBe('도달 후 센서 조건 제외')
  expect(comparisonValidationAdvice(report,true).title).toBe('표시된 센서 제외 조건 확인')
  expect(comparisonValidationAdvice(report,true).samples).toBeUndefined()
})

it('does not diagnose stale, stopped, or unproven reports and keeps missing metrics explicit', () => {
  const report=fixture()
  expect(comparisonEvidence(report,false)).toEqual([])
  expect(comparisonEvidence({...report,reason:'stopped'},true)).toEqual([])
  expect(comparisonEvidence({...report,gpu_execution_proven:false},true)).toEqual([])
  expect(comparisonEvidence({...report,forward_effective_samples:undefined},true)[0].status).toBe('기록 부족')
})
