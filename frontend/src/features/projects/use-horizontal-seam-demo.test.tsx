// @vitest-environment jsdom
import { StrictMode, type ReactNode } from 'react'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api'
import { createWorkspaceStore, workspaceStore } from '@/stores'
import { findPendingRayTraceResultSourceContext } from '@/features/raytracing'
import { createSceneFixture } from '@/test/scene-fixture'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'
import { createBitsamProject } from './bitsam-project'
import { prepareHorizontalSeamDemo, useHorizontalSeamDemo } from './use-horizontal-seam-demo'

function fixtures() {
  const scene = createSceneFixture()
  const job = createCompletedRayTraceJobFixture()
  const source = createWorkspaceStore()
  const actions = source.getState().actions
  actions.addCadCase({ path: 'source.step', displayName: 'horizontal_gap_0p3_16x.step' })
  job.result.emitters.forEach(actions.upsertEmitter)
  job.result.receivers.forEach(actions.upsertReceiver)
  actions.setRayTraceConfig({ ...source.getState().rayTraceConfig, ...job.result.config, auto_convergence: false, compute_backend: 'cpu' })
  const project = createBitsamProject(scene, source.getState(), new Date(), job.result)
  const manifest = { schema: 'horizontal-seam-demo.v1', defaultCase: '16x', cases: [{ id: '16x', cadName: 'horizontal_gap_0p3_16x.step', cadUrl: '/outputs/demo-horizontal-gap-0p3-16x.step', projectUrl: '/outputs/demo-horizontal-gap-0p3-16x.bitsam' }] }
  const fetchFile = vi.fn<typeof fetch>().mockImplementation(async (input) => new Response(
    String(input).endsWith('.bitsam') ? JSON.stringify(project) :
    String(input).endsWith('.json') ? JSON.stringify(manifest) : 'ISO-10303-21;'))
  const client = { ...apiClient,
    uploadCad: vi.fn().mockResolvedValue({ path: 'uploaded-demo.step', display_name: 'horizontal_gap_0p3_16x.step' }),
    getScene: vi.fn().mockResolvedValue(scene),
    startRayTrace: vi.fn().mockResolvedValue(job),
    stopRayTrace: vi.fn().mockResolvedValue(job),
  }
  return { scene, job, project, fetchFile, client }
}

afterEach(async () => {
  cleanup()
  await new Promise((resolve) => setTimeout(resolve, 5))
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('explicit horizontal seam demo', () => {
  it('restores compatible settings and starts one real request with its exact source context, preserving other Cases', async () => {
    const fixture = fixtures()
    const store = createWorkspaceStore()
    store.getState().actions.addCadCase({ path: 'user.step', displayName: 'user.step' })
    const firstCase = store.getState().cadCases[0]
    const run = await prepareHorizontalSeamDemo({ ...fixture, store, queryClient: new QueryClient(), signal: new AbortController().signal, onMessage: vi.fn() })
    expect(fixture.client.startRayTrace).toHaveBeenCalledTimes(1)
    const request = fixture.client.startRayTrace.mock.calls[0][0]
    expect(request.emitters).toEqual(fixture.project.workspace.emitters)
    expect(request.receivers).toEqual(fixture.project.workspace.receivers)
    expect(request.config.compute_backend).toBe('cpu')
    expect(store.getState().cadCases[0].caseId).toBe(firstCase.caseId)
    expect(store.getState().cadCases).toHaveLength(2)
    expect(store.getState().activeRayTraceJobId).toBe(run.jobId)
    expect(store.getState().restoredRayTraceResult).toBeNull()
    expect(store.getState().cadCases[1].latestResult).toBeNull()
    const context = findPendingRayTraceResultSourceContext(run.jobId)!
    expect(context.cad_case_id).toBe(run.caseId)
    expect(context.requests).toEqual([request])
    expect(context.scene.scene_token).toBe(fixture.scene.metadata.scene_token)
  })

  it('fails closed for mismatched CAD instead of running or restoring archived results', async () => {
    const fixture = fixtures()
    fixture.client.getScene.mockResolvedValue({ ...fixture.scene, metadata: { ...fixture.scene.metadata, face_count: fixture.scene.metadata.face_count + 1 } })
    await expect(prepareHorizontalSeamDemo({ ...fixture, store: createWorkspaceStore(), queryClient: new QueryClient(), signal: new AbortController().signal, onMessage: vi.fn() })).rejects.toThrow('형상이 일치하지')
    expect(fixture.client.startRayTrace).not.toHaveBeenCalled()
  })

  it('stops a known job if the component is aborted during the start response', async () => {
    const fixture = fixtures()
    const controller = new AbortController()
    fixture.client.startRayTrace.mockImplementation(async () => { controller.abort(); return fixture.job })
    const store = createWorkspaceStore()
    await expect(prepareHorizontalSeamDemo({ ...fixture, store, queryClient: new QueryClient(), signal: controller.signal, onMessage: vi.fn() })).rejects.toThrow()
    expect(fixture.client.stopRayTrace).toHaveBeenCalledWith(fixture.job.job_id)
    expect(store.getState().activeRayTraceJobId).toBeNull()
  })

  it('does nothing at the ordinary URL and starts once through StrictMode replay at the explicit demo URL', async () => {
    const fixture = fixtures()
    vi.stubGlobal('fetch', fixture.fetchFile)
    vi.spyOn(apiClient, 'uploadCad').mockImplementation(fixture.client.uploadCad)
    vi.spyOn(apiClient, 'getScene').mockImplementation(fixture.client.getScene)
    vi.spyOn(apiClient, 'startRayTrace').mockImplementation(fixture.client.startRayTrace)
    vi.spyOn(apiClient, 'stopRayTrace').mockImplementation(fixture.client.stopRayTrace)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode><QueryClientProvider client={queryClient}>{children}</QueryClientProvider></StrictMode>
    const ordinary = renderHook(() => useHorizontalSeamDemo(''), { wrapper })
    expect(ordinary.result.current.stage).toBe('inactive')
    expect(fixture.fetchFile).not.toHaveBeenCalled()
    expect(fixture.client.startRayTrace).not.toHaveBeenCalled()
    ordinary.unmount()
    const demo = renderHook(() => useHorizontalSeamDemo('?demo=horizontal-seam'), { wrapper })
    await waitFor(() => expect(demo.result.current.stage).toBe('running'))
    expect(fixture.client.uploadCad).toHaveBeenCalledTimes(1)
    expect(fixture.client.startRayTrace).toHaveBeenCalledTimes(1)
    expect(fixture.fetchFile).toHaveBeenCalledTimes(3)
    expect(fixture.client.stopRayTrace).not.toHaveBeenCalled()
  })
})