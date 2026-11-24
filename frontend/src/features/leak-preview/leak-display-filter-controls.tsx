import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { defaultLeakDisplayFilter, type LeakDisplayFilter } from './leak-display-filter'

export function LeakDisplayFilterControls({filter,hasNit,peakNit,defaultMax,shownPoints,totalPoints,shownCandidates,totalCandidates,onChange}: {
  filter: LeakDisplayFilter; hasNit: boolean; defaultMax: number; shownPoints: number; totalPoints: number
  peakNit: number
  shownCandidates: number; totalCandidates: number; onChange(patch: Partial<LeakDisplayFilter>): void
}) {
  const inputClass = 'h-7 w-full rounded border px-1'
  return <section aria-label="Leak 표시 필터" className="space-y-2 rounded-lg border p-2">
    <div className="flex items-center gap-1 text-xs font-semibold">표시 필터<HelpTooltip label="Leak 표시 필터 도움말">원본 결과는 유지하고 점·후보 목록·그룹에서 조건에 맞지 않는 관찰 표본을 숨깁니다. 최대 반사는 저장된 대표 경로의 반사 횟수 상한이며 셀 광량을 재계산하지 않습니다. 최소 nit는 관찰 휘도 결과에서만 적용합니다. 백분율은 필터 적용 전 최대값을 기준으로 계산합니다. 관찰 휘도에서는 셀 평균 nit, 경로 탐색에서는 셀의 검출 수, 일반 Preview에서는 상대 강도를 사용합니다. 최대 반사 값을 입력하면 필터가 켜집니다. 후보 검출 횟수와 각도별 전체 집계는 원본 통계입니다. 숨김은 과검출의 물리적 오류를 고치거나 실물 가시성을 판정하지 않습니다. 초기화하면 모든 표시 조건을 해제합니다.</HelpTooltip>
      <Button className="ml-auto h-6 px-2" size="sm" variant="ghost" onClick={() => onChange({...defaultLeakDisplayFilter, topPercent: undefined})}>초기화</Button></div>
    <div className="grid grid-cols-3 gap-2 text-xs">
      <div className="col-span-3 flex items-center gap-2">
        <label className="flex items-center gap-2">밝기 상위 (%)
          <NumberInput aria-label="밝기 상위 (%)" className="h-7 w-16 rounded border px-1" min={1} max={100} step={1} value={filter.topPercent ?? 100} onValueChange={value => onChange({topPercent: Math.round(Math.max(1, Math.min(100, value)))})} />
        </label>
        <HelpTooltip label="밝기 상위 필터 도움말">다른 표시 조건을 통과한 후보 중 가장 밝은 그룹부터 지정한 비율만큼 표시합니다. 20%를 입력하면 후보 10개 중 2개가 보입니다. 남길 개수는 올림하며, 100%는 전체 표시입니다. 관찰 휘도는 그룹에서 표시 조건을 통과한 셀의 최고 nit로 순위를 매깁니다. 경로 탐색에서는 검출 수, 일반 Preview에서는 상대 강도를 사용합니다.</HelpTooltip>
      </div>
      <label className="space-y-1"><span className="flex items-center gap-1"><input aria-label="최대 반사 필터 사용" type="checkbox" checked={filter.maxReflections !== null} onChange={event => onChange({maxReflections:event.currentTarget.checked ? defaultMax : null})} />최대 반사</span>
        <NumberInput aria-label="표시 최대 반사 횟수" className={inputClass} min={0} max={4096} step={1} value={filter.maxReflections ?? defaultMax} onValueChange={value => onChange({maxReflections:Math.max(0,Math.floor(value))})} /></label>
      <label className="space-y-1"><span>최소 nit</span><NumberInput aria-label="표시 최소 nit" className={inputClass} min={0} step="any" disabled={!hasNit} value={filter.minNit} onValueChange={value => onChange({minNit:Math.max(0,value)})} /></label>
      <label className="space-y-1"><span>{hasNit ? '최소 밝기 (%)' : '최소 강도 (%)'}</span><NumberInput aria-label="표시 최소 밝기 비율 (%)" className={inputClass} min={0} max={100} step="any" value={filter.minBrightnessRatio*100} onValueChange={value => onChange({minBrightnessRatio:Math.max(0,Math.min(100,value))/100})} /></label>
    </div>
    <div role="status" className="text-xs">표시 {shownPoints} / {totalPoints}점 · 후보 {shownCandidates} / {totalCandidates}</div>
    {(!hasNit || peakNit > 0) && <div role="status" className="text-xs text-muted-foreground">
      {hasNit ? `최고 휘도 ${peakNit.toPrecision(4)} nit` : 'nit 데이터 없음'}
    </div>}
  </section>
}
