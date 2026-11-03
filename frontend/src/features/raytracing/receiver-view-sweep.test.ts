// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import type { RayTraceJob, RayTraceRequest } from '@/api'
import { createWorkspaceStore } from '@/stores'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createViewAngleReceiver, defaultReceiverView } from './receiver-view'
import { createReceiverViewSweepController, type ReceiverViewReport } from './receiver-view-sweep'

function setup() {
  const store=createWorkspaceStore(), scene=createSceneFixture(), result=createCompletedRayTraceJobFixture().result
  store.getState().actions.addCadCase({path:'angle.step',displayName:'Angle'})
  store.getState().actions.upsertEmitter(result.emitters[0])
  store.getState().actions.upsertReceiver(createViewAngleReceiver('receiver_001',scene,[30,30,10],{ ...defaultReceiverView,range_deg:1 },3))
  store.getState().actions.setRayTraceConfig({ ...result.config, compute_backend:'cpu',auto_convergence:false })
  return {store,scene}
}
function complete(request: RayTraceRequest): RayTraceJob {
  const job=createCompletedRayTraceJobFixture()
  job.result.receivers=structuredClone(request.receivers)
  job.result.emitters=structuredClone(request.emitters)
  job.result.config={ ...job.result.config,...request.config }
  return job
}

it('runs each angle independently using a shared scene while leaving the user Case untouched', async () => {
  const {store,scene}=setup(), before=structuredClone(store.getState().cadCases), receivers=structuredClone(store.getState().receivers)
  const startRayTrace=vi.fn(async (request: RayTraceRequest) => {
    expect(store.getState().batchRayTraceActive).toBe(true)
    return complete(request)
  })
  const client={getScene:vi.fn(async()=>scene),getGpuCudaStatus:vi.fn(),startRayTrace,getRayTraceJob:vi.fn(),stopRayTrace:vi.fn()}
  let reports: ReceiverViewReport[]=[]
  const controller=createReceiverViewSweepController({store,client,onStateChange:(_state,next)=>{reports=next}})
  const state=await controller.run('receiver_001',scene)
  expect(state.status).toBe('completed')
  expect(startRayTrace).toHaveBeenCalledTimes(9)
  expect(client.getScene).toHaveBeenCalledOnce()
  const requests=startRayTrace.mock.calls.map(([request])=>request)
  expect(new Set(requests.map((request)=>JSON.stringify(request.receivers[0].normal))).size).toBe(9)
  expect(requests.every((request)=>request.receivers.length===1 && !('view_angles' in request.receivers[0]))).toBe(true)
  expect(reports).toHaveLength(9)
  expect(store.getState().receivers).toEqual(receivers)
  expect(store.getState().cadCases).toEqual(before)
  expect(store.getState().batchRayTraceActive).toBe(false)
})

it('shares one detached BSDF library across the reported 99-angle setup without cloning completed results', async () => {
  const { store, scene } = setup(), actions = store.getState().actions
  const asset = createBsdfFixture()
  asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
  actions.addBsdfAsset(asset)
  actions.upsertMaterialAssignment({ assignmentId: 'bsdf', componentId: 1, targetType: 'part', faceIds: [],
    baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', enabled: true, bsdfAssetId: asset.id })
  actions.upsertReceiver(createViewAngleReceiver('receiver_001', scene, [30,30,10], {
    ...defaultReceiverView, horizontal_deg: 85, vertical_deg: 20, range_deg: 10, step_deg: 2,
  }, 3))
  actions.upsertEmitter({ ...store.getState().emitters[0], ray_count: 1_000_000 })
  actions.setRayTraceConfig({ ...store.getState().rayTraceConfig, max_stored_paths: 100 })
  const originalAsset = store.getState().bsdfAssets![0]
  const before = structuredClone(store.getState().cadCases)
  const requests: RayTraceRequest[] = []
  const results: NonNullable<Extract<RayTraceJob, { status: 'completed' }>['result']>[] = []
  const client = { getScene: vi.fn(async () => scene), getGpuCudaStatus: vi.fn(),
    startRayTrace: vi.fn(async (request: RayTraceRequest) => {
      requests.push(request)
      const job = complete(request)
      if (job.status === 'completed') results.push(job.result)
      return job
    }), getRayTraceJob: vi.fn(), stopRayTrace: vi.fn() }
  let reports: ReceiverViewReport[] = []
  const controller = createReceiverViewSweepController({ store, client, onStateChange: (_state, next) => { reports = next } })
  const state = await controller.run('receiver_001', scene)
  expect(state.status).toBe('completed')
  expect(requests).toHaveLength(99)
  expect(new Set(requests.map((request) => request.bsdf_assets![0])).size).toBe(1)
  expect(requests[0].bsdf_assets![0]).not.toBe(originalAsset)
  expect(requests[0].bsdf_assets![0]).toEqual(originalAsset)
  expect(requests.every((request) => request.emitters[0].ray_count === 1_000_000 && request.config.max_stored_paths === 100 &&
    request.config.primary_sampling_strategy === 'source' && request.config.bounce_sampling_strategy === 'source')).toBe(true)
  expect(reports).toHaveLength(99)
  reports.forEach((report, index) => expect(report.result).toBe(results[index]))
  expect(store.getState().cadCases).toEqual(before)
  expect(store.getState().bsdfAssets![0]).toBe(originalAsset)
  expect(client.getScene).toHaveBeenCalledOnce()
})

it('does not start while another analysis owns the global lock', async () => {
  const {store,scene}=setup()
  store.getState().actions.setSingleRayTraceRun({caseId:store.getState().activeCadCaseId!,jobId:'other'})
  const controller=createReceiverViewSweepController({store})
  await expect(controller.run('receiver_001',scene)).rejects.toThrow('다른 해석')
  expect(store.getState().singleRayTraceRun?.jobId).toBe('other')
})

it('stops scheduling angles if the source settings change after a completed angle', async () => {
  const {store,scene}=setup()
  const startRayTrace=vi.fn(async (request: RayTraceRequest)=>complete(request))
  const client={getScene:vi.fn(async()=>scene),getGpuCudaStatus:vi.fn(),startRayTrace,getRayTraceJob:vi.fn(),stopRayTrace:vi.fn()}
  const controller=createReceiverViewSweepController({store,client,onStateChange:(state)=>{
    if(state.cases.some((item)=>item.status==='completed')) store.getState().actions.setRayTraceConfig({...store.getState().rayTraceConfig,max_depth:7})
  }})
  const state=await controller.run('receiver_001',scene)
  expect(startRayTrace).toHaveBeenCalledOnce()
  expect(state.status).toBe('completed_with_errors')
  expect(store.getState().batchRayTraceActive).toBe(false)
})

it('cancels the active backend job and never starts the remaining angles', async () => {
  const {store,scene}=setup()
  const queued: RayTraceJob={job_id:'active-angle',status:'queued',phase:'queued',processed_rays:0,total_rays:100,progress:0,elapsed_sec:0,estimated_remaining_sec:null,rays_per_sec:0,created_at:1}
  const cancelled: RayTraceJob={...queued,status:'cancelled',phase:'stopped',completed_at:2}
  let stopped=false
  const client={getScene:vi.fn(async()=>scene),getGpuCudaStatus:vi.fn(),startRayTrace:vi.fn(async()=>queued),
    getRayTraceJob:vi.fn(async()=>stopped ? cancelled : queued),stopRayTrace:vi.fn(async()=>{stopped=true;return cancelled})}
  const controller=createReceiverViewSweepController({store,client,pollIntervalMs:1})
  const running=controller.run('receiver_001',scene)
  await vi.waitFor(()=>expect(client.startRayTrace).toHaveBeenCalledOnce())
  await controller.cancel()
  const state=await running
  expect(client.stopRayTrace).toHaveBeenCalledWith('active-angle')
  expect(client.startRayTrace).toHaveBeenCalledOnce()
  expect(state.status).toBe('cancelled')
  expect(state.cases.every((item)=>item.status==='cancelled')).toBe(true)
  expect(store.getState().batchRayTraceActive).toBe(false)
})
