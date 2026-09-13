import { useEffect, useMemo, useState, type RefObject } from 'react'
import type { SceneComponent, ScenePayload } from '@/api'
import { AppDialog, ComponentColorPalette, ViewerFacePickControl } from '@/components/common'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'
import { findBaseMaterial, findSurfaceProperty, surfacePropertiesForCategory } from './material-catalog'
import { CompiledPreview } from './material-editor-dialog'
import { componentCadSurfaces, partMaterialAssignment } from './surface-assignment-model'
import { resolveComponentColorHex } from '@/features/viewer/viewer-display'

export function SurfacePropertyDialog({ open, onOpenChange, component, scene, componentName, returnFocusRef }: {
  open: boolean
  onOpenChange(open: boolean): void
  component: SceneComponent | null
  scene?: ScenePayload
  componentName: string
  returnFocusRef?: RefObject<HTMLElement | null>
}) {
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const selectedFaceIds = useWorkspaceStore(workspaceSelectors.selectedFaceIds)
  const assignments = useWorkspaceStore(workspaceSelectors.materialAssignments)
  const componentColors = useWorkspaceStore(workspaceSelectors.componentColorOverrides)
  const faceColors = useWorkspaceStore(workspaceSelectors.faceColorOverrides)
  const [colorEditorKey, setColorEditorKey] = useState<string | null>(null)
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const pickArmed = useWorkspaceStore(workspaceSelectors.materialFacePickArmed)
  const [surfaceId, setSurfaceId] = useState('')
  const [search, setSearch] = useState('')
  const groups = useMemo(() => scene && component ? componentCadSurfaces(scene, component) : [], [scene, component])
  const colorByFace = useMemo(() => new Map(faceColors.filter((entry) => entry.componentId === component?.component_id)
    .flatMap((entry) => entry.faceIds.map((id) => [id, entry.color] as const))), [faceColors, component?.component_id])
  const componentColor = component ? componentColors[component.component_id] ?? resolveComponentColorHex(component, scene?.components.indexOf(component) ?? 0) : '#64748b'
  const part = component ? partMaterialAssignment(assignments, component.component_id) : undefined
  const base = findBaseMaterial(part?.baseMaterialId ?? 'pc_black')
  const defaultSurfaceId = part?.surfaceId ?? base.defaultSurfaceId
  const selected = new Set(selectedFaceIds)
  const selectedGroups = groups.filter((group) => group.faceIds.some((id) => selected.has(id)))
  const targetFaceIds = selectedGroups.flatMap((group) => group.faceIds)
  const activeScopes = roiScopes.filter((scope) => scope.active)
  const roiFaces = activeScopes.length ? new Set(activeScopes.flatMap((scope) => scope.components.flatMap((entry) => entry.faceIds))) : null
  const visibleFaces = (faceIds: number[]) => faceIds.filter((id) => !roiFaces || roiFaces.has(id))
  const faceOverrides = useMemo(() => {
    const resolved = new Map<number, string>()
    for (const item of assignments) {
      if (!item.enabled || item.componentId !== component?.component_id || item.targetType !== 'faces') continue
      item.faceIds.forEach((id) => resolved.set(id, item.surfaceId))
    }
    return resolved
  }, [assignments, component?.component_id])
  const resolveSurfaceId = (faceId: number) => faceOverrides.get(faceId) ?? defaultSurfaceId
  const selectionKey = selectedGroups.map((group) => group.key).join(',')
  const currentSurfaceIds = new Set(targetFaceIds.map(resolveSurfaceId))
  const initialSurfaceId = currentSurfaceIds.size === 1 ? [...currentSurfaceIds][0] : defaultSurfaceId

  useEffect(() => {
    if (!open) return
    setSurfaceId(initialSurfaceId)
  }, [open, component?.component_id, selectionKey, initialSurfaceId])

  useEffect(() => {
    setColorEditorKey(null)
    if (open) setSearch('')
    return () => actions.setMaterialFacePickArmed(false)
  }, [open, component?.component_id, actions])

  const apply = () => {
    if (!component || !targetFaceIds.length || !surfaceId) return
    actions.upsertMaterialAssignment({
      assignmentId: `material-faces-${component.component_id}-${targetFaceIds.join('-')}`,
      componentId: component.component_id, targetType: 'faces', faceIds: targetFaceIds,
      baseMaterialId: base.id, surfaceId, profileId: '', bsdfAssetId: '', enabled: true,
    })
    actions.setMaterialFacePickArmed(false)
  }
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
  }

  return <AppDialog open={open} onOpenChange={onOpenChange} floating title="Surface Property"
    help="원본 CAD 면별 표면 마감을 지정합니다. ROI 빗금 절단면은 제외됩니다."
    contentClassName="flex flex-col" returnFocusRef={returnFocusRef} onSubmit={apply}
    footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
      <Button variant="outline" disabled={!targetFaceIds.length} onClick={restoreDefault}>Use part default</Button>
      <Button disabled={!targetFaceIds.length || !surfaceId} onClick={apply}>Apply to selected faces</Button></>}>
    <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
      <div className="rounded-lg border border-border bg-muted/20 p-3">
        <div className="font-semibold">{componentName}</div>
        <div className="mt-1 text-xs text-muted-foreground">{base.name} · 기본 {findSurfaceProperty(defaultSurfaceId).name}</div>
      </div>
      <ViewerFacePickControl armed={pickArmed} assigned={selectedGroups.length > 0} kind="surface"
        cadFaceCount={selectedGroups.length} onToggle={() => actions.setMaterialFacePickArmed(!pickArmed)} />
      <input aria-label="Search CAD faces" placeholder="Search faces" value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
        className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm" />
      <div role="group" aria-label="CAD face properties" className="max-h-52 overflow-y-auto rounded-lg border border-border">
        {groups.filter((group) => group.label.toLowerCase().includes(search.toLowerCase())).map((group) => {
          const checked = group.faceIds.some((id) => selected.has(id))
          const available = visibleFaces(group.faceIds)
          const ids = new Set(group.faceIds.map(resolveSurfaceId))
          const overridden = group.faceIds.some((id) => faceOverrides.has(id))
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
            <span className="ml-auto truncate text-xs text-muted-foreground">{ids.size === 1 ? findSurfaceProperty([...ids][0]).name : 'Mixed'}{overridden ? ' · 지정' : ' · 기본'}</span>
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
      <label className="grid min-w-0 grid-cols-1 gap-1 text-sm font-medium"><span>Surface Property</span>
        <select aria-label="Face surface property" className="h-9 w-full rounded-lg border border-input bg-background px-2" value={surfaceId || defaultSurfaceId}
          onChange={(event) => setSurfaceId(event.currentTarget.value)}>
          {surfacePropertiesForCategory(base.category).map((surface) => <option key={surface.id} value={surface.id}>{surface.name}</option>)}
          {!surfacePropertiesForCategory(base.category).some((surface) => surface.id === (surfaceId || defaultSurfaceId))
            ? <option value={surfaceId || defaultSurfaceId}>{findSurfaceProperty(surfaceId || defaultSurfaceId).name}</option> : null}
        </select>
      </label>
      <CompiledPreview compact baseMaterialId={base.id} surfaceId={surfaceId || defaultSurfaceId} />
    </div>
  </AppDialog>
}
