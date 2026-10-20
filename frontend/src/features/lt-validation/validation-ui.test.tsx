// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResultPanel } from '@/features/results/result-panel'
import { workspaceStore } from '@/stores'
import { compareValidationCases, importValidationFile } from './client'
import { LtValidationDialog } from './validation-dialog'
import { ManualEditor } from './manual-editor'
import { ValidationPlots, type ComparisonPlotData } from './validation-plots'
import type { ValidationCase, ValidationCaseResult, ValidationDataset } from './types'

vi.mock('./client', () => ({ importValidationFile: vi.fn(), compareValidationCases: vi.fn() }))

const plotData: ComparisonPlotData = {
  uEdges: [-1, 0, 1], vEdges: [-1, 0, 1], simulator: [[10, 20], [30, 40]], lighttools: [[15, 15], [20, 50]], difference: [[-5, 5], [10, -10]],
  uSimulator: [40, 60], uLighttools: [35, 65], uDifference: [5, -5], vSimulator: [30, 70], vLighttools: [30, 70], vDifference: [0, 0],
  simulatorFlux: [[0.1, 0.2], [0.3, 0.4]], simulatorIlluminance: [[100000, 200000], [300000, 400000]],
}

function dataset(kind: ValidationDataset['kind']): ValidationDataset {
  return { schema: 'lt-validation.v1', kind, filename: kind === 'bitsam' ? 'sample.bitsam' : 'sample.lts', sources: [], issues: [], receivers: [
    { id: 'front', name: 'Front', quantity: 'photometric', raw: true, flux_lm: [[1, 2], [3, 4]], u_edges_mm: [-1, 0, 1], v_edges_mm: [-1, 0, 1], center_mm: null, u_axis: null, v_axis: null, normal: null, acceptance_angle_deg: null, aperture: null, total_flux_lm: 10, issues: [] },
    { id: 'rear', name: 'Rear', quantity: 'unknown', raw: null, flux_lm: null, u_edges_mm: null, v_edges_mm: null, center_mm: null, u_axis: null, v_axis: null, normal: null, acceptance_angle_deg: null, aperture: null, total_flux_lm: null, issues: [] },
  ] }
}

function caseResult(item: ValidationCase, ready = true): ValidationCaseResult {
  return { id: item.id, name: item.name, status: ready ? 'ready' : 'blocked', issues: ready ? [] : [{ code: 'unknown_pose', severity: 'error', message: '리시버 위치 미확인' }],
    sim_total_lm: 1, lt_total_lm: 1.1, flux_deviation_pct: -9.0909, pearson: 0.91234, overlap_pct: 85,
    sim_relative_pct: 100, lt_relative_pct: 100, sim_change_pct: 0, lt_change_pct: 0,
    plots: { u_mm: [-0.5, 0.5], v_mm: [-0.5, 0.5], u_edges_mm: plotData.uEdges, v_edges_mm: plotData.vEdges, sim_pct: plotData.simulator, lt_pct: plotData.lighttools, difference_pp: plotData.difference, column_sim_pct: plotData.uSimulator, column_lt_pct: plotData.uLighttools, column_difference_pp: plotData.uDifference, row_sim_pct: plotData.vSimulator, row_lt_pct: plotData.vLighttools, row_difference_pp: plotData.vDifference },
  }
}

beforeEach(() => {
  vi.mocked(importValidationFile).mockImplementation(async (_file, filename) => dataset(filename.endsWith('.bitsam') ? 'bitsam' : 'lighttools'))
  vi.mocked(compareValidationCases).mockImplementation(async (cases, baselineId) => ({ cases: cases.map((item) => caseResult(item)), baseline_id: baselineId }))
})
afterEach(() => { cleanup(); vi.resetAllMocks(); workspaceStore.getState().actions.resetWorkspace() })

describe('LT validation UI', () => {
  it('opens from the empty Result panel without CAD or an analysis result', () => {
    render(<ResultPanel onOpenAnalysis={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'LT 검증' }))
    expect(screen.getByRole('dialog', { name: 'LT 결과 검증' })).not.toBeNull()
    expect(screen.getByLabelText('Simulator 파일')).not.toBeNull()
    expect(screen.getByLabelText('LightTools 파일')).not.toBeNull()
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('uploads paired files, selects receivers, compares arbitrary cases, and invalidates stale metrics on edits', async () => {
    render(<LtValidationDialog open onOpenChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Simulator 파일'), { target: { files: [new File(['bitsam'], 'test.bitsam')] } })
    await screen.findByText('sample.bitsam')
    fireEvent.change(screen.getByLabelText('LightTools 파일'), { target: { files: [new File(['lts'], 'test.lts')] } })
    await screen.findByText('sample.lts')
    fireEvent.change(screen.getByLabelText('LightTools receiver'), { target: { value: 'rear' } })
    fireEvent.click(screen.getByRole('button', { name: 'Case 추가' }))
    fireEvent.change(screen.getByLabelText('Case 이름'), { target: { value: 'Panel B' } })
    const caseSelector = screen.getByLabelText('검증 Case') as HTMLSelectElement
    const secondId = caseSelector.value
    fireEvent.change(screen.getByLabelText('기준 Case'), { target: { value: secondId } })
    fireEvent.click(screen.getByRole('button', { name: '전체 비교' }))
    await screen.findByRole('table', { name: 'LT 매칭 결과 표' })
    const args = vi.mocked(compareValidationCases).mock.calls[0]
    expect(args[0]).toHaveLength(2)
    expect(args[0][0].lt_receiver_id).toBe('rear')
    expect(args[0][1].name).toBe('Panel B')
    expect(args[1]).toBe(secondId)
    expect(screen.getByRole('img', { name: 'Simulator 히트맵' })).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Case 이름'), { target: { value: 'Panel C' } })
    expect(screen.queryByRole('table', { name: 'LT 매칭 결과 표' })).toBeNull()
    expect(screen.queryByRole('img', { name: 'Simulator 히트맵' })).toBeNull()
  })

  it('does not render blocked pair metrics or plots even if a response contains numbers', async () => {
    vi.mocked(compareValidationCases).mockImplementation(async (cases, baselineId) => ({ cases: cases.map((item) => caseResult(item, false)), baseline_id: baselineId }))
    render(<LtValidationDialog open onOpenChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '전체 비교' }))
    const table = await screen.findByRole('table', { name: 'LT 매칭 결과 표' })
    expect(screen.getByText('리시버 위치 미확인')).not.toBeNull()
    expect(within(table).queryByText('0.91234')).toBeNull()
    expect(within(table).queryByText('85')).toBeNull()
    expect(within(table).getByText('1.1')).not.toBeNull()
    expect(screen.queryByRole('img', { name: 'Simulator 히트맵' })).toBeNull()
  })

  it('retains applied case data while the dialog is closed', async () => {
    const view = render(<LtValidationDialog open onOpenChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Case 이름'), { target: { value: 'Retained' } })
    view.rerender(<LtValidationDialog open={false} onOpenChange={vi.fn()} />)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    view.rerender(<LtValidationDialog open onOpenChange={vi.fn()} />)
    expect((screen.getByLabelText('Case 이름') as HTMLInputElement).value).toBe('Retained')
  })

  it('restores original filenames from a validated session and preserves small flux totals', async () => {
    const sim = dataset('bitsam')
    const lt = dataset('lighttools')
    sim.filename = 'Before-original.bitsam'
    lt.filename = 'Before-original.lts'
    sim.receivers[0].total_flux_lm = 0.0012345
    lt.receivers[0].total_flux_lm = 0.0042
    vi.mocked(importValidationFile)
      .mockResolvedValueOnce({ ...sim, filename: 'session-data.json' })
      .mockResolvedValueOnce({ ...lt, filename: 'session-data.json' })
    vi.mocked(compareValidationCases).mockImplementation(async (cases, baselineId) => ({
      cases: cases.map((item) => ({ ...caseResult(item), sim_total_lm: 0.0012345, lt_total_lm: 0.0042 })), baseline_id: baselineId,
    }))
    const session = { schema: 'lt-validation-session.v1', baseline_id: 'stored', cases: [{ id: 'stored', name: 'Stored', sim, lt, sim_receiver_id: 'front', lt_receiver_id: 'front' }] }
    const file = new File([JSON.stringify(session)], 'saved-session.json', { type: 'application/json' })
    Object.defineProperty(file, 'text', { value: async () => JSON.stringify(session) })
    render(<LtValidationDialog open onOpenChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('LT 검증 세션 파일'), { target: { files: [file] } })
    expect(await screen.findByText('Before-original.bitsam')).not.toBeNull()
    expect(screen.getByText('Before-original.lts')).not.toBeNull()
    expect(screen.getByText('조건 · 2행 × 2열 · 0.0012345 lm')).not.toBeNull()
    expect(screen.getByText('조건 · 2행 × 2열 · 0.0042 lm')).not.toBeNull()
    expect(vi.mocked(importValidationFile).mock.calls.map((call) => call[1])).toEqual(['session-data.json', 'session-data.json'])
    fireEvent.click(screen.getByRole('button', { name: '전체 비교' }))
    const table = await screen.findByRole('table', { name: 'LT 매칭 결과 표' })
    expect(within(table).getByText('0.0012345')).not.toBeNull()
    expect(within(table).getByText('0.0042')).not.toBeNull()
  })

  it('keeps manual input unknown and refuses an unconfirmed unlabeled grid', async () => {
    const onSave = vi.fn()
    render(<ManualEditor dataset={null} receiverId={null} busy={false} onSave={onSave} onCancel={vi.fn()} />)
    expect((screen.getByLabelText('LT 데이터 상태') as HTMLSelectElement).value).toBe('')
    expect((screen.getByLabelText('리시버 중심 X, Y, Z (mm)') as HTMLInputElement).value).toBe('')
    fireEvent.change(screen.getByLabelText('LT 표 단위'), { target: { value: 'flux_lm' } })
    fireEvent.change(screen.getByLabelText('LT 원시 mesh 표'), { target: { value: '1 2\n3 4' } })
    fireEvent.change(screen.getByLabelText('U 셀 경계 (mm)'), { target: { value: '0 1 2' } })
    fireEvent.change(screen.getByLabelText('V 셀 경계 (mm)'), { target: { value: '0 1 2' } })
    fireEvent.click(screen.getByRole('button', { name: '입력 적용' }))
    expect(await screen.findByRole('alert')).not.toBeNull()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('uses the same map scale, places positive V above negative V, and labels sum profiles and flux units', () => {
    render(<ValidationPlots data={plotData} />)
    const sim = screen.getByRole('img', { name: 'Simulator 히트맵' })
    const lt = screen.getByRole('img', { name: 'LightTools 히트맵' })
    expect(sim.getAttribute('data-scale-max')).toBe('50')
    expect(lt.getAttribute('data-scale-max')).toBe('50')
    expect(Number(sim.querySelector('[data-cell="1,0"]')?.getAttribute('y'))).toBeLessThan(Number(sim.querySelector('[data-cell="0,0"]')?.getAttribute('y')))
    expect(sim.querySelector('[data-cell="0,0"] title')?.textContent).toContain('0.1 lm | 100,000 lx')
    expect(screen.getAllByText('Simulator Σ 100%')).toHaveLength(2)
    expect(screen.getAllByText('LT Σ 100%')).toHaveLength(2)
    expect(screen.getByRole('img', { name: 'U 합산 프로파일과 차이' })).not.toBeNull()
    expect(screen.getByRole('img', { name: 'V 합산 프로파일과 차이' })).not.toBeNull()
    const vProfile = screen.getByRole('img', { name: 'V 합산 프로파일과 차이' })
    expect(within(vProfile).getByText('+0.01')).not.toBeNull()
    expect(within(vProfile).getByText('−0.01')).not.toBeNull()
    expect(within(vProfile).queryByText('+0')).toBeNull()
  })

  it('edits sources and Aim with structured blank fields without copying simulator geometry', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<ManualEditor dataset={null} receiverId={null} busy={false} onSave={onSave} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByText('광원 조건', { selector: 'summary' }))
    fireEvent.click(screen.getByRole('button', { name: '광원 추가' }))
    expect((screen.getByRole('textbox', { name: 'LT 광원 발광 법선 X, Y, Z' }) as HTMLInputElement).value).toBe('')
    expect((screen.getByLabelText('LT 광원 광속 기준') as HTMLSelectElement).value).toBe('')
    fireEvent.change(screen.getByRole('textbox', { name: 'LT 광원 총광속 (lm)' }), { target: { value: '0.5' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'LT 광원 중심 X, Y, Z (mm)' }), { target: { value: '4.5, 0, 0' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Aim region 사용' }))
    expect((screen.getByRole('textbox', { name: 'LT Aim 중심 X, Y, Z (mm)' }) as HTMLInputElement).value).toBe('')
    fireEvent.click(screen.getByRole('button', { name: '입력 적용' }))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][0].sources[0]).toMatchObject({ total_flux_lm: 0.5, center_mm: [4.5, 0, 0], normal: null, aim: { center_mm: null } })
  })
})
