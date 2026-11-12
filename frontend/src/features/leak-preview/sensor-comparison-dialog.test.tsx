// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiClient } from '@/api'
import { createSceneFixture } from '@/test/scene-fixture'
import { buildRayTraceRequest, createDatumEmitter, createDatumReceiver } from '@/features/raytracing/ray-tracing-model'
import { SensorComparisonDialog } from './sensor-comparison-dialog'
import type { SensorComparisonJob } from '@/api/client'
import { workspaceStore } from '@/stores'
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const request = () => buildRayTraceRequest({ scene: createSceneFixture(), projectName: 'validation',
  materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: workspaceStore.getState().rayTraceConfig,
  emitters: [createDatumEmitter('source', [0,0,1], [0,0,0])], receivers: [createDatumReceiver('sensor', [0,0,10], [0,0,0])] })

it('guides low forward ESS, reveals steps on demand, and applies a valid plan without starting a run', async () => {
  const input=request()
  const start=vi.spyOn(apiClient,'startSensorComparison')
  vi.spyOn(apiClient,'getSensorComparison').mockResolvedValue({job_id:'done',status:'completed',scene_token:input.scene_token,input_request:structuredClone(input),
    progress:1,batch:8,phase:'finished',stop_requested:false,error:null,report:{
      sensor:{receiver_id:'sensor',width_mm:input.receivers[0].width_mm,height_mm:input.receivers[0].height_mm,acceptance_angle_deg:input.receivers[0].acceptance_angle_deg},options:{receiver_id:'sensor',batches:8,forward_samples:16384,reverse_samples:1024,tolerance_percent:5},
      verdict:'inconclusive',reason:'insufficient_effective_samples',gpu_execution_proven:true,
      forward_hits:100,reverse_positive_samples:1000,forward_effective_samples:16.2,reverse_effective_samples:378}})
  render(<SensorComparisonDialog request={input} onClose={() => undefined} />)
  await waitFor(() => expect(screen.getByText('다음: F 표본 증가 후 재비교')).toBeTruthy())
  expect(screen.queryByText(/유효표본은 발사 수/)).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'광량 비교 다음 단계 도움말'}))
  expect(screen.getByText(/유효표본은 발사 수/)).toBeTruthy()
  fireEvent.keyDown(document.activeElement!,{key:'Escape'})
  fireEvent.click(screen.getByRole('button',{name:'권장 표본 적용'}))
  expect(screen.getByRole('spinbutton',{name:'순방향 표본'})).toHaveProperty('value','65536')
  expect(screen.getByRole('spinbutton',{name:'역추적 표본'})).toHaveProperty('value','1024')
  expect(screen.getByRole('spinbutton',{name:'허용 차이'})).toHaveProperty('value','5')
  expect(screen.getByText('권장 표본 설정 적용 · 비교 시작 필요')).toBeTruthy()
  expect(start).not.toHaveBeenCalled()
})

it('rejects a same-size result with a changed receiver pose',async()=>{
  const input=request(),old=structuredClone(input); old.receivers[0].center[0]+=1
  vi.spyOn(apiClient,'getSensorComparison').mockResolvedValue({job_id:'old-pose',input_request:old,status:'completed',scene_token:input.scene_token,
    progress:1,batch:8,phase:'finished',stop_requested:false,error:null,report:{sensor:{receiver_id:'sensor',width_mm:input.receivers[0].width_mm,height_mm:input.receivers[0].height_mm,acceptance_angle_deg:input.receivers[0].acceptance_angle_deg},
      options:{receiver_id:'sensor',batches:8,forward_samples:16384,reverse_samples:1024,tolerance_percent:5},verdict:'inconclusive',reason:'insufficient_effective_samples',gpu_execution_proven:true,forward_hits:100,reverse_positive_samples:100,forward_effective_samples:16,reverse_effective_samples:100}})
  render(<SensorComparisonDialog request={input} onClose={()=>undefined}/>)
  await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('이전 작업 결과'))
  expect(screen.getByText('다음: 현재 조건으로 다시 비교')).toBeTruthy()
  expect(screen.queryByRole('button',{name:'권장 표본 적용'})).toBeNull()
})

it('starts GPU sensor comparison with the frozen request and can explicitly stop it', async () => {
  const input = request()
  const job: SensorComparisonJob = { job_id: 'comparison', scene_token: input.scene_token, status: 'running', phase: 'reverse',
    progress: .3, batch: 3, stop_requested: false, error: null, report: null }
  const get = vi.spyOn(apiClient, 'getSensorComparison').mockResolvedValue(null)
  const start = vi.spyOn(apiClient, 'startSensorComparison').mockImplementation(async () => { get.mockResolvedValue(job); return job })
  const stop = vi.spyOn(apiClient, 'stopSensorComparison').mockResolvedValue({ ...job, stop_requested: true })
  render(<SensorComparisonDialog request={input} onClose={() => undefined} />)
  await waitFor(() => expect(get).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: '비교 시작' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '비교 시작' }).hasAttribute('disabled')).toBe(true))
  expect(start).toHaveBeenCalledWith(input, { receiver_id: 'sensor', batches: 8, forward_samples: 16384, reverse_samples: 1024, tolerance_percent: 5 })
  fireEvent.click(screen.getByRole('button', { name: '중지' }))
  await waitFor(() => expect(stop).toHaveBeenCalledWith('comparison'))
  expect(screen.getByText(/NVIDIA CUDA GPU/)).toBeTruthy()
})

it('labels a completed zero-sample result inconclusive and does not claim GPU execution', async () => {
  const input = request()
  vi.spyOn(apiClient, 'getSensorComparison').mockResolvedValue({ job_id: 'old', status: 'completed', scene_token: 'other',
    progress: 1, batch: 8, phase: 'finished', stop_requested: false, error: null,
    report: { sensor: { receiver_id: 'other' }, options: { receiver_id: 'other', batches: 8, forward_samples: 16384, reverse_samples: 1024, tolerance_percent: 5 },
      verdict: 'inconclusive', reason: 'zero_samples_not_zero_light_proof', gpu_execution_proven: false,
      forward_flux_lumen: 0, reverse_flux_lumen: 0, forward_hits: 0, reverse_positive_samples: 0 } })
  render(<SensorComparisonDialog request={input} onClose={() => undefined} />)
  await waitFor(() => expect(screen.getByText('미확정 · 0 검출 · 광량 0의 증거 아님')).toBeTruthy())
  expect(screen.getByText('GPU 실행 미확인')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('이전 작업 결과')
  expect(screen.getByText('순방향 미검출 진단 · 양방향 양수 기여 미확인 · 원인 미확정')).toBeTruthy()
  expect(screen.queryByText(/좁은 틈·작은 센서/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '순방향 미검출 진단 도움말' }))
  expect(screen.getByText(/좁은 틈·작은 센서/)).toBeTruthy()
})

it('identifies BSDF host evaluation with CUDA intersections as mixed execution', async () => {
  const input = request()
  vi.spyOn(apiClient, 'getSensorComparison').mockResolvedValue({ job_id: 'bsdf', status: 'completed', scene_token: input.scene_token,
    progress: 1, batch: 8, phase: 'finished', stop_requested: false, error: null, report: {
      sensor: { receiver_id: 'sensor' }, options: { receiver_id: 'sensor', batches: 8, forward_samples: 16384, reverse_samples: 1024, tolerance_percent: 5 },
      verdict: 'inconclusive', reason: 'uncertainty_too_large', gpu_execution_proven: true, compute_execution_state: 'gpu_mixed',
      batches: [{ forward_execution: { gpu_cuda_gpu_success_count: 1 }, reverse_execution: { observer_bsdf_evaluation_backend: 'cpu', gpu_cuda_gpu_success_count: 2 } }],
    } })
  render(<SensorComparisonDialog request={input} onClose={() => undefined} />)
  await waitFor(() => expect(screen.getByText('BSDF CPU 평가 · CAD 교차 GPU')).toBeTruthy())
  expect(screen.getByText('GPU 실행 확인 · CPU 보조 포함')).toBeTruthy()
})
