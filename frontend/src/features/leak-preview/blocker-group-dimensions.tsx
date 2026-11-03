import { useState } from 'react'
import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'
import type { LeakPreviewBlocker } from './leak-preview-model'

export type BlockerDimensions = Pick<LeakPreviewBlocker,'widthMm'|'heightMm'|'depthMm'|'offsetMm'>
const fields = [['widthMm','폭'],['heightMm','높이'],['depthMm','깊이'],['offsetMm','Offset']] as const

export function BlockerGroupDimensions({ group,disabled,onApply }: {
  group:LeakPreviewBlocker; disabled:boolean; onApply(dimensions:Partial<BlockerDimensions>):void
}) {
  const [draft,setDraft] = useState({widthMm:'',heightMm:'',depthMm:'',offsetMm:''})
  const [status,setStatus] = useState('')
  const [error,setError] = useState('')
  const apply = () => {
    const dimensions: Partial<BlockerDimensions> = {}
    for (const [key,label] of fields) {
      if (!draft[key].trim()) continue
      const value = Number(draft[key])
      if (!Number.isFinite(value) || (key !== 'offsetMm' && value < .01)) {
        setError(`${label}: ${key === 'offsetMm' ? '유한한 숫자' : '0.01 mm 이상의 숫자'}를 입력하세요.`)
        return
      }
      dimensions[key] = value
    }
    if (!Object.keys(dimensions).length) return
    onApply(dimensions)
    setDraft({widthMm:'',heightMm:'',depthMm:'',offsetMm:''})
    setStatus(`${group.regions?.filter((region) => region.shape !== 'ellipse').length ?? 1}개 적용 완료`)
  }
  return <div className="space-y-2 rounded border border-border p-2">
    <div className="flex items-center gap-1 text-xs font-semibold">그룹 치수<HelpTooltip label={`${group.label} 그룹 치수 도움말`}>
      입력한 항목만 이 그룹의 모든 사각 Block과 그룹 기본값에 적용합니다. 빈 항목은 유지합니다. 각 영역의 기준 위치·방향·표시·적용 상태는 유지하며 다른 그룹과 홀 마개는 변경하지 않습니다.
    </HelpTooltip></div>
    <div className="grid grid-cols-2 gap-1.5">{fields.map(([key,label]) => <label key={key} className="text-xs">{label} (mm)
      <input inputMode="decimal" aria-label={`${group.label} 그룹 ${label}`} disabled={disabled}
        className="mt-1 h-8 w-full rounded border border-border bg-background px-1" placeholder="유지" value={draft[key]}
        onChange={(event) => { setDraft({...draft,[key]:event.currentTarget.value}); setStatus(''); setError('') }} />
    </label>)}</div>
    {error ? <div role="alert" className="text-xs text-destructive">{error}</div> : null}
    {status ? <div role="status" className="text-xs text-muted-foreground">{status}</div> : null}
    <Button size="sm" variant="outline" className="w-full" aria-label={`${group.label} 그룹 치수 전체 적용`}
      disabled={disabled || !Object.values(draft).some((value) => value.trim())} onClick={apply}>전체 적용</Button>
  </div>
}
