// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import type { SensorComparisonJob } from '@/api/client'
import { createSceneFixture } from '@/test/scene-fixture'
import { buildRayTraceRequest, createDatumEmitter, createDatumReceiver } from '@/features/raytracing/ray-tracing-model'
import { workspaceStore } from '@/stores'
import { buildAiReviewPayload, installAiReviewExport, type ReviewSnapshot } from './ai-review-export'

function fixture() {
  const scene = createSceneFixture()
  const request = buildRayTraceRequest({ scene, projectName: 'review', materialAssignments: [], transformRules: [],
    excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: workspaceStore.getState().rayTraceConfig,
    emitters: [createDatumEmitter('source', [0, 0, 1], [0, 0, 0])], receivers: [createDatumReceiver('sensor', [0, 0, 10], [0, 0, 0])] })
  const snapshot: ReviewSnapshot = { caseId: 'case', scene, currentRequest: request, requestError: null,
    preview: { result: null, runSignature: 'saved', currentSignature: 'current', selectedPoint: { representativePath: [[0, 0, 0], [0, 0, 10]] }, settings: {}, inputMatches: false },
    pathValidation: { valid: true, reason: 'reference_path_valid' }, forward: { jobId: 'forward', savedResult: null } }
  const comparison: SensorComparisonJob = { job_id: 'comparison', scene_token: scene.metadata.scene_token,
    input_request: structuredClone(request), input_request_sha256: 'request-hash', status: 'completed', phase: 'finished', progress: 1, batch: 8, stop_requested: false, error: null,
    report: { sensor: { receiver_id: 'sensor' }, options: { receiver_id: 'sensor', batches: 8, forward_samples: 16384, reverse_samples: 1024, tolerance_percent: 5 },
      verdict: 'inconclusive', reason: 'insufficient_effective_samples', forward_hits: 35, reverse_positive_samples: 1,
      forward_diagnostics: [{ index: 2, report: { termination: { roulette_terminated_count: 1 } } }],
      batches: [{ index: 3, forward_flux_lumen: 6.04504e-12, reverse_flux_lumen: 1.29165e-4, forward_execution: {}, reverse_execution: {} }] } }
  return { snapshot, comparison }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('retains raw bidirectional batches, conditions, selected path and termination evidence together', () => {
  const { snapshot, comparison } = fixture()
  const payload = buildAiReviewPayload(snapshot, comparison, { status: 'completed', result: { metrics: { diagnostic: 'raw' } } }, [], true)
  const parsed = JSON.parse(JSON.stringify(payload))
  expect(parsed.same_sensor_comparison).toEqual(comparison)
  expect(parsed.current_validation_request).toEqual(snapshot.currentRequest)
  expect(parsed.reverse_preview.selectedPoint).toEqual(snapshot.preview.selectedPoint)
  expect(parsed.identity.comparison_input_matches_current).toBe(true)
  expect(parsed.identity.preview_input_matches_current).toBe(false)
  expect(parsed.scene.mesh).toEqual(snapshot.scene!.mesh)
  expect(parsed.standalone_forward.job.result.metrics.diagnostic).toBe('raw')
  expect(payload.evidence_gaps.join(' ')).toContain('전체 검출 Ray 궤적을 저장하지 않음')
})

it.each(['receiver', 'scene', 'missing_snapshot'] as const)('does not certify comparison identity for %s', mode => {
  const { snapshot, comparison } = fixture()
  if (mode === 'receiver') comparison.input_request!.receivers[0].center[0] += 1
  if (mode === 'scene') comparison.scene_token = 'other-scene'
  if (mode === 'missing_snapshot') delete comparison.input_request
  const payload = buildAiReviewPayload(snapshot, comparison, null, [], true)
  expect(payload.identity.comparison_input_matches_current).toBe(false)
  expect(payload.evidence_gaps.join(' ')).toContain('함께 집계하지 말 것')
  expect(payload.same_sensor_comparison).toBe(comparison)
})

it('exports partial and failed lookups explicitly and omits mesh only on request', () => {
  const { snapshot, comparison } = fixture()
  comparison.status = 'running'
  const payload = buildAiReviewPayload(snapshot, comparison, null, ['순방향 작업 조회 실패'], false)
  expect(payload.scene).not.toHaveProperty('mesh')
  expect(payload.evidence_gaps.join(' ')).toContain('광량 비교 미완료: running')
  expect(payload.evidence_gaps.join(' ')).toContain('조회 실패')
  expect(buildAiReviewPayload(snapshot, null, null, [], false).evidence_gaps.join(' ')).toContain('양방향 광량 비교 결과 없음')
})

it('freezes inputs before fetching, downloads one local file and cleans up the console hook', async () => {
  const { snapshot, comparison } = fixture()
  const getComparison = vi.fn(async () => { snapshot.currentRequest!.receivers[0].center[0] += 99; return comparison })
  const api = { getSensorComparison: getComparison, getRayTraceJob: vi.fn().mockRejectedValue(new Error('expired')) }
  const blobUrl = vi.fn(() => 'blob:test')
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: blobUrl })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer) } })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const cleanup = installAiReviewExport(() => snapshot, api)
  const host = window as Window & { exportLeakAiReview?: (options?: { includeGeometry?: boolean }) => Promise<{ filename: string; bytes: number; gaps: string[] }> }
  const result = await host.exportLeakAiReview!()
  expect(click).toHaveBeenCalledTimes(1)
  expect(blobUrl).toHaveBeenCalledTimes(1)
  expect(result.filename).toMatch(/^leak-ai-review-.*\.json$/)
  expect(result.bytes).toBeGreaterThan(100)
  expect(result.gaps.join(' ')).toContain('expired')
  expect(result.gaps.join(' ')).not.toContain('함께 집계하지 말 것')
  expect(document.querySelector('a[download]')).toBeNull()
  expect(api.getRayTraceJob).toHaveBeenCalledWith('forward')
  cleanup()
  expect(host.exportLeakAiReview).toBeUndefined()
})
