// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import type { RayTraceJob } from '@/api'
import { createViewAngleReceiver, defaultReceiverView } from './receiver-view'

const mocks=vi.hoisted(()=>({getScene:vi.fn(),getGpuCudaStatus:vi.fn(),startRayTrace:vi.fn(),getRayTraceJob:vi.fn(),stopRayTrace:vi.fn()}))
vi.mock('@/api',async(importOriginal)=>({...await importOriginal<typeof import('@/api')>(),apiClient:mocks}))
import { useReceiverViewSweepManager } from './receiver-view-sweep-manager'

afterEach(()=>{cleanup();workspaceStore.getState().actions.resetWorkspace();vi.clearAllMocks()})

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
