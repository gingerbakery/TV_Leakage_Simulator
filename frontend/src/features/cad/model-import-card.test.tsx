// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { leakPreviewStore } from '@/features/leak-preview/leak-preview-store'
import { createSceneFixture } from '@/test/scene-fixture'
import { ModelImportCard } from './model-import-card'

const upload = vi.hoisted(() => vi.fn())
vi.mock('@/api', async () => ({ ...await vi.importActual<typeof import('@/api')>('@/api'),
  useUploadCadMutation: () => ({ mutateAsync: upload, isPending: false, error: null }),
}))
afterEach(() => { cleanup(); upload.mockReset(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear() })

it('places ACC above Blocks in the folded common editor and stays on import after an ACC upload', async () => {
  workspaceStore.getState().actions.addCadCase({path:'main.step',displayName:'main'})
  upload.mockResolvedValue({path:'uploaded-acc.step',display_name:'new ACC'})
  const imported = vi.fn(), accessoryImported = vi.fn()
  render(<AppProviders><ModelImportCard scene={createSceneFixture()} onImported={imported} onAccessoryImported={accessoryImported} /></AppProviders>)
  expect(screen.queryByRole('button',{name:'Block 만들기'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  expect(screen.getAllByRole('button',{name:'Block 만들기'})).toHaveLength(1)
  expect(screen.getByText('Accessory CAD').compareDocumentPosition(screen.getByRole('button',{name:'Block 만들기'})) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Choose Accessory CAD file'), {target:{files:[new File(['STEP'],'acc.step')]}})
  await waitFor(() => expect(accessoryImported).toHaveBeenCalledOnce())
  expect(imported).not.toHaveBeenCalled()
  expect(workspaceStore.getState().cadCases[0].accessoryCads[0].cad.path).toBe('uploaded-acc.step')
  expect(screen.getByRole('button',{name:'공통 보완 형상 접기'})).toBeTruthy()
})
