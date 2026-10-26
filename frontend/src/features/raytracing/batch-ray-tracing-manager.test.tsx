// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RayTraceJob, RayTraceRequest } from '@/api'
import { workspaceStore } from '@/stores'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

const apiClientMock = vi.hoisted(() => ({
  getScene: vi.fn(),
  getGpuCudaStatus: vi.fn(),
  startRayTrace: vi.fn(),
  getRayTraceJob: vi.fn(),
  stopRayTrace: vi.fn(),
}))

vi.mock('@/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api')>(),
  apiClient: apiClientMock,
}))

import { useBatchRayTracingManager } from './batch-ray-tracing-manager'

function completedJob(request: RayTraceRequest, index: number): RayTraceJob {
  const result = createRayTraceResultFixture()
  result.run_id = `batch-manager-run-${index}`
  result.config = { ...result.config, ...structuredClone(request.config) }
  result.emitters = structuredClone(request.emitters)
  result.receivers = structuredClone(request.receivers)
  result.total_rays = request.emitters
    .filter((emitter) => emitter.enabled)
    .reduce((sum, emitter) => sum + emitter.ray_count, 0)
  return {
    job_id: `batch-manager-job-${index}`,
    status: 'completed',
    phase: 'completed',
    processed_rays: result.total_rays,
    total_rays: result.total_rays,
    progress: 1,
    elapsed_sec: result.runtime_sec,
    estimated_remaining_sec: 0,
    rays_per_sec: result.total_rays / result.runtime_sec,
    created_at: 1,
    completed_at: 2,
    result,
  }
}

function addConfiguredCase(path: string): string {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path, displayName: path })
  const fixture = createRayTraceResultFixture()
  actions.upsertEmitter(fixture.emitters[0])
  actions.upsertReceiver(fixture.receivers[0])
  return workspaceStore.getState().activeCadCaseId!
}

describe('Batch Ray Tracing manager lifecycle', () => {
  beforeEach(() => {
    workspaceStore.getState().actions.resetWorkspace()
    apiClientMock.getScene.mockReset()
      .mockResolvedValue(structuredClone(createSceneFixture()))
    apiClientMock.startRayTrace.mockReset()
      .mockImplementation(async (request: RayTraceRequest) =>
        completedJob(request, apiClientMock.startRayTrace.mock.calls.length))
    apiClientMock.getRayTraceJob.mockReset()
    apiClientMock.stopRayTrace.mockReset()
  })

  afterEach(() => {
    cleanup()
    workspaceStore.getState().actions.resetWorkspace()
  })

  it('clears a terminal Batch summary when the Workspace Case universe changes', async () => {
    const firstCaseId = addConfiguredCase('old-first.step')
    const secondCaseId = addConfiguredCase('old-second.step')
    const { result } = renderHook(() => useBatchRayTracingManager())

    act(() => result.current.selectCases([firstCaseId, secondCaseId]))
    await act(async () => result.current.run())

    expect(result.current.state.status).toBe('completed')
    expect(result.current.state.cases.map((item) => item.caseId)).toEqual([
      firstCaseId,
      secondCaseId,
    ])

    act(() => {
      workspaceStore.getState().actions.resetWorkspace()
      addConfiguredCase('new-first.step')
      addConfiguredCase('new-second.step')
    })

    await waitFor(() => expect(result.current.state.status).toBe('idle'))
    expect(result.current.state.cases).toEqual([])
    expect(result.current.selectedCaseIds).toEqual([])
  })
})
