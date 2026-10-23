// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { apiClient } from '@/api'
import { workspaceStore, type MaterialAssignment } from '@/stores'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { BsdfAssetPanel } from './bsdf-asset-panel'
import { MaterialEditorDialog } from './material-editor-dialog'
import { SurfacePropertyDialog } from './surface-property-dialog'

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverStub
const asset = createBsdfFixture()
const scene = createSceneFixture()
const part: MaterialAssignment = { assignmentId: 'material-part-1', componentId: 1, targetType: 'part', faceIds: [],
  baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: asset.id, enabled: true,
  opticalOverride: { reflectance: 0.3, loss: 0.7, specularRatio: 0.25, diffuseRatio: 0.75 } }

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); vi.restoreAllMocks() })

describe('BSDF editor integration', () => {
  it('preserves calculation options when the same original files are registered again', async () => {
    const enabled = { ...asset, simulationReady: true,
      transport: { model: 'isotropic_no_shift' as const, wavelengthNm: 550, aoiExtrapolation: 'clamp' as const } }
    workspaceStore.getState().actions.addBsdfAsset(enabled)
    vi.spyOn(apiClient, 'importBsdf').mockResolvedValue(asset)
    render(<BsdfAssetPanel value={asset.id} onChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: {
      files: asset.files.map((file) => ({ name: file.name, size: file.text.length,
        arrayBuffer: async () => new TextEncoder().encode(file.text).buffer })),
    } })
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await screen.findByText(/등록 완료/)
    expect(workspaceStore.getState().bsdfAssets).toEqual([enabled])
    expect(screen.getByLabelText('Enable isotropic No Shift BSDF tracing')).toHaveProperty('checked', true)
  })
  it('enables an explicit transport model and persists wavelength in the shared asset', () => {
    workspaceStore.getState().actions.addBsdfAsset(asset)
    render(<BsdfAssetPanel value={asset.id} onChange={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Enable isotropic No Shift BSDF tracing'))
    expect(workspaceStore.getState().bsdfAssets![0]!.transport).toEqual({ model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' })
    expect(screen.getByRole('status').textContent).toContain('BSDF 표 계산 사용')
    fireEvent.change(screen.getByLabelText('BSDF calculation wavelength nm'), { target: { value: '600' } })
    expect(screen.getByRole('status').textContent).toContain('측정 범위')
    fireEvent.click(screen.getByLabelText('Enable isotropic No Shift BSDF tracing'))
    expect(workspaceStore.getState().bsdfAssets![0]!.transport).toBeUndefined()
  })
  it('imports into the library without silently attaching and reports parser errors', async () => {
    const onChange = vi.fn()
    const upload = vi.spyOn(apiClient, 'importBsdf').mockResolvedValue(asset)
    render(<BsdfAssetPanel value="" onChange={onChange} />)
    const files = asset.files.map((file) => ({ name: file.name, size: file.text.length, arrayBuffer: async () => new TextEncoder().encode(file.text).buffer }))
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: { files } })
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await waitFor(() => expect(workspaceStore.getState().bsdfAssets).toEqual([asset]))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByLabelText('연결할 BSDF')).toHaveProperty('value', '')
    fireEvent.change(screen.getByLabelText('연결할 BSDF'), { target: { value: asset.id } })
    expect(onChange).toHaveBeenCalledWith(asset.id)
    upload.mockRejectedValue(new Error('Missing grid.txt'))
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: { files } })
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Missing grid.txt'))
  })

  it('accumulates repeated file selections and replaces duplicate names before registration', async () => {
    const upload = vi.spyOn(apiClient, 'importBsdf').mockResolvedValue(asset)
    render(<BsdfAssetPanel value="" onChange={vi.fn()} />)
    const makeFile = (name: string, text: string) => ({ name, size: text.length,
      arrayBuffer: async () => new TextEncoder().encode(text).buffer })
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: { files: [makeFile('master.txt', 'old')] } })
    expect(screen.getByText(/대기 1개/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: { files: [
      makeFile('MASTER.txt', 'new'), makeFile('grid.txt', 'grid'), makeFile('rt.txt', 'rt'),
    ] } })
    expect(screen.getByText(/대기 3개/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await waitFor(() => expect(upload).toHaveBeenCalled())
    expect(upload.mock.calls[0]![0]).toEqual(expect.arrayContaining([
      { name: 'MASTER.txt', text: 'new' }, { name: 'grid.txt', text: 'grid' }, { name: 'rt.txt', text: 'rt' },
    ]))
  })

  it('stages part linking, retains it on unrelated optical edits and saves it in profiles', () => {
    const actions = workspaceStore.getState().actions
    actions.addBsdfAsset(asset)
    actions.upsertMaterialAssignment({ ...part, bsdfAssetId: '' })
    render(<AppProviders><MaterialEditorDialog open onOpenChange={vi.fn()} component={scene.components[0]} scene={scene} componentName="Deco" /></AppProviders>)
    fireEvent.change(screen.getByLabelText('연결할 BSDF'), { target: { value: asset.id } })
    expect(workspaceStore.getState().materialAssignments[0].bsdfAssetId).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Apply to part' }))
    expect(workspaceStore.getState().materialAssignments[0].bsdfAssetId).toBe(asset.id)
    expect(workspaceStore.getState().materialAssignments[0].opticalOverride).toEqual(part.opticalOverride)
    fireEvent.click(screen.getByLabelText('Save current draft as a new profile'))
    fireEvent.change(screen.getByPlaceholderText('새 프로필 이름'), { target: { value: 'Test BSDF' } })
    fireEvent.click(screen.getByLabelText('Confirm save'))
    expect(workspaceStore.getState().customOpticalProfiles[0].bsdfAssetId).toBe(asset.id)
  })

  it('rejects invalid UTF-8 before import instead of replacing original bytes', async () => {
    const upload = vi.spyOn(apiClient, 'importBsdf')
    render(<BsdfAssetPanel value="" onChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: {
      files: [{ name: 'invalid.txt', size: 1, arrayBuffer: async () => new Uint8Array([255]).buffer }],
    } })
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('ASCII/UTF-8'))
    expect(upload).not.toHaveBeenCalled()
    expect(workspaceStore.getState().bsdfAssets).toEqual([])
  })

  it('does not import into another case when the API completes after a case switch', async () => {
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'first.step', displayName: 'first' })
    let finish!: (value: typeof asset) => void
    const upload = vi.spyOn(apiClient, 'importBsdf').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    render(<BsdfAssetPanel value="" onChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Import LT ASCII BSDF files'), { target: {
      files: asset.files.map((file) => ({ name: file.name, size: file.text.length, arrayBuffer: async () => new TextEncoder().encode(file.text).buffer })),
    } })
    fireEvent.click(screen.getByRole('button', { name: '등록' }))
    await waitFor(() => expect(upload).toHaveBeenCalled())
    act(() => actions.addCadCase({ path: 'second.step', displayName: 'second' }))
    await act(async () => finish(asset))
    expect(workspaceStore.getState().bsdfAssets).toEqual([])
    expect(screen.getByRole('alert').textContent).toContain('등록을 취소')
  })

  it('preserves linked sources through surface editing and allows explicit unlink without changing optics', () => {
    const actions = workspaceStore.getState().actions
    actions.addBsdfAsset(asset); actions.upsertMaterialAssignment(part)
    render(<AppProviders><SurfacePropertyDialog open onOpenChange={vi.fn()} component={scene.components[0]} scene={scene} componentName="Deco" initialTarget="part" /></AppProviders>)
    fireEvent.change(screen.getByLabelText('Custom Reflectance'), { target: { value: '0.4' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply to part default' }))
    expect(workspaceStore.getState().materialAssignments[0]).toMatchObject({ bsdfAssetId: asset.id, opticalOverride: { reflectance: 0.4 } })
    fireEvent.change(screen.getByLabelText('연결할 BSDF'), { target: { value: '' } })
    expect(workspaceStore.getState().materialAssignments[0].bsdfAssetId).toBe(asset.id)
    fireEvent.click(screen.getByRole('button', { name: 'Apply to part default' }))
    expect(workspaceStore.getState().materialAssignments[0]).toMatchObject({ bsdfAssetId: '', opticalOverride: { reflectance: 0.4 } })
    expect(workspaceStore.getState().bsdfAssets).toEqual([asset])
  })
})
