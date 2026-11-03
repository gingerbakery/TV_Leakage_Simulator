import { useEffect, useRef, useState } from 'react'
import { BoxSelect, CircleHelp, Eye, EyeOff, Minus, Pencil, Plus, Trash2 } from 'lucide-react'
import type { ScenePayload } from '@/api'
import { Popover } from 'radix-ui'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'
import { createBlockGroup, nextBlockName } from './blocker-groups'
import { getLeakPreviewBounds, type LeakPreviewBlocker } from './leak-preview-model'
import { leakPreviewStore, useLeakPreviewStore } from './leak-preview-store'
import { cn } from '@/lib/utils'
import { BlockerGroupDimensions } from './blocker-group-dimensions'

export function BlockerEditor({ scene, disabled, onStart }: { scene: ScenePayload; disabled: boolean; onStart(): void }) {
  const blockers = useLeakPreviewStore((state) => state.blockers)
  const drawingId = useLeakPreviewStore((state) => state.blockerAreaSelectionId)
  const visible = useLeakPreviewStore((state) => state.blockersVisible)
  const setVisible = useLeakPreviewStore((state) => state.setBlockersVisible)
  const add = useLeakPreviewStore((state) => state.addBlocker)
  const update = useLeakPreviewStore((state) => state.updateBlocker)
  const remove = useLeakPreviewStore((state) => state.removeBlocker)
  const updateRegion = useLeakPreviewStore((state) => state.updateBlockerRegion)
  const removeRegion = useLeakPreviewStore((state) => state.removeBlockerRegion)
  const begin = useLeakPreviewStore((state) => state.beginBlockerAreaSelection)
  const finish = useLeakPreviewStore((state) => state.finishBlockerAreaSelection)
  const selected = useLeakPreviewStore((state) => state.selectedBlocker)
  const select = useLeakPreviewStore((state) => state.selectBlocker)
  const rows = useRef(new Map<string, HTMLDivElement>())
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    if (!selected?.regionId) return
    setCollapsedGroups((current) => {
      if (!current.has(selected.groupId)) return current
      const next = new Set(current); next.delete(selected.groupId); return next
    })
  }, [selected])
  useEffect(() => { if (selected) rows.current.get(selected.groupId)?.scrollIntoView?.({ block: 'nearest' }) }, [selected])
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [editing, setEditing] = useState<string | null>(null)
  const start = (id: string) => {
    onStart(); begin(id)
    actions.setRoiBoxSelectionArmed(leakPreviewStore.getState().blockers.find((group) => group.id === id)?.shape !== 'ellipse')
  }
  const stop = () => { finish(); actions.setRoiBoxSelectionArmed(false) }
  const create = (shape: 'box' | 'ellipse') => {
    stop()
    const group = createBlockGroup(nextBlockName(blockers), shape, getLeakPreviewBounds(scene).center)
    add(group); start(group.id)
  }
  const fields = (region: LeakPreviewBlocker, change: (patch: Partial<LeakPreviewBlocker>) => void, prefix: string) => <div className="grid grid-cols-3 gap-1.5">
    {(['X', 'Y', 'Z'] as const).map((axis, index) => <label key={axis} className="text-xs">{axis} (mm)<NumberInput aria-label={`${prefix} ${axis}`} className="h-8 w-full rounded border border-border bg-background px-1" value={region.baseCenter[index]} decimals={2} disabled={disabled} onValueChange={(value) => { const center = [...region.baseCenter] as [number, number, number]; center[index] = value; change({ baseCenter: center }) }} /></label>)}
    {([['폭', 'widthMm'], ['높이', 'heightMm'], ['깊이', 'depthMm'], ['Offset', 'offsetMm']] as const).map(([label, key]) => <label key={key} className="text-xs">{label} (mm)<NumberInput aria-label={`${prefix} ${label}`} className="h-8 w-full rounded border border-border bg-background px-1" value={region[key]} min={key === 'offsetMm' ? undefined : .01} decimals={2} disabled={disabled} onValueChange={(value) => change({ [key]: value })} /></label>)}
    <Button size="sm" variant={region.reverse ? 'default' : 'outline'} disabled={disabled} onClick={() => change({ reverse: !region.reverse })}>Reverse</Button>
  </div>
  return <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
    <div className="flex items-center justify-between gap-1"><span className="flex items-center gap-1 text-sm font-semibold">Blockers<Popover.Root><Popover.Trigger asChild><button type="button" aria-label="Blocker 도움말" className="inline-flex size-5 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-primary/40"><CircleHelp className="size-3.5" /></button></Popover.Trigger><Popover.Portal><Popover.Content side="right" sideOffset={6} className="z-50 max-h-80 w-72 overflow-auto rounded-md border border-border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">사각 블록은 Ctrl+왼쪽 드래그로 같은 그룹에 추가합니다. 추가 중에도 일반 드래그 회전·우클릭 이동·휠 확대와 F1~F4를 사용할 수 있습니다. 그룹 Edit의 그룹 치수에서 입력한 항목을 모든 사각 Block에 일괄 적용할 수 있습니다. 홀 막기는 원형 홀 중심 근처를 클릭해 같은 그룹에 마개를 추가합니다. Z축 원형의 닫힌 CAD 윤곽에서 중심과 지름을 인식하며, 새 마개는 미세한 여유를 포함한 인식 지름의 3배로 생성합니다. 기존 저장 마개는 변경하지 않습니다. 마개에 기울기나 회전을 넣지 않습니다. 깊이 기준면은 클릭 방향에서 보이는 홀 입구의 Z 좌표입니다. 기본 총 깊이는 5 mm로 입구 면에서 Z ±2.5 mm입니다. 홀 목록은 −/+로 접고 펼칠 수 있으며, 뷰어에서 개별 홀을 선택하면 해당 목록이 열립니다. 원형 홀을 찾지 못하면 마개를 생성하지 않습니다. 열린 윤곽·비원형 홀·기울어진 홀은 자동 인식 대상이 아닙니다. 목록의 그룹·홀이나 Viewer의 마개를 선택하면 서로 강조되며 Section에서도 같은 선택과 절단을 적용합니다. ON인 형상은 완전 흡수체로 빛을 실제 차단합니다. 눈 아이콘은 표시만 바꿉니다. 정밀해석에서는 Run Options의 Blocker를 켜세요. 기존 Allowed Area는 같은 범위의 사각 차광 형상으로 변환됩니다.</Popover.Content></Popover.Portal></Popover.Root></span>
      <Button size="sm" variant="outline" disabled={!blockers.length} onClick={() => setVisible(!visible)}>{visible ? <Eye /> : <EyeOff />}{visible ? 'Hide All' : 'Show All'}</Button></div>
    <div className="flex gap-1"><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('box')}>Block 만들기</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('ellipse')}>홀 막기</Button>{drawingId ? <Button size="sm" disabled={disabled} onClick={stop}>선택 완료</Button> : null}</div>
    {blockers.map((group) => <div key={group.id} role="group" aria-label={`${group.label} 목록`} data-selected={selected?.groupId === group.id}
      ref={(node) => { if (node) rows.current.set(group.id, node); else rows.current.delete(group.id) }}
      onClickCapture={() => { if (selected?.groupId !== group.id) select(group.id) }}
      className={cn('space-y-2 rounded border p-2', selected?.groupId === group.id ? 'border-orange-500 bg-orange-500/10' : 'border-border')}>
      <div className="flex items-center gap-1">
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} 선택`} aria-pressed={selected?.groupId === group.id && selected.regionId === null} onClick={() => select(group.id)}><BoxSelect /></Button>
        <input aria-label={`${group.label} name`} className="h-8 min-w-0 flex-1 rounded bg-transparent px-1 text-xs font-semibold focus:bg-muted/50" value={group.label} disabled={disabled} onFocus={() => select(group.id)} onChange={(event) => update(group.id, { label: event.currentTarget.value })} onBlur={() => { if (!group.label.trim()) update(group.id, { label: nextBlockName(blockers) }) }} />
        <input type="checkbox" aria-label={`${group.label} 적용`} disabled={disabled} checked={group.enabled} onChange={(event) => update(group.id, { enabled: event.currentTarget.checked })} />
        {group.shape === 'ellipse' ? <Button size="sm" variant="ghost" className="h-7 gap-1 px-1 text-xs"
          aria-label={`${group.label} 홀 목록 ${collapsedGroups.has(group.id) ? '펼치기' : '접기'}`}
          aria-expanded={!collapsedGroups.has(group.id)} aria-controls={`blocker-holes-${group.id}`}
          onClick={() => setCollapsedGroups((current) => { const next = new Set(current); if (next.has(group.id)) next.delete(group.id); else next.add(group.id); return next })}>
          {collapsedGroups.has(group.id) ? <Plus className="size-3" /> : <Minus className="size-3" />}홀 · {group.regions?.length ?? 1}개
        </Button> : <span className="whitespace-nowrap text-xs">Block · {group.regions?.length ?? 1}개</span>}
        <Button size="icon-xs" variant={drawingId === group.id ? 'default' : 'ghost'} aria-label={`${group.label} 영역 추가`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); else { stop(); start(group.id) } }}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} ${group.visible !== false ? 'Hide' : 'Show'}`} onClick={() => update(group.id, { visible: group.visible === false })}>{group.visible !== false ? <Eye /> : <EyeOff />}</Button>
        {group.shape !== 'ellipse' ? <Button size="icon-xs" variant="ghost" aria-label={`${group.label} Edit`} onClick={() => setEditing(editing === group.id ? null : group.id)}><Pencil /></Button> : null}
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} Delete`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); remove(group.id) }}><Trash2 /></Button>
      </div>
      {drawingId === group.id ? <div className="flex items-center gap-1 text-xs"><BoxSelect className="size-3" />{group.shape === 'ellipse' ? '홀 선택 중' : '영역 선택 중'}</div> : null}
      {group.shape === 'ellipse' && !collapsedGroups.has(group.id) ? <div id={`blocker-holes-${group.id}`} className="space-y-1">
        <div className="text-xs text-muted-foreground">새 마개 · Z ±2.5 mm</div>
        {group.regions?.map((region, index) => <div key={region.id} className="flex gap-1">
          <Button size="sm" variant="outline" className={cn('h-7 flex-1 justify-start text-xs', selected?.groupId === group.id && selected.regionId === region.id && 'border-orange-500 bg-orange-500/15')}
            aria-label={`${group.label} 홀 ${index+1} 선택`} aria-pressed={selected?.groupId === group.id && selected.regionId === region.id}
            onFocus={() => select(group.id, region.id)} onClick={() => select(group.id, region.id)}>홀 {index+1}</Button>
          <Button size="icon-xs" variant="ghost" aria-label={`${group.label} 홀 ${index+1} 삭제`} disabled={disabled} onClick={() => removeRegion(group.id, region.id)}><Trash2 /></Button>
        </div>)}
      </div> : null}
      {editing === group.id && group.shape !== 'ellipse' ? <div className="space-y-2">
        <BlockerGroupDimensions group={group} disabled={disabled} onApply={(dimensions) => update(group.id,{
          ...dimensions, regions:group.regions?.map((region) => region.shape === 'ellipse' ? region : {...region,...dimensions}),
        })} />
        <label className="flex items-center gap-2 text-xs">형상<select aria-label={`${group.label} 형상`} className="rounded border border-border bg-background p-1" disabled={disabled} value={group.shape ?? 'box'} onChange={(event) => { const shape = event.currentTarget.value as 'box' | 'ellipse'; update(group.id, { shape, regions: group.regions?.map((region) => ({ ...region, shape })) }) }}><option value="box">사각 블록</option><option value="ellipse">홀 마개</option></select></label>
        {group.regions ? <><div className="text-xs">새 영역 깊이 (mm)<NumberInput aria-label={`${group.label} 새 영역 깊이`} min={.01} value={group.depthMm} disabled={disabled} onValueChange={(depthMm) => update(group.id, { depthMm })} /></div>{group.regions.map((region, index) => <details key={region.id} className={cn('rounded border p-2', selected?.groupId === group.id && selected.regionId === region.id ? 'border-orange-500' : 'border-border')}><summary className="cursor-pointer text-xs" onClick={() => select(group.id, region.id)}>영역 {index + 1}</summary><div className="mt-2 space-y-2">{fields(region, (patch) => updateRegion(group.id, region.id, patch), `${group.label} 영역 ${index + 1}`)}<Button size="sm" variant="outline" aria-label={`${group.label} 영역 ${index + 1} 삭제`} disabled={disabled} onClick={() => removeRegion(group.id, region.id)}>영역 삭제</Button></div></details>)}</> : fields(group, (patch) => update(group.id, patch), group.label)}
      </div> : null}
    </div>)}
  </section>
}
