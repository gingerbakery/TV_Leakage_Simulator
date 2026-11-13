import { expect, it } from 'vitest'
import type { SensorComparisonReport } from '@/api/client'
import { comparisonConclusion, comparisonValidationAdvice, pathValidationAdvice } from './validation-guidance'

function report(patch:Partial<SensorComparisonReport>={}):SensorComparisonReport {
  return {sensor:{receiver_id:'sensor'},options:{receiver_id:'sensor',batches:8,forward_samples:16384,reverse_samples:1024,tolerance_percent:5},
    verdict:'inconclusive',reason:'insufficient_effective_samples',gpu_execution_proven:true,
    forward_hits:100,reverse_positive_samples:1000,forward_effective_samples:16.2,reverse_effective_samples:378,...patch}
}
it('does not recommend sample increases for an isolated replay execution error',()=>{
  const advice=comparisonValidationAdvice(report({reason:'reference_replay_failed'}),true)
  expect(advice.title).toBe('재생 실행 오류 확인 후 재비교')
  expect(advice.samples).toBeUndefined()
})

it('prioritizes the deficient direction in the reported 16.2/378 case, without changing the other side', () => {
  const advice=comparisonValidationAdvice(report(),true)
  expect(advice.title).toBe('F 표본 증가 후 재비교')
  expect(advice.samples).toEqual({batches:8,forward_samples:65536,reverse_samples:1024})
  expect(advice.steps.join(' ')).toContain('개선이 없으면')
  const reverse=comparisonValidationAdvice(report({forward_effective_samples:378,reverse_effective_samples:16.2}),true)
  expect(reverse.samples).toEqual({batches:8,forward_samples:16384,reverse_samples:4096})
})

it('caps valid recommendations at actual per-run and total budgets and stops at the limit', () => {
  const capped=report()
  capped.options.forward_samples=65536
  expect(comparisonValidationAdvice(capped,true).samples?.forward_samples).toBe(250000)
  capped.options.forward_samples=240000; capped.options.batches=16
  const next=comparisonValidationAdvice(capped,true).samples!
  expect(next.batches*(next.forward_samples+next.reverse_samples)).toBeLessThanOrEqual(4000000)
  expect(next.reverse_samples).toBe(1024)
  capped.options.forward_samples=250000; capped.options.reverse_samples=100000; capped.options.batches=8
  expect(comparisonValidationAdvice(capped,true).samples).toBeUndefined()
})

it('directs sparse batches to path diagnostics instead of automatically increasing repeats', () => {
  const value=report({reason:'sparse_independent_batches',forward_effective_samples:80})
  expect(comparisonValidationAdvice(value,true).title).toBe('회차별 검출·경로 확인')
  expect(comparisonValidationAdvice(value,true).samples).toBeUndefined()
  value.options.batches=16
  expect(comparisonValidationAdvice(value,true).samples).toBeUndefined()
})

it('gives a conclusion and diagnostic action for the reported single detection and zero reverse contribution', () => {
  const value = report({ reason:'zero_samples_not_zero_light_proof', forward_hits:1, reverse_positive_samples:0,
    forward_effective_samples:1, reverse_effective_samples:0 })
  expect(comparisonConclusion(value,true)).toBe('미확정 · 양방향 검출 부족')
  expect(comparisonValidationAdvice(value,true).title).toBe('대표 경로·순방향 진단 확인')
  expect(comparisonValidationAdvice(value,true).samples).toBeUndefined()
  expect(comparisonConclusion(value,false)).toBe('판정 불가 · 이전 조건의 결과')
})

it('withholds a passing conclusion for incomplete, invalid, or unproven comparison results', () => {
  const value = report({ verdict:'match', reason:'difference_interval_inside_tolerance' })
  expect(comparisonConclusion(value,true)).toBe('일치 · 허용 범위 내')
  expect(comparisonConclusion({...value,gpu_execution_proven:false},true)).toBe('판정 불가 · 필수 실행 증거 누락')
  expect(comparisonConclusion({...value,reason:'invalid_reference_path'},true)).toBe('판정 불가 · 대표 경로 불일치')
  expect(comparisonConclusion({...value,reason:'incomplete_batches'},true)).toBe('미완료 · 독립 반복 미완료')
})

it('does not recommend more samples for stale conditions, missing execution proof, mismatch or missing diagnostics', () => {
  expect(comparisonValidationAdvice(report(),false).samples).toBeUndefined()
  expect(comparisonValidationAdvice(report({gpu_execution_proven:false}),true).title).toBe('GPU 실행 증거 확인')
  const mismatch=comparisonValidationAdvice(report({verdict:'mismatch',reason:'difference_interval_outside_tolerance'}),true)
  expect(mismatch.samples).toBeUndefined()
  expect(mismatch.steps.join(' ')).toContain('허용 차이를 넓혀 통과시키지')
  expect(comparisonValidationAdvice(report({forward_effective_samples:undefined}),true).samples).toBeUndefined()
  expect(comparisonValidationAdvice(report({reason:'stopped',gpu_execution_proven:false}),true).title).toBe('독립 반복 완료 후 판정')
})

it('distinguishes path existence from flux convergence and gives reason-specific repairs', () => {
  expect(pathValidationAdvice(true,'reference_path_valid').steps.join(' ')).toContain('광량 일치를 보장하지')
  expect(pathValidationAdvice(false,'reflection_depth_mismatch').steps[0]).toContain('Max reflections')
  expect(pathValidationAdvice(false,'occluded_segment').steps[0]).toContain('Block')
  expect(pathValidationAdvice(false,'receiver_position_direction_acceptance_or_roi_mismatch').steps[0]).toContain('0mm')
})
