import {
  useEffect,
  useMemo,
  useState,
  type RefObject,
} from 'react'
import type { SceneComponent } from '@/api'
import {
  MousePointerClick,
  Pencil,
  Save,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'

import { AppDialog, HelpTooltip } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type MaterialAssignment,
  type MaterialTargetType,
  type SavedOpticalProfile,
} from '@/stores'

import {
  baseMaterials,
  compileOpticalProfile,
  findBaseMaterial,
  findSurfaceProperty,
  opticalProfilePresets,
  surfacePropertiesForCategory,
} from './material-catalog'

interface MaterialEditorDialogProps {
  open: boolean
  onOpenChange(open: boolean): void
  component: SceneComponent | null
  componentName: string
  returnFocusRef?: RefObject<HTMLElement | null>
}

const selectClassName =
  'h-9 w-full rounded-lg border border-input bg-background px-2.5 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20'

function buildAssignmentId(
  componentId: number,
  targetType: MaterialTargetType,
  faceIds: number[],
): string {
  if (targetType === 'part') return `material-part-${componentId}`
  return `material-faces-${componentId}-${faceIds.join('-')}`
}

interface PartDraft {
  baseMaterialId: string
  surfaceId: string
  profileId: string
}

function draftFromAssignment(assignment: MaterialAssignment | null): PartDraft {
  const baseMaterialId = assignment?.baseMaterialId ?? 'pc_black'
  const base = findBaseMaterial(baseMaterialId)
  return {
    baseMaterialId,
    surfaceId: assignment?.surfaceId ?? base.defaultSurfaceId,
    profileId: assignment?.profileId ?? '',
  }
}

function CompiledPreview({
  baseMaterialId,
  surfaceId,
}: {
  baseMaterialId: string
  surfaceId: string
}) {
  const compiledProfile = compileOpticalProfile(baseMaterialId, surfaceId)
  const selectedBase = findBaseMaterial(baseMaterialId)
  const selectedSurface = findSurfaceProperty(surfaceId)

  return (
    <section className="rounded-xl border border-primary/20 bg-primary/5 p-3">
      <div className="flex items-center gap-2 text-xs font-semibold">
        <Sparkles className="size-3.5 text-primary" />
        Compiled optical preview
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['Reflectance', compiledProfile.reflectance.toFixed(3)],
          ['Loss', compiledProfile.loss.toFixed(3)],
          ['Specular', compiledProfile.specularRatio.toFixed(2)],
          ['Diffuse', compiledProfile.diffuseRatio.toFixed(2)],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-lg border border-border bg-background/45 p-2"
          >
            <div className="text-[0.62rem] text-muted-foreground">
              {label}
            </div>
            <div className="mt-1 font-mono text-sm font-semibold">
              {value}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[0.68rem] leading-4 text-muted-foreground">
        {selectedBase.category} · {selectedSurface.scatterModel} ·
        roughness {compiledProfile.roughness.toFixed(2)} · σ{' '}
        {compiledProfile.scatterSigmaDeg.toFixed(1)}°
      </p>
    </section>
  )
}

function SurfacePropertySelect({
  value,
  category,
  onChange,
}: {
  value: string
  category: string
  onChange(id: string): void
}) {
  return (
    <label className="space-y-1.5 text-xs font-medium">
      <span>Surface property</span>
      <select
        className={selectClassName}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        {surfacePropertiesForCategory(category).map((surface) => (
          <option key={surface.id} value={surface.id}>
            {surface.name}
          </option>
        ))}
      </select>
    </label>
  )
}

interface FaceEditorState {
  /** null while composing a brand-new face group that hasn't been applied yet. */
  assignmentId: string | null
  faceIds: number[]
  surfaceId: string
}

export function MaterialEditorDialog({
  open,
  onOpenChange,
  component,
  componentName,
  returnFocusRef,
}: MaterialEditorDialogProps) {
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const materialFacePickArmed = useWorkspaceStore(
    workspaceSelectors.materialFacePickArmed,
  )
  const assignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const customOpticalProfiles = useWorkspaceStore(
    workspaceSelectors.customOpticalProfiles,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  const [partDraft, setPartDraft] = useState<PartDraft>(() =>
    draftFromAssignment(null),
  )
  const [faceEditor, setFaceEditor] = useState<FaceEditorState | null>(null)
  const [isNamingProfile, setIsNamingProfile] = useState(false)
  const [profileNameDraft, setProfileNameDraft] = useState('')

  const allOpticalProfiles = useMemo(
    () => [...opticalProfilePresets, ...customOpticalProfiles],
    [customOpticalProfiles],
  )
  const isCustomProfileSelected = customOpticalProfiles.some(
    (profile) => profile.id === partDraft.profileId,
  )

  const componentFaceIds = useMemo(
    () => new Set(component?.face_indices ?? []),
    [component],
  )
  const targetFaceIds = useMemo(
    () => selectedFaceIds.filter((faceId) => componentFaceIds.has(faceId)),
    [componentFaceIds, selectedFaceIds],
  )
  const hasActiveRoiScope = useMemo(() => {
    if (!component) return false
    return roiScopes.some(
      (scope) =>
        scope.active &&
        scope.components.some(
          (entry) => entry.componentId === component.component_id,
        ),
    )
  }, [component, roiScopes])

  const partAssignment = useMemo(
    () =>
      component
        ? (assignments.find(
            (assignment) =>
              assignment.componentId === component.component_id &&
              assignment.targetType === 'part',
          ) ?? null)
        : null,
    [assignments, component],
  )
  const faceAssignments = useMemo(
    () =>
      component
        ? assignments.filter(
            (assignment) =>
              assignment.componentId === component.component_id &&
              assignment.targetType === 'faces',
          )
        : [],
    [assignments, component],
  )

  // Reset drafts only when the dialog opens (or the target component
  // changes) - not on every store update, so editing the part material
  // never silently discards an in-progress face pick.
  useEffect(() => {
    if (!open || !component) return
    setPartDraft(draftFromAssignment(partAssignment))
    setFaceEditor(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, component?.component_id])

  // Leaving the dialog (or switching target) must not leave the viewer
  // stuck in "every click toggles a face" mode.
  useEffect(() => {
    if (!open) actions.setMaterialFacePickArmed(false)
  }, [open, actions])
  useEffect(() => {
    actions.setMaterialFacePickArmed(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [component?.component_id])

  const handleApplyPart = () => {
    if (!component) return
    const assignment: MaterialAssignment = {
      assignmentId: buildAssignmentId(component.component_id, 'part', []),
      componentId: component.component_id,
      targetType: 'part',
      faceIds: [],
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: partDraft.surfaceId,
      profileId: partDraft.profileId,
      bsdfAssetId: '',
      enabled: true,
    }
    actions.upsertMaterialAssignment(assignment)
  }

  const handleRemovePart = () => {
    if (!partAssignment) return
    actions.removeMaterialAssignment(partAssignment.assignmentId)
    setPartDraft(draftFromAssignment(null))
  }

  const handleSaveProfile = () => {
    const name = profileNameDraft.trim()
    if (!name) return
    const profile: SavedOpticalProfile = {
      id: `custom-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      name,
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: partDraft.surfaceId,
      bsdfAssetId: '',
    }
    actions.addCustomOpticalProfile(profile)
    setPartDraft({ ...partDraft, profileId: profile.id })
    setIsNamingProfile(false)
    setProfileNameDraft('')
  }

  const handleRemoveCustomProfile = () => {
    if (!isCustomProfileSelected) return
    actions.removeCustomOpticalProfile(partDraft.profileId)
    setPartDraft({ ...partDraft, profileId: '' })
  }

  const startDesignate = () => {
    if (!component) return
    setFaceEditor(null)
    actions.setSelectedComponentIds([component.component_id])
    actions.setSelectedFaceIds([])
    actions.setMaterialFacePickArmed(true)
  }

  const cancelDesignate = () => {
    actions.setMaterialFacePickArmed(false)
    actions.setSelectedFaceIds([])
  }

  const finishDesignate = () => {
    if (!component || targetFaceIds.length === 0) return
    actions.setMaterialFacePickArmed(false)
    const existingId = buildAssignmentId(
      component.component_id,
      'faces',
      targetFaceIds,
    )
    const existing = assignments.find(
      (assignment) => assignment.assignmentId === existingId,
    )
    setFaceEditor({
      assignmentId: existing?.assignmentId ?? null,
      faceIds: targetFaceIds,
      surfaceId: existing?.surfaceId ?? partDraft.surfaceId,
    })
  }

  const startEditFaceAssignment = (assignment: MaterialAssignment) => {
    actions.setMaterialFacePickArmed(false)
    setFaceEditor({
      assignmentId: assignment.assignmentId,
      faceIds: assignment.faceIds,
      surfaceId: assignment.surfaceId,
    })
  }

  const handleApplyFaceEditor = () => {
    if (!component || !faceEditor || faceEditor.faceIds.length === 0) return
    const assignment: MaterialAssignment = {
      assignmentId: buildAssignmentId(
        component.component_id,
        'faces',
        faceEditor.faceIds,
      ),
      componentId: component.component_id,
      targetType: 'faces',
      faceIds: faceEditor.faceIds,
      // Base material always follows the part - only the surface finish can
      // differ per face group.
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: faceEditor.surfaceId,
      profileId: '',
      bsdfAssetId: '',
      enabled: true,
    }
    actions.upsertMaterialAssignment(assignment)
    setFaceEditor(null)
    actions.setSelectedFaceIds([])
  }

  const handleRemoveFaceAssignment = (assignmentId: string) => {
    actions.removeMaterialAssignment(assignmentId)
    if (faceEditor?.assignmentId === assignmentId) setFaceEditor(null)
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title="Material assignment"
      description={
        component
          ? `${componentName}의 Base material · Surface property를 지정하고, 필요하면 특정 Face만 다른 Surface property로 바꿉니다.`
          : 'Material 대상을 선택하세요.'
      }
      size="lg"
      returnFocusRef={returnFocusRef}
      onSubmit={faceEditor ? handleApplyFaceEditor : handleApplyPart}
      footer={
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Close
        </Button>
      }
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <section className="rounded-xl border border-border bg-background/45 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[0.65rem] tracking-wide text-muted-foreground uppercase">
                Target
              </div>
              <div className="mt-1 text-sm font-semibold">
                {componentName || 'No component'}
              </div>
            </div>
            {hasActiveRoiScope ? <Badge variant="outline">ROI</Badge> : null}
          </div>
        </section>

        {/* 1 & 2: Base material and its default Surface property - one
            always-applicable pair for the whole part. */}
        <section className="space-y-3">
          <div className="flex items-center gap-1.5">
            <div className="text-xs font-semibold">Part material</div>
            <HelpTooltip label="Part material 도움말">
              부품 전체에 적용되는 기본 재질입니다. Base material(소재)과
              Surface property(광택·거칠기)를 조합해 반사율과 산란 특성을
              계산합니다. 아래 Face 지정 Surface property가 없는 모든 면에
              이 값이 적용됩니다.
            </HelpTooltip>
          </div>
          <div className="space-y-1.5">
            <label className="block space-y-1.5 text-xs font-medium">
              <span>Saved optical profile</span>
              <div className="flex gap-1.5">
                <select
                  className={selectClassName}
                  value={partDraft.profileId}
                  onChange={(event) => {
                    const nextProfileId = event.currentTarget.value
                    const profile = allOpticalProfiles.find(
                      (item) => item.id === nextProfileId,
                    )
                    if (!profile) {
                      setPartDraft({ ...partDraft, profileId: nextProfileId })
                      return
                    }
                    setPartDraft({
                      profileId: nextProfileId,
                      baseMaterialId: profile.baseMaterialId,
                      surfaceId: profile.surfaceId,
                    })
                  }}
                >
                  <option value="">None · use current draft</option>
                  <optgroup label="Built-in">
                    {opticalProfilePresets.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name}
                      </option>
                    ))}
                  </optgroup>
                  {customOpticalProfiles.length > 0 ? (
                    <optgroup label="My profiles">
                      {customOpticalProfiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                </select>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label="Save current draft as a new profile"
                  onClick={() => {
                    setProfileNameDraft('')
                    setIsNamingProfile(true)
                  }}
                >
                  <Save />
                </Button>
                {isCustomProfileSelected ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label="Remove saved profile"
                    onClick={handleRemoveCustomProfile}
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </div>
            </label>

            {isNamingProfile ? (
              <div className="flex items-center gap-1.5">
                <input
                  type="text"
                  autoFocus
                  className={selectClassName}
                  placeholder="새 프로필 이름"
                  value={profileNameDraft}
                  onChange={(event) =>
                    setProfileNameDraft(event.currentTarget.value)
                  }
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      handleSaveProfile()
                    } else if (event.key === 'Escape') {
                      setIsNamingProfile(false)
                    }
                  }}
                />
                <Button
                  type="button"
                  size="icon-sm"
                  disabled={!profileNameDraft.trim()}
                  aria-label="Confirm save"
                  onClick={handleSaveProfile}
                >
                  <Save />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Cancel save"
                  onClick={() => setIsNamingProfile(false)}
                >
                  <X />
                </Button>
              </div>
            ) : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-xs font-medium">
              <span>Base material</span>
              <select
                className={selectClassName}
                value={partDraft.baseMaterialId}
                onChange={(event) => {
                  const nextBase = findBaseMaterial(event.currentTarget.value)
                  setPartDraft({
                    baseMaterialId: nextBase.id,
                    surfaceId: nextBase.defaultSurfaceId,
                    profileId: '',
                  })
                }}
              >
                {baseMaterials.map((material) => (
                  <option key={material.id} value={material.id}>
                    {material.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1.5 text-xs font-medium">
              <span>Surface property</span>
              <select
                className={selectClassName}
                value={partDraft.surfaceId}
                onChange={(event) =>
                  setPartDraft({
                    ...partDraft,
                    surfaceId: event.currentTarget.value,
                    profileId: '',
                  })
                }
              >
                {surfacePropertiesForCategory(
                  findBaseMaterial(partDraft.baseMaterialId).category,
                ).map((surface) => (
                  <option key={surface.id} value={surface.id}>
                    {surface.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <CompiledPreview
            baseMaterialId={partDraft.baseMaterialId}
            surfaceId={partDraft.surfaceId}
          />

          <div className="flex items-center justify-end gap-2">
            {partAssignment ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleRemovePart}
              >
                Remove
              </Button>
            ) : null}
            <Button size="sm" disabled={!component} onClick={handleApplyPart}>
              Apply to part
            </Button>
          </div>
        </section>

        {/* 3: optional per-face Surface property override - base material
            is never independently chosen here, it always follows the part. */}
        <section className="rounded-xl border border-border bg-background/45 p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <div className="text-xs font-semibold">
                Face 지정 Surface property
              </div>
              <HelpTooltip label="Face 지정 Surface property 도움말">
                기본값은 위 부품 Surface property를 따라갑니다. 특정 Face만
                다른 마감으로 바꾸고 싶을 때만 지정하세요. Base material은
                항상 부품과 동일합니다.
              </HelpTooltip>
            </div>
            <Badge variant="outline">{faceAssignments.length}</Badge>
          </div>

          {faceAssignments.length > 0 ? (
            <div className="space-y-1.5">
              {faceAssignments.map((assignment) => {
                const surface = findSurfaceProperty(assignment.surfaceId)
                const isEditing =
                  faceEditor?.assignmentId === assignment.assignmentId
                return (
                  <div
                    key={assignment.assignmentId}
                    className={`flex items-center justify-between gap-2 rounded-lg border p-2 ${
                      isEditing
                        ? 'border-primary/40 bg-primary/5'
                        : 'border-border bg-background/40'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-semibold">
                        {surface.name}
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Edit face surface property for ${assignment.faceIds.length} faces`}
                        onClick={() => startEditFaceAssignment(assignment)}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Remove face surface property for ${assignment.faceIds.length} faces`}
                        onClick={() =>
                          handleRemoveFaceAssignment(assignment.assignmentId)
                        }
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : null}

          {materialFacePickArmed ? (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-primary/30 bg-primary/5 p-2.5">
              <span className="text-[0.68rem] leading-4 text-muted-foreground">
                Viewer에서 면을 클릭해 선택하세요 (다시 클릭하면 해제)
                {targetFaceIds.length > 0 ? (
                  <span className="ml-1 font-semibold text-foreground">
                    · 선택됨
                  </span>
                ) : null}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                <Button variant="ghost" size="sm" onClick={cancelDesignate}>
                  취소
                </Button>
                <Button
                  size="sm"
                  disabled={targetFaceIds.length === 0}
                  onClick={finishDesignate}
                >
                  선택 완료
                </Button>
              </div>
            </div>
          ) : !faceEditor ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!component}
              onClick={startDesignate}
            >
              <MousePointerClick />
              지정
            </Button>
          ) : null}

          {faceEditor ? (
            <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold">
                  Face 지정 Surface property 편집
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setFaceEditor(null)}
                >
                  취소
                </Button>
              </div>
              <SurfacePropertySelect
                value={faceEditor.surfaceId}
                category={findBaseMaterial(partDraft.baseMaterialId).category}
                onChange={(surfaceId) =>
                  setFaceEditor((current) =>
                    current ? { ...current, surfaceId } : current,
                  )
                }
              />
              <CompiledPreview
                baseMaterialId={partDraft.baseMaterialId}
                surfaceId={faceEditor.surfaceId}
              />
              <div className="flex items-center justify-end gap-2">
                {faceEditor.assignmentId ? (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() =>
                      handleRemoveFaceAssignment(faceEditor.assignmentId!)
                    }
                  >
                    Remove
                  </Button>
                ) : null}
                <Button size="sm" onClick={handleApplyFaceEditor}>
                  Apply
                </Button>
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </AppDialog>
  )
}
