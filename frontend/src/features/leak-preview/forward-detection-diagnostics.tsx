import type { SensorComparisonReport } from '@/api/client'
import { CircleHelp } from 'lucide-react'
import { Popover } from 'radix-ui'

const pathReasons: Record<string, string> = {
  occluded_segment: '경로 차폐',
  source_covered_by_blocker: '광원 차폐',
  source_position_or_emission_mismatch: '광원 위치·방출 조건 불일치',
  reflection_material_mismatch: '반사 재질·방향 불일치',
  reflection_depth_mismatch: '최대 반사 횟수 초과',
  reflection_disabled: '반사 계산 꺼짐',
  receiver_position_direction_acceptance_or_roi_mismatch: '센서 위치·방향·수용각·ROI 불일치',
  reflection_vertex_off_surface: '반사점·현재 형상 불일치',
  visible_endpoint_off_surface: '검출점·현재 형상 불일치',
}

export function ForwardDetectionDiagnostics({ report }: { report: SensorComparisonReport }) {
  const witness = report.witness?.find(row => row.receiver_id === report.sensor.receiver_id)
  const invalidPath = witness?.path_valid === false || report.reason === 'invalid_reference_path'
  if (!invalidPath && report.forward_hits !== 0) return null
  const status = invalidPath
    ? pathReasons[witness?.diagnosis ?? ''] ?? '대표 경로 조건 불일치'
    : (report.reverse_positive_samples ?? 0) > 0
      ? '역추적 양수 · 순방향 원인 미확정'
      : '양방향 양수 기여 미확인 · 원인 미확정'
  return <div role="status" className="flex items-center gap-1 text-xs">
    <span>순방향 미검출 진단 · {status}</span>
    <Popover.Root><Popover.Trigger asChild><button type="button" aria-label="순방향 미검출 진단 도움말"
      className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-primary/40"><CircleHelp className="size-3.5" /></button></Popover.Trigger>
      <Popover.Portal><Popover.Content side="bottom" align="start" sideOffset={6}
        className="z-50 max-h-80 w-80 max-w-[90vw] overflow-auto rounded-md border border-border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">
      {invalidPath
        ? `저장된 대표 경로가 현재 조건을 통과하지 못했습니다 (${witness?.diagnosis ?? report.reason}). 이 경로의 실패가 모든 경로의 차폐나 빛샘 없음을 뜻하지 않습니다.`
        : '이번 순방향 표본에서 센서 검출이 0입니다. 좁은 틈·작은 센서·제한된 수용각으로 표본이 도달하지 않았을 가능성이 있지만, 이 결과만으로 표본 부족이나 구현 오류를 확정할 수 없습니다.'}
      {' '}대표 경로: {witness ? witness.path_valid ? '유효' : '불일치' : '검증 정보 없음'}.
      {' '}순방향 검출 {report.forward_hits ?? '—'} / 역추적 양수 {report.reverse_positive_samples ?? '—'}.
      {' '}기존 종료 조건 검출 {report.original_termination_probe?.forward_hits ?? '—'}.
      {' '}기존 종료 조건은 별도 표본 실행이므로 검출 수 차이만으로 에너지 컷오프가 원인이라고 단정할 수 없습니다. 미완료·이전 작업은 해당 작업의 증거만 나타냅니다. 경로 조건과 회차별 결과를 확인하세요. 0 검출은 빛샘 없음의 증거가 아닙니다.
      </Popover.Content></Popover.Portal>
    </Popover.Root>
  </div>
}
