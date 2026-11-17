import type { SensorComparisonReport } from '@/api/client'

const categories = ['positive','source_not_encountered','source_emission_zero','contribution_zero','camera_roi'] as const
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0

/** Missing/partial records never become a zero count or a population-level cause. */
export function linkedPathEvidence(report: SensorComparisonReport) {
  const expected = report.options.batches
  const paths = report.path_evidence ?? []
  let valid = paths.length === expected && new Set(paths.map(row => row.index)).size === expected
  let recorded = 0, hits = 0, near = 0, nearSegment = 0, pointChecked = 0, checked = 0, stopped = 0, retained = 0
  const reasons: Record<string, number> = {}
  for (const row of paths) {
    const capture = row.detected_paths
    if (!count(row.index) || row.index < 1 || row.index > expected || !capture || !Array.isArray(capture.samples) ||
      !count(capture.recorded_count) || !count(capture.actual_hit_count) || capture.recorded_count !== capture.samples.length ||
      capture.recorded_count > capture.actual_hit_count || new Set(capture.samples.map(s => s.ray_id)).size !== capture.samples.length) { valid = false; continue }
    recorded += capture.recorded_count; hits += capture.actual_hit_count
    for (const sample of capture.samples) {
      if (finite(sample.selected_point_radius_mm) && finite(sample.selected_point_min_segment_distance_mm)) {
        pointChecked++
        if (sample.selected_point_min_segment_distance_mm <= sample.selected_point_radius_mm) nearSegment++
      }
      if (finite(sample.selected_point_min_reflection_distance_mm) && finite(sample.selected_point_radius_mm) &&
        sample.selected_point_min_reflection_distance_mm <= sample.selected_point_radius_mm) near++
    }
    const pair = row.policy_pair
    if (pair && count(pair.checked_count) && pair.checked_count === pair.records.length && new Set(pair.records.map(r => r.ray_id)).size === pair.records.length) {
      for (const record of pair.records) {
        if (!capture.samples.some(sample => sample.ray_id === record.ray_id)) continue
        checked++
        if (record.state !== 'verified') continue
        if (['energy_cutoff','roulette_terminated','depth_limit'].includes(record.reason)) {
          stopped++; reasons[record.reason] = (reasons[record.reason] ?? 0) + 1
        } else if (record.reason === 'original_also_detected') retained++
      }
    }
  }
  valid = valid && count(report.forward_hits) && hits === report.forward_hits
  const reverse = report.reverse_diagnostics ?? []
  const totals = Object.fromEntries(categories.map(key => [key, 0])) as Record<typeof categories[number], number>
  let reverseValid = reverse.length === expected && new Set(reverse.map(row => row.index)).size === expected
  let samples = 0
  for (const row of reverse) {
    const stage = row.stages
    if (!count(row.index) || row.index < 1 || row.index > expected || !stage?.available || stage.missing_records !== 0 || !count(stage.sample_count) ||
      !stage.counts || categories.some(key => !count(stage.counts![key])) || categories.reduce((sum,key) => sum+stage.counts![key],0) !== stage.sample_count) { reverseValid = false; continue }
    samples += stage.sample_count
    for (const key of categories) totals[key] += stage.counts[key]
  }
  reverseValid = reverseValid && samples === report.batches?.reduce((sum,row) => sum+(row.reverse_sample_count ?? NaN),0) && totals.positive === report.reverse_positive_samples
  return { valid, recorded, hits, near, nearSegment, pointChecked, checked, stopped, retained, reasons, reverseValid, samples, reverse: totals }
}

export function linkedEvidenceAdvice(report: SensorComparisonReport) {
  if (report.complete !== true || report.verdict !== 'inconclusive') return null
  const evidence = linkedPathEvidence(report)
  if (evidence.valid && evidence.stopped > 0) return { title:'확인된 광선 종료 지점 확인', steps:[
    `동일 광선 재검사 ${evidence.checked}개 중 ${evidence.stopped}개에서 원래 종료 조건의 영향이 확인됐습니다. 에너지 ${evidence.reasons.energy_cutoff ?? 0}, 확률적 종료 ${evidence.reasons.roulette_terminated ?? 0}, 반사 한도 ${evidence.reasons.depth_limit ?? 0}입니다.`,
    '검증 JSON의 path_evidence.policy_pair에서 해당 광선의 종료 위치·반사 횟수·기여를 확인하세요. 확인한 광선에 한정된 결과이며 전체 광량 손실이나 실물 원인을 확정하지 않습니다.'] }
  if (evidence.reverseValid && evidence.reverse.source_emission_zero > 0 && report.reverse_positive_samples === 0) return { title:'광원 도달 후 발광 조건 확인', steps:[
    `역추적 ${evidence.samples}개 중 ${evidence.reverse.source_emission_zero}개는 광원 연결이 확인됐지만 발광 기여가 0입니다. 광원의 발광면·법선·방출 방향을 실제 사양과 대조하세요.`] }
  if (evidence.valid && evidence.recorded > 0 && evidence.pointChecked === evidence.recorded && evidence.nearSegment === 0) return { title:'검출 경로와 선택점 연결 확인', steps:[
    `실제 검출 ${evidence.hits}개 중 기록된 ${evidence.recorded}개에서 선택점 반경을 지나는 구간이 확인되지 않았습니다. 저장 한도 밖 경로와 반경 밖 경로까지 부재로 판정하지 않습니다.`,
    '검증 JSON의 path_evidence.detected_paths에서 실제 반사점과 선택점까지의 거리를 확인하세요. 센서 검출만으로 선택점의 누설을 확정하지 않습니다.'] }
  if (evidence.reverseValid && evidence.reverse.source_not_encountered === evidence.samples && evidence.samples > 0) return { title:'역추적 광원 연결 경로 확인', steps:[
    `역추적 ${evidence.samples}개 모두에서 직접 또는 가시 광원 연결을 확인하지 못했습니다. 광원에 도달할 수 없다는 증명이 아닙니다. 저장된 대표 경로의 광원 연결 구간부터 점검하세요.`] }
  return null
}
