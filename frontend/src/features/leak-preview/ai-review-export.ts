import type { RayTraceRequest, RayTraceResult, ScenePayload } from '@/api'
import type { SensorComparisonJob } from '@/api/client'
import { sameComparisonInput } from './comparison-input-identity'
import { aiReviewGuide } from './ai-review-guide'

export interface ReviewSnapshot {
  caseId: string | null
  scene: ScenePayload | null
  preview: { result: RayTraceResult | null; runSignature: string | null; currentSignature: string; selectedPoint: unknown; settings: unknown; inputMatches: boolean }
  currentRequest: RayTraceRequest | null
  requestError: string | null
  pathValidation: unknown
  forward: { jobId: string | null; savedResult: RayTraceResult | null }
}

/** Copy JSON data without creating a single string for the entire CAD scene. */
function copyJsonValue(value: unknown, copies = new WeakMap<object, unknown>(), active = new WeakSet<object>()): unknown {
  if (value === null || typeof value !== 'object') return value
  if (active.has(value)) throw new TypeError('AI 검토 자료에 순환 참조가 있어 저장할 수 없습니다. 기존 결과는 유지됩니다.')
  if (copies.has(value)) return copies.get(value)
  active.add(value)
  const copied: unknown[] | Record<string, unknown> = Array.isArray(value) ? [] : {}
  copies.set(value, copied)
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) (copied as unknown[]).push(copyJsonValue(value[i], copies, active) ?? null)
  } else {
    for (const key of Object.keys(value)) {
      const item = (value as Record<string, unknown>)[key]
      if (item !== undefined) Object.defineProperty(copied, key, {
        value: copyJsonValue(item, copies, active), enumerable: true, writable: true, configurable: true,
      })
    }
  }
  active.delete(value)
  return copied
}

export function captureAiReviewSnapshot(snapshot: ReviewSnapshot, includeGeometry = true): ReviewSnapshot {
  // Remove geometry BEFORE copying; a large lazy CAD mesh must never be materialized in diagnostic mode.
  const scene = snapshot.scene
  const input = includeGeometry || !scene ? snapshot : { ...snapshot, scene: {
    metadata: scene.metadata, units: scene.units, coordinate_system: scene.coordinate_system,
  } }
  return copyJsonValue(input) as ReviewSnapshot
}

/** Compact JSON in bounded chunks, preserving JSON.stringify property order and number encoding. */
export function jsonChunks(value: unknown, chunkSize = 65536): string[] {
  if (!Number.isInteger(chunkSize) || chunkSize < 2) throw new RangeError('chunkSize must be at least 2')
  const chunks: string[] = []
  let buffer = ''
  const active = new Set<object>()
  function append(token: string) {
    for (let start = 0; start < token.length;) {
      const count = Math.min(chunkSize - buffer.length, token.length - start)
      buffer += token.slice(start, start + count)
      start += count
      if (buffer.length === chunkSize) {
        // Blob encodes each string separately; never split a UTF-16 surrogate pair.
        const last = buffer.charCodeAt(buffer.length - 1)
        const carry = last >= 0xd800 && last <= 0xdbff ? buffer.slice(-1) : ''
        chunks.push(carry ? buffer.slice(0, -1) : buffer)
        buffer = carry
      }
    }
  }
  function visit(item: unknown) {
    if (item === null || typeof item !== 'object') { append(JSON.stringify(item) ?? 'null'); return }
    if (active.has(item)) throw new TypeError('Circular AI review data')
    active.add(item)
    if (Array.isArray(item)) {
      append('[')
      for (let i = 0; i < item.length; i++) { if (i) append(','); visit(item[i]) }
      append(']')
    } else {
      append('{')
      let first = true
      for (const key of Object.keys(item)) {
        const entry = (item as Record<string, unknown>)[key]
        if (entry === undefined || typeof entry === 'function' || typeof entry === 'symbol') continue
        if (!first) append(',')
        first = false
        append(JSON.stringify(key)); append(':'); visit(entry)
      }
      append('}')
    }
    active.delete(item)
  }
  visit(value)
  if (buffer) chunks.push(buffer)
  return chunks
}

export function buildAiReviewPayload(snapshot: ReviewSnapshot, comparison: SensorComparisonJob | null, forwardJob: unknown, errors: string[], includeGeometry: boolean) {
  const sameScene = Boolean(comparison?.scene_token && comparison.scene_token === snapshot.scene?.metadata.scene_token)
  const comparisonMatches = Boolean(sameScene && snapshot.currentRequest && sameComparisonInput(comparison?.input_request, snapshot.currentRequest))
  const gaps = [...errors]
  if (!snapshot.preview.result) gaps.push('Preview 역추적 원본 결과 없음')
  if (!snapshot.preview.inputMatches) gaps.push('Preview 실행 입력과 현재 입력의 일치 미확인')
  if (!comparison?.report) gaps.push('동일 센서 양방향 광량 비교 결과 없음')
  if (!comparisonMatches) gaps.push('광량 비교와 현재 선택점·실행 조건 일치 미확인: 함께 집계하지 말 것')
  if (comparison && comparison.status !== 'completed') gaps.push(`광량 비교 미완료: ${comparison.status}`)
  if (!snapshot.pathValidation) gaps.push('현재 선택점의 별도 경로 검증 기록 없음; 비교 report.witness/reference_replay도 확인')
  if (snapshot.requestError) gaps.push(`현재 검증 요청 구성 실패: ${snapshot.requestError}`)
  if (!includeGeometry || !snapshot.scene) gaps.push('CAD 표시 메쉬 미포함: 독립적인 형상 차폐 검토 불가')
  gaps.push('CAD 표시 메쉬는 서버 정밀 교차 형상의 동일성·완전성을 보장하지 않음; 재실행 가능한 프로젝트 파일이 아님')
  gaps.push('광량 비교는 전체 검출 Ray 궤적을 저장하지 않음; 대표 경로·bounded 진단 표본만 존재')
  gaps.push('일반 순방향 결과의 당시 요청 스냅샷은 미확보: 현재 요청으로 대신하지 말 것')
  return {
    contract: 'leak_ai_review_temporary_v1',
    created_at: new Date().toISOString(),
    purpose: '역추적과 순방향의 차이 및 미검출 원인에 대한 AI 근거 검토. 저장된 데이터만 내보내며 새 해석은 실행하지 않음.',
    ai_interpretation_guide: aiReviewGuide,
    ai_review_instructions: [
      '먼저 identity와 evidence_gaps를 검사하고 서로 다른 입력·미완료 결과는 비교에서 제외하라.',
      '결론을 확인된 사실 / 원인 후보 / 부족한 증거 / 다음 행동 하나로 짧게 작성하라. 원인을 확정할 수 없으면 미확정으로 명시하라.',
      'Preview nit와 동일 센서 적분 lm를 직접 비교하지 말라. 검출 건수와 유효 표본 수를 구분하라.',
      'reference_path_valid는 경로 조건 통과이며 광량 또는 실제 확률적 경로 재현의 보장이 아니다.',
      '대표 경로 reflectance_only 누적 손실과 실제 추정 광량을 동일시하지 말라. 1 Ray 재생의 분기 변경·roulette 종료로 전체 미검출 원인을 확정하지 말라.',
      '원래 종료 조건 probe와 비교 복사본의 min_energy=0 설정을 구분하라. 실제 config와 실행 증거를 확인하라.',
      '전 장면 구간 분류를 선택점 차폐로 해석하지 말라. reference_sampling 반경·회차 누락·일관성을 확인하라.',
      '검출이 양수이면 완전 미검출로 결론내리지 말라. 소수 표본의 큰 기여·ESS 부족·역추적 양수 부족을 확인하라.',
      '물리적 정확도 또는 GPU 실행을 증거 없이 주장하지 말라. 광속·허용오차를 늘려 문제를 숨기지 말라.',
    ],
    identity: { case_id: snapshot.caseId, scene_token: snapshot.scene?.metadata.scene_token ?? null,
      preview_input_matches_current: snapshot.preview.inputMatches,
      comparison_scene_matches: sameScene, comparison_input_matches_current: comparisonMatches,
      comparison_input_sha256: comparison?.input_request_sha256 ?? null,
      physical_input_sha256: comparison?.report?.physical_input_sha256 ?? null,
      standalone_forward_input_identity: 'unknown' },
    evidence_gaps: gaps,
    reverse_preview: snapshot.preview,
    current_validation_request: snapshot.currentRequest,
    path_validation: snapshot.pathValidation,
    same_sensor_comparison: comparison,
    standalone_forward: { ...snapshot.forward, job: forwardJob },
    scene: includeGeometry ? snapshot.scene : snapshot.scene ? { metadata: snapshot.scene.metadata, units: snapshot.scene.units, coordinate_system: snapshot.scene.coordinate_system } : null,
  }
}

/** A checksum failure must not prevent preserving the diagnostic evidence. */
export async function createAiReviewBlob(payload: ReturnType<typeof buildAiReviewPayload>, hashLimit = 32 * 1024 * 1024): Promise<Blob> {
  let parts = jsonChunks(payload)
  let sha256: string | null = null
  let hashBasis = 'UTF-8 JSON.stringify(payload), property order preserved'
  try {
    if (parts.reduce((sum, part) => sum + part.length, 0) > hashLimit) {
      throw new Error('payload exceeds checksum memory limit')
    }
    if (!globalThis.crypto?.subtle) throw new Error('Web Crypto unavailable')
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(parts.join('')))
    sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  } catch (error) {
    hashBasis = `not computed: ${String(error)}`
    payload.evidence_gaps.push(`전체 payload SHA-256 미계산; payload_sha256=null: ${String(error)}`)
    parts = jsonChunks(payload)
  }
  return new Blob(['{"payload_sha256":', JSON.stringify(sha256), ',"hash_basis":', JSON.stringify(hashBasis), ',"payload":', ...parts, '}'], { type: 'application/json' })
}

const activeExports = new WeakSet<Window>()
function readWithDeadline<T>(read: () => Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('결과 조회 30초 초과; 서버 작업은 중단하지 않음')), 30000)
    Promise.resolve().then(read).then(resolve, reject).finally(() => clearTimeout(timer))
  })
}

/** Production builds keep this console-only hook for temporary on-site diagnostics. */
export function installAiReviewExport(snapshot: () => ReviewSnapshot, api: {
  getSensorComparison(): Promise<SensorComparisonJob | null>
  getRayTraceJob(jobId: string): Promise<unknown>
}) {
  const host = window as Window & { exportLeakAiReview?: (options?: { includeGeometry?: boolean }) => Promise<{ filename: string; bytes: number; gaps: string[] }> }
  const command = async (options: { includeGeometry?: boolean } = {}) => {
    if (activeExports.has(host)) throw new Error('AI 검토 파일 저장이 이미 진행 중입니다. 완료 후 다시 실행하세요.')
    activeExports.add(host)
    try {
      // Freeze UI inputs before any asynchronous API reads; never clone store actions.
      const includeGeometry = options.includeGeometry === true
      const frozen = captureAiReviewSnapshot(snapshot(), includeGeometry)
      const results = await Promise.allSettled([readWithDeadline(() => api.getSensorComparison()), frozen.forward.jobId ? readWithDeadline(() => api.getRayTraceJob(frozen.forward.jobId!)) : Promise.resolve(null)])
      const errors = results.flatMap((result, index) => result.status === 'rejected' ? [`${index === 0 ? '광량 비교' : '순방향 작업'} 조회 실패: ${String(result.reason)}`] : [])
      const comparison = results[0].status === 'fulfilled' ? results[0].value : null
      const forward = results[1].status === 'fulfilled' ? results[1].value : null
      const payload = buildAiReviewPayload(frozen, comparison, forward, errors, includeGeometry)
      const blob = await createAiReviewBlob(payload)
      const filename = `leak-ai-review-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      try { link.href = url; link.download = filename; document.body.appendChild(link); link.click() }
      finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
      return { filename, bytes: blob.size, gaps: payload.evidence_gaps }
    } finally { activeExports.delete(host) }
  }
  host.exportLeakAiReview = command
  return () => { if (host.exportLeakAiReview === command) delete host.exportLeakAiReview }
}
