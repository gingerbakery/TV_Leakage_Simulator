// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createDatumEmitter } from '@/features/raytracing/ray-tracing-model'
import { leakPreviewStore } from './leak-preview-store'
import { LeakPreviewPanel } from './leak-preview-panel'

const mocks = vi.hoisted(() => ({ start: vi.fn() }))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual,
    useStartRayTraceMutation: () => ({ mutateAsync: mocks.start, isPending: false }),
    useStopRayTraceMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRayTraceJobQuery: () => ({ data: undefined }),
  }
})
afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear(); mocks.start.mockReset() })

it('enables Ray Tracing datum emitters and starts a BSDF Preview using CPU Source without changing the original', async () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'test.step', displayName: 'Test' })
  const emitter = { ...createDatumEmitter('emitter_001', [1, 2, 3], [0, 0, 0]), power_lumen: 17, ray_count: 321 }
  actions.upsertEmitter(emitter)
  const asset = createBsdfFixture()
  asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
  actions.addBsdfAsset(asset)
  actions.upsertMaterialAssignment({ assignmentId: 'bsdf', componentId: 1, targetType: 'faces', faceIds: [0],
    baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', enabled: true, bsdfAssetId: asset.id })
  const config = structuredClone(workspaceStore.getState().rayTraceConfig)
  mocks.start.mockResolvedValue({ job_id: 'preview-test' })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  const existing = screen.getByRole('button', { name: '기존 Emitter 사용' }) as HTMLButtonElement
  expect(existing.disabled).toBe(false)
  fireEvent.click(existing)
  expect(screen.getByText('BSDF · CPU / Source sampling')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  const request = mocks.start.mock.calls[0][0].request
  expect(request.config).toMatchObject({ compute_backend: 'cpu', primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
  expect(request.bsdf_assets).toEqual([asset])
  expect(request.emitters).toHaveLength(1)
  expect(request.emitters[0]).toMatchObject({ emitter_id: emitter.emitter_id, center: emitter.center, power_lumen: 17 })
  expect(workspaceStore.getState().emitters).toEqual([emitter])
  expect(workspaceStore.getState().rayTraceConfig).toEqual(config)
})

it('keeps earlier CAD source faces selected when reopening the selection', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'test.step', displayName: 'Test' })
  leakPreviewStore.getState().setSourceFaceIds([0, 1])
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'CAD Face 선택' }))
  expect(workspaceStore.getState().selectedFaceIds).toEqual([0, 1])
})
