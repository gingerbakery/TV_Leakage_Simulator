// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { act } from '@testing-library/react'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { defaultObserverSettings, createEscapeEnclosureReceivers, detectLeakPreviewCandidates, createLeakPreviewReceivers } from './leak-preview-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { buildRayTraceRequest, createDatumEmitter } from '@/features/raytracing/ray-tracing-model'
import { apiClient } from '@/api'
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

it('keeps Point rows on one line and puts coordinates, exit metadata and grouping inside closed details', () => {
  const saved = createSavedLeakPreviewFixture()
  const candidate = { ...saved.candidates[0], peakLuminanceNit: .0023, groupedCount: 12,
    apertureId: 'gap', apertureMethod: 'opposed_neighborhood_multiscale', apertureVariationMm: .02,
    observerEvidence: { axis: 'neg_z' as const, stepDeg: 10, horizontalDeg: 45, verticalDeg: 0,
      directCount: 1, reflectedCount: 0, direction: [0,0,-1] as [number,number,number], receiverCenter: [0,0,-100] as [number,number,number] } }
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(saved.result, [], [candidate]))
  expect(screen.getByRole('button', { name: 'Leak point List 접기' })).toBeTruthy()
  const row = screen.getByRole('article', { name: 'Point1' })
  const label = within(row).getByRole('button', { name: 'Point1 · 45°/0° · 광량 2.30e-3 nit' })
  expect(label.classList.contains('whitespace-nowrap')).toBe(true)
  expect(label.classList.contains('truncate')).toBe(true)
  expect(row.querySelector('details')!.open).toBe(false)
  expect(within(row).getByText(/깊이 변동/).closest('details')).toBe(row.querySelector('details'))
  fireEvent.click(within(row).getByText('상세 정보'))
  expect(row.querySelector('details')!.open).toBe(true)
})

it('shows measured below-threshold diagnostics instead of treating all zero lists as zero light', () => {
  const result = createRayTraceResultFixture()
  result.metrics._observer_probe_summary = { contract:'observer_radiance_parallel_v1', measure:'luminance_cd_m2', execution_backend:'cpu',
    calibration_state:'uncalibrated', limitations:['finite_sampling'], settings:{...defaultObserverSettings,resolution:8,min_luminance_nit:.01},
    attempted_count:64,planned_count:64,direct_count:0,reflected_count:0,excluded_count:0,depth_limited_count:0,
    complete:true,stopped:false,runtime_sec:0,views:[],cells:[],filtered_count:8,positive_before_threshold_count:8,
    peak_luminance_before_threshold_nit:.005,photometric_diagnostics_scope:'finalized_cells_after_allowed_regions' }
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result,[],[]))
  expect(screen.getByText('최소 휘도 0.01 nit 미달로 제외 · 빛샘 없음 판정이 아닙니다.')).toBeTruthy()
  expect(screen.getByText(/필터 전 최고 5.000e-3 nit/)).toBeTruthy()
})

it('runs one shared action on the selected point and shows a spinning validation indicator until completion', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'toolbar.step', displayName: 'toolbar' })
  workspaceStore.getState().actions.upsertEmitter(createDatumEmitter('lamp', [0,0,1], [0,0,0]))
  const saved = createSavedLeakPreviewFixture()
  const candidates = [0,1].map(i => ({ ...saved.candidates[0], id: `point-${i}`, label: `point ${i}`,
    representativePath: [[i,0,0],[i,0,-100]] as [number,number,number][], observerEvidence: { axis: 'neg_z' as const,
      stepDeg: 1, horizontalDeg: 0, verticalDeg: 0, directCount: 1, reflectedCount: 0, direction: [0,0,-1] as [number,number,number], receiverCenter: [i,0,-100] as [number,number,number] } }))
  let finish!: (value: Awaited<ReturnType<typeof apiClient.validateObserverReceiver>>) => void
  const validate = vi.spyOn(apiClient, 'validateObserverReceiver').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  try {
    render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
    act(() => leakPreviewStore.getState().setDetection(saved.result, [], candidates))
    expect(screen.getAllByRole('button', { name: '경로 검증' })).toHaveLength(1)
    expect(within(screen.getByRole('article', { name: 'Point1' })).queryByRole('button', { name: '경로 검증' })).toBeNull()
    act(() => leakPreviewStore.getState().selectCandidate('point-1','viewer'))
    fireEvent.click(screen.getByRole('button', { name: '경로 검증' }))
    await waitFor(() => expect(screen.getByRole('img', { name: '경로 검증 진행 중' })).toBeTruthy())
    expect(validate.mock.calls[0][0].receivers[0].observer_reference_path).toEqual(candidates[1].representativePath)
    expect(screen.getByRole('img', { name: '경로 검증 진행 중' }).classList.contains('animate-spin')).toBe(true)
    await act(async () => finish({ simulation_run_started: false, geometry_cache_hit: true, runtime_sec: 1,
      receiver_checks: [{ receiver_id: 'x', path_valid: true, diagnosis: 'reference_path_valid', forward_hit_count: null }] }))
    await waitFor(() => expect(screen.queryByRole('img', { name: '경로 검증 진행 중' })).toBeNull())
    expect(screen.getByText('경로 검증 통과 · 생성될 Receiver 도달')).toBeTruthy()
  } finally { validate.mockRestore() }
})

it('groups current candidates without removing points and can undo the grouping', () => {
  const saved = createSavedLeakPreviewFixture()
  const candidates = [0,1].map(i => ({ ...saved.candidates[0], id: `similar-${i}`, sourceCandidateIds: undefined,
    center: [i,0,0] as [number,number,number], representativePath: [[i,0,-5],[i,0,0],[i,0,10]] as [number,number,number][] }))
  const points = candidates.map(candidate => ({ position: candidate.center, candidateId: candidate.id, relativeStrength: 1 }))
  render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(saved.result, points, candidates))
  fireEvent.click(screen.getByRole('button', { name: '유사 경로 묶기' }))
  expect(leakPreviewStore.getState().candidates).toHaveLength(1)
  expect(leakPreviewStore.getState().points).toHaveLength(2)
  expect(new Set(leakPreviewStore.getState().points.map(point => point.candidateId)).size).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: '묶음 되돌리기' }))
  expect(leakPreviewStore.getState().candidates).toHaveLength(2)
  expect(leakPreviewStore.getState().points).toHaveLength(2)
})

it('validates saved representative evidence without starting another Preview or ray-tracing run', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'witness.step', displayName: 'Witness' })
  workspaceStore.getState().actions.upsertEmitter(createDatumEmitter('lamp', [0,0,1], [0,0,0]))
  const fixture = createSavedLeakPreviewFixture()
  fixture.candidates[0].observerEvidence = { axis: 'neg_z', stepDeg: 1, horizontalDeg: 0, verticalDeg: 0,
    directCount: 1, reflectedCount: 0, direction: [0,0,-1], receiverCenter: [0,0,-100] }
  fixture.candidates[0].representativePath = [[0,0,0], [0,0,-100]]
  const validate = vi.spyOn(apiClient, 'validateObserverReceiver').mockResolvedValue({ simulation_run_started: false,
    geometry_cache_hit: true, runtime_sec: .01, receiver_checks: [{ receiver_id: 'witness', path_valid: true,
      diagnosis: 'reference_path_valid', forward_hit_count: null }] })
  try {
    render(<AppProviders><LeakPreviewPanel scene={createSceneFixture()} onOpenPrecision={() => undefined} /></AppProviders>)
    act(() => leakPreviewStore.getState().setDetection(fixture.result, fixture.points, fixture.candidates))
    fireEvent.click(screen.getByRole('article', { name: 'Point1' }))
    fireEvent.click(screen.getByRole('button', { name: '경로 검증' }))
    await waitFor(() => expect(screen.getByText('경로 검증 통과 · 생성될 Receiver 도달')).toBeTruthy())
    expect(validate).toHaveBeenCalledOnce()
    expect(validate.mock.calls[0][0].receivers[0].observer_reference_path).toEqual(fixture.candidates[0].representativePath)
    expect(mocks.start).not.toHaveBeenCalled()
  } finally { validate.mockRestore() }
})

it('keeps a saved precision result when serialization changes only object key order', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'saved-precision.step', displayName: 'Saved precision' })
  mocks.start.mockResolvedValue({ job_id: 'saved-precision' })
  const scene = createSceneFixture()
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true, search_mode: 'full_detail' }) })
  fireEvent.click(screen.getByRole('button', { name: '전체 정밀 검색 시작' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('saved-precision'))
  const result = createSavedLeakPreviewFixture().result
  result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
    escaped_count: 0, selected_count: 0, excluded_count: 0, cells: [],
    bins: [0,30,60,80].map((min_deg,i) => ({ min_deg, max_deg: [30,60,80,90][i], count: 0, flux_lumen: 0 })) }
  result.metrics._observer_probe_summary = { contract: 'observer_radiance_parallel_v1', measure: 'luminance_cd_m2',
    search_contract: 'observer_full_spacing_v1', execution_backend: 'cpu', calibration_state: 'uncalibrated', limitations: ['finite_sampling'],
    settings: { ...leakPreviewStore.getState().observer, resolution: 8 }, attempted_count: 128, planned_count: 128,
    direct_count: 0, reflected_count: 0, excluded_count: 0, depth_limited_count: 0,
    complete: true, stopped: false, runtime_sec: 0, views: [], cells: [] }
  mocks.job = { job_id: 'saved-precision', status: 'completed', result }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  await waitFor(() => expect(leakPreviewStore.getState().result).toBe(result))
  const settings = snapshotLeakPreviewProjectState(), saved = snapshotLeakPreviewResult()!
  const config = workspaceStore.getState().rayTraceConfig
  const observer = leakPreviewStore.getState().observer
  act(() => {
    workspaceStore.setState({ rayTraceConfig: Object.fromEntries(Object.entries(config).reverse()) as typeof config })
    leakPreviewStore.getState().restoreProjectState({ ...settings,
      observer: Object.fromEntries(Object.entries(observer).reverse()) as typeof observer }, saved)
  })
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  await waitFor(() => expect(leakPreviewStore.getState().result).toBe(result))
  expect(screen.queryByText(/^Preview 재실행 필요$/)).toBeNull()
  act(() => leakPreviewStore.getState().setObserver({ spacing_mm: 3 }))
  expect(leakPreviewStore.getState().result).toBeNull()
})

it.each(['resume', 'restart', 'changed'] as const)('handles a stopped radiance Preview: %s', async (action) => {
  workspaceStore.getState().actions.addCadCase({ path: 'resume.step', displayName: 'Resume' })
  mocks.start.mockResolvedValueOnce({ job_id: 'paused-preview' }).mockResolvedValueOnce({ job_id: 'next-preview' })
  const scene = createSceneFixture()
  const view = render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true }) })
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(leakPreviewStore.getState().jobId).toBe('paused-preview'))
  const result = createRayTraceResultFixture()
  result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
    escaped_count: 0, selected_count: 0, excluded_count: 0, cells: [],
    bins: [0,30,60,80].map((min_deg,i) => ({ min_deg, max_deg: [30,60,80,90][i], count: 0, flux_lumen: 0 })) }
  // Forward tracing may stop before the observer has committed its first sample.
  result.metrics._observer_probe_summary = { contract: 'observer_radiance_parallel_v1', measure: 'luminance_cd_m2',
    execution_backend: 'cpu', calibration_state: 'uncalibrated', limitations: ['finite_sampling', 'forward_incomplete', 'partial_result'],
    settings: { ...defaultObserverSettings, resolution: 8 }, attempted_count: 0, planned_count: 128,
    direct_count: 0, reflected_count: 0, excluded_count: 0, depth_limited_count: 0,
    complete: false, stopped: true, runtime_sec: 0, views: [], cells: [] }
  mocks.job = { job_id: 'paused-preview', status: 'completed', phase: 'stopped', resume_available: true,
    progress: .25, processed_rays: 32, total_rays: 128, result }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  await waitFor(() => expect(screen.getByRole('button', { name: '휘도 역추적 이어 실행' })).toBeTruthy())
  expect(leakPreviewStore.getState().result).toBe(result)
  expect(screen.queryByText(/역추적 집계가 없거나 유효하지 않습니다/)).toBeNull()
  if (action === 'changed') {
    act(() => leakPreviewStore.getState().setQuality('deep'))
    expect(screen.queryByRole('button', { name: '휘도 역추적 이어 실행' })).toBeNull()
  }
  fireEvent.click(screen.getByRole('button', { name: action === 'restart' ? 'Preview 처음부터' : action === 'changed' ? 'Run Leak Preview' : '휘도 역추적 이어 실행' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2))
  const before = mocks.start.mock.calls[0][0].request, after = mocks.start.mock.calls[1][0].request
  expect(after.resume_scope).toBe(before.resume_scope)
  if (action === 'restart') expect(after).toEqual({ ...before, restart_from_beginning: true })
  else if (action === 'resume') expect(after).toEqual(before)
  else expect(after).not.toEqual(before)
})

it('sorts radiance views by brightness and preserves the selected angle when sorting changes', () => {
  const scene=createSceneFixture(),result=createRayTraceResultFixture()
  result.metrics._observer_probe_summary={contract:'observer_radiance_parallel_v1',measure:'luminance_cd_m2',
    execution_backend:'cpu',calibration_state:'uncalibrated',limitations:['finite_sampling'],
    settings:{...defaultObserverSettings,resolution:8},attempted_count:128,planned_count:128,
    direct_count:2,reflected_count:0,excluded_count:0,depth_limited_count:0,complete:true,stopped:false,runtime_sec:0,
    views:[-20,20].map((horizontal_deg,i) => ({horizontal_deg,vertical_deg:0,direct_count:1,reflected_count:0,
      direction:[0,0,-1],observer_center:[0,0,-10],u_axis:[1,0,0],v_axis:[0,1,0],spacing_mm:[1,1],peak_luminance_nit:i?10:5})),
    cells:[5,10].map((luminance_nit,view_index) => ({view_index,row:0,column:0,position:[view_index,0,0],
      direct_count:1,reflected_count:0,min_depth:0,max_depth:0,sample_count:1,luminance_nit}))}
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result,[],[]))
  const table=screen.getByRole('table',{name:'시야각 역추적 결과'})
  expect(within(table).getAllByRole('row')[1].textContent).toContain('20 / 0')
  fireEvent.click(within(table).getByRole('button',{name:'20 / 0'}))
  fireEvent.change(screen.getByLabelText('시야각 결과 정렬'),{target:{value:'angle'}})
  expect(within(table).getAllByRole('row')[1].textContent).toContain('-20 / 0')
  expect(leakPreviewStore.getState().highlight).toEqual({kind:'observer',horizontal:20,vertical:0})
})

it('rejects an old count-only server response when the user requested radiance', async () => {
  workspaceStore.getState().actions.addCadCase({ path: 'radiance.step', displayName: 'Radiance' })
  mocks.start.mockResolvedValue({ job_id: 'old-server' })
  const scene=createSceneFixture()
  const view=render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => { leakPreviewStore.getState().setSourceFaceIds([0]); leakPreviewStore.getState().setObserver({ enabled: true }) })
  expect((screen.getByLabelText('역추적 검출 기준') as HTMLSelectElement).value).toBe('radiance')
  fireEvent.click(screen.getByRole('button', { name: 'Run Leak Preview' }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce())
  const result=createRayTraceResultFixture()
  result.metrics._escape_direction_summary={ contract: 'final_escape_directions_v1', direction: 'neg_z',
    escaped_count: 0, selected_count: 0, excluded_count: 0, cells: [],
    bins: [0,30,60,80].map((min_deg,i) => ({ min_deg, max_deg: [30,60,80,90][i],count: 0,flux_lumen: 0 })) }
  result.metrics._observer_probe_summary={ contract: 'observer_path_support_v1', measure: 'path_support_counts',
    execution_backend: 'cpu', settings: { ...defaultObserverSettings, resolution: 8 },
    attempted_count: 64, planned_count: 64, direct_count: 0, reflected_count: 0, excluded_count: 0,
    depth_limited_count: 0, complete: true, stopped: false, runtime_sec: 0, views: [], cells: [] }
  mocks.job={ job_id: 'old-server', status: 'completed', result }
  view.rerender(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  await waitFor(() => expect(screen.getByText(/역추적 집계가 없거나 유효하지 않습니다/)).toBeTruthy())
  expect(leakPreviewStore.getState().result).toBeNull()
})

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
  expect(request.observer_probe).toMatchObject({ measurement: 'radiance', search_mode: 'full_detail', spacing_mm: 1, samples_per_cell: 8 })
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
  expect(screen.getByRole('article', { name: 'Point205' }).getAttribute('aria-current')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: '이전 후보' }))
  expect(screen.getAllByRole('article')).toHaveLength(100)
  fireEvent.click(screen.getByRole('button', { name: 'Leak point List 접기' }))
  act(() => leakPreviewStore.getState().selectCandidate('candidate-204', 'viewer'))
  expect(screen.getByRole('article', { name: 'Point205' })).toBeTruthy()
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
    cells: [{ view_index: 0, row: 0, column: 0, position: [30, 30, 10], direct_count: 1, reflected_count: 0, min_depth: 0, max_depth: 0,
      representative_path: [[30,30,10],canonical.center], path_validation:{status:'valid',endpoint_kind:'cad_surface'},
      aperture:{contract:'bounded_planar_opening_v1',id:'test-opening',position:[30,30,10],exterior_shell_certified:false} }] }
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
  fireEvent.click(screen.getByRole('article', { name: 'Point1' }))
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
  const firstRow = screen.getByRole('article', { name: 'Point1' })
  const secondRow = screen.getByRole('article', { name: 'Point2' })
  const scroll = vi.fn()
  firstRow.scrollIntoView = scroll
  secondRow.scrollIntoView = scroll
  act(() => leakPreviewStore.getState().setHighlight({ kind: 'escape', bin: 0 }))
  fireEvent.click(screen.getByRole('button', { name: 'Leak point List 접기' }))
  act(() => leakPreviewStore.getState().selectCandidate(second.id, 'viewer'))
  expect(screen.getByRole('button', { name: 'Leak point List 접기' }).getAttribute('aria-expanded')).toBe('true')
  expect(secondRow.getAttribute('aria-current')).toBe('true')
  expect(firstRow.hasAttribute('aria-current')).toBe(false)
  expect(scroll.mock.contexts.at(-1)).toBe(secondRow)
  expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest' })
  expect(leakPreviewStore.getState().highlight).toBeNull()
  scroll.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'Leak point List 접기' }))
  expect(screen.queryByRole('article', { name: 'Point2' })).toBeNull()
  expect(scroll).not.toHaveBeenCalled()
  act(() => leakPreviewStore.getState().selectCandidate(second.id, 'viewer'))
  expect(screen.getByRole('article', { name: 'Point2' }).getAttribute('aria-current')).toBe('true')
  expect(scroll.mock.contexts.at(-1)).toBe(secondRow)
  fireEvent.click(firstRow)
  expect(leakPreviewStore.getState().selectedCandidateId).toBe(first.id)
  expect(firstRow.getAttribute('aria-current')).toBe('true')
  expect(leakPreviewStore.getState().points).toEqual(detection.points)
  expect(leakPreviewStore.getState().result).toBe(result)
  act(() => leakPreviewStore.getState().removeCandidate(second.id))
  expect(screen.queryByRole('article', { name: 'Point2' })).toBeNull()
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
  fireEvent.click(screen.getByRole('button',{name:'Leak point List 접기'}))
  expect(screen.getByRole('button',{name:'Leak point List 펼치기'}).getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('button',{name:'Delete Point1'})).toBeNull()
  expect(leakPreviewStore.getState().candidates).toBe(candidates)
  expect(leakPreviewStore.getState().result).toBe(previous)
  fireEvent.click(screen.getByRole('button',{name:'Leak point List 펼치기'}))
  expect(screen.getByRole('button',{name:'Delete Point1'})).toBeTruthy()
  act(() => actions.setEmitterVisible('emitter_001', false))
  expect(leakPreviewStore.getState().result).toBe(previous)
  expect(leakPreviewStore.getState().candidates.length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: 'Delete Point1' }))
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
  fireEvent.click(screen.getByRole('article', { name: 'Point1' }))
  fireEvent.click(screen.getAllByRole('button', { name: 'ROI·Receiver 생성' })[0])
  expect(screen.getAllByRole('button', { name: 'ROI·Receiver 생성' })[0].hasAttribute('disabled')).toBe(true)
  expect(workspaceStore.getState().roiScopes).toEqual([])
  const deletedId = leakPreviewStore.getState().candidates[0].id
  fireEvent.click(screen.getByRole('button', { name: 'Delete Point1' }))
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
  act(() => leakPreviewStore.getState().setObserver({ measurement: 'paths', refine_angles: false, horizontal_deg: 0, vertical_deg: 0, range_deg: 2, step_deg: .5 }))
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


it('distinguishes observer path detections from brightness priority and lists all grouped angles', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'angles.step', displayName: 'Angles' })
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  const candidate = { ...detectLeakPreviewCandidates(scene, result).candidates[0], relativeStrength: .001,
    observerEvidence: { axis: 'neg_z' as const, stepDeg: 15, horizontalDeg: -60, verticalDeg: 15, directCount: 1, reflectedCount: 0,
      direction: [0,0,-1] as [number,number,number], receiverCenter: [0,0,-10] as [number,number,number] },
    observerAngles: [{ horizontalDeg: -60, verticalDeg: 15, directCount: 1, reflectedCount: 0 },
      { horizontalDeg: -60, verticalDeg: 30, directCount: 0, reflectedCount: 2 }], groupedCount: 2 }
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result, [], [candidate]))
  expect(screen.getByText('경로 검출 · 광량 미평가')).toBeTruthy()
  expect(screen.queryByText(/Priority · Low/)).toBeNull()
  expect(screen.getByText('검출 각도 · 2방향')).toBeTruthy()
  expect(screen.getByText('좌우 -60° · 상하 15° · 직광 1 · 반사광 0')).toBeTruthy()
  expect(screen.getByText('좌우 -60° · 상하 30° · 직광 0 · 반사광 2')).toBeTruthy()
  expect(screen.getByText('직광 1 · 반사광 2')).toBeTruthy()
})

it('labels automatically estimated exits and depth variation without requiring face tags', () => {
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  const candidate = {...detectLeakPreviewCandidates(scene,result).candidates[0], apertureId:'estimated:gap',
    aperturePosition:[0,0,0] as [number,number,number], apertureMethod:'opposed_neighborhood_multiscale', apertureVariationMm:.03}
  render(<AppProviders><LeakPreviewPanel scene={scene} onOpenPrecision={() => undefined} /></AppProviders>)
  act(() => leakPreviewStore.getState().setDetection(result,[],[candidate]))
  expect(screen.getByText('출구 추정 · 깊이 변동 0.03 mm')).toBeTruthy()
  expect(screen.getByText('자동 출구 추정 · 최외곽 확정 아님')).toBeTruthy()
})
