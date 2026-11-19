// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import type { RayTraceJob } from '@/api'
import { createViewAngleReceiver, defaultReceiverView } from './receiver-view'

const mocks=vi.hoisted(()=>({getScene:vi.fn(),getGpuCudaStatus:vi.fn(),startRayTrace:vi.fn(),getRayTraceJob:vi.fn(),stopRayTrace:vi.fn()}))
vi.mock('@/api',async(importOriginal)=>({...await importOriginal<typeof import('@/api')>(),apiClient:mocks}))
import { useReceiverViewSweepManager } from './receiver-view-sweep-manager'
import { ReceiverViewSweepDialog } from './receiver-view-sweep-dialog'

afterEach(()=>{cleanup();vi.useRealTimers();workspaceStore.getState().actions.resetWorkspace();vi.clearAllMocks()})

it('shows the active phase and processed rays when overall progress would round to zero', () => {
  vi.useFakeTimers()
  const startedAt = Date.now()
  const current = {
    caseId: 'active', name: '14° / 9°', status: 'running' as const, progress: 0.00001,
    phaseDetail: 'Ray 계산 진행 중', processedRays: 10, totalRays: 1_000_000,
    jobId: 'job', runId: null, error: null, geometryCacheHit: null, segmentIndex: 0, rayMultiplier: 1,
  }
  const manager: React.ComponentProps<typeof ReceiverViewSweepDialog>['manager'] = {
    open: true, setOpen: vi.fn(), reportOpen: false, setReportOpen: vi.fn(),
    reports: [], error: '', stopping: false, scene: undefined, isRunning: true,
    run: vi.fn(), cancel: vi.fn(),
    state: { batchId: 'sweep', status: 'running', currentCaseId: current.caseId,
      progress: current.progress / 2, startedAt, completedAt: null,
      cases: [current, { ...current, caseId: 'next', name: '14° / 10°', status: 'pending' }] },
  }
  const { rerender } = render(<ReceiverViewSweepDialog manager={manager} />)
  expect(screen.getByRole('status').textContent).toContain('<0.01%')
  expect(screen.getByRole('status').textContent).toContain('14° / 9° · Ray 계산 진행 중')
  expect(screen.getByRole('status').textContent).toContain(`10 / ${(1_000_000).toLocaleString()} Ray`)
  expect(screen.getByText('대기')).toBeTruthy()
  act(() => vi.advanceTimersByTime(2_000))
  expect(screen.getByRole('timer').textContent).toBe('00:00:02')
  rerender(<ReceiverViewSweepDialog manager={{ ...manager, isRunning: false,
    state: { ...manager.state!, status: 'completed', completedAt: startedAt + 3_000 },
  }} />)
  act(() => vi.advanceTimersByTime(5_000))
  expect(screen.getByRole('timer').textContent).toBe('00:00:03')
})

it('keeps the queue alive when the progress window closes and stops it explicitly on cancel',async()=>{
  const scene=createSceneFixture(), actions=workspaceStore.getState().actions
  actions.addCadCase({path:'angle.step',displayName:'Angle'})
  actions.upsertEmitter(createRayTraceResultFixture().emitters[0])
  actions.upsertReceiver(createViewAngleReceiver('receiver_001',scene,[30,30,10],{...defaultReceiverView,range_deg:1},3))
  actions.setRayTraceConfig({...workspaceStore.getState().rayTraceConfig,compute_backend:'cpu',auto_convergence:false})
  const queued:RayTraceJob={job_id:'angle-job',status:'queued',phase:'queued',processed_rays:0,total_rays:100,progress:0,elapsed_sec:0,estimated_remaining_sec:null,rays_per_sec:0,created_at:1}
  mocks.getScene.mockResolvedValue(scene);mocks.startRayTrace.mockResolvedValue(queued);mocks.getRayTraceJob.mockResolvedValue(queued)
  mocks.stopRayTrace.mockResolvedValue({...queued,status:'cancelled',phase:'stopped',completed_at:2})
  const {result}=renderHook(()=>useReceiverViewSweepManager())
  let running:Promise<void>
  act(()=>{running=result.current.run('receiver_001',scene)})
  await waitFor(()=>expect(mocks.startRayTrace).toHaveBeenCalledOnce())
  act(()=>result.current.setOpen(false))
  expect(result.current.isRunning).toBe(true)
  expect(workspaceStore.getState().batchRayTraceActive).toBe(true)
  await act(async()=>{await result.current.cancel();await running!})
  expect(result.current.state?.status).toBe('cancelled')
  expect(mocks.startRayTrace).toHaveBeenCalledOnce()
  expect(mocks.stopRayTrace).toHaveBeenCalledWith('angle-job')
  expect(workspaceStore.getState().batchRayTraceActive).toBe(false)
})
