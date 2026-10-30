// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { act } from '@testing-library/react'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { defaultObserverSettings, createEscapeEnclosureReceivers, detectLeakPreviewCandidates, createLeakPreviewReceivers } from './leak-preview-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createDatumEmitter } from '@/features/raytracing/ray-tracing-model'
import { leakPreviewStore } from './leak-preview-store'
import { LeakPreviewPanel } from './leak-preview-panel'

const mocks = vi.hoisted(() => ({ start: vi.fn(), job: undefined as unknown }))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual,
    useStartRayTraceMutation: () => ({ mutateAsync: mocks.start, isPending: false }),
    useStopRayTraceMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRayTraceJobQuery: () => ({ data: mocks.job }),
  }
})
afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear(); mocks.start.mockReset(); mocks.job = undefined })

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
  expect((screen.getByLabelText('Preview 검출 방식') as HTMLSelectElement).selectedOptions[0].textContent).toContain('Source sampling')
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


it('invalidates old Preview results after Allowed Area changes and includes the regions in the next request', async () => {
  leakPreviewStore.getState().setDetectionMode('planar')
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'allowed.step', displayName: 'Allowed' })
  actions.upsertEmitter(createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0]))
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'preview-test' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '기존 Emitter 사용' }))
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  const result = createRayTraceResultFixture()
  const receiver = createLeakPreviewReceivers(scene, [], ['pos_z'])[0]
  result.receivers = [receiver]
  result.receiver_grids = [{ receiver_id: receiver.receiver_id, resolution: [1, 1], flux_lumen: [[1]], bin_area_mm2: 1, hit_count: 1 }]
  result.stored_paths = []
  mocks.job = { job_id: 'preview-test', status: 'completed', phase: 'completed', result }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  await waitFor(() => expect(leakPreviewStore.getState().candidates.length).toBeGreaterThan(0))
  const previous = leakPreviewStore.getState().result
  act(() => actions.setEmitterVisible('emitter_001', false))
  expect(leakPreviewStore.getState().result).toBe(previous)
  expect(leakPreviewStore.getState().candidates.length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: 'Delete Candidate 1' }))
  expect(leakPreviewStore.getState().candidates).toEqual([])
  expect(leakPreviewStore.getState().points).toEqual([])
  expect(leakPreviewStore.getState().result).toBe(previous)
  act(() => {
    leakPreviewStore.getState().beginIgnoreAreaSelection()
    leakPreviewStore.getState().addIgnoreAreaRegion({ plane: 'xy', xMin: 1000, xMax: 1001, yMin: 1000, yMax: 1001, zMin: 0, zMax: 20 })
    leakPreviewStore.getState().finishIgnoreAreaSelection()
  })
  expect(leakPreviewStore.getState().candidates).toEqual([])
  expect(screen.getByText('Preview 재실행 필요')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2))
  expect(mocks.start.mock.calls[1][0].request.preview_blockers).toHaveLength(2)
})


it('shows extreme-angle counts and reports CPU fallback instead of implying GPU execution', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'direction.step', displayName: 'Direction' })
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  const receiver = createLeakPreviewReceivers(scene, [], ['neg_z'])[0]
  result.config.compute_backend = 'gpu_cuda'
  result.receivers = [receiver]
  result.receiver_grids = [{ receiver_id: receiver.receiver_id, resolution: [1, 1], flux_lumen: [[1]], bin_area_mm2: 1, hit_count: 37 }]
  result.metrics._performance_summary = { compute_execution_state: 'gpu_requested_cpu_only', gpu_cuda_gpu_success_count: 0 }
  result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
    escaped_count: 37, selected_count: 37, excluded_count: 0,
    bins: [0,30,60,80].map((min_deg, i) => ({ min_deg, max_deg: [30,60,80,90][i], count: [1,2,3,31][i], flux_lumen: .25 })),
    cells: [{ receiver_id: receiver.receiver_id, row: 0, column: 0, position: receiver.center,
      count: 37, grazing_count: 34, min_depth: 0, max_depth: 1, angle_sum_deg: 3000 }] }
  leakPreviewStore.getState().setDetection(result, [], [])
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  expect(screen.getByText('80–90°')).toBeTruthy()
  expect(screen.getByText('31')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('CPU 대체 실행')
})


it('accepts extreme compound observer angles and sends the sweep without changing Ray Tracing inputs', async () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'observer.step', displayName: 'Observer' })
  actions.upsertEmitter(createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0]))
  mocks.start.mockResolvedValue({ job_id: 'observer-job' })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '기존 Emitter 사용' }))
  fireEvent.click(screen.getByLabelText('시야각 역추적 사용'))
  const horizontal = screen.getByLabelText('좌우 시야각 (°)')
  fireEvent.change(horizontal, { target: { value: '-89.9' } })
  fireEvent.blur(horizontal)
  const vertical = screen.getByLabelText('상하 시야각 (°)')
  fireEvent.change(vertical, { target: { value: '20' } })
  fireEvent.blur(vertical)
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  expect(mocks.start.mock.calls[0][0].request.observer_probe).toMatchObject({ horizontal_deg: -89.9, vertical_deg: 20 })
  const active = workspaceStore.getState().cadCases.find((item) => item.caseId === workspaceStore.getState().activeCadCaseId)
  expect(active?.leakPreviewState?.observer).toMatchObject({ enabled: true, horizontal_deg: -89.9, vertical_deg: 20 })
})

it('migrates Allowed Areas to Block groups with independent visibility and application', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'visibility.step', displayName: 'Visibility' })
  const store = leakPreviewStore.getState()
  store.beginIgnoreAreaSelection()
  store.addIgnoreAreaRegion({ plane: 'xy', xMin: 0, xMax: 1, yMin: 0, yMax: 1 })
  store.finishIgnoreAreaSelection()
  store.addBlocker({ id: 'block', label: 'Block', enabled: true, baseCenter: [0,0,0], uAxis: [1,0,0], vAxis: [0,1,0], normal: [0,0,1], widthMm: 1, heightMm: 1, offsetMm: 0, depthMm: 1, reverse: false })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByLabelText('Block 2 Hide'))
  fireEvent.click(screen.getByLabelText('Block Hide'))
  expect(leakPreviewStore.getState().blockers[1]).toMatchObject({ label: 'Block 2', enabled: true, visible: false })
  expect(leakPreviewStore.getState().ignoreAreas).toEqual([])
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: true, visible: false })
  fireEvent.click(screen.getByLabelText('Block 2 적용'))
  fireEvent.click(screen.getByLabelText('Block 적용'))
  expect(leakPreviewStore.getState().blockers[1]).toMatchObject({ enabled: false, visible: false })
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: false, visible: false })
  fireEvent.click(screen.getByLabelText('Block Show'))
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: false, visible: true })
})

it('connects both angle tables to viewer highlights and deletes their candidate glow points', () => {
  const scene = createSceneFixture()
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  const result = createRayTraceResultFixture()
  result.receivers = createEscapeEnclosureReceivers(scene)
  result.receiver_grids = result.receivers.map((receiver, index) => ({ receiver_id: receiver.receiver_id,
    resolution: [1, 1], bin_area_mm2: 1, flux_lumen: [[index === 0 ? 1 : 0]], hit_count: index === 0 ? 1 : 0 }))
  result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
    escaped_count: 1, selected_count: 1, excluded_count: 0,
    bins: [0, 30, 60, 80].map((min_deg, index) => ({ min_deg, max_deg: [30, 60, 80, 90][index], count: index === 3 ? 1 : 0, flux_lumen: index === 3 ? 1 : 0 })),
    cells: [{ receiver_id: result.receivers[0].receiver_id, row: 0, column: 0, position: [60, 30, 5], count: 1, grazing_count: 1, min_depth: 0, max_depth: 0, angle_sum_deg: 85, angle_bin_counts: [0, 0, 0, 1] }] }
  result.metrics._observer_probe_summary = { contract: 'observer_path_support_v1', execution_backend: 'cpu', measure: 'path_support_counts',
    settings: { ...defaultObserverSettings, resolution: 8 }, attempted_count: 64, planned_count: 64,
    direct_count: 1, reflected_count: 0, excluded_count: 0, depth_limited_count: 0, complete: true, stopped: false, runtime_sec: 0,
    views: [{ horizontal_deg: 10, vertical_deg: 20, direct_count: 1, reflected_count: 0, direction: [0, 0, 1], observer_center: [30, 30, 100], u_axis: [1, 0, 0], v_axis: [0, 1, 0], spacing_mm: [5, 5] }],
    cells: [{ view_index: 0, row: 0, column: 0, position: [30, 30, 10], direct_count: 1, reflected_count: 0, min_depth: 0, max_depth: 0 }] }
  const detection = detectLeakPreviewCandidates(scene, result)
  act(() => leakPreviewStore.getState().setDetection(result, detection.points, detection.candidates))
  fireEvent.click(screen.getByRole('button', { name: '10 / 20' }))
  expect(leakPreviewStore.getState().highlight).toEqual({ kind: 'observer', horizontal: 10, vertical: 20 })
  expect(screen.getByRole('button', { name: '10 / 20' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: '80–90°' }))
  expect(leakPreviewStore.getState().highlight).toEqual({ kind: 'escape', bin: 3 })
  fireEvent.click(screen.getAllByRole('button', { name: 'ROI·Receiver 생성' })[0])
  expect(workspaceStore.getState().roiScopes[0]?.clipBox).toMatchObject({ plane: 'xy', zUnbounded: true })
  const deletedId = leakPreviewStore.getState().candidates[0].id
  fireEvent.click(screen.getByRole('button', { name: 'Delete Candidate 1' }))
  expect(leakPreviewStore.getState().points.some((point) => point.candidateId === deletedId)).toBe(false)
})

it('automatically budgets sampling and blocks excessive angles before starting', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'auto.step', displayName: 'Auto' })
  workspaceStore.getState().actions.upsertEmitter(createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0]))
  mocks.start.mockResolvedValue({ job_id: 'auto-job' })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '기존 Emitter 사용' }))
  fireEvent.click(screen.getByLabelText('시야각 역추적 사용'))
  expect(screen.getByRole('button', { name: '기본' }).getAttribute('aria-pressed')).toBe('true')
  const advanced = screen.getByText('고급 설정').closest('details')!
  expect(advanced.open).toBe(false)
  act(() => leakPreviewStore.getState().setObserver({ horizontal_deg: 0, vertical_deg: 0, range_deg: 2, step_deg: .5 }))
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  expect(mocks.start.mock.calls[0][0].request.observer_probe).toMatchObject({ resolution: 43, samples_per_cell: 1, step_deg: .5 })
  act(() => leakPreviewStore.getState().setObserver({ range_deg: 10, step_deg: .1 }))
  expect(screen.getByRole('alert').textContent).toContain('각도 조합')
  expect((screen.getByRole('button', { name: 'Run Leak Preview' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: /간격 자동 조정/ }))
  expect(leakPreviewStore.getState().observer.step_deg).toBe(2)
  expect((screen.getByRole('button', { name: 'Run Leak Preview' }) as HTMLButtonElement).disabled).toBe(false)
})
