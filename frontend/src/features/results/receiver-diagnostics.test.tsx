// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ReceiverDiagnostics } from './receiver-diagnostics'
import { mergeReceiverDiagnostics, readReceiverDiagnostics, receiverDiagnosticLabels, type ReceiverDiagnosticReport } from './receiver-diagnostics-model'

afterEach(cleanup)
function fixture(): ReceiverDiagnosticReport {
  const counts = Object.fromEntries(Object.keys(receiverDiagnosticLabels).map(key=>[key,0])) as ReceiverDiagnosticReport['receivers'][0]['counts']
  counts.outside_roi=2
  return { contract:'forward_receiver_segments_v1',complete:true,primary_ray_count:10,escaped_segments:2,escaped_segment_flux_lumen:1,
    termination:{energy_cutoff_count:3,depth_limit_count:4,roulette_terminated_count:0},receivers:[{
      receiver_id:'sensor',counts,segment_flux_lumen:{...counts},segments_tested:2,arrived_count:2,actual_receiver_hits:0,actual_receiver_flux_lumen:0,consistent:true,
    }] }
}
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
