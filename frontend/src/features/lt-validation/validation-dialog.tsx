import { useRef, useState } from 'react'
import { Download, FlaskConical, Plus, Trash2, Upload } from 'lucide-react'
import { AppDialog, HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'
import { compareValidationCases, importValidationFile } from './client'
import { fieldClass, ManualEditor } from './manual-editor'
import { formatMetric, ValidationPlots } from './validation-plots'
import type { ValidationCase, ValidationCaseResult, ValidationDataset, ValidationIssue, ValidationResponse } from './types'

function newCase(name: string): ValidationCase {
  return { id: crypto.randomUUID(), name, sim: null, lt: null, sim_receiver_id: null, lt_receiver_id: null }
}

function IssueList({ issues }: { issues: ValidationIssue[] }) {
  if (!issues.length) return null
  return <ul className="space-y-1 text-xs" aria-label="검증 경고">
    {issues.map((issue, index) => <li key={`${issue.code}-${index}`} className={`rounded-md border px-2 py-1.5 ${issue.severity === 'error' ? 'border-destructive/30 bg-destructive/5 text-destructive' : 'border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-300'}`}>{issue.message}</li>)}
  </ul>
}

function DatasetCard({ title, dataset, selectedId, busy, onFile, onReceiver }: {
  title: 'Simulator' | 'LightTools'
  dataset: ValidationDataset | null
  selectedId: string | null
  busy: boolean
  onFile(file: File): void
  onReceiver(id: string): void
}) {
  const receiver = dataset?.receivers.find((item) => item.id === selectedId)
  const isSimulator = title === 'Simulator'
  return <section className="min-w-0 space-y-2 rounded-lg border border-border p-3">
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-1 text-sm font-semibold">{title}<HelpTooltip label={`${title} 파일 도움말`}>{isSimulator ? '.bitsam 안의 저장된 해석 결과와 리시버 조건을 읽습니다. 현재 CAD와 해석 상태는 바뀌지 않습니다.' : '.lts의 원시 Illuminance mesh와 TotalPower를 읽습니다. 구조·좌표·단위를 확인할 수 없는 데이터는 비교를 차단합니다. 원시 표 또는 누락 조건은 LT 입력으로 보완할 수 있습니다.'}</HelpTooltip></h3>
      <span className="text-xs text-muted-foreground">{receiver ? receiver.raw === true ? '원시' : receiver.raw === false ? '보간 / 평활화' : '원시 여부 미확인' : '미입력'}</span>
    </div>
    <input aria-label={`${title} 파일`} type="file" accept={isSimulator ? '.bitsam,.json' : '.lts,.json'} disabled={busy}
      className="w-full min-w-0 text-xs file:mr-2 file:rounded-md file:border file:border-border file:bg-background file:px-2 file:py-1.5 file:text-foreground"
      onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) onFile(file) }} />
    {dataset ? <>
      <div className="truncate text-xs text-muted-foreground" title={dataset.filename}>{dataset.filename}</div>
      <label className="block space-y-1 text-xs">Receiver
        <select aria-label={`${title} receiver`} className={fieldClass} value={selectedId ?? ''} disabled={busy || dataset.receivers.length === 0} onChange={(event) => onReceiver(event.currentTarget.value)}>
          <option value="">선택</option>{dataset.receivers.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
        </select>
      </label>
      {receiver ? <details className="text-xs">
        <summary className="cursor-pointer">조건 · {receiver.flux_lm?.length ?? '—'}행 × {receiver.flux_lm?.[0]?.length ?? '—'}열 · {formatMetric(receiver.total_flux_lm, 9)} lm</summary>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md bg-muted/50 p-2">
          <dt>중심 (mm)</dt><dd>{receiver.center_mm?.join(', ') ?? '미확인'}</dd>
          <dt>U / V (mm)</dt><dd>{receiver.u_edges_mm?.length ? `${formatMetric(receiver.u_edges_mm[0])}…${formatMetric(receiver.u_edges_mm.at(-1))}` : '미확인'} / {receiver.v_edges_mm?.length ? `${formatMetric(receiver.v_edges_mm[0])}…${formatMetric(receiver.v_edges_mm.at(-1))}` : '미확인'}</dd>
          <dt>U 방향</dt><dd>{receiver.u_axis?.join(', ') ?? '미확인'}</dd>
          <dt>V 방향</dt><dd>{receiver.v_axis?.join(', ') ?? '미확인'}</dd>
          <dt>수광 법선</dt><dd>{receiver.normal?.join(', ') ?? '미확인'}</dd>
          <dt>수광각</dt><dd>{formatMetric(receiver.acceptance_angle_deg)}°</dd>
          <dt>Aperture</dt><dd>{receiver.aperture ?? '미확인'}</dd>
          <dt>광원 flux</dt><dd>{dataset.sources.length ? dataset.sources.map((source) => `${source.name || source.id}: ${formatMetric(source.total_flux_lm)} lm (${source.flux_reference ?? '기준 미확인'})`).join(' · ') : '미확인'}</dd>
        </dl>
      </details> : null}
      <IssueList issues={[...dataset.issues, ...(receiver?.issues ?? [])]} />
    </> : null}
  </section>
}

function relative(value: number | null, change: number | null) {
  return value == null ? '—' : `${formatMetric(value, 2)}%${change == null ? '' : ` (${change > 0 ? '+' : ''}${formatMetric(change, 2)}%)`}`
}

function ResultsTable({ response, activeId, onSelect }: { response: ValidationResponse; activeId: string; onSelect(id: string): void }) {
  return <section className="space-y-2">
    <h3 className="flex items-center gap-1 text-sm font-semibold">결과 표<HelpTooltip label="결과 표 도움말">총 수광량은 셀 flux 합계(lm)이며 ray 수가 아닙니다. 편차는 (Simulator / LT − 1) × 100입니다. 분포는 각 총합을 1로 정규화하여 Pearson과 Σmin × 100 중첩률을 계산합니다. 기준 대비 비율은 도구별 baseline을 100%로 하며 괄호는 변화율입니다. 조건·원시값 검증 상태만 표시하며 합격/불합격을 판정하지 않습니다.</HelpTooltip></h3>
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full whitespace-nowrap text-right text-xs" aria-label="LT 매칭 결과 표">
        <thead className="bg-muted/60"><tr>{['Case', '상태', 'Sim (lm)', 'LT (lm)', '편차 (%)', 'Pearson', '중첩 (%)', 'Sim 기준 대비', 'LT 기준 대비'].map((name) => <th key={name} scope="col" className="px-3 py-2 font-medium first:text-left">{name}</th>)}</tr></thead>
        <tbody>{response.cases.map((item) => <tr key={item.id} className={`border-t border-border ${item.id === activeId ? 'bg-primary/5' : ''}`}>
          <th scope="row" className="px-3 py-2 text-left font-medium"><button type="button" onClick={() => onSelect(item.id)} className="rounded px-1 py-0.5 text-primary hover:underline focus-visible:ring-2 focus-visible:ring-primary">{item.name}{item.id === response.baseline_id ? ' · 기준' : ''}</button></th>
          <td className={`px-3 py-2 ${item.status === 'ready' ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-300'}`}>{item.status === 'ready' ? '비교 가능' : '비교 차단'}</td>
          <td className="px-3 py-2">{formatMetric(item.sim_total_lm, 9)}</td><td className="px-3 py-2">{formatMetric(item.lt_total_lm, 9)}</td>
          <td className="px-3 py-2">{formatMetric(item.status === 'ready' ? item.flux_deviation_pct : null)}</td><td className="px-3 py-2">{formatMetric(item.status === 'ready' ? item.pearson : null, 5)}</td><td className="px-3 py-2">{formatMetric(item.status === 'ready' ? item.overlap_pct : null)}</td>
          <td className="px-3 py-2">{relative(item.sim_relative_pct, item.sim_change_pct)}</td><td className="px-3 py-2">{relative(item.lt_relative_pct, item.lt_change_pct)}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>
}

function CasePlots({ result }: { result: ValidationCaseResult }) {
  const plots = result.status === 'ready' ? result.plots : null
  return <section className="space-y-3">
    <h3 className="text-sm font-semibold">{result.name} · {result.status === 'ready' ? '분포 비교' : '비교 차단'}</h3>
    <IssueList issues={result.issues} />
    {plots ? <ValidationPlots data={{
      uEdges: plots.u_edges_mm, vEdges: plots.v_edges_mm,
      simulator: plots.sim_pct, lighttools: plots.lt_pct, difference: plots.difference_pp,
      uSimulator: plots.column_sim_pct, uLighttools: plots.column_lt_pct, uDifference: plots.column_difference_pp,
      vSimulator: plots.row_sim_pct, vLighttools: plots.row_lt_pct, vDifference: plots.row_difference_pp,
      simulatorFlux: plots.sim_flux_lm, lighttoolsFlux: plots.lt_flux_lm,
      simulatorIlluminance: plots.sim_illuminance_lx, lighttoolsIlluminance: plots.lt_illuminance_lx,
    }} /> : null}
  </section>
}

export function LtValidationDialog({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const [cases, setCases] = useState<ValidationCase[]>(() => [newCase('Before')])
  const [activeId, setActiveId] = useState(cases[0].id)
  const [baselineId, setBaselineId] = useState(cases[0].id)
  const [response, setResponse] = useState<ValidationResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [manual, setManual] = useState(false)
  const sessionInput = useRef<HTMLInputElement>(null)
  const active = cases.find((item) => item.id === activeId) ?? cases[0]
  const activeResult = response?.cases.find((item) => item.id === active.id)

  function updateCase(patch: Partial<ValidationCase>) {
    setCases((current) => current.map((item) => item.id === active.id ? { ...item, ...patch } : item))
    setResponse(null)
    setError(null)
  }
  function selectCase(id: string) { setActiveId(id); setManual(false); setError(null) }
  async function upload(side: 'sim' | 'lt', file: Blob, filename: string) {
    setBusy(true); setError(null); setResponse(null)
    try {
      const dataset = await importValidationFile(file, filename)
      if ((side === 'sim') !== (dataset.kind === 'bitsam')) throw new Error(side === 'sim' ? 'Simulator에는 .bitsam 결과를 선택하세요.' : 'LT에는 .lts 또는 LT 원시 데이터 JSON을 선택하세요.')
      const receiver = dataset.receivers.find((item) => /front/i.test(item.name)) ?? dataset.receivers[0]
      updateCase(side === 'sim' ? { sim: dataset, sim_receiver_id: receiver?.id ?? null } : { lt: dataset, lt_receiver_id: receiver?.id ?? null })
      setManual(false)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '파일을 읽을 수 없습니다.') }
    finally { setBusy(false) }
  }
  async function applyManual(dataset: ValidationDataset) {
    setBusy(true); setError(null); setResponse(null)
    try {
      const validated = await importValidationFile(new Blob([JSON.stringify(dataset)], { type: 'application/json' }), 'manual-lt.json')
      updateCase({ lt: validated, lt_receiver_id: active.lt_receiver_id ?? validated.receivers[0]?.id ?? null })
      setManual(false)
    } finally { setBusy(false) }
  }
  async function compare() {
    setBusy(true); setError(null); setResponse(null); setManual(false)
    try { setResponse(await compareValidationCases(cases, baselineId)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '비교하지 못했습니다.') }
    finally { setBusy(false) }
  }
  function exportSession() {
    const blob = new Blob([JSON.stringify({ schema: 'lt-validation-session.v1', cases, baseline_id: baselineId }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = 'lt-validation-session.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  async function importSession(file: File) {
    setBusy(true); setError(null); setResponse(null)
    try {
      const parsed = JSON.parse(await file.text()) as { schema?: string; cases?: ValidationCase[]; baseline_id?: string }
      if (parsed.schema !== 'lt-validation-session.v1' || !Array.isArray(parsed.cases) || !parsed.cases.length || parsed.cases.length > 50 || new Set(parsed.cases.map((item) => item?.id)).size !== parsed.cases.length) throw new Error('유효한 LT 검증 세션 JSON이 아닙니다. 최대 50개 Case를 지원합니다.')
      const checked: ValidationCase[] = []
      for (const item of parsed.cases) {
        if (!item || typeof item.id !== 'string' || typeof item.name !== 'string') throw new Error('세션 Case 정보가 유효하지 않습니다.')
        const next: ValidationCase = { id: item.id, name: item.name, sim: null, lt: null, sim_receiver_id: null, lt_receiver_id: null }
        for (const side of ['sim', 'lt'] as const) {
          if (!item[side]) continue
          const dataset = await importValidationFile(new Blob([JSON.stringify(item[side])], { type: 'application/json' }), 'session-data.json')
          if ((side === 'sim') !== (dataset.kind === 'bitsam')) throw new Error('세션의 Simulator/LT 구분이 올바르지 않습니다.')
          next[side] = { ...dataset, filename: typeof item[side]?.filename === 'string' ? item[side].filename : dataset.filename }
          const field = side === 'sim' ? 'sim_receiver_id' : 'lt_receiver_id'
          next[field] = dataset.receivers.some((receiver) => receiver.id === item[field]) ? item[field] : null
        }
        checked.push(next)
      }
      setCases(checked); setActiveId(checked[0].id); setBaselineId(checked.some((item) => item.id === parsed.baseline_id) ? parsed.baseline_id! : checked[0].id); setManual(false)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '세션을 읽을 수 없습니다.') }
    finally { setBusy(false) }
  }
  return <AppDialog open={open} onOpenChange={onOpenChange} title="LT 결과 검증" size="lg" contentClassName="max-h-[94vh] overflow-x-hidden overflow-y-auto sm:max-w-[min(94vw,86rem)]" help=".bitsam과 LT 원시 수광 데이터를 case별로 비교합니다. 조건·좌표·총광속 검증을 통과한 데이터만 지표를 계산합니다. 현재 프로젝트와 해석을 변경하지 않으며, 적용된 데이터는 창을 닫아도 유지됩니다. 세션 저장으로 입력을 JSON 파일에 보관할 수 있습니다.">
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-40 flex-1 flex-col gap-1 text-xs"><span>Case</span><select aria-label="검증 Case" className={fieldClass} disabled={busy} value={active.id} onChange={(event) => selectCase(event.currentTarget.value)}>{cases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs"><span>이름</span><input aria-label="Case 이름" className={fieldClass} disabled={busy} value={active.name} onChange={(event) => updateCase({ name: event.currentTarget.value })} /></label>
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs"><span>기준 Case</span><select aria-label="기준 Case" className={fieldClass} disabled={busy} value={baselineId} onChange={(event) => { setBaselineId(event.currentTarget.value); setResponse(null) }}>{cases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <Button variant="outline" disabled={busy || cases.length >= 50} onClick={() => { const item = newCase(`Case ${cases.length + 1}`); setCases((current) => [...current, item]); setActiveId(item.id); setResponse(null); setManual(false) }}><Plus />Case 추가</Button>
        <Button variant="outline" aria-label="현재 Case 삭제" disabled={busy || cases.length <= 1} onClick={() => { const remaining = cases.filter((item) => item.id !== active.id); setCases(remaining); setActiveId(remaining[0].id); if (baselineId === active.id) setBaselineId(remaining[0].id); setResponse(null); setManual(false) }}><Trash2 /></Button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <DatasetCard title="Simulator" dataset={active.sim} selectedId={active.sim_receiver_id} busy={busy} onFile={(file) => void upload('sim', file, file.name)} onReceiver={(id) => updateCase({ sim_receiver_id: id || null })} />
        <DatasetCard title="LightTools" dataset={active.lt} selectedId={active.lt_receiver_id} busy={busy} onFile={(file) => void upload('lt', file, file.name)} onReceiver={(id) => { updateCase({ lt_receiver_id: id || null }); setManual(false) }} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="outline" disabled={busy} aria-expanded={manual} onClick={() => setManual((value) => !value)}>LT 원시 입력 / 조건 보완</Button>
        <div className="flex flex-wrap gap-2">
          <input ref={sessionInput} type="file" accept=".json" aria-label="LT 검증 세션 파일" className="hidden" onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void importSession(file) }} />
          <Button variant="outline" disabled={busy} onClick={() => sessionInput.current?.click()}><Upload />세션 열기</Button>
          <Button variant="outline" disabled={busy} onClick={exportSession}><Download />세션 저장</Button>
          <Button disabled={busy} onClick={() => void compare()}><FlaskConical />{busy ? '처리 중…' : '전체 비교'}</Button>
        </div>
      </div>
      {manual ? <ManualEditor key={`${active.id}-${active.lt_receiver_id ?? 'new'}`} dataset={active.lt} receiverId={active.lt_receiver_id} busy={busy} onSave={applyManual} onCancel={() => setManual(false)} /> : null}
      {error ? <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-sm text-destructive">{error}</p> : null}
      {response ? <ResultsTable response={response} activeId={active.id} onSelect={selectCase} /> : null}
      {activeResult ? <CasePlots result={activeResult} /> : null}
    </div>
  </AppDialog>
}
