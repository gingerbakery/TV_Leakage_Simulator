// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { SensorBatchDiagnostics } from './sensor-batch-diagnostics'
import type { SensorComparisonBatch } from '@/api/client'

afterEach(cleanup)
const execution = { forward_execution: {}, reverse_execution: {} }

it('keeps diagnostics collapsed and exposes weighted per-batch moments without changing samples', () => {
  // Forward contributions 2e-15,4e-15; reverse launch values 0,0,4e-15,8e-15.
  // Both ESS = 1.8 despite different forward sum/reverse mean conventions.
  const rows: SensorComparisonBatch[] = [{ ...execution, index: 1,
    forward_flux_lumen: 6e-15, forward_flux_square_sum: 20e-30, forward_hits: 2,
    reverse_flux_lumen: 3e-15, reverse_second_moment: 20e-30, reverse_sample_count: 4, reverse_positive_samples: 2,
  }, { ...execution, index: 2, forward_flux_lumen: 0, reverse_flux_lumen: 0,
    forward_flux_square_sum: 0, forward_hits: 0, reverse_second_moment: 0, reverse_sample_count: 4, reverse_positive_samples: 0 }]
  const before = structuredClone(rows)
  render(<SensorBatchDiagnostics rows={rows} />)
  expect(screen.queryByRole('table')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '회차별 결과 펼치기' }))
  const table = screen.getByRole('table', { name: '회차별 광량 비교' })
  expect(within(table).getByText('6.00000e-15')).toBeTruthy()
  expect(within(table).getByText('3.00000e-15')).toBeTruthy()
  expect(within(table).getByText('1.8 / 1.8')).toBeTruthy()
  expect(within(table).getByText('0.0 / 0.0')).toBeTruthy()
  expect(screen.getByText('양수 회차 · 최대 회차 비중 F 1/2 · 100.0% / R 1/2 · 100.0%')).toBeTruthy()
  expect(rows).toEqual(before)
})

it('shows missing legacy values as unknown rather than zero or agreement', () => {
  render(<SensorBatchDiagnostics rows={[execution]} />)
  fireEvent.click(screen.getByRole('button', { name: '회차별 결과 펼치기' }))
  expect(screen.getByText('양수 회차 · 최대 회차 비중 F — / R —')).toBeTruthy()
  expect(within(screen.getByRole('table')).getAllByText('— / —')).toHaveLength(2)
  expect(screen.queryByText('일치')).toBeNull()
})
