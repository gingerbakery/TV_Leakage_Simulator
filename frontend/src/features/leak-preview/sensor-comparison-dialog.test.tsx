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
})
