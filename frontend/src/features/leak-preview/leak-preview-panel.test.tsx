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
import { buildRayTraceRequest, createDatumEmitter } from '@/features/raytracing/ray-tracing-model'
import { createViewAngleReceiver } from '@/features/raytracing/receiver-view'
import { leakPreviewStore, snapshotLeakPreviewProjectState, snapshotLeakPreviewResult } from './leak-preview-store'
import { createSavedLeakPreviewFixture } from '@/test/leak-preview-fixture'
import { LeakPreviewPanel } from './leak-preview-panel'

const mocks = vi.hoisted(() => ({ start: vi.fn(), job: undefined as unknown, queryError: null as Error | null }))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual,
    useStartRayTraceMutation: () => ({ mutateAsync: mocks.start, isPending: false }),
    useStopRayTraceMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRayTraceJobQuery: () => ({ data: mocks.job, isError: Boolean(mocks.queryError), error: mocks.queryError, refetch: vi.fn() }),
  }
})
afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear(); mocks.start.mockReset(); mocks.job = undefined; mocks.queryError = null })

it('keeps saved results while the scene loads and rebinds a restarted server session', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'saved.step', displayName: 'Saved' })
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'saved-preview' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setSourceFaceIds([0]))
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('saved-preview'))
  const fixture = createSavedLeakPreviewFixture()
  act(() => leakPreviewStore.getState().setDetection(fixture.result, fixture.points, fixture.candidates))
  const settings = snapshotLeakPreviewProjectState(), saved = snapshotLeakPreviewResult()!
  view.unmount()
  leakPreviewStore.getState().restoreProjectState(settings, saved)
  const restored = render(<AppProviders><LeakPreviewPanel scene={undefined} onOpenPrecision={() => undefined} /></AppProviders>)
  expect(leakPreviewStore.getState().result).toBe(fixture.result)
  const restarted = { ...scene, metadata: { ...scene.metadata, scene_token: 'restarted-server' } }
  restored.rerender(<AppProviders><LeakPreviewPanel scene={restarted} onOpenPrecision={() => undefined} /></AppProviders>)
  expect(leakPreviewStore.getState().result).toBe(fixture.result)
  expect(screen.getByText('저장된 Preview 결과')).toBeTruthy()
  expect(screen.getAllByRole('article')).toHaveLength(fixture.candidates.length)
  expect(mocks.start).toHaveBeenCalledOnce()
  // A real input change still invalidates saved evidence.
  act(() => leakPreviewStore.getState().setQuality('deep'))
  expect(leakPreviewStore.getState().result).toBeNull()
})

it('starts a full hemisphere search and hides unused local angles', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'hemisphere.step', displayName: 'Hemisphere' })
  mocks.start.mockResolvedValue({ job_id: 'hemisphere' })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true }) })
  fireEvent.change(screen.getByLabelText('각도 영역'), { target: { value: 'hemisphere' } })
  expect(screen.queryByLabelText('좌우 시야각 (°)')).toBeNull()
  expect(screen.getByLabelText('각도 간격 (°)')).toHaveProperty('value', '15')
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  expect(mocks.start.mock.calls[0][0].request.observer_probe).toMatchObject({ coverage: 'hemisphere', step_deg: 15 })
})

it('starts a fixed-spacing whole-model search using precision geometry and the configured reflection depth', async () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'full.step', displayName: 'Full' })
  actions.upsertEmitter(createDatumEmitter('emitter_001', [30,30,10], [0,0,0]))
  actions.setRayTraceConfig({ ...workspaceStore.getState().rayTraceConfig, max_depth: 50, max_stored_paths: 100, k_abs: .9, k_brdf: .5 })
  mocks.start.mockResolvedValue({ job_id: 'full-detail' })
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '기존 Emitter 사용' }))
  fireEvent.change(screen.getByLabelText('Preview 검색 모드'), { target: { value: 'full_detail' } })
  expect((screen.getByLabelText('시야각 역추적 사용') as HTMLInputElement).checked).toBe(true)
  expect(screen.queryByLabelText('해상도 / 축')).toBeNull()
  const spacing = screen.getByLabelText('전체 정밀 검사 간격 (mm)')
  fireEvent.change(spacing, { target: { value: '1' } }); fireEvent.blur(spacing)
  fireEvent.click(screen.getByRole('button', { name: '전체 정밀 검색 시작' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  const request = mocks.start.mock.calls[0][0].request
  expect(request.geometry_mode).toBe('precision')
  expect(request.observer_probe).toMatchObject({ search_mode: 'full_detail', spacing_mm: 1, samples_per_cell: 2 })
  expect(request.config.max_depth).toBe(50)
  expect(request.config).toMatchObject({ max_stored_paths: 100, k_abs: .9, k_brdf: .5 })
  expect(request.analysis_roi).toBeUndefined()
  expect(request.optical_profiles.some((profile: { profile_id: string }) => profile.profile_id === 'default')).toBe(false)
})

it('limits rendered candidate rows and reveals the viewer-selected candidate on another page', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'many.step', displayName: 'Many' })
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  const base = detectLeakPreviewCandidates(scene, result).candidates[0]
  const candidates = Array.from({ length: 205 }, (_, i) => ({ ...base, id: `candidate-${i}`, relativeStrength: 1-i/1000 }))
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result, [], candidates))
  expect(screen.getAllByRole('article')).toHaveLength(100)
  act(() => leakPreviewStore.getState().selectCandidate('candidate-204', 'viewer'))
  expect(screen.getAllByRole('article')).toHaveLength(5)
  expect(screen.getByRole('article', { name: 'Candidate 205' }).getAttribute('aria-current')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: '이전 후보' }))
  expect(screen.getAllByRole('article')).toHaveLength(100)
  fireEvent.click(screen.getByRole('button', { name: 'Leak Candidates 접기' }))
  act(() => leakPreviewStore.getState().selectCandidate('candidate-204', 'viewer'))
  expect(screen.getByRole('article', { name: 'Candidate 205' })).toBeTruthy()
  expect(leakPreviewStore.getState().candidates).toHaveLength(205)
})

it('creates only a view-angle receiver from the detected snapshot while preserving BSDF, sources, and sampling', () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'bsdf-angle.step', displayName: 'BSDF angle' })
  actions.upsertEmitter(createDatumEmitter('emitter_001', [30, 30, 10], [0, 0, 0]))
  const asset = createBsdfFixture()
  asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
  actions.addBsdfAsset(asset)
  actions.upsertMaterialAssignment({ assignmentId: 'bsdf', componentId: 1, targetType: 'part', faceIds: [],
    baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', enabled: true, bsdfAssetId: asset.id })
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  const canonical = createViewAngleReceiver('canonical', scene, [30, 30, 10],
    { axis: 'neg_z', horizontal_deg: 80, vertical_deg: 20, range_deg: 0, step_deg: .5 }, 3)
  result.receivers = createEscapeEnclosureReceivers(scene)
  result.receiver_grids = result.receivers.map((receiver) => ({ receiver_id: receiver.receiver_id,
    resolution: [1, 1], flux_lumen: [[0]], bin_area_mm2: 1, hit_count: 0 }))
  result.metrics._escape_direction_summary = { direction: 'neg_z' }
  result.metrics._observer_probe_summary = { contract: 'observer_path_support_v1', execution_backend: 'cpu',
    measure: 'path_support_counts', settings: { ...defaultObserverSettings, step_deg: .5, resolution: 8 },
    attempted_count: 128, planned_count: 128, direct_count: 1, reflected_count: 0, excluded_count: 0,
    depth_limited_count: 0, complete: true, stopped: false, runtime_sec: .1,
    views: [{ horizontal_deg: 80, vertical_deg: 20, direct_count: 1, reflected_count: 0,
      direction: canonical.normal.map((value) => -value), observer_center: canonical.center,
      u_axis: canonical.u_axis, v_axis: canonical.v_axis, spacing_mm: [1, 1] }],
    cells: [{ view_index: 0, row: 0, column: 0, position: [30, 30, 10], direct_count: 1, reflected_count: 0, min_depth: 0, max_depth: 0 }] }
  const onOpenPrecision = vi.fn()
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={onOpenPrecision} /></AppProviders>)
  const detection = detectLeakPreviewCandidates(scene, result)
  act(() => {
    leakPreviewStore.getState().setDetection(result, detection.points, detection.candidates)
    leakPreviewStore.getState().toggleDirection('pos_x')
    leakPreviewStore.getState().setObserver({ horizontal_deg: -30, vertical_deg: -10, range_deg: 2, step_deg: 1 })
  })
  const before = structuredClone({ emitters: workspaceStore.getState().emitters,
    materialAssignments: workspaceStore.getState().materialAssignments, config: workspaceStore.getState().rayTraceConfig })
  fireEvent.click(screen.getByRole('button', { name: '시야각 Receiver 생성' }))
  expect(onOpenPrecision).toHaveBeenCalledOnce()
  expect(workspaceStore.getState().roiScopes).toEqual([])
  expect(workspaceStore.getState().receivers).toHaveLength(1)
  const receiver = workspaceStore.getState().receivers[0]
  expect(receiver.view_angles).toEqual({ axis: 'neg_z', horizontal_deg: 80, vertical_deg: 20, range_deg: 0, step_deg: .5 })
  expect(receiver.base_center).toEqual([30, 30, 10])
  expect(workspaceStore.getState().emitters).toEqual(before.emitters)
  expect(workspaceStore.getState().materialAssignments).toEqual(before.materialAssignments)
  expect(workspaceStore.getState().rayTraceConfig).toEqual(before.config)
  const request = buildRayTraceRequest({ scene, projectName: 'BSDF angle', emitters: before.emitters,
    receivers: [receiver], materialAssignments: before.materialAssignments, bsdfAssets: [asset],
    transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: before.config })
  expect(request.bsdf_assets).toEqual([asset])
  expect(request.config).toMatchObject({ primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
  expect(request.analysis_roi).toBeUndefined()
  const competing = { ...receiver, receiver_id: 'receiver_other', display_name: 'Other receiver',
    reference_mode: null, view_angles: undefined }
  act(() => {
    actions.upsertReceiver(competing)
    actions.setReceiverEnabled(receiver.receiver_id, false)
  })
  fireEvent.click(screen.getByRole('button', { name: '시야각 Receiver 생성' }))
  expect(workspaceStore.getState().receivers).toHaveLength(2)
  expect(workspaceStore.getState().receivers.filter((item) => item.enabled).map((item) => item.receiver_id)).toEqual([receiver.receiver_id])
  expect(workspaceStore.getState().receivers.find((item) => item.receiver_id === competing.receiver_id)).toEqual({ ...competing, enabled: false })
  fireEvent.click(screen.getByRole('button', { name: 'ROI·Receiver 생성' }))
  expect(workspaceStore.getState().roiScopes).toHaveLength(1)
  expect(workspaceStore.getState().receivers).toHaveLength(2)
})

it('reveals and scrolls to the viewer-picked candidate, including repeated picks of a collapsed selection', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'picking.step', displayName: 'Picking' })
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
  result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [3, 1],
    bin_area_mm2: 1, flux_lumen: [[1, 0, .5]], hit_count: 2 }]
  result.stored_paths = []
  const detection = detectLeakPreviewCandidates(scene, result)
  expect(detection.candidates).toHaveLength(2)
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result, detection.points, detection.candidates))
  const [first, second] = leakPreviewStore.getState().candidates
  const firstRow = screen.getByRole('article', { name: 'Candidate 01' })
  const secondRow = screen.getByRole('article', { name: 'Candidate 02' })
  const scroll = vi.fn()
  firstRow.scrollIntoView = scroll
  secondRow.scrollIntoView = scroll
  act(() => leakPreviewStore.getState().setHighlight({ kind: 'escape', bin: 0 }))
  fireEvent.click(screen.getByRole('button', { name: 'Leak Candidates 접기' }))
  act(() => leakPreviewStore.getState().selectCandidate(second.id, 'viewer'))
  expect(screen.getByRole('button', { name: 'Leak Candidates 접기' }).getAttribute('aria-expanded')).toBe('true')
  expect(secondRow.getAttribute('aria-current')).toBe('true')
  expect(firstRow.hasAttribute('aria-current')).toBe(false)
  expect(scroll.mock.contexts.at(-1)).toBe(secondRow)
  expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest' })
  expect(leakPreviewStore.getState().highlight).toBeNull()
  scroll.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'Leak Candidates 접기' }))
  expect(screen.queryByRole('article', { name: 'Candidate 02' })).toBeNull()
  expect(scroll).not.toHaveBeenCalled()
  act(() => leakPreviewStore.getState().selectCandidate(second.id, 'viewer'))
  expect(screen.getByRole('article', { name: 'Candidate 02' }).getAttribute('aria-current')).toBe('true')
  expect(scroll.mock.contexts.at(-1)).toBe(secondRow)
  fireEvent.click(firstRow)
  expect(leakPreviewStore.getState().selectedCandidateId).toBe(first.id)
  expect(firstRow.getAttribute('aria-current')).toBe('true')
  expect(leakPreviewStore.getState().points).toEqual(detection.points)
  expect(leakPreviewStore.getState().result).toBe(result)
  act(() => leakPreviewStore.getState().removeCandidate(second.id))
  expect(screen.queryByRole('article', { name: 'Candidate 02' })).toBeNull()
})

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
  const candidates = leakPreviewStore.getState().candidates
  fireEvent.click(screen.getByRole('button',{name:'Leak Candidates 접기'}))
  expect(screen.getByRole('button',{name:'Leak Candidates 펼치기'}).getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('button',{name:'Delete Candidate 1'})).toBeNull()
  expect(leakPreviewStore.getState().candidates).toBe(candidates)
  expect(leakPreviewStore.getState().result).toBe(previous)
  fireEvent.click(screen.getByRole('button',{name:'Leak Candidates 펼치기'}))
  expect(screen.getByRole('button',{name:'Delete Candidate 1'})).toBeTruthy()
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
  expect(screen.getByText(/Preview 재실행 필요/)).toBeTruthy()
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
  const scene = createSceneFixture()
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '기존 Emitter 사용' }))
  fireEvent.click(screen.getByLabelText('시야각 역추적 사용'))
  expect(screen.getByRole('button', { name: '기본' }).getAttribute('aria-pressed')).toBe('true')
  const advanced = screen.getByText('고급 설정').closest('details')!
  expect(advanced.open).toBe(false)
  act(() => leakPreviewStore.getState().setObserver({ horizontal_deg: 0, vertical_deg: 0, range_deg: 2, step_deg: .5 }))
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  expect(mocks.start.mock.calls[0][0].request.observer_probe).toMatchObject({ resolution: 43, samples_per_cell: 1, step_deg: .5 })
  mocks.job = { job_id: 'auto-job', status: 'cancelled', phase: 'stopped', progress: 0 }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setObserver({ range_deg: 10, step_deg: .1 }))
  expect(screen.getByRole('alert').textContent).toContain('각도 조합')
  expect((screen.getByRole('button', { name: 'Run Leak Preview' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: /간격 자동 조정/ }))
  expect(leakPreviewStore.getState().observer.step_deg).toBe(2)
  expect((screen.getByRole('button', { name: 'Run Leak Preview' }) as HTMLButtonElement).disabled).toBe(false)
})


it('keeps an accepted job active while its status is unavailable and exposes polling errors', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'unknown.step', displayName: 'Unknown' })
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'unknown-job' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setSourceFaceIds([0]))
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('unknown-job'))
  expect(screen.getByRole('button', { name: 'Preview Stop' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Run Leak Preview' })).toBeNull()
  mocks.queryError = new Error('network unavailable')
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  expect(screen.getByRole('alert').textContent).toContain('unknown-job')
  expect(screen.getByRole('alert').textContent).toContain('network unavailable')
  expect(screen.getByRole('button', { name: '상태 다시 조회' })).toBeTruthy()
})

it('shows the server error and job identity when a hemisphere job fails halfway', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'failed.step', displayName: 'Failed' })
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'failed-halfway' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true, coverage: 'hemisphere', step_deg: 15, search_mode: 'full_detail' }) })
  fireEvent.click(screen.getByRole('button', { name: '전체 정밀 검색 시작' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('failed-halfway'))
  mocks.job = { job_id: 'failed-halfway', status: 'failed', phase: 'failed', progress: .5, error: 'synthetic observer error' }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  expect(screen.getByRole('alert').textContent).toContain('50%')
  expect(screen.getByRole('alert').textContent).toContain('failed-halfway')
  expect(screen.getByRole('alert').textContent).toContain('synthetic observer error')
  expect(screen.getByRole('button', { name: '전체 정밀 검색 시작' }).hasAttribute('disabled')).toBe(false)
})

it('reports a result processing exception without losing the job identity', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'broken.step', displayName: 'Broken' })
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'broken-result' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setDetectionMode('planar') })
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('broken-result'))
  const result = { ...createRayTraceResultFixture(), receiver_grids: undefined }
  mocks.job = { job_id: 'broken-result', status: 'completed', phase: 'completed', progress: 1, result }
  const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  try {
    view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
    expect(screen.getByRole('alert').textContent).toContain('결과 처리 실패')
    expect(screen.getByRole('alert').textContent).toContain('broken-result')
    expect(leakPreviewStore.getState().jobId).toBe('broken-result')
    expect(logged).toHaveBeenCalled()
  } finally { logged.mockRestore() }
})


it('retains an active full-detail job when shared analysis settings change', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'active.step', displayName: 'Active' })
  const scene = createSceneFixture()
  mocks.start.mockResolvedValue({ job_id: 'active-halfway' })
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true, coverage: 'hemisphere', step_deg: 15, search_mode: 'full_detail' }) })
  fireEvent.click(screen.getByRole('button', { name: '전체 정밀 검색 시작' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('active-halfway'))
  mocks.job = { job_id: 'active-halfway', status: 'running', phase: 'tracing', progress: .5, processed_rays: 50, total_rays: 100 }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => workspaceStore.getState().actions.setRayTraceConfig({ ...workspaceStore.getState().rayTraceConfig, max_depth: 17 }))
  expect(leakPreviewStore.getState().jobId).toBe('active-halfway')
  expect(screen.getByRole('button', { name: 'Preview Stop' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: '전체 정밀 검색 시작' })).toBeNull()
  expect(screen.getByText('설정 변경 · 실행 중인 작업은 기존 조건으로 계속 진행합니다.')).toBeTruthy()
})
