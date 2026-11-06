import { useEffect, useState, type ReactNode } from 'react'
import { useRayTraceJobQuery, type ScenePayload } from '@/api'
import { CollapsibleSection } from '@/components/common/collapsible-section'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { BlockerEditor } from '@/features/leak-preview/blocker-editor'
import { leakPreviewStore, useLeakPreviewStore } from '@/features/leak-preview/leak-preview-store'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'

export function CaseGeometryPanel({ scene, disabled, children }: { scene?: ScenePayload; disabled: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const cases = useWorkspaceStore(workspaceSelectors.cadCases)
  const activeId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const active = cases.find(item => item.caseId === activeId)
  const jobId = useLeakPreviewStore(state => state.jobId)
  const boundIdentity = useLeakPreviewStore(state => state.sceneIdentity)
  const caseReady = !boundIdentity || !activeId || boundIdentity.startsWith(JSON.stringify([activeId]).slice(0,-1)+',')
  const { data: job } = useRayTraceJobQuery(jobId)
  const busy = Boolean(jobId && (!job || job.status === 'queued' || job.status === 'running'))
  const locked = disabled || busy || !caseReady
  useEffect(() => {
    if (locked && caseReady && leakPreviewStore.getState().blockerAreaSelectionId) {
      leakPreviewStore.getState().finishBlockerAreaSelection()
      actions.setRoiBoxSelectionArmed(false)
    }
  }, [locked, caseReady, actions])
  useEffect(() => {
    if (!open || !scene || locked) return
    const state = leakPreviewStore.getState()
    // Case restoration belongs to SimulatorShell. Never bind an old Case's
    // live Preview state to a newly selected Case before that restore occurs.
    if (state.sceneIdentity && activeId && !state.sceneIdentity.startsWith(JSON.stringify([activeId]).slice(0,-1)+',')) return
    if (state.sceneToken !== scene.metadata.scene_token && state.blockerAreaSelectionId) {
      state.finishBlockerAreaSelection()
      actions.setRoiBoxSelectionArmed(false)
    }
    state.ensureScene(scene.metadata.scene_token)
    leakPreviewStore.getState().migrateAllowedAreas()
  }, [scene, locked, actions, open, activeId, boundIdentity])
  const link = (section: 'accessoryCads' | 'blockers', label: string) => {
    const sourceId = active?.setupLinks?.[section]
    const source = cases.find(item => item.caseId === sourceId)
    return sourceId ? <div className="flex items-center justify-between gap-1 text-xs"><span>{label} · {source?.name || source?.cad.displayName || sourceId} 연결됨</span><Button size="sm" variant="outline" disabled={locked} onClick={() => { if (activeId) actions.unlinkCaseSetup(activeId, section) }}>{label} 연동 해제</Button></div> : null
  }
  return <CollapsibleSection title="공통 보완 형상" defaultOpen={false} onOpenChange={setOpen}>
    <div className="space-y-2">
      <div className="flex items-center gap-1 text-xs">CASE {String(active?.order ?? '').padStart(2,'0')}<HelpTooltip label="공통 보완 형상 도움말">현재 CASE의 ACC CAD와 Block을 관리합니다. 상단 Copy Setup에서 ACC CAD·Block과 대상 CASE들을 선택하면 복사하고 원본 변경에 연동합니다. CASE의 원점과 배치가 같아야 합니다. 사양이 달라진 대상은 해당 항목만 연동 해제한 뒤 수정하세요. 대상에서 직접 수정해도 해당 항목은 독립 설정으로 바뀝니다. ACC 형상이 바뀌면 기존 파일을 제거하고 새 파일을 추가합니다. ACC 목록의 체크는 장면 포함 여부이며 일반 부품 Hide와 다릅니다. Block 표시와 해석 적용은 별개이며 정밀해석에서는 Run Options의 Blocker도 켜야 합니다. 이 영역을 접거나 작업 탭을 옮기면 Block 그리기가 종료되고 만든 형상은 유지됩니다.</HelpTooltip></div>
      {link('accessoryCads', 'ACC')}
      <fieldset disabled={locked} className="min-w-0">{children}</fieldset>
      <div className="border-t border-border pt-2">
        {link('blockers', 'Block')}
        {open && scene && !disabled && caseReady ? <BlockerEditor scene={scene} disabled={locked} onStart={() => {
          leakPreviewStore.getState().finishIgnoreAreaSelection()
          actions.setEmitterFaceSelectionArmed(false)
          actions.setSelectedFaceIds([])
        }} /> : <div role="status" className="text-xs text-muted-foreground">{!caseReady ? 'CASE 설정 복원 중' : disabled ? '형상 준비 또는 해석 중 · Block 편집 대기' : 'CAD 로딩 후 Block 편집 가능'}</div>}
      </div>
    </div>
  </CollapsibleSection>
}
