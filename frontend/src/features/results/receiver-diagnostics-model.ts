export const receiverDiagnosticLabels = {
  parallel: '센서 평면과 평행', behind_origin: '평면 교점이 진행 방향 뒤', outside_area: '연장선이 센서 면적 밖',
  occluded: '센서 전 형상 충돌', other_receiver_first: '다른 센서가 먼저 검출',
  back_face: '뒷면 입사 제외', outside_acceptance: '수용각 제외', outside_roi: 'ROI 제외', accepted: '최종 검출',
} as const
export type DiagnosticCategory = keyof typeof receiverDiagnosticLabels
export interface ReceiverDiagnosticRow {
  receiver_id: string; segments_tested: number; counts: Record<DiagnosticCategory, number>
  segment_flux_lumen: Record<DiagnosticCategory, number>; actual_receiver_hits: number
  actual_receiver_flux_lumen: number; arrived_count: number; consistent: boolean
}
export interface ReceiverDiagnosticReport {
  contract: 'forward_receiver_segments_v1'; complete: boolean; primary_ray_count: number
  receivers: ReceiverDiagnosticRow[]; termination: Record<string, unknown>
  escaped_segments: number; escaped_segment_flux_lumen: number
}
const record = (v: unknown): v is Record<string, unknown> => Boolean(v && typeof v === 'object' && !Array.isArray(v))
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0
export function readReceiverDiagnostics(value: unknown): ReceiverDiagnosticReport | null {
  if (!record(value) || value.contract !== 'forward_receiver_segments_v1' || typeof value.complete !== 'boolean' ||
      !number(value.primary_ray_count) || !number(value.escaped_segments) || !number(value.escaped_segment_flux_lumen) ||
      !record(value.termination) || !Array.isArray(value.receivers)) return null
  for (const row of value.receivers) {
    if (!record(row) || typeof row.receiver_id !== 'string' || typeof row.consistent !== 'boolean' ||
        !number(row.segments_tested) || !number(row.actual_receiver_hits) || !number(row.actual_receiver_flux_lumen) ||
        !number(row.arrived_count) || !record(row.counts) || !record(row.segment_flux_lumen)) return null
    for (const key of Object.keys(receiverDiagnosticLabels)) if (!number(row.counts[key]) || !number(row.segment_flux_lumen[key])) return null
  }
  return value as unknown as ReceiverDiagnosticReport
}

export function mergeReceiverDiagnostics(a: unknown, b: unknown, termination: unknown): ReceiverDiagnosticReport | undefined {
  const left = readReceiverDiagnostics(a), right = readReceiverDiagnostics(b)
  if (!left || !right || left.receivers.length !== right.receivers.length) return
  const total = left.primary_ray_count + right.primary_ray_count
  if (total <= 0) return
  const weight = (x: number, y: number) => (x*left.primary_ray_count + y*right.primary_ray_count)/total
  const rows: ReceiverDiagnosticRow[] = []
  for (const r of right.receivers) {
    const l = left.receivers.find(row => row.receiver_id === r.receiver_id)
    if (!l) return
    const counts = { ...r.counts }, flux = { ...r.segment_flux_lumen }
    for (const key of Object.keys(counts) as DiagnosticCategory[]) {
      counts[key] += l.counts[key]
      flux[key] = weight(l.segment_flux_lumen[key], r.segment_flux_lumen[key])
    }
    rows.push({ ...r, counts, segment_flux_lumen: flux, segments_tested: l.segments_tested+r.segments_tested,
      arrived_count: l.arrived_count+r.arrived_count, actual_receiver_hits: l.actual_receiver_hits+r.actual_receiver_hits,
      actual_receiver_flux_lumen: weight(l.actual_receiver_flux_lumen,r.actual_receiver_flux_lumen), consistent:l.consistent&&r.consistent })
  }
  return { ...right, primary_ray_count:total, complete:left.complete&&right.complete, receivers:rows,
    termination:record(termination)?termination:{}, escaped_segments:left.escaped_segments+right.escaped_segments,
    escaped_segment_flux_lumen:weight(left.escaped_segment_flux_lumen,right.escaped_segment_flux_lumen) }
}
