// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { createHash, webcrypto } from 'node:crypto'
import type { SensorComparisonJob } from '@/api/client'
import { createSceneFixture } from '@/test/scene-fixture'
import { buildRayTraceRequest, createDatumEmitter, createDatumReceiver } from '@/features/raytracing/ray-tracing-model'
import { workspaceStore } from '@/stores'
import { buildAiReviewPayload, captureAiReviewSnapshot, createAiReviewBlob, jsonChunks, installAiReviewExport, type ReviewSnapshot } from './ai-review-export'

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

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

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


it('omits lazy geometry before reading or copying it', () => {
  const { snapshot } = fixture()
  Object.defineProperty(snapshot.scene!, 'mesh', { enumerable: true, get() { throw new Error('must not read mesh') } })
  const frozen = captureAiReviewSnapshot(snapshot, false)
  expect(frozen.scene).not.toHaveProperty('mesh')
  expect(frozen.scene!.metadata).toEqual(snapshot.scene!.metadata)
  snapshot.currentRequest!.receivers[0].center[0] = 999
  expect(frozen.currentRequest!.receivers[0].center[0]).not.toBe(999)
})

it('serializes bounded chunks without stringifying whole objects, preserving JSON and Unicode', () => {
  const value = { text: '한글😀'.repeat(100), rows: Array.from({ length: 1000 }, (_, i) => [i, i / 3]), missing: undefined, nonfinite: Infinity }
  const expected = JSON.stringify(value)
  const original = JSON.stringify
  vi.spyOn(JSON, 'stringify').mockImplementation(((item: unknown) => {
    if (item !== null && typeof item === 'object') throw new RangeError('simulated whole-object string limit')
    return original(item)
  }) as typeof JSON.stringify)
  const parts = jsonChunks(value, 31)
  expect(parts.length).toBeGreaterThan(100)
  expect(parts.every(part => part.length <= 31)).toBe(true)
  expect(parts.join('')).toBe(expected)
  const encoded = parts.map(part => new TextEncoder().encode(part))
  expect(encoded.map(part => new TextDecoder().decode(part)).join('')).toBe(expected)
})


async function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

function downloadHarness() {
  const createUrl = vi.fn((_blob: Blob) => 'blob:review')
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  return { createUrl, host: window as unknown as Window & { exportLeakAiReview: () => Promise<{ gaps: string[] }> } }
}

it('writes a parseable downloaded document whose checksum independently verifies', async () => {
  const { snapshot, comparison } = fixture()
  const { host, createUrl } = downloadHarness()
  vi.stubGlobal('crypto', webcrypto)
  const cleanup = installAiReviewExport(() => snapshot, {
    getSensorComparison: async () => comparison,
    getRayTraceJob: async () => ({ status: 'completed', result: { flux: 3.412e-7, label: '한글😀' } }),
  })
  try {
    await host.exportLeakAiReview()
    const downloaded = JSON.parse(await readBlob(createUrl.mock.calls[0][0] as unknown as Blob))
    expect(downloaded.payload_sha256).toBe(createHash('sha256').update(JSON.stringify(downloaded.payload)).digest('hex'))
    expect(downloaded.payload.same_sensor_comparison).toEqual(comparison)
    expect(downloaded.payload.path_validation).toEqual(snapshot.pathValidation)
    expect(downloaded.payload.standalone_forward.job.result.label).toBe('한글😀')
    expect(downloaded.payload.scene).not.toHaveProperty('mesh')
  } finally { cleanup() }
})

it.each(['unavailable', 'rejected', 'large'] as const)('still preserves evidence when checksum is %s', async mode => {
  const { snapshot, comparison } = fixture()
  const digest = vi.fn().mockRejectedValue(new Error('blocked'))
  vi.stubGlobal('crypto', mode === 'unavailable' ? {} : { subtle: { digest } })
  const payload = buildAiReviewPayload(snapshot, comparison, null, [], false)
  const downloaded = JSON.parse(await readBlob(await createAiReviewBlob(payload, mode === 'large' ? 1 : undefined)))
  expect(downloaded.payload_sha256).toBeNull()
  expect(downloaded.hash_basis).toContain('not computed')
  expect(downloaded.payload.same_sensor_comparison).toEqual(comparison)
  expect(downloaded.payload.evidence_gaps.join(' ')).toContain('SHA-256 미계산')
  if (mode === 'large') expect(digest).not.toHaveBeenCalled()
})

it('exports available records after lookup timeout, rejects concurrent exports across hook replacement, then unlocks', async () => {
  vi.useFakeTimers()
  const { snapshot } = fixture()
  const { host, createUrl } = downloadHarness()
  vi.stubGlobal('crypto', {})
  const api = { getSensorComparison: vi.fn(() => new Promise<null>(() => {})), getRayTraceJob: vi.fn(async () => ({ status: 'completed' })) }
  const cleanup = installAiReviewExport(() => snapshot, api)
  const pending = host.exportLeakAiReview()
  const replacementCleanup = installAiReviewExport(() => snapshot, api)
  try {
    await expect(host.exportLeakAiReview()).rejects.toThrow('이미 진행 중')
    await vi.advanceTimersByTimeAsync(30000)
    const result = await pending
    expect(result.gaps.join(' ')).toContain('결과 조회 30초 초과')
    expect(createUrl).toHaveBeenCalledTimes(1)
    expect(snapshot.pathValidation).toEqual({ valid: true, reason: 'reference_path_valid' })
    api.getSensorComparison.mockImplementation(async () => null)
    await host.exportLeakAiReview()
    expect(createUrl).toHaveBeenCalledTimes(2)
  } finally { cleanup(); replacementCleanup() }
})

it('records synchronous API failures without losing the other lookup', async () => {
  const { snapshot } = fixture()
  const { host } = downloadHarness()
  vi.stubGlobal('crypto', {})
  const getForward = vi.fn(async () => ({ status: 'completed' }))
  const cleanup = installAiReviewExport(() => snapshot, {
    getSensorComparison() { throw new Error('sync failure') }, getRayTraceJob: getForward,
  })
  try {
    const result = await host.exportLeakAiReview()
    expect(result.gaps.join(' ')).toContain('sync failure')
    expect(getForward).toHaveBeenCalledOnce()
  } finally { cleanup() }
})

it('does not duplicate shared records and reports cycles before a stack overflow', () => {
  const { snapshot } = fixture()
  const shared = { rows: [1, 2, 3] }
  snapshot.preview.settings = { first: shared, second: shared }
  const copied = captureAiReviewSnapshot(snapshot, false).preview.settings as { first: unknown; second: unknown }
  expect(copied.first).toBe(copied.second)
  expect(copied.first).not.toBe(shared)
  snapshot.preview.settings = snapshot
  expect(() => captureAiReviewSnapshot(snapshot, false)).toThrow('순환 참조')
})

it('releases the export lock and removes the link after download creation fails', async () => {
  const { snapshot } = fixture()
  const { host } = downloadHarness()
  vi.stubGlobal('crypto', {})
  const cleanup = installAiReviewExport(() => snapshot, { getSensorComparison: async () => null, getRayTraceJob: async () => null })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementationOnce(() => { throw new Error('download blocked') })
  try {
    await expect(host.exportLeakAiReview()).rejects.toThrow('download blocked')
    expect(document.querySelector('a[download]')).toBeNull()
    click.mockImplementation(() => undefined)
    await host.exportLeakAiReview()
    expect(click).toHaveBeenCalledTimes(2)
  } finally { cleanup() }
})
