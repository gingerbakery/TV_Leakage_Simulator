import { useId, useState } from 'react'
import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'
import { buildManualDataset, createManualDraft, mapExtractedFlux, type ManualDraft } from './manual-data'
import type { ValidationDataset } from './types'
import { SourceEditor } from './source-editor'

export const fieldClass = 'block h-8 w-full min-w-0 rounded-md border border-border bg-background px-2 text-sm outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50'

export function ManualEditor({ dataset, receiverId, busy, onSave, onCancel }: {
  dataset: ValidationDataset | null
  receiverId: string | null
  busy: boolean
  onSave(dataset: ValidationDataset): Promise<void>
  onCancel(): void
}) {
  const editorId = useId()
  const [draft, setDraft] = useState(() => createManualDraft(dataset, dataset?.receivers.find((item) => item.id === receiverId)))
  const [error, setError] = useState<string | null>(null)
  const [fastAxis, setFastAxis] = useState('')
  const [uOrder, setUOrder] = useState('')
  const [vOrder, setVOrder] = useState('')
  const receiver = dataset?.receivers.find((item) => item.id === receiverId)
  const extractedValues = receiver?.provenance?.raw_flux_lm_flat
  const canMapExtracted = !receiver?.flux_lm && Array.isArray(extractedValues) && extractedValues.every((value) => typeof value === 'number' && Number.isFinite(value))
  function field<K extends keyof ManualDraft>(key: K, value: ManualDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
    setError(null)
  }
  function textInput(key: keyof ManualDraft, label: string, help?: string) {
    return <div className="min-w-0 space-y-1 text-xs" key={key}>
      <span className="flex items-center gap-1"><label htmlFor={`${editorId}-${key}`}>{label}</label>{help ? <HelpTooltip label={`${label} 도움말`}>{help}</HelpTooltip> : null}</span>
      <input id={`${editorId}-${key}`} aria-label={label} className={fieldClass} value={String(draft[key])} onChange={(event) => field(key, event.currentTarget.value)} />
    </div>
  }
  async function save() {
    try {
      setError(null)
      await onSave(buildManualDataset(draft, dataset, receiverId))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'LT 입력을 확인하세요.')
    }
  }
  return (
    <fieldset disabled={busy} className="space-y-3 rounded-lg border border-primary/25 bg-primary/3 p-3">
      <legend className="flex items-center gap-1 px-1 text-sm font-semibold">LT 원시 표 · 조건 보완
        <HelpTooltip label="LT 원시 입력 도움말">LT에서 확인한 값만 입력하세요. 화면 사진의 색상이나 보간된 값을 원시 mesh로 사용하지 않습니다. 비어 있는 조건은 확인 불가로 유지하며 Simulator 값을 자동 복사하지 않습니다. SumMesh는 IntegrationInterval의 의미와 단위가 검증된 후 셀 flux 또는 조도로 환산한 값만 입력하세요.</HelpTooltip>
      </legend>
      {receiver?.quantity === 'radiometric' ? <p className="text-xs text-destructive">Radiometric 원본은 이 입력에서 photometric 비교로 변환할 수 없습니다.</p> : null}
      <div className="grid gap-3 sm:grid-cols-3">
        {textInput('name', 'LT 리시버 이름')}
        <div className="space-y-1 text-xs"><span className="flex items-center gap-1"><label htmlFor={`${editorId}-raw`}>데이터 상태</label><HelpTooltip label="데이터 상태 도움말">원시 mesh임을 확인한 경우에만 원시를 선택하세요. 보간·평활화된 표와 미확인 표는 비교 지표를 계산하지 않습니다.</HelpTooltip></span>
          <select id={`${editorId}-raw`} aria-label="LT 데이터 상태" className={fieldClass} value={draft.raw} onChange={(event) => field('raw', event.currentTarget.value as ManualDraft['raw'])}>
            <option value="">미확인</option><option value="raw">원시 mesh</option><option value="smoothed">보간 / 평활화</option>
          </select>
        </div>
        <label className="space-y-1 text-xs"><span className="block">표 단위</span>
          <select aria-label="LT 표 단위" className={fieldClass} value={draft.unit} onChange={(event) => field('unit', event.currentTarget.value as ManualDraft['unit'])}>
            <option value="">선택</option><option value="flux_lm">셀 flux (lm)</option><option value="illuminance_lx">조도 (lx)</option>
          </select>
        </label>
        {textInput('total', 'LT TotalPower (lm)', 'LT에 저장된 총 수광량을 독립적으로 입력합니다. 셀 flux 합계와 일치하는지 검증하며, 합계를 맞추기 위해 셀값을 조정하지 않습니다.')}
        {textInput('uEdges', 'U 셀 경계 (mm)', '작은 좌표부터 큰 좌표 순서로 입력합니다. N열에는 N+1개 셀 경계가 필요합니다. 예: −2.5, −1.5, −0.5, 0.5, 1.5, 2.5.')}
        {textInput('vEdges', 'V 셀 경계 (mm)', '작은 좌표부터 큰 좌표 순서로 입력합니다. N행에는 N+1개 셀 경계가 필요합니다.')}
      </div>
      {canMapExtracted ? <div className="space-y-2 rounded-md border border-amber-500/40 p-2">
        <div className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">추출 배열 · {extractedValues.length}개 · 좌표 순서 미확인
          <HelpTooltip label="추출 배열 배치 도움말">LTS에서 환산된 셀 flux 배열입니다. 파일의 직렬화 순서를 LT 원시 표의 좌표와 대조한 뒤 빠른 축 및 각 축의 순서를 선택하세요. 상관관계를 높이기 위한 반전은 허용하지 않습니다. 데이터 상태·리시버 위치·축 방향은 별도로 확인해야 합니다.</HelpTooltip>
        </div>
        <div className="grid gap-2 sm:grid-cols-4">
          <label className="space-y-1 text-xs">빠른 축<select aria-label="추출 배열 빠른 축" className={fieldClass} value={fastAxis} onChange={(event) => setFastAxis(event.currentTarget.value)}><option value="">선택</option><option value="U">U 먼저 변함</option><option value="V">V 먼저 변함</option></select></label>
          <label className="space-y-1 text-xs">U 저장 순서<select aria-label="추출 배열 U 순서" className={fieldClass} value={uOrder} onChange={(event) => setUOrder(event.currentTarget.value)}><option value="">선택</option><option value="ascending">작은 → 큰 좌표</option><option value="descending">큰 → 작은 좌표</option></select></label>
          <label className="space-y-1 text-xs">V 저장 순서<select aria-label="추출 배열 V 순서" className={fieldClass} value={vOrder} onChange={(event) => setVOrder(event.currentTarget.value)}><option value="">선택</option><option value="ascending">작은 → 큰 좌표</option><option value="descending">큰 → 작은 좌표</option></select></label>
          <Button className="self-end" variant="outline" onClick={() => {
            try {
              const matrix = mapExtractedFlux(extractedValues as number[], draft.uEdges, draft.vEdges, fastAxis, uOrder, vOrder, receiver?.provenance?.raw_dimensions)
              setDraft((current) => ({ ...current, matrix: matrix.map((row) => row.join('\t')).join('\n'), tableFormat: 'matrix', unit: 'flux_lm', coordinateOrderConfirmed: true, extractedOrder: `fast=${fastAxis};U=${uOrder};V=${vOrder}` }))
              setError(null)
            } catch (cause) { setError(cause instanceof Error ? cause.message : '배열 순서를 확인하세요.') }
          }}>확인한 순서 적용</Button>
        </div>
      </div> : null}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs">표 형식
          <select aria-label="LT 표 형식" className={`${fieldClass} w-auto`} value={draft.tableFormat} onChange={(event) => field('tableFormat', event.currentTarget.value as ManualDraft['tableFormat'])}>
            <option value="matrix">셀값만</option><option value="coordinates">Y\X 좌표 포함</option>
          </select>
        </label>
        <HelpTooltip label="원시 표 형식 도움말">탭·쉼표·공백으로 열을, 줄바꿈으로 행을 나눕니다. 좌표 포함 표는 첫 행에 Y\X와 U 중심좌표, 첫 열에 V 중심좌표를 넣습니다. 명시된 좌표에 따라 순서를 정렬하며 입력 셀 경계와 검증합니다. 셀값만 입력할 때는 첫 행이 최소 V, 첫 열이 최소 U여야 합니다.</HelpTooltip>
        {draft.tableFormat === 'matrix' ? <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={draft.coordinateOrderConfirmed} onChange={(event) => field('coordinateOrderConfirmed', event.currentTarget.checked)} />첫 행 최소 V · 첫 열 최소 U 확인</label> : null}
      </div>
      <textarea aria-label="LT 원시 mesh 표" value={draft.matrix} onChange={(event) => field('matrix', event.currentTarget.value)} className="min-h-32 w-full rounded-md border border-border bg-background p-2 font-mono text-xs outline-none focus:ring-2 focus:ring-primary/40" spellCheck={false} />
      <details className="rounded-md border border-border p-2">
        <summary className="cursor-pointer text-sm font-medium">리시버 조건</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {textInput('center', '리시버 중심 X, Y, Z (mm)')}
          {textInput('uAxis', '로컬 U 방향 X, Y, Z', 'World 좌표의 단위 벡터입니다. 리시버 가로 좌표의 양의 방향을 입력합니다.')}
          {textInput('vAxis', '로컬 V 방향 X, Y, Z', 'World 좌표의 단위 벡터입니다. 리시버 세로 좌표의 양의 방향을 입력합니다.')}
          {textInput('normal', '수광 법선 X, Y, Z', '실제로 빛을 받는 면의 법선 단위 벡터입니다. 방향 반전 설정을 반영한 값을 입력합니다.')}
          {textInput('acceptance', '수광각 (deg)', '유효 수광 법선을 중심으로 한 허용 반각입니다. 0° 초과, 90° 이하의 값을 입력합니다. 전체 각도가 아닙니다.')}
          <label className="space-y-1 text-xs"><span className="block">Aperture</span><select aria-label="LT aperture" className={fieldClass} value={draft.aperture} onChange={(event) => field('aperture', event.currentTarget.value as ManualDraft['aperture'])}><option value="">미확인</option><option value="none">없음 확인</option></select></label>
          {textInput('tolerance', 'TotalPower 반올림 허용량 (lm)', '사진에 표시된 총광속의 반올림이 확인된 경우에만 입력합니다. 최대 5e−8 lm이며 기본 검증 허용오차를 임의로 완화하지 않습니다.')}
        </div>
      </details>
      <details className="rounded-md border border-border p-2">
        <summary className="cursor-pointer text-sm font-medium">광원 조건</summary>
        <SourceEditor value={draft.sources} onChange={(value) => field('sources', value)} />
      </details>
      {receiver?.provenance ? <details className="rounded-md border border-border p-2"><summary className="cursor-pointer text-sm font-medium">추출 메타데이터</summary><pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/50 p-2 text-xs">{JSON.stringify(receiver.provenance, null, 2)}</pre></details> : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={onCancel}>취소</Button><Button onClick={() => void save()}>{busy ? '검증 중…' : '입력 적용'}</Button></div>
    </fieldset>
  )
}
