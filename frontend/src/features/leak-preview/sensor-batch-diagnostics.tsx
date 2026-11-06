import type { SensorComparisonBatch } from '@/api/client'
import { CollapsibleSection } from '@/components/common/collapsible-section'
import { HelpTooltip } from '@/components/common/help-tooltip'

const finite = (value: number | undefined): value is number => value !== undefined && Number.isFinite(value) && value >= 0
const flux = (value?: number) => finite(value) ? value.toExponential(5) : '—'

function effectiveSamples(row: SensorComparisonBatch, reverse: boolean): string {
  const value = reverse ? row.reverse_flux_lumen : row.forward_flux_lumen
  const moment = reverse ? row.reverse_second_moment : row.forward_flux_square_sum
  const samples = reverse ? row.reverse_sample_count : 1
  if (!finite(value) || !finite(moment) || !finite(samples)) return '—'
  if (moment === 0) return value === 0 ? '0.0' : '—'
  // Forward fields are weighted sums; reverse fields are per-launch moments.
  const ratio = value / Math.sqrt(moment)
  const ess = ratio * ratio * samples
  return Number.isFinite(ess) ? ess.toFixed(1) : '—'
}

function concentration(rows: SensorComparisonBatch[], reverse: boolean): string {
  const values = rows.map(row => reverse ? row.reverse_flux_lumen : row.forward_flux_lumen)
  if (!values.every(finite) || values.length === 0) return '—'
  const maximum = Math.max(...values)
  if (maximum === 0) return `0/${values.length} · —`
  const share = 100 / values.reduce((sum, value) => sum + value / maximum, 0)
  return `${values.filter(value => value > 0).length}/${values.length} · ${share.toFixed(1)}%`
}

export function SensorBatchDiagnostics({ rows }: { rows: SensorComparisonBatch[] }) {
  return <CollapsibleSection title="회차별 결과" count={rows.length} defaultOpen={false}>
    <div className="flex items-center gap-1 text-xs font-medium">표본 분포
      <HelpTooltip label="회차별 결과 도움말">양수 회차 수와 전체 회차 광량 합에서 가장 큰 한 회차가 차지하는 비중입니다. 양수 회차가 적거나 최대 비중이 크면 일부 회차에 기여가 집중되어 있습니다. 이것만으로 표본 부족과 계산 오류를 구분하거나 광학적 정확도를 판정할 수 없습니다. F는 순방향, R은 역추적입니다. 회차별 유효 표본은 기여량과 제곱합으로 계산하며 전체 유효 표본과 별개입니다. 누락된 이전 결과 값은 —로 표시합니다.</HelpTooltip>
    </div>
    <div className="text-xs">양수 회차 · 최대 회차 비중 F {concentration(rows, false)} / R {concentration(rows, true)}</div>
    <div className="overflow-x-auto">
      <table aria-label="회차별 광량 비교" className="w-full whitespace-nowrap text-xs tabular-nums">
        <thead><tr>{['회', '순방향 (lm)', '역추적 (lm)', '순 검출 / 역 양수', '유효 표본 F / R'].map(label => <th key={label} scope="col" className="px-1 py-1 text-left">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>
          <th scope="row" className="px-1 py-1 text-left">{row.index ?? index + 1}</th>
          <td className="px-1">{flux(row.forward_flux_lumen)}</td><td className="px-1">{flux(row.reverse_flux_lumen)}</td>
          <td className="px-1">{row.forward_hits ?? '—'} / {row.reverse_positive_samples ?? '—'}</td>
          <td className="px-1">{effectiveSamples(row, false)} / {effectiveSamples(row, true)}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </CollapsibleSection>
}
