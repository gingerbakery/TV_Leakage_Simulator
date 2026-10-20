import { HelpTooltip } from '@/components/common'

export interface ComparisonPlotData {
  uEdges: number[]
  vEdges: number[]
  simulator: number[][]
  lighttools: number[][]
  difference: number[][]
  uSimulator: number[]
  uLighttools: number[]
  uDifference: number[]
  vSimulator: number[]
  vLighttools: number[]
  vDifference: number[]
  simulatorFlux?: number[][]
  lighttoolsFlux?: number[][]
  simulatorIlluminance?: number[][]
  lighttoolsIlluminance?: number[][]
}

export function formatMetric(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value !== 0 && Math.abs(value) < 0.001) return value.toExponential(3)
  return value.toLocaleString('en-US', { maximumFractionDigits: digits })
}

const mix = (low: number[], high: number[], fraction: number) =>
  `rgb(${low.map((value, index) => Math.round(value + (high[index] - value) * fraction)).join(',')})`

function cellColor(value: number, maximum: number, difference: boolean) {
  const fraction = maximum > 0 ? Math.min(1, Math.abs(value) / maximum) : 0
  return mix([245, 248, 251], difference && value > 0 ? [190, 40, 48] : [20, 86, 150], fraction)
}

function Heatmap({ title, values, uEdges, vEdges, maximum, difference = false, flux, illuminance }: {
  title: string
  values: number[][]
  uEdges: number[]
  vEdges: number[]
  maximum: number
  difference?: boolean
  flux?: number[][]
  illuminance?: number[][]
}) {
  const uMin = uEdges[0]
  const uMax = uEdges.at(-1)!
  const vMin = vEdges[0]
  const vMax = vEdges.at(-1)!
  const width = 300
  const height = Math.min(230, Math.max(120, width * (vMax - vMin) / (uMax - uMin)))
  const x = (value: number) => 46 + (value - uMin) / (uMax - uMin) * width
  const y = (value: number) => 12 + (vMax - value) / (vMax - vMin) * height
  const unit = difference ? '%p' : '% / cell'
  return (
    <figure className="min-w-0 rounded-lg border border-border p-3">
      <figcaption className="mb-2 flex items-center gap-1 text-sm font-semibold">
        {title}
        <HelpTooltip label={`${title} 도움말`}>
          {difference ? 'Simulator − LT의 정규화 셀 비율 차이입니다. 빨강은 Simulator가 큰 영역, 파랑은 LT가 큰 영역입니다.' : '전체 수광 flux를 100%로 정규화한 셀별 비율입니다. 두 히트맵은 동일한 색상 범위를 사용합니다. 화면 보간·평활화는 하지 않습니다.'}
        </HelpTooltip>
      </figcaption>
      <svg role="img" aria-label={`${title} 히트맵`} viewBox={`0 0 360 ${height + 65}`} className="w-full" data-scale-max={maximum}>
        <rect x="46" y="12" width={width} height={height} fill="#f5f8fb" />
        {values.flatMap((row, rowIndex) => row.map((value, columnIndex) => (
          <rect key={`${rowIndex}-${columnIndex}`} data-cell={`${rowIndex},${columnIndex}`} data-value={value}
            x={x(uEdges[columnIndex])} y={y(vEdges[rowIndex + 1])}
            width={x(uEdges[columnIndex + 1]) - x(uEdges[columnIndex])}
            height={y(vEdges[rowIndex]) - y(vEdges[rowIndex + 1])}
            fill={cellColor(value, maximum, difference)} shapeRendering="crispEdges">
            <title>{`U ${formatMetric(uEdges[columnIndex])}…${formatMetric(uEdges[columnIndex + 1])} mm, V ${formatMetric(vEdges[rowIndex])}…${formatMetric(vEdges[rowIndex + 1])} mm: ${formatMetric(value)} ${unit}${flux?.[rowIndex]?.[columnIndex] != null ? ` | ${formatMetric(flux[rowIndex][columnIndex], 6)} lm` : ''}${illuminance?.[rowIndex]?.[columnIndex] != null ? ` | ${formatMetric(illuminance[rowIndex][columnIndex])} lx` : ''}`}</title>
          </rect>
        )))}
        <rect x="46" y="12" width={width} height={height} fill="none" stroke="currentColor" strokeOpacity=".4" />
        <g fill="currentColor" fontSize="10">
          <text x="42" y="18" textAnchor="end">{formatMetric(vMax)}</text>
          <text x="42" y={height + 12} textAnchor="end">{formatMetric(vMin)}</text>
          <text x="46" y={height + 28}>{formatMetric(uMin)}</text>
          <text x="346" y={height + 28} textAnchor="end">{formatMetric(uMax)}</text>
          <text x="196" y={height + 47} textAnchor="middle">Local U (mm)</text>
          <text transform={`translate(12 ${height / 2 + 12}) rotate(-90)`} textAnchor="middle">Local V (mm)</text>
        </g>
      </svg>
      <div className="h-2 rounded-sm" style={{ background: difference ? 'linear-gradient(to right, #145696, #f5f8fb, #be2830)' : 'linear-gradient(to right, #f5f8fb, #145696)' }} />
      <div className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{difference ? `−${formatMetric(maximum)}` : '0'}</span>
        {difference ? <span>0</span> : null}
        <span>{difference ? '+' : ''}{formatMetric(maximum)} {unit}</span>
      </div>
    </figure>
  )
}

function Profile({ axis, edges, simulator, lighttools, difference }: {
  axis: 'U' | 'V'
  edges: number[]
  simulator: number[]
  lighttools: number[]
  difference: number[]
}) {
  const minimum = edges[0]
  const maximum = edges.at(-1)!
  const valueMax = lighttools.reduce((max, value) => Math.max(max, value), simulator.reduce((max, value) => Math.max(max, value), 0.01))
  const diffMax = difference.reduce((max, value) => Math.max(max, Math.abs(value)), 0.01)
  const x = (index: number) => 48 + (((edges[index] + edges[index + 1]) / 2 - minimum) / (maximum - minimum)) * 520
  const points = (values: number[], top: number, height: number, low: number, high: number) =>
    values.map((value, index) => `${x(index)},${top + height - (value - low) / (high - low) * height}`).join(' ')
  return (
    <figure className="rounded-lg border border-border p-3" data-profile-axis={axis}>
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-1 font-semibold">{axis === 'U' ? 'Column 합산 · U' : 'Row 합산 · V'}
          <HelpTooltip label={`${axis} 합산 프로파일 도움말`}>
            {axis === 'U' ? '각 열의 위아래 셀 비율을 합산합니다.' : '각 행의 좌우 셀 비율을 합산합니다.'} 평균이나 단면값이 아닙니다. 각 곡선의 합계는 100%이며, 아래 차이 곡선은 Simulator − LT입니다.
          </HelpTooltip>
        </span>
        <span className="flex gap-3 text-xs"><span className="text-orange-600">● Simulator</span><span className="text-blue-600">● LT</span></span>
      </figcaption>
      <svg viewBox="0 0 592 260" role="img" aria-label={`${axis} 합산 프로파일과 차이`} className="w-full">
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line x1="48" x2="568" y1={140 - fraction * 112} y2={140 - fraction * 112} stroke="currentColor" strokeOpacity=".15" />
            <text x="42" y={144 - fraction * 112} textAnchor="end" fill="currentColor" fontSize="10">{formatMetric(valueMax * fraction, 1)}%</text>
          </g>
        ))}
        <polyline points={points(simulator, 28, 112, 0, valueMax)} fill="none" stroke="#ea580c" strokeWidth="2.5" />
        <polyline points={points(lighttools, 28, 112, 0, valueMax)} fill="none" stroke="#2563eb" strokeWidth="2" />
        <line x1="48" x2="568" y1="198" y2="198" stroke="currentColor" strokeOpacity=".3" strokeDasharray="4 3" />
        <polyline points={points(difference, 172, 52, -diffMax, diffMax)} fill="none" stroke="#be185d" strokeWidth="2" />
        <g fill="currentColor" fontSize="10">
          <text x="48" y="161">Difference (%p)</text>
          <text x="42" y="178" textAnchor="end">+{formatMetric(diffMax, 3)}</text>
          <text x="42" y="202" textAnchor="end">0</text>
          <text x="42" y="228" textAnchor="end">−{formatMetric(diffMax, 3)}</text>
          <text x="48" y="242">{formatMetric(minimum)}</text>
          <text x="568" y="242" textAnchor="end">{formatMetric(maximum)}</text>
          <text x="308" y="255" textAnchor="middle">Local {axis} (mm)</text>
        </g>
      </svg>
      <div className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
        <span>Simulator Σ {formatMetric(simulator.reduce((sum, value) => sum + value, 0))}%</span>
        <span>LT Σ {formatMetric(lighttools.reduce((sum, value) => sum + value, 0))}%</span>
      </div>
    </figure>
  )
}

export function ValidationPlots({ data }: { data: ComparisonPlotData }) {
  if (data.simulator.reduce((count, row) => count + row.length, 0) > 20000) {
    return <p className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">격자가 20,000셀을 초과하여 그래프 표시를 생략했습니다. 결과 표와 세션 저장을 사용할 수 있습니다.</p>
  }
  const maxCell = (grid: number[][]) => grid.reduce((max, row) => row.reduce((rowMax, value) => Math.max(rowMax, Math.abs(value)), max), 0)
  const maximum = Math.max(maxCell(data.simulator), maxCell(data.lighttools))
  const differenceMaximum = maxCell(data.difference)
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <Heatmap title="Simulator" values={data.simulator} uEdges={data.uEdges} vEdges={data.vEdges} maximum={maximum} flux={data.simulatorFlux} illuminance={data.simulatorIlluminance} />
        <Heatmap title="LightTools" values={data.lighttools} uEdges={data.uEdges} vEdges={data.vEdges} maximum={maximum} flux={data.lighttoolsFlux} illuminance={data.lighttoolsIlluminance} />
        <Heatmap title="Difference · Sim − LT" values={data.difference} uEdges={data.uEdges} vEdges={data.vEdges} maximum={differenceMaximum} difference />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Profile axis="U" edges={data.uEdges} simulator={data.uSimulator} lighttools={data.uLighttools} difference={data.uDifference} />
        <Profile axis="V" edges={data.vEdges} simulator={data.vSimulator} lighttools={data.vLighttools} difference={data.vDifference} />
      </div>
    </div>
  )
}
