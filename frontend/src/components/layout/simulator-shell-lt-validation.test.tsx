// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { importValidationFile } from '@/features/lt-validation/client'
import { workspaceStore } from '@/stores'
import { SimulatorShell } from './simulator-shell'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, useSceneQuery: () => ({ data: undefined, error: null, isPending: false }), useRayTraceJobQuery: () => ({ data: undefined, error: null }) }
})
vi.mock('@/components/layout/viewer-workspace', () => ({ ViewerWorkspace: () => <div>Viewer stub</div> }))
vi.mock('@/features/lt-validation/client', () => ({ importValidationFile: vi.fn(), compareValidationCases: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
  workspaceStore.getState().actions.resetWorkspace()
})

it('keeps LT case files and baseline after the Result workflow panel unmounts and remounts', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.mocked(importValidationFile).mockResolvedValue({ schema: 'lt-validation.v1', kind: 'bitsam', filename: 'retained.bitsam', receivers: [], sources: [], issues: [] })
  render(<AppProviders><SimulatorShell /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'Step 05 Result' }))
  fireEvent.click(screen.getByRole('button', { name: 'LT 검증' }))
  const dialog = screen.getByRole('dialog', { name: 'LT 결과 검증' })
  fireEvent.change(within(dialog).getByLabelText('Case 이름'), { target: { value: 'Reference' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Case 추가' }))
  fireEvent.change(within(dialog).getByLabelText('Case 이름'), { target: { value: 'After blue' } })
  const secondId = (within(dialog).getByLabelText('검증 Case') as HTMLSelectElement).value
  fireEvent.change(within(dialog).getByLabelText('기준 Case'), { target: { value: secondId } })
  fireEvent.change(within(dialog).getByLabelText('Simulator 파일'), { target: { files: [new File(['test'], 'retained.bitsam')] } })
  await within(dialog).findByText('retained.bitsam')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'LT 결과 검증' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: 'Step 01 Model Import' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'LT 검증' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: 'Step 05 Result' }))
  fireEvent.click(screen.getByRole('button', { name: 'LT 검증' }))
  const restored = screen.getByRole('dialog', { name: 'LT 결과 검증' })
  expect((within(restored).getByLabelText('Case 이름') as HTMLInputElement).value).toBe('After blue')
  expect((within(restored).getByLabelText('기준 Case') as HTMLSelectElement).value).toBe(secondId)
  expect((within(restored).getByLabelText('검증 Case') as HTMLSelectElement).options).toHaveLength(2)
  expect(within(restored).getByText('retained.bitsam')).not.toBeNull()
  expect(workspaceStore.getState().activeCad).toBeNull()
  expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
})
