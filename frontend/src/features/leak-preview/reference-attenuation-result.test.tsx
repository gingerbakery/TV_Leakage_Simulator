// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ReferenceAttenuationResult } from './reference-attenuation-result'

afterEach(cleanup)
function fixture() {
  return { contract:'reference_reflectance_chain_v1', state:'complete', reason:'reference_path_valid',
    expected_reflections:2, final_reflectance_ratio:.36, final_log10_ratio:Math.log10(.36),
    effective_min_energy_lumen:.5, first_below_min_energy_reflection:2, largest_loss_reflection:1,
    steps:[1,2].map(index => ({ reflection_index:index, component_name:`Part ${index}`, profile_id:'mirror',
      scatter_model:'specular', effective_reflectance:.6, incoming_nominal_packet_lumen:index===1?1:.6,
      outgoing_nominal_packet_lumen:index===1?.6:.36, cumulative_log10_ratio:Math.log10(.6**index),
      directional_brdf_per_sr:0, directional_delta_weight:.6 })) }
}

it('shows cumulative reflection loss and the first threshold crossing with details closed', () => {
  render(<ReferenceAttenuationResult value={fixture()} />)
  expect(screen.getByText(/반사 2회 · 반사율 누적 잔존비/)).toBeTruthy()
  expect(screen.getByText('기준 패킷이 최소 에너지 미만 · 첫 2회차')).toBeTruthy()
  expect(screen.getByText('가장 큰 반사 손실 · 1회차')).toBeTruthy()
  expect(screen.getByRole('table').closest('details')?.open).toBe(false)
  expect(screen.queryByText(/센서 수광량/)).toBeNull()
})

it('retains logarithmic loss when the nominal packet underflows', () => {
  const report=fixture()
  report.final_reflectance_ratio=0; report.final_log10_ratio=-400
  render(<ReferenceAttenuationResult value={report} />)
  expect(screen.getByText('-400.00')).toBeTruthy()
})

it('withholds a completed loss verdict for invalid, failed, and malformed audits', () => {
  const report=fixture(); report.state='invalid'; report.reason='reflection_material_mismatch'
  const view=render(<ReferenceAttenuationResult value={report} />)
  expect(screen.getByText(/반사 손실 판정 보류/)).toBeTruthy()
  expect(screen.queryByText(/반사 2회 · 반사율 누적 잔존비/)).toBeNull()
  view.rerender(<ReferenceAttenuationResult value={{ contract:'reference_reflectance_chain_v1',state:'failed',reason:'attenuation_audit_failed',steps:[] }} />)
  expect(screen.getByText(/반사 손실 판정 보류/)).toBeTruthy()
  view.rerender(<ReferenceAttenuationResult value={{ ...fixture(), final_log10_ratio:Infinity }} />)
  expect(screen.getByText(/반사 손실 기록 없음/)).toBeTruthy()
  view.rerender(<ReferenceAttenuationResult value={undefined} />)
  expect(screen.getByText(/반사 손실 기록 없음/)).toBeTruthy()
})
