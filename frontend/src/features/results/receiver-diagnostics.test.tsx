// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ReceiverDiagnostics } from './receiver-diagnostics'
import { mergeReceiverDiagnostics, readReceiverDiagnostics, readReferenceSampling, referenceSamplingConclusion, receiverDiagnosticLabels, type ReceiverDiagnosticReport, type ReferenceSampling } from './receiver-diagnostics-model'

afterEach(cleanup)
function fixture(): ReceiverDiagnosticReport {
  const counts = Object.fromEntries(Object.keys(receiverDiagnosticLabels).map(key=>[key,0])) as ReceiverDiagnosticReport['receivers'][0]['counts']
  counts.outside_roi=2
  return { contract:'forward_receiver_segments_v1',complete:true,primary_ray_count:10,escaped_segments:2,escaped_segment_flux_lumen:1,
    termination:{energy_cutoff_count:3,depth_limit_count:4,roulette_terminated_count:0},receivers:[{
      receiver_id:'sensor',counts,segment_flux_lumen:{...counts},segments_tested:2,arrived_count:2,actual_receiver_hits:0,actual_receiver_flux_lumen:0,consistent:true,
    }] }
}
function localFixture(): ReferenceSampling {
  return { contract:'reference_origin_neighborhood_v1', point:[0,0,0], radius_mm:1,
    segments_started:0, min_origin_distance_mm:5,
    counts:Object.fromEntries(Object.keys(receiverDiagnosticLabels).map(key=>[key,0])) as ReferenceSampling['counts'], examples:{} }
}

it('distinguishes whole-scene occlusion from absence of selected-point origins', () => {
  const report=fixture(), row=report.receivers[0]
  row.counts.occluded=1171
  row.reference_sampling=localFixture()
  render(<ReceiverDiagnostics value={report} />)
  expect(screen.getByText('선택점 주변 출발 표본 0 · 상류 경로 확인 필요')).toBeTruthy()
  expect(screen.getByText('전체 장면 구간')).toBeTruthy()
  expect(screen.getByText(/가장 가까운 출발점 5.000 mm/)).toBeTruthy()
  expect(screen.queryByText(/전체 대표 경로의 재현/)).toBeNull()
})

it('reports local direction misses separately from a sampled obstruction and shows actual component evidence', () => {
  const local=localFixture()
  local.segments_started=2; local.counts.outside_area=2
  expect(referenceSamplingConclusion(local)).toBe('선택점 주변 출발 있음 · 센서 영역 연결 표본 0')
  local.counts.outside_area=1; local.counts.occluded=1
  local.examples.occluded={origin:[0,0,.2],direction:[0,0,1],origin_distance_mm:.2,
    obstruction_point:[0,0,3.2],obstruction_face_index:4,obstruction_component_id:42,obstruction_component_name:'Cover'}
  const report=fixture(); report.receivers[0].reference_sampling=local
  render(<ReceiverDiagnostics value={report} />)
  expect(screen.getByText('선택점 주변의 센서 방향 표본이 형상에 차폐됨')).toBeTruthy()
  expect(screen.getByText(/차폐 Cover/)).toBeTruthy()
  expect(screen.getByText(/차폐 Cover/).closest('details')?.open).toBe(false)
})

it('does not infer a selected-point cause from old or inconsistent local data', () => {
  render(<ReceiverDiagnostics value={fixture()} />)
  expect(screen.getByText('선택점 주변 기록 없음 · 선택점 원인 판정 불가')).toBeTruthy()
  const local=localFixture(); local.segments_started=1
  expect(readReferenceSampling(local)).toBeUndefined()
})

it('merges local evidence only for the same point and keeps the nearest actual sample', () => {
  const a=fixture(), b=fixture()
  const first=localFixture(), second=localFixture()
  first.segments_started=1; first.counts.outside_area=1
  second.segments_started=2; second.counts.accepted=2; second.min_origin_distance_mm=.1
  a.receivers[0].reference_sampling=first; b.receivers[0].reference_sampling=second
  const merged=mergeReceiverDiagnostics(a,b,{})!.receivers[0].reference_sampling!
  expect(merged.segments_started).toBe(3)
  expect(merged.counts.accepted).toBe(2)
  expect(merged.min_origin_distance_mm).toBe(.1)
  second.point=[1,0,0]
  expect(mergeReceiverDiagnostics(a,b,{})!.receivers[0].reference_sampling).toBeUndefined()
  delete b.receivers[0].reference_sampling
  expect(mergeReceiverDiagnostics(a,b,{})!.receivers[0].reference_sampling).toBeUndefined()
})
it('distinguishes arrival followed by rejection and keeps explanations closed', () => {
  render(<ReceiverDiagnostics value={fixture()} />)
  expect(screen.getByText(/sensor · 도달 2 · 검출 0/)).toBeTruthy()
  expect(screen.queryByText(/各|각 실제 진행 구간/)).toBeNull()
  fireEvent.click(screen.getByText(/sensor · 도달/))
  expect(screen.getByText('센서 도달 후 조건 제외')).toBeTruthy()
  expect(screen.getByText('ROI 제외')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'순방향 진단 결과 도움말'}))
  expect(screen.getByText(/각 실제 진행 구간/)).toBeTruthy()
})
it('labels partial results and inconsistent evidence without confirming a cause', () => {
  const report=fixture(); report.complete=false; report.receivers[0].consistent=false
  render(<ReceiverDiagnostics value={report} />)
  expect(screen.getByText('부분 진단 · 완료 구간 기준')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('판정 보류')
  expect(screen.queryByText('센서 도달 후 조건 제외')).toBeNull()
})
it('does not invent diagnostics for old or malformed results', () => {
  expect(readReceiverDiagnostics({})).toBeNull()
  const bad=fixture(); bad.receivers[0].counts.accepted=NaN
  expect(readReceiverDiagnostics(bad)).toBeNull()
  render(<ReceiverDiagnostics value={undefined} requested />)
  expect(screen.getByText(/진단 정보 없음/)).toBeTruthy()
})
it('adds repeated segment counts and weights flux by primary counts', () => {
  const a=fixture(), b=fixture(); b.primary_ray_count=30; b.escaped_segment_flux_lumen=3
  b.receivers[0].segment_flux_lumen.outside_roi=6
  const merged=mergeReceiverDiagnostics(a,b,{depth_limit_count:8})!
  expect(merged.primary_ray_count).toBe(40)
  expect(merged.receivers[0].counts.outside_roi).toBe(4)
  expect(merged.receivers[0].segment_flux_lumen.outside_roi).toBe(5)
  expect(merged.escaped_segment_flux_lumen).toBe(2.5)
  expect(mergeReceiverDiagnostics(undefined,b,{})).toBeUndefined()
})
