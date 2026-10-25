// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiClient } from '@/api'
import { AppProviders } from '@/app/providers'
import { ComponentTreePanel } from '@/features/components'
import { workspaceStore } from '@/stores'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
  vi.restoreAllMocks()
})

it('opens the library before CAD import and preserves registered BSDF for imported components', async () => {
  const asset = createBsdfFixture()
  const upload = vi.spyOn(apiClient, 'importBsdf').mockResolvedValue(asset)
  const props = { onEditMaterial: vi.fn(), onEditTransform: vi.fn(), onDelete: vi.fn() }
  const view = render(<AppProviders><ComponentTreePanel {...props} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'Material Library' }))
  expect(screen.getByRole('dialog', { name: 'Material Library' })).toBeTruthy()
  expect(screen.getByLabelText('소재').textContent).toContain('PC Black')
  fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: {
    files: asset.files.map((file) => ({ name: file.name, size: file.text.length,
      arrayBuffer: async () => new TextEncoder().encode(file.text).buffer })),
  } })
  fireEvent.click(screen.getByRole('button', { name: '등록' }))
  await screen.findByText(/등록 완료/)
  expect(upload).toHaveBeenCalledOnce()
  expect(screen.getByLabelText('BSDF 라이브러리')).toHaveProperty('value', asset.id)
  expect(workspaceStore.getState().materialAssignments).toEqual([])
  fireEvent.click(screen.getByRole('button', { name: '닫기' }))
  act(() => workspaceStore.getState().actions.clearSceneState())
  view.rerender(<AppProviders><ComponentTreePanel {...props} scene={createSceneFixture()} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'Material Library' }))
  expect(screen.getByLabelText('BSDF 라이브러리').textContent).toContain(asset.files[0].name)
  expect(workspaceStore.getState().bsdfAssets).toEqual([asset])
})

it.each([{ isLoading: true }, { errorMessage: 'Import failed' }])('keeps library available during scene loading/errors: %j', (state) => {
  render(<AppProviders><ComponentTreePanel {...state}
    onEditMaterial={vi.fn()} onEditTransform={vi.fn()} onDelete={vi.fn()} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'Material Library' }))
  expect(screen.getByRole('dialog', { name: 'Material Library' })).toBeTruthy()
})
