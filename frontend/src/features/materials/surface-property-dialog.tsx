import { useEffect, useMemo, useState, type RefObject } from 'react'
import type { SceneComponent, ScenePayload } from '@/api'
import { AppDialog, ComponentColorPalette, ViewerFacePickControl } from '@/components/common'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors, type OpticalValueOverride } from '@/stores'
import { findBaseMaterial, findSurfaceProperty, surfacePropertiesForCategory } from './material-catalog'
import { OpticalProfilePreview, OpticalValueEditor } from './optical-profile-editor'
import { faceMaterialAssignment, isCustomOpticalValid, resolveAssignmentProfile, resolveOpticalProfile, unassignedOpticalProfile, withSurfaceFinish } from './optical-profile'
import { componentCadSurfaces, partMaterialAssignment } from './surface-assignment-model'
import { resolveComponentColorHex } from '@/features/viewer/viewer-display'
import { BsdfAssetPanel } from './bsdf-asset-panel'
import { bsdfAssetName } from './bsdf-asset'

type SettingsMode = 'inherit' | 'preset' | 'custom'
type EditTarget = 'faces' | 'part'
interface SurfaceDraft {
  mode: SettingsMode
  surfaceId: string
  custom: OpticalValueOverride
  bsdfAssetId: string
}

export function SurfacePropertyDialog({ open, onOpenChange, component, scene, componentName, returnFocusRef, initialTarget = 'faces' }: {
  open: boolean
  onOpenChange(open: boolean): void
  component: SceneComponent | null
  scene?: ScenePayload
  componentName: string
  returnFocusRef?: RefObject<HTMLElement | null>
  initialTarget?: EditTarget
}) {
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const selectedFaceIds = useWorkspaceStore(workspaceSelectors.selectedFaceIds)
  const assignments = useWorkspaceStore(workspaceSelectors.materialAssignments)
  const bsdfAssets = useWorkspaceStore(workspaceSelectors.bsdfAssets)
  const componentColors = useWorkspaceStore(workspaceSelectors.componentColorOverrides)
  const faceColors = useWorkspaceStore(workspaceSelectors.faceColorOverrides)
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const angleDependent = useWorkspaceStore((state) => state.rayTraceConfig.angle_dependent_reflectance !== false)
  const pickArmed = useWorkspaceStore(workspaceSelectors.materialFacePickArmed)
  const [target, setTarget] = useState<EditTarget>(initialTarget)
  const [dirty, setDirty] = useState(false)
  const [search, setSearch] = useState('')
  const [colorEditorKey, setColorEditorKey] = useState<string | null>(null)
  const groups = useMemo(() => scene && component ? componentCadSurfaces(scene, component) : [], [scene, component])
  const colorByFace = useMemo(() => new Map(faceColors.filter((entry) => entry.componentId === component?.component_id)
    .flatMap((entry) => entry.faceIds.map((id) => [id, entry.color] as const))), [faceColors, component?.component_id])
  const componentColor = component ? componentColors[component.component_id] ?? resolveComponentColorHex(component, scene?.components.indexOf(component) ?? 0) : '#64748b'
  const part = component ? partMaterialAssignment(assignments, component.component_id) : undefined
  const base = findBaseMaterial(part?.baseMaterialId ?? 'pc_black')
  const defaultSurfaceId = part?.surfaceId ?? base.defaultSurfaceId
  const partProfile = part ? resolveAssignmentProfile(part, assignments) : unassignedOpticalProfile
  const selected = new Set(selectedFaceIds)
  const selectedGroups = groups.filter((group) => group.faceIds.some((id) => selected.has(id)))
  const targetFaceIds = selectedGroups.flatMap((group) => group.faceIds)
  const activeScopes = roiScopes.filter((scope) => scope.active)
  const roiFaces = activeScopes.length ? new Set(activeScopes.flatMap((scope) => scope.components.flatMap((entry) => entry.faceIds))) : null
  const visibleFaces = (faceIds: number[]) => faceIds.filter((id) => !roiFaces || roiFaces.has(id))
  const faceAssignment = (faceId: number) => component ? faceMaterialAssignment(assignments, component.component_id, faceId) : undefined
  const initialAssignment = target === 'part' ? part : faceAssignment(targetFaceIds[0]) ?? part
  const initialProfile = initialAssignment ? resolveAssignmentProfile(initialAssignment, assignments) : unassignedOpticalProfile
  const sourceSignatures = new Set(targetFaceIds.map((faceId) => {
    const assignment = faceAssignment(faceId) ?? part
    return JSON.stringify(assignment ? [assignment.targetType, assignment.surfaceId, assignment.bsdfAssetId, resolveAssignmentProfile(assignment, assignments)] : null)
  }))
  const mixed = target === 'faces' && sourceSignatures.size > 1
  const initialDraft: SurfaceDraft = {
    mode: target === 'faces' && !faceAssignment(targetFaceIds[0]) ? 'inherit' : initialAssignment?.opticalOverride ? 'custom' : 'preset',
    surfaceId: initialAssignment?.surfaceId ?? defaultSurfaceId,
    custom: initialAssignment ? initialProfile : resolveOpticalProfile(base.id, defaultSurfaceId),
    bsdfAssetId: initialAssignment?.bsdfAssetId ?? '',
  }
  const initialKey = JSON.stringify(initialDraft)
  const selectionKey = targetFaceIds.join(',')
  const [draft, setDraft] = useState<SurfaceDraft>(initialDraft)
  useEffect(() => {
    if (!open) return
    setDraft(JSON.parse(initialKey) as SurfaceDraft)
    setDirty(false)
  }, [open, component?.component_id, activeCadCaseId, target, selectionKey, initialKey])
  useEffect(() => {
    if (open) { setTarget(initialTarget); setSearch(''); setColorEditorKey(null) }
    return () => actions.setMaterialFacePickArmed(false)
  }, [open, component?.component_id, initialTarget, actions])

  const preview = draft.mode === 'inherit' ? partProfile
    : resolveOpticalProfile(base.id, draft.surfaceId, draft.mode === 'custom' ? draft.custom : undefined)
  const customValid = draft.mode !== 'custom' || isCustomOpticalValid(draft.custom)
  const canApply = Boolean(component && (target === 'part' || targetFaceIds.length) && customValid && (!mixed || dirty))
  const edit = (value: SurfaceDraft) => { setDraft(value); setDirty(true) }
  const restoreDefault = () => {
    const faces = new Set(targetFaceIds)
    for (const item of assignments) {
      if (item.componentId !== component?.component_id || item.targetType !== 'faces') continue
      const remaining = item.faceIds.filter((id) => !faces.has(id))
      if (remaining.length === item.faceIds.length) continue
      if (remaining.length) actions.upsertMaterialAssignment({ ...item, faceIds: remaining })
      else actions.removeMaterialAssignment(item.assignmentId)
    }
    actions.setMaterialFacePickArmed(false)
    edit({ mode: 'inherit', surfaceId: defaultSurfaceId, custom: partProfile, bsdfAssetId: part?.bsdfAssetId ?? '' })
  }
  const apply = () => {
    if (!component || !canApply) return
    if (target === 'faces' && draft.mode === 'inherit') { restoreDefault(); return }
    actions.upsertMaterialAssignment({
      assignmentId: target === 'part' ? part?.assignmentId ?? `material-part-${component.component_id}` : `material-faces-${component.component_id}-${targetFaceIds.join('-')}`,
      componentId: component.component_id, targetType: target, faceIds: target === 'faces' ? targetFaceIds : [],
      baseMaterialId: base.id, surfaceId: draft.surfaceId, profileId: '', bsdfAssetId: draft.bsdfAssetId,
      opticalOverride: draft.mode === 'custom' ? { ...preview } : undefined, enabled: true,
    })
    actions.setMaterialFacePickArmed(false)
    setDirty(false)
  }
  const changeFinish = (surfaceId: string) => {
    const keepCustom = draft.mode === 'custom' || (draft.mode === 'inherit' && Boolean(part?.opticalOverride))
    edit({ ...draft, surfaceId, mode: keepCustom ? 'custom' : 'preset',
      custom: keepCustom ? withSurfaceFinish(preview, base.id, surfaceId) : resolveOpticalProfile(base.id, surfaceId) })
  }

  return <AppDialog open={open} onOpenChange={onOpenChange} floating title="Surface Property"
    help="부품 기본값 또는 원본 CAD 면의 광학 값을 지정합니다. ROI 빗금 절단면은 제외됩니다."
    contentClassName="flex flex-col" returnFocusRef={returnFocusRef}
    footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
      {target === 'faces' ? <Button variant="outline" disabled={!targetFaceIds.length} onClick={restoreDefault}>Use part default</Button> : null}
      <Button disabled={!canApply} onClick={apply}>{target === 'part' ? 'Apply to part default' : 'Apply to selected faces'}</Button></>}>
    <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
      <div className="rounded-lg border border-border bg-muted/20 p-3">
        <div className="font-semibold">{componentName}</div>
        <div className="mt-1 text-xs text-muted-foreground">{part ? `${base.name} · ${part.opticalOverride ? 'Custom' : findSurfaceProperty(defaultSurfaceId).name}` : '부품 광학 값 미지정 · 반사 없음'}</div>
      </div>
      <label className="grid gap-1 text-xs font-medium"><span>적용 대상</span>
        <select aria-label="Surface edit target" className="h-9 rounded-lg border border-input bg-background px-2" value={target}
          onChange={(event) => { setTarget(event.currentTarget.value as EditTarget); actions.setMaterialFacePickArmed(false) }}>
          <option value="faces">선택 CAD 면</option><option value="part">부품 기본값 · 면별 지정은 유지</option>
        </select>
      </label>
      {target === 'faces' ? <>
        <ViewerFacePickControl armed={pickArmed} assigned={selectedGroups.length > 0} kind="surface"
          cadFaceCount={selectedGroups.length} onToggle={() => actions.setMaterialFacePickArmed(!pickArmed)} />
        <input aria-label="Search CAD faces" placeholder="Search faces" value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm" />
        <div role="group" aria-label="CAD face properties" className="max-h-40 overflow-y-auto rounded-lg border border-border">
          {groups.filter((group) => group.label.toLowerCase().includes(search.toLowerCase())).map((group) => {
            const checked = group.faceIds.some((id) => selected.has(id))
            const available = visibleFaces(group.faceIds)
            const labels = new Set(group.faceIds.map((id) => {
              const assignment = faceAssignment(id) ?? part
              const asset = bsdfAssets.find((item) => item.id === assignment?.bsdfAssetId)
              return assignment ? `${assignment.bsdfAssetId ? `BSDF: ${asset ? bsdfAssetName(asset) : '누락'}` : assignment.opticalOverride ? 'Custom' : findSurfaceProperty(assignment.surfaceId).name} · ${assignment.targetType === 'faces' ? '면 지정' : '부품 상속'}` : '미지정'
            }))
            const colors = new Set(group.faceIds.map((id) => colorByFace.get(id)))
            const customColor = colors.size === 1 ? [...colors][0] : undefined
            return <div key={group.key} className={`border-b border-border/60 px-3 py-2 text-sm last:border-0 ${!available.length ? 'opacity-40' : 'hover:bg-muted/40'}`}>
              <div className="flex items-center gap-2">
                <label className="flex min-w-0 flex-1 items-center gap-2">
                  <input type="checkbox" aria-label={`Select ${group.label}`} checked={checked} disabled={!available.length}
                    onChange={() => {
                      const next = new Set(selectedFaceIds.filter((id) => component?.face_indices.includes(id)))
                      group.faceIds.forEach((id) => next.delete(id))
                      if (!checked) available.forEach((id) => next.add(id))
                      actions.setFaceSelection([...next], next.size && component ? [component.component_id] : [])
                    }} />
                  <span className="font-medium">{group.label}</span>
                  <span className="ml-auto truncate text-xs text-muted-foreground">{labels.size === 1 ? [...labels][0] : '여러 설정'}</span>
                </label>
                <button type="button" aria-label={`${group.label} 표시색`} aria-expanded={colorEditorKey === group.key}
                  title={`Display color · ${colors.size > 1 ? 'Mixed' : customColor ? '면 지정' : '부품 기본색'}`}
                  disabled={!available.length} onClick={() => setColorEditorKey(colorEditorKey === group.key ? null : group.key)}
                  className={`size-6 shrink-0 rounded-full border-2 shadow-sm ${customColor ? 'border-primary' : 'border-border'}`}
                  style={{ background: colors.size > 1 ? 'conic-gradient(#64748b 50%, #f8fafc 50%)' : customColor ?? componentColor }} />
              </div>
              {colorEditorKey === group.key && component ? <div className="flex justify-end pt-2">
                <ComponentColorPalette componentName={group.label} value={customColor} fallbackColor={componentColor} resetLabel="부품 기본색"
                  onValueChange={(color) => actions.setFaceColor(component.component_id, group.faceIds, color)} />
              </div> : null}
            </div>
          })}
        </div>
        <div className="flex justify-between text-xs text-muted-foreground"><span>선택 {selectedGroups.length} / {groups.length} CAD 면</span><span>ROI 밖 면은 비활성</span></div>
      </> : null}
      {mixed && !dirty ? <p role="status" className="text-xs text-amber-600">서로 다른 값이 선택되었습니다. 적용할 설정을 선택하세요.</p> : null}
      <label className="grid gap-1 text-xs font-medium"><span>광학 값 설정</span>
        <select aria-label="Optical settings mode" className="h-9 rounded-lg border border-input bg-background px-2" value={draft.mode}
          onChange={(event) => {
            const mode = event.currentTarget.value as SettingsMode
            edit({ ...draft, mode, bsdfAssetId: mode === 'inherit' ? part?.bsdfAssetId ?? '' : draft.bsdfAssetId,
              custom: mode === 'custom' ? { ...(draft.mode === 'inherit' && !part ? resolveOpticalProfile(base.id, draft.surfaceId) : preview) } : draft.custom })
          }}>
          {target === 'faces' ? <option value="inherit">부품 기본값 사용</option> : null}
          <option value="preset">Preset · 카탈로그 값으로 설정</option><option value="custom">Custom · 독립 값 직접 입력</option>
        </select>
      </label>
      <label className="grid min-w-0 grid-cols-1 gap-1 text-xs font-medium"><span>Surface Property · 마감 프리셋</span>
        <select aria-label="Face surface property" className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm" value={draft.surfaceId}
          onChange={(event) => changeFinish(event.currentTarget.value)}>
          {surfacePropertiesForCategory(base.category).map((surface) => <option key={surface.id} value={surface.id}>{surface.name}</option>)}
          {!surfacePropertiesForCategory(base.category).some((surface) => surface.id === draft.surfaceId)
            ? <option value={draft.surfaceId}>{findSurfaceProperty(draft.surfaceId).name}</option> : null}
        </select>
      </label>
      {draft.mode === 'custom' ? <>
        <span className="text-xs text-muted-foreground">독립 값 · 마감 변경 시 반사율 유지</span>
        <OpticalValueEditor value={draft.custom} onChange={(custom) => edit({ ...draft, custom })} />
      </> : null}
      {!customValid ? <p role="alert" className="text-xs text-destructive">비율은 0–1, σ는 0보다 큰 값이어야 합니다. None의 반사율은 0입니다.</p> : null}
      <BsdfAssetPanel value={draft.mode === 'inherit' ? part?.bsdfAssetId ?? '' : draft.bsdfAssetId}
        onChange={(bsdfAssetId) => edit({ ...draft, bsdfAssetId, mode: draft.mode === 'inherit' ? 'custom' : draft.mode,
          custom: draft.mode === 'inherit' ? { ...preview } : draft.custom })} />
      {!(mixed && !dirty) && (target === 'part' || targetFaceIds.length > 0) ? <OpticalProfilePreview profile={preview}
        label={(draft.mode === 'inherit' ? part?.bsdfAssetId : draft.bsdfAssetId) ? '보관된 기존 광학 값 · BSDF 연결 해제 시 사용' : !dirty && !(target === 'part' && !part) ? '현재 적용 광학 값' : '적용 예정 광학 값'} /> : null}
      <div className="text-xs text-muted-foreground">입사각 반사율 보정: {angleDependent ? 'ON · 실제 반사율은 각도에 따라 달라짐' : 'OFF · 입력 반사율 고정'}</div>
    </div>
  </AppDialog>
}
