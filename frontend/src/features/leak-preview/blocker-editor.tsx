import { useState } from 'react'
import { BoxSelect, CircleHelp, Eye, EyeOff, Pencil, Plus, Trash2 } from 'lucide-react'
import type { ScenePayload } from '@/api'
import { Popover } from 'radix-ui'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'
import { createBlockGroup, nextBlockName } from './blocker-groups'
import { getLeakPreviewBounds, type LeakPreviewBlocker } from './leak-preview-model'
import { useLeakPreviewStore } from './leak-preview-store'

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
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [editing, setEditing] = useState<string | null>(null)
  const start = (id: string) => { onStart(); begin(id); actions.setRoiBoxSelectionArmed(true) }
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
    <div className="flex items-center justify-between gap-1"><span className="flex items-center gap-1 text-sm font-semibold">Blockers<Popover.Root><Popover.Trigger asChild><button type="button" aria-label="Blocker 도움말" className="inline-flex size-5 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-primary/40"><CircleHelp className="size-3.5" /></button></Popover.Trigger><Popover.Portal><Popover.Content side="right" sideOffset={6} className="z-50 max-h-80 w-72 overflow-auto rounded-md border border-border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">드래그할 때마다 같은 그룹에 영역을 추가합니다. 사각 블록은 보드·부품용 직육면체이며, 홀 막기는 선택 범위에 맞춘 원형·타원형 마개입니다. CAD 홀 윤곽을 자동 인식하지 않으므로 폭·높이·깊이와 위치를 확인하세요. 드래그 모서리 주변 CAD 면을 기준으로 배치하며 빈 공간에서는 현재 기준 평면을 사용합니다. 선택 완료 후에도 +로 영역을 추가할 수 있습니다. ON인 형상은 완전 흡수체로 빛을 실제 차단합니다. 눈 아이콘은 표시만 바꿉니다. 정밀해석에서는 Run Options의 Blocker를 켜세요. 기존 Allowed Area는 같은 범위의 사각 차광 형상으로 변환됩니다.</Popover.Content></Popover.Portal></Popover.Root></span>
      <Button size="sm" variant="outline" disabled={!blockers.length} onClick={() => setVisible(!visible)}>{visible ? <Eye /> : <EyeOff />}{visible ? 'Hide All' : 'Show All'}</Button></div>
    <div className="flex gap-1"><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('box')}>Block 만들기</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('ellipse')}>홀 막기</Button>{drawingId ? <Button size="sm" disabled={disabled} onClick={stop}>선택 완료</Button> : null}</div>
    {blockers.map((group) => <div key={group.id} className="space-y-2 rounded border border-border p-2">
      <div className="flex items-center gap-1">
        <input aria-label={`${group.label} name`} className="h-8 min-w-0 flex-1 rounded bg-transparent px-1 text-xs font-semibold focus:bg-muted/50" value={group.label} disabled={disabled} onChange={(event) => update(group.id, { label: event.currentTarget.value })} onBlur={() => { if (!group.label.trim()) update(group.id, { label: nextBlockName(blockers) }) }} />
        <input type="checkbox" aria-label={`${group.label} 적용`} disabled={disabled} checked={group.enabled} onChange={(event) => update(group.id, { enabled: event.currentTarget.checked })} />
        <span className="whitespace-nowrap text-xs">{group.shape === 'ellipse' ? '홀' : 'Block'} · {group.regions?.length ?? 1}개</span>
        <Button size="icon-xs" variant={drawingId === group.id ? 'default' : 'ghost'} aria-label={`${group.label} 영역 추가`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); else { stop(); start(group.id) } }}><Plus /></Button>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} ${group.visible !== false ? 'Hide' : 'Show'}`} onClick={() => update(group.id, { visible: group.visible === false })}>{group.visible !== false ? <Eye /> : <EyeOff />}</Button>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} Edit`} onClick={() => setEditing(editing === group.id ? null : group.id)}><Pencil /></Button>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} Delete`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); remove(group.id) }}><Trash2 /></Button>
      </div>
      {drawingId === group.id ? <div className="flex items-center gap-1 text-xs"><BoxSelect className="size-3" />영역 선택 중</div> : null}
      {editing === group.id ? <div className="space-y-2">
        <label className="flex items-center gap-2 text-xs">형상<select aria-label={`${group.label} 형상`} className="rounded border border-border bg-background p-1" disabled={disabled} value={group.shape ?? 'box'} onChange={(event) => { const shape = event.currentTarget.value as 'box' | 'ellipse'; update(group.id, { shape, regions: group.regions?.map((region) => ({ ...region, shape })) }) }}><option value="box">사각 블록</option><option value="ellipse">홀 마개</option></select></label>
        {group.regions ? <><div className="text-xs">새 영역 깊이 (mm)<NumberInput aria-label={`${group.label} 새 영역 깊이`} min={.01} value={group.depthMm} disabled={disabled} onValueChange={(depthMm) => update(group.id, { depthMm })} /></div>{group.regions.map((region, index) => <details key={region.id} className="rounded border border-border p-2"><summary className="cursor-pointer text-xs">영역 {index + 1}</summary><div className="mt-2 space-y-2">{fields(region, (patch) => updateRegion(group.id, region.id, patch), `${group.label} 영역 ${index + 1}`)}<Button size="sm" variant="outline" aria-label={`${group.label} 영역 ${index + 1} 삭제`} disabled={disabled} onClick={() => removeRegion(group.id, region.id)}>영역 삭제</Button></div></details>)}</> : fields(group, (patch) => update(group.id, patch), group.label)}
      </div> : null}
    </div>)}
  </section>
}
