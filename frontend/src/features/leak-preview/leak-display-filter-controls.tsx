import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { defaultLeakDisplayFilter, type LeakDisplayFilter } from './leak-display-filter'

export function LeakDisplayFilterControls({filter,hasNit,defaultMax,shownPoints,totalPoints,shownCandidates,totalCandidates,onChange}: {
  filter: LeakDisplayFilter; hasNit: boolean; defaultMax: number; shownPoints: number; totalPoints: number
  shownCandidates: number; totalCandidates: number; onChange(patch: Partial<LeakDisplayFilter>): void
}) {
  const inputClass = 'h-7 w-full rounded border px-1'
  return <section aria-label="Leak 표시 필터" className="space-y-2 rounded-lg border p-2">
    <div className="flex items-center gap-1 text-xs font-semibold">표시 필터<HelpTooltip label="Leak 표시 필터 도움말">원본 결과는 유지하고 점·후보 목록·그룹에서 조건에 맞지 않는 관찰 표본을 숨깁니다. 최대 반사는 저장된 대표 경로의 반사 횟수 상한이며 셀 광량을 재계산하지 않습니다. 최소 nit는 저장된 셀 평균 휘도, 최소 밝기 비율은 필터 적용 전 최대 유효 관찰 휘도 대비 비율입니다. 광원 대비 잔존 에너지 비율은 아닙니다. 두 휘도 조건은 모두 충족해야 하며 경계값은 포함합니다. 휘도 데이터가 없는 경로 탐색 결과에는 nit·밝기 비율을 적용할 수 없습니다. 후보 검출 횟수와 각도별 전체 집계는 원본 통계입니다. 숨김은 과검출의 물리적 오류를 고치거나 실물 가시성을 판정하지 않습니다. 초기화하면 모든 표시 조건을 해제합니다.</HelpTooltip>
      <Button className="ml-auto h-6 px-2" size="sm" variant="ghost" onClick={() => onChange(defaultLeakDisplayFilter)}>초기화</Button></div>
    <div className="grid grid-cols-3 gap-2 text-xs">
      <label className="space-y-1"><span className="flex items-center gap-1"><input aria-label="최대 반사 필터 사용" type="checkbox" checked={filter.maxReflections !== null} onChange={event => onChange({maxReflections:event.currentTarget.checked ? defaultMax : null})} />최대 반사</span>
        <NumberInput aria-label="표시 최대 반사 횟수" className={inputClass} min={0} max={4096} step={1} disabled={filter.maxReflections === null} value={filter.maxReflections ?? defaultMax} onValueChange={value => onChange({maxReflections:Math.max(0,Math.floor(value))})} /></label>
      <label className="space-y-1"><span>최소 nit</span><NumberInput aria-label="표시 최소 nit" className={inputClass} min={0} step={.01} disabled={!hasNit} value={filter.minNit} onValueChange={value => onChange({minNit:Math.max(0,value)})} /></label>
      <label className="space-y-1"><span>최소 밝기 (%)</span><NumberInput aria-label="표시 최소 밝기 비율 (%)" className={inputClass} min={0} max={100} step={1} disabled={!hasNit} value={filter.minBrightnessRatio*100} onValueChange={value => onChange({minBrightnessRatio:Math.max(0,Math.min(100,value))/100})} /></label>
    </div>
    <div role="status" className="text-xs">표시 {shownPoints} / {totalPoints}점 · 후보 {shownCandidates} / {totalCandidates}</div>
    {!hasNit ? <div role="status" className="text-xs text-muted-foreground">nit 데이터 없음</div> : null}
  </section>
}
