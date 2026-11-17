import type { SensorComparisonReport } from '@/api/client'
import { linkedPathEvidence } from './linked-path-evidence'
import { readReceiverDiagnostics, readReferenceSampling, type DiagnosticCategory } from '@/features/results/receiver-diagnostics-model'

export interface EvidenceFinding {
  item: string
  value: string
  status: string
}
export interface SelectedPointEvidence {
  checked: number
  expected: number
  complete: boolean
  radius: number | null
  counts: Record<DiagnosticCategory, number>
  started: number
  nearest: number | null
}
const finite = (value: number | undefined): value is number => value !== undefined && Number.isFinite(value) && value >= 0

/** Aggregate only the selected sensor, with one consistent record per batch. */
export function selectedPointEvidence(report: SensorComparisonReport): SelectedPointEvidence {
  const result: SelectedPointEvidence = { checked:0, expected:report.options.batches, complete:false,
    radius:null, counts:{parallel:0,behind_origin:0,outside_area:0,occluded:0,other_receiver_first:0,
      back_face:0,outside_acceptance:0,outside_roi:0,accepted:0}, started:0, nearest:null }
  const seen = new Set<number>()
  if (!Number.isSafeInteger(result.expected) || result.expected <= 0) return result
  let referencePoint: number[] | undefined
  let totalHits = 0
  for (const batch of report.forward_diagnostics ?? []) {
    if (!Number.isInteger(batch.index) || batch.index < 1 || batch.index > result.expected || seen.has(batch.index)) return result
    seen.add(batch.index)
    const diagnostic = readReceiverDiagnostics(batch.report)
    const matching = diagnostic?.receivers.filter(row => row.receiver_id === report.sensor.receiver_id)
    const row = matching?.length === 1 ? matching[0] : undefined
    const local = readReferenceSampling(row?.reference_sampling)
    if (!diagnostic?.complete || !row?.consistent || !local || row.counts.accepted !== row.actual_receiver_hits ||
      Object.values(row.counts).reduce((sum, count) => sum + count, 0) !== row.segments_tested ||
      local.segments_started > row.segments_tested ||
      (Object.keys(local.counts) as DiagnosticCategory[]).some(key => local.counts[key] > row.counts[key])) continue
    if (referencePoint && (local.radius_mm !== result.radius || local.point.some((value, index) => value !== referencePoint![index]))) return result
    referencePoint = local.point
    result.radius = local.radius_mm
    result.checked++
    result.started += local.segments_started
    totalHits += row.actual_receiver_hits
    for (const key of Object.keys(local.counts) as DiagnosticCategory[]) result.counts[key] += local.counts[key]
    if (local.min_origin_distance_mm !== null) result.nearest = result.nearest === null
      ? local.min_origin_distance_mm : Math.min(result.nearest, local.min_origin_distance_mm)
  }
  result.complete = result.checked === result.expected && finite(report.forward_hits) && totalHits === report.forward_hits
  return result
}

export function comparisonEvidence(report: SensorComparisonReport, sameInput: boolean): EvidenceFinding[] {
  if (!sameInput || report.gpu_execution_proven !== true || report.reason.includes('gpu_execution') ||
    ['stopped','incomplete_batches','reference_replay_failed','invalid_reference_path'].includes(report.reason)) return []
  const sampleFinding = (item: string, countLabel: string, count: number | undefined, effective: number | undefined): EvidenceFinding => ({
    item, value:`${countLabel} ${finite(count) ? count.toLocaleString() : '—'} · 유효 ${finite(effective) ? effective.toFixed(1) : '—'} / 30`,
    status:!finite(count) || !finite(effective) ? '기록 부족' : count < 30 && effective < 30 ? '양수·유효표본 부족'
      : count < 30 ? '양수표본 부족' : effective < 30 ? '유효표본 부족' : '최소 충족',
  })
  const findings = [sampleFinding('순방향','검출',report.forward_hits,report.forward_effective_samples),
    sampleFinding('역추적','양수',report.reverse_positive_samples,report.reverse_effective_samples)]
  const linked = linkedPathEvidence(report)
  if (report.path_evidence) findings.push({item:'실제 검출 경로', value:linked.valid ? `기록 ${linked.recorded}/${linked.hits} · 근접 구간 ${linked.pointChecked === linked.recorded ? linked.nearSegment : '미확인'} · 반사점 ${linked.near}` : '기록 불완전',
    status:linked.valid ? linked.recorded < linked.hits ? '일부 경로 기록' : '검출 경로 기록 완료' : '판정 보류'})
  if (linked.valid && linked.checked) findings.push({item:'동일 광선 종료 비교',value:`재검사 ${linked.checked} · 원래 조건 종료 ${linked.stopped} · 검출 유지 ${linked.retained}`,
    status:linked.stopped ? '해당 광선 영향 확인' : linked.retained === linked.checked ? '검출 유지 확인' : '일부 재현 미확인'})
  if (report.reverse_diagnostics) findings.push({item:'역추적 단계',value:linked.reverseValid ? `연결 미확인 ${linked.reverse.source_not_encountered} · 발광 0 ${linked.reverse.source_emission_zero} · 기여 0 ${linked.reverse.contribution_zero} · ROI ${linked.reverse.camera_roi}` : '기록 불완전',status:linked.reverseValid ? '단계 집계 완료' : '판정 보류'})
  const point = selectedPointEvidence(report)
  if (!point.complete) {
    findings.push({item:'선택점 경로',value:`주변 기록 ${point.checked}/${point.expected}회`,status:'판정 보류'})
    return findings
  }
  const counts = point.counts
  let status = '주변 출발 표본 없음'
  if (counts.accepted > 0) status = '주변 출발 검출 있음'
  else if (counts.back_face + counts.outside_acceptance + counts.outside_roi > 0) status = '도달 후 센서 조건 제외'
  else if (counts.other_receiver_first > 0) status = '다른 센서 선검출'
  else if (counts.occluded > 0) status = '주변 센서 방향 표본 차폐'
  else if (point.started > 0) status = '센서 영역 연결 표본 없음'
  findings.push({item:'선택점 경로',value:`반경 ${point.radius} mm · 출발 ${point.started.toLocaleString()} · 검출 ${counts.accepted.toLocaleString()}`,status})
  return findings
}
