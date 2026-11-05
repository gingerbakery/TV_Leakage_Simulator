import { useEffect, useRef, useState } from 'react'
import { BoxSelect, ChevronDown, ChevronRight, CircleHelp, Eye, EyeOff, Pencil, Plus, Check, Trash2 } from 'lucide-react'
import type { ScenePayload } from '@/api'
import { Popover } from 'radix-ui'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { workspaceStore, useWorkspaceStore, workspaceSelectors } from '@/stores'
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
  const regionRows = useRef(new Map<string, HTMLDivElement>())
  const drawingOwner = useRef<{ caseId: string | null; groupId: string } | null>(null)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set())
  const [listExpanded, setListExpanded] = useState(true)
  useEffect(() => {
    if (!selected) return
    setListExpanded(true)
    if (!selected.regionId) return
    setCollapsedGroups((current) => {
      if (!current.has(selected.groupId)) return current
      const next = new Set(current); next.delete(selected.groupId); return next
    })
  }, [selected])
  useEffect(() => {
    if (!selected) return
    const row = selected.regionId ? regionRows.current.get(`${selected.groupId}:${selected.regionId}`) : null
    const target = row ?? rows.current.get(selected.groupId)
    target?.scrollIntoView?.({ block: 'nearest' })
  }, [selected, collapsedGroups, listExpanded])
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  useEffect(() => () => {
    const owner = drawingOwner.current
    if (owner && owner.caseId === workspaceStore.getState().activeCadCaseId &&
      owner.groupId === leakPreviewStore.getState().blockerAreaSelectionId) {
      finish()
      actions.setRoiBoxSelectionArmed(false)
    }
  }, [actions, finish])
  const [editing, setEditing] = useState<string | null>(null)
  const [editingRegion, setEditingRegion] = useState<string | null>(null)
  const start = (id: string) => {
    setListExpanded(true)
    setCollapsedGroups((current) => { const next = new Set(current); next.delete(id); return next })
    onStart()
    drawingOwner.current = { caseId: workspaceStore.getState().activeCadCaseId, groupId: id }
    begin(id)
    // Drawing must be visible; retain the independent analysis ON/OFF state.
    setVisible(true)
    if (leakPreviewStore.getState().blockers.find((group) => group.id === id)?.visible === false) update(id, { visible: true })
    actions.setRoiBoxSelectionArmed(leakPreviewStore.getState().blockers.find((group) => group.id === id)?.shape !== 'ellipse')
  }
  const stop = () => { finish(); actions.setRoiBoxSelectionArmed(false); drawingOwner.current = null }
  const create = (shape: 'box' | 'ellipse') => {
    stop()
    const group = createBlockGroup(nextBlockName(blockers), shape, getLeakPreviewBounds(scene).center)
    add(group)
    actions.setRayTraceConfig({ ...workspaceStore.getState().rayTraceConfig, apply_preview_blockers: true })
    start(group.id)
  }
  const fields = (region: LeakPreviewBlocker, change: (patch: Partial<LeakPreviewBlocker>) => void, prefix: string) => <div className="grid grid-cols-3 gap-1.5">
    {(['X', 'Y', 'Z'] as const).map((axis, index) => <label key={axis} className="text-xs">{axis} (mm)<NumberInput aria-label={`${prefix} ${axis}`} className="h-8 w-full rounded border border-border bg-background px-1" value={region.baseCenter[index]} decimals={2} commitOnBlur disabled={disabled} onValueChange={(value) => { const center = [...region.baseCenter] as [number, number, number]; center[index] = value; change({ baseCenter: center }) }} /></label>)}
    {([['폭', 'widthMm'], ['높이', 'heightMm'], ['깊이', 'depthMm'], ['Offset', 'offsetMm']] as const).map(([label, key]) => <label key={key} className="text-xs">{label} (mm)<NumberInput aria-label={`${prefix} ${label}`} className="h-8 w-full rounded border border-border bg-background px-1" value={region[key]} min={key === 'offsetMm' ? undefined : .01} decimals={2} commitOnBlur disabled={disabled} onValueChange={(value) => change({ [key]: value })} /></label>)}
    <Button size="sm" variant={region.reverse ? 'default' : 'outline'} disabled={disabled} onClick={() => change({ reverse: !region.reverse })}>Reverse</Button>
  </div>
  return <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
    <div className="flex items-center justify-between gap-1"><span className="flex items-center gap-1 text-sm font-semibold">Blockers<Popover.Root><Popover.Trigger asChild><button type="button" aria-label="Blocker 도움말" className="inline-flex size-5 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-primary/40"><CircleHelp className="size-3.5" /></button></Popover.Trigger><Popover.Portal><Popover.Content side="right" sideOffset={6} className="z-50 max-h-80 w-72 overflow-auto rounded-md border border-border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">Block 만들기는 새 그룹을 만들고, 형상 추가는 선택한 그룹에 형상을 추가합니다. 목록은 꺾쇠 버튼으로 접고 펼칩니다. 사각 블록은 CAD 면을 클릭해 깊이를 고정한 뒤 왼쪽 드래그로 같은 그룹에 추가합니다. 생성 평면과 가로·세로 축은 현재 뷰 화면을 따르며 CAD 면의 경사를 따르지 않습니다. Ctrl+왼쪽 드래그로도 바로 추가할 수 있습니다. 선택한 깊이는 모서리가 모델 밖이나 틈에 있어도 유지됩니다. 뷰를 회전하면 다음 드래그는 회전한 화면 평면에 생성됩니다. 기존 Block의 기울기는 변경하지 않습니다. Block 만들기 중 왼쪽 드래그는 항상 박스 그리기이며 회전은 F3을 사용합니다. 면을 먼저 클릭하지 않고 바로 드래그하면 시작점의 면을 사용합니다. 우클릭 이동·휠 확대와 F1~F4도 사용할 수 있습니다. 그룹 치수는 모든 사각 Block에 일괄 적용하며 각 행의 치수는 해당 Block만 편집합니다. 개별 숫자는 Enter 또는 포커스 이동 시 반영하고, 빈 입력과 Esc는 기존 값을 유지합니다. 폭·높이·깊이는 최소 0.01 mm입니다. Esc로 생성 모드를 종료하면 이미 만든 형상은 유지됩니다. 입력 중인 칸이나 도움말 창의 Esc는 그 편집과 창을 우선합니다. 다른 작업 탭으로 이동해도 생성 모드는 종료합니다. 형상 추가를 시작하면 그룹 표시를 켜지만 해석 적용 OFF는 유지합니다. 홀 막기는 원형 홀 중심 근처를 클릭해 같은 그룹에 마개를 추가합니다. Z축 원형의 닫힌 CAD 윤곽에서 중심과 지름을 인식하며, 새 마개는 미세한 여유를 포함한 인식 지름의 3배로 생성합니다. 기존 저장 마개는 변경하지 않습니다. 마개에 기울기나 회전을 넣지 않습니다. 깊이 기준면은 클릭 방향에서 보이는 홀 입구의 Z 좌표입니다. 기본 총 깊이는 5 mm로 입구 면에서 Z ±2.5 mm입니다. Block과 홀 목록은 꺾쇠로 접고 펼칠 수 있으며, 뷰어에서 개별 형상을 선택하면 해당 목록이 열립니다. 원형 홀을 찾지 못하면 마개를 생성하지 않습니다. 열린 윤곽·비원형 홀·기울어진 홀은 자동 인식 대상이 아닙니다. 목록의 그룹·홀이나 Viewer의 마개를 선택하면 서로 강조되며 Section에서도 같은 선택과 절단을 적용합니다. ON인 형상은 투과 없는 대체 형상으로 빛을 차단하고 기본 재질로 반사합니다. 별도 기본 재질이 없으면 기존 Preview 기본값인 반사율 35%의 혼합 반사를 사용합니다. 그룹·개별 적용 체크는 해석 포함 여부이며 눈 아이콘은 표시만 바꿉니다. 그룹 적용이 OFF이면 개별 적용이 ON이어도 해석에서 제외합니다. 정밀해석에서는 Run Options의 Blocker를 켜세요. 기존 Allowed Area는 같은 범위의 사각 차광 형상으로 변환됩니다.</Popover.Content></Popover.Portal></Popover.Root></span>
      <div className="flex gap-1"><Button size="sm" variant="ghost" aria-label={`Block 전체 목록 ${listExpanded ? '접기' : '펼치기'}`} aria-expanded={listExpanded} aria-controls="blocker-group-list" disabled={!blockers.length} onClick={() => setListExpanded(!listExpanded)}>{listExpanded ? <ChevronDown /> : <ChevronRight />}목록 {blockers.length}</Button><Button size="sm" variant="outline" disabled={!blockers.length} onClick={() => setVisible(!visible)}>{visible ? <Eye /> : <EyeOff />}{visible ? 'Hide All' : 'Show All'}</Button></div></div>
    <div className="flex gap-1"><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('box')}>Block 만들기</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => create('ellipse')}>홀 막기</Button>{drawingId ? <Button size="sm" disabled={disabled} onClick={stop}>선택 완료</Button> : null}</div>
    <div id="blocker-group-list" hidden={!listExpanded} className="space-y-2">{blockers.map((group) => <div key={group.id} role="group" aria-label={`${group.label} 목록`} data-selected={selected?.groupId === group.id}
      ref={(node) => { if (node) rows.current.set(group.id, node); else rows.current.delete(group.id) }}
      onClickCapture={() => { if (selected?.groupId !== group.id) select(group.id) }}
      className={cn('space-y-2 rounded border p-2', selected?.groupId === group.id ? 'border-orange-500 bg-orange-500/10' : 'border-border')}>
      <div className="flex items-center gap-1">
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} 선택`} aria-pressed={selected?.groupId === group.id && selected.regionId === null} onClick={() => select(group.id)}><BoxSelect /></Button>
        <input aria-label={`${group.label} name`} className="h-8 min-w-0 flex-1 rounded bg-transparent px-1 text-xs font-semibold focus:bg-muted/50" value={group.label} disabled={disabled} onFocus={() => select(group.id)} onChange={(event) => update(group.id, { label: event.currentTarget.value })} onBlur={() => { if (!group.label.trim()) update(group.id, { label: nextBlockName(blockers) }) }} />
        <label className="flex items-center gap-1 whitespace-nowrap text-xs"><input type="checkbox" aria-label={`${group.label} 적용`} disabled={disabled} checked={group.enabled} onChange={(event) => update(group.id, { enabled: event.currentTarget.checked })} />적용</label>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} ${group.visible !== false ? 'Hide' : 'Show'}`} onClick={() => update(group.id, { visible: group.visible === false })}>{group.visible !== false ? <Eye /> : <EyeOff />}</Button>
        <Button size="icon-xs" variant="ghost" aria-label={`${group.label} Delete`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); remove(group.id) }}><Trash2 /></Button>
      </div>
      <div className="flex flex-wrap items-center gap-1 border-t border-border pt-2">
        <Button size="sm" variant="ghost" className="h-7 gap-1 px-1 text-xs"
          aria-label={`${group.label} ${group.shape === 'ellipse' ? '홀' : 'Block'} 목록 ${collapsedGroups.has(group.id) ? '펼치기' : '접기'}`}
          aria-expanded={!collapsedGroups.has(group.id)} aria-controls={`blocker-regions-${group.id}`}
          onClick={() => setCollapsedGroups((current) => { const next = new Set(current); if (next.has(group.id)) next.delete(group.id); else next.add(group.id); return next })}>
          {collapsedGroups.has(group.id) ? <ChevronRight /> : <ChevronDown />}{group.shape === 'ellipse' ? '홀' : 'Block'} · {group.regions?.length ?? 1}개
        </Button>
        <Button size="sm" variant={drawingId === group.id ? 'default' : 'outline'} aria-label={`${group.label} ${drawingId === group.id ? '추가 완료' : '형상 추가'}`} disabled={disabled} onClick={() => { if (drawingId === group.id) stop(); else { stop(); start(group.id) } }}>{drawingId === group.id ? <Check /> : <Plus />}{drawingId === group.id ? '추가 완료' : '형상 추가'}</Button>
        {group.shape !== 'ellipse' ? <Button size="sm" variant="ghost" aria-label={`${group.label} 그룹 치수`} aria-expanded={editing === group.id} onClick={() => { setEditing(editing === group.id ? null : group.id); setCollapsedGroups((current) => { const next = new Set(current); next.delete(group.id); return next }) }}><Pencil />그룹 치수</Button> : null}
      </div>
      {!group.enabled ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">그룹 해석 적용 OFF</div> : null}
      {drawingId === group.id ? <div className="flex items-center gap-1 text-xs"><BoxSelect className="size-3" />{group.shape === 'ellipse' ? '홀 선택 중' : '영역 선택 중'}</div> : null}
      {!collapsedGroups.has(group.id) ? <div id={`blocker-regions-${group.id}`} className="space-y-1">
        {(group.regions ?? [group]).map((region, index) => <div key={region.id} className="space-y-1"
          ref={(node) => { const key = `${group.id}:${region.id}`; if (node) regionRows.current.set(key,node); else regionRows.current.delete(key) }}>
          <div className="flex items-center gap-1">
          <Button size="sm" variant="outline" className={cn('h-7 min-w-0 flex-1 justify-start text-xs', selected?.groupId === group.id && selected.regionId === (group.regions ? region.id : null) && 'border-orange-500 bg-orange-500/15')}
            aria-label={`${group.label} ${group.shape === 'ellipse' ? '홀' : 'Block'} ${index+1} 선택`} aria-pressed={selected?.groupId === group.id && selected.regionId === (group.regions ? region.id : null)}
            onFocus={() => select(group.id, group.regions ? region.id : null)} onClick={() => select(group.id, group.regions ? region.id : null)}>{group.shape === 'ellipse' ? '홀' : 'Block'} {index+1}</Button>
          {group.regions ? <label className="flex items-center gap-1 text-xs"><input type="checkbox" aria-label={`${group.label} 영역 ${index+1} 적용`} checked={region.enabled} disabled={disabled} onChange={(event) => updateRegion(group.id,region.id,{enabled:event.currentTarget.checked})} />적용</label> : null}
          {group.shape !== 'ellipse' ? <Button size="sm" variant="ghost" className="h-7 px-1 text-xs" aria-label={`${group.label} 영역 ${index+1} 치수 편집`} aria-expanded={editingRegion === `${group.id}:${region.id}`} onClick={() => { select(group.id,group.regions ? region.id : null); setEditingRegion(editingRegion === `${group.id}:${region.id}` ? null : `${group.id}:${region.id}`) }}>치수</Button> : null}
          {group.regions ? <Button size="icon-xs" variant="ghost" aria-label={`${group.label} 영역 ${index+1} ${region.visible !== false ? 'Hide' : 'Show'}`} onClick={() => updateRegion(group.id,region.id,{visible:region.visible === false})}>{region.visible !== false ? <Eye /> : <EyeOff />}</Button> : null}
          <Button size="icon-xs" variant="ghost" aria-label={`${group.label} ${group.shape === 'ellipse' ? '홀' : 'Block'} ${index+1} 삭제`} disabled={disabled} onClick={() => { if (group.regions) removeRegion(group.id, region.id); else { if (drawingId === group.id) stop(); remove(group.id) } }}><Trash2 /></Button>
          </div>
          {editingRegion === `${group.id}:${region.id}` && group.shape !== 'ellipse' ? <div className="rounded border border-border p-2">{fields(region, (patch) => group.regions ? updateRegion(group.id,region.id,patch) : update(group.id,patch), group.regions ? `${group.label} 영역 ${index+1}` : group.label)}</div> : null}
        </div>)}
      </div> : null}
      {editing === group.id && group.shape !== 'ellipse' ? <div hidden={collapsedGroups.has(group.id)} className="space-y-2">
        <BlockerGroupDimensions group={group} disabled={disabled} onApply={(dimensions) => update(group.id,{
          ...dimensions, regions:group.regions?.map((region) => region.shape === 'ellipse' ? region : {...region,...dimensions}),
        })} />
        <label className="flex items-center gap-2 text-xs">형상<select aria-label={`${group.label} 형상`} className="rounded border border-border bg-background p-1" disabled={disabled} value={group.shape ?? 'box'} onChange={(event) => { const shape = event.currentTarget.value as 'box' | 'ellipse'; update(group.id, { shape, regions: group.regions?.map((region) => ({ ...region, shape })) }) }}><option value="box">사각 블록</option><option value="ellipse">홀 마개</option></select></label>
        {group.regions ? <div className="text-xs">새 영역 깊이 (mm)<NumberInput aria-label={`${group.label} 새 영역 깊이`} min={.01} value={group.depthMm} commitOnBlur disabled={disabled} onValueChange={(depthMm) => update(group.id, { depthMm })} /></div> : null}
      </div> : null}
    </div>)}</div>
  </section>
}
