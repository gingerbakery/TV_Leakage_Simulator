import { useId, useState } from 'react'
import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'

const controlClass = 'block h-8 w-full min-w-0 rounded-md border border-border bg-background px-2 text-sm outline-none focus:ring-2 focus:ring-primary/40'
type EditableSource = Record<string, unknown>

function text(value: unknown) { return value == null ? '' : Array.isArray(value) ? value.join(', ') : String(value) }

export function SourceEditor({ value, onChange }: { value: string; onChange(value: string): void }) {
  const editorId = useId()
  const [selected, setSelected] = useState(0)
  let sources: EditableSource[] = []
  let valid = true
  try {
    const parsed: unknown = JSON.parse(value)
    if (!Array.isArray(parsed) || parsed.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error('invalid source array')
    sources = parsed as EditableSource[]
  } catch { valid = false }
  const index = Math.min(selected, Math.max(0, sources.length - 1))
  const source = sources[index]
  const aim = source?.aim && typeof source.aim === 'object' ? source.aim as EditableSource : null
  function update(next: EditableSource[]) { onChange(JSON.stringify(next, null, 2)) }
  function field(key: string, next: unknown, inAim = false) {
    update(sources.map((item, at) => at === index ? inAim ? { ...item, aim: { ...aim, [key]: next } } : { ...item, [key]: next } : item))
  }
  function input(key: string, label: string, inAim = false, help?: string) {
    const id = `${editorId}-${inAim ? 'aim' : 'source'}-${key}`
    return <div key={key} className="min-w-0 space-y-1 text-xs">
      <span className="flex items-center gap-1"><label htmlFor={id}>{label}</label>{help ? <HelpTooltip label={`${label} 도움말`}>{help}</HelpTooltip> : null}</span>
      <input id={id} aria-label={`LT ${inAim ? 'Aim ' : '광원 '}${label}`} className={controlClass} value={text((inAim ? aim : source)?.[key])} onChange={(event) => field(key, event.currentTarget.value, inAim)} />
    </div>
  }
  return <div className="mt-3 space-y-3">
    <div className="flex items-end gap-2">
      <label className="min-w-0 flex-1 space-y-1 text-xs">광원<select aria-label="LT 광원 선택" className={controlClass} value={source ? index : ''} onChange={(event) => setSelected(Number(event.currentTarget.value))} disabled={!sources.length}>
        {!sources.length ? <option value="">미입력</option> : sources.map((item, at) => <option key={at} value={at}>{text(item.name) || `Source ${at + 1}`}</option>)}
      </select></label>
      <Button variant="outline" disabled={!valid} onClick={() => { update([...sources, { id: crypto.randomUUID(), name: `Source ${sources.length + 1}`, total_flux_lm: null, flux_reference: null, center_mm: null, u_axis: null, v_axis: null, normal: null, width_mm: null, height_mm: null, angular_distribution: null, aim: null }]); setSelected(sources.length) }}>광원 추가</Button>
      <Button variant="outline" disabled={!source} onClick={() => { update(sources.filter((_, at) => at !== index)); setSelected(Math.max(0, index - 1)) }}>광원 삭제</Button>
    </div>
    {source ? <>
      <div className="grid gap-3 sm:grid-cols-3">
        {input('name', '이름')}
        {input('total_flux_lm', '총광속 (lm)')}
        <div className="space-y-1 text-xs"><span className="flex items-center gap-1"><label htmlFor={`${editorId}-basis`}>광속 기준</label><HelpTooltip label="광원 광속 기준 도움말">LT와 Simulator가 같은 광속 정의를 사용하는지 확인합니다. 실제 발광량과 Aim region 기준 광속을 구분하며, 모르는 경우 미확인으로 둡니다.</HelpTooltip></span><select id={`${editorId}-basis`} aria-label="LT 광원 광속 기준" className={controlClass} value={text(source.flux_reference)} onChange={(event) => field('flux_reference', event.currentTarget.value || null)}><option value="">미확인</option><option value="emitted">실제 발광량</option><option value="aim_region">Aim region 기준</option></select></div>
        {input('center_mm', '중심 X, Y, Z (mm)')}
        {input('u_axis', 'U 방향 X, Y, Z')}
        {input('v_axis', 'V 방향 X, Y, Z')}
        {input('normal', '발광 법선 X, Y, Z', false, '실제 바깥쪽 발광 방향의 World 단위 벡터입니다. 양면 발광 또는 방향 정의를 확인하지 못한 경우 비워 둡니다.')}
        {input('width_mm', '너비 (mm)')}
        {input('height_mm', '높이 (mm)')}
        {input('angular_distribution', '각도 분포', false, 'LT에서 확인한 각도 분포 이름을 입력합니다. 예: lambertian. 비교 가능한 모델과 정의인지 검증하며, 동일해 보이게 하기 위한 이름 변경은 하지 않습니다.')}
      </div>
      <div className="rounded-md border border-border p-2">
        <div className="flex items-center gap-1.5 text-xs font-medium"><label className="flex items-center gap-1.5"><input type="checkbox" checked={Boolean(aim)} onChange={(event) => field('aim', event.currentTarget.checked ? { center_mm: null, u_axis: null, v_axis: null, width_mm: null, height_mm: null, distribution: null } : null)} />Aim region 사용</label><HelpTooltip label="Aim region 조건 도움말">광원 Aim region의 위치·축·크기·분포를 입력합니다. 켜기만 하면 모든 수치는 미확인 상태이며 다른 데이터에서 복사하지 않습니다.</HelpTooltip></div>
        {aim ? <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {input('center_mm', '중심 X, Y, Z (mm)', true)}
          {input('u_axis', 'U 방향 X, Y, Z', true)}
          {input('v_axis', 'V 방향 X, Y, Z', true)}
          {input('width_mm', '너비 (mm)', true)}
          {input('height_mm', '높이 (mm)', true)}
          {input('distribution', '분포', true)}
        </div> : null}
      </div>
    </> : null}
    {!valid ? <p role="alert" className="text-xs text-destructive">광원 조건 JSON이 올바르지 않습니다.</p> : null}
    <details className="rounded-md border border-border p-2"><summary className="cursor-pointer text-xs">고급 JSON</summary>
      <textarea aria-label="LT 광원 조건 JSON" value={value} onChange={(event) => onChange(event.currentTarget.value)} className="mt-2 min-h-40 w-full rounded-md border border-border bg-background p-2 font-mono text-xs" spellCheck={false} />
    </details>
  </div>
}
