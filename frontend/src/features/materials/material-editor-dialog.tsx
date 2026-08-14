import {
  useEffect,
  useId,
  useMemo,
  useState,
  type RefObject,
} from 'react'
import type { SceneComponent, ScenePayload } from '@/api'
import {
  Pencil,
  Save,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'

import {
  AppDialog,
  HelpTooltip,
  ViewerFacePickControl,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type MaterialAssignment,
  type MaterialTargetType,
  type OpticalValueOverride,
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
  scene?: ScenePayload
  componentName: string
  returnFocusRef?: RefObject<HTMLElement | null>
}

const selectClassName =
  'h-9 w-full rounded-lg border border-input bg-background px-2.5 text-base outline-none focus:border-primary focus:ring-2 focus:ring-primary/20'

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
  opticalOverride?: OpticalValueOverride
}

function draftFromAssignment(assignment: MaterialAssignment | null): PartDraft {
  const baseMaterialId = assignment?.baseMaterialId ?? 'pc_black'
  const base = findBaseMaterial(baseMaterialId)
  return {
    baseMaterialId,
    surfaceId: assignment?.surfaceId ?? base.defaultSurfaceId,
    profileId: assignment?.profileId ?? '',
    opticalOverride: assignment?.opticalOverride,
  }
}

function catalogValues(baseMaterialId: string, surfaceId: string): OpticalValueOverride {
  const value = compileOpticalProfile(baseMaterialId, surfaceId)
  return {
    reflectance: value.reflectance,
    loss: value.loss,
    specularRatio: value.specularRatio,
    diffuseRatio: value.diffuseRatio,
  }
}

function OpticalValueEditor({
  value,
  onChange,
}: {
  value: OpticalValueOverride
  onChange(value: OpticalValueOverride): void
}) {
  const fields: Array<[keyof OpticalValueOverride, string]> = [
    ['reflectance', 'Reflectance'],
    ['loss', 'Loss'],
    ['specularRatio', 'Specular'],
    ['diffuseRatio', 'Diffuse'],
  ]
  const energyValid = Math.abs(value.reflectance + value.loss - 1) <= 0.001
  const scatterValid = Math.abs(value.specularRatio + value.diffuseRatio - 1) <= 0.001

  return (
    <div className="space-y-2 rounded-lg border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/25">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map(([key, label]) => (
          <label
            key={key}
            className="grid min-w-0 grid-cols-1 gap-1 text-sm font-medium"
          >
            <span>{label}</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.01"
              aria-label={`Custom ${label}`}
              className={`${selectClassName} block min-w-0 max-w-full`}
              value={value[key]}
              onChange={(event) =>
                onChange({
                  ...value,
                  [key]: Math.min(1, Math.max(0, Number(event.currentTarget.value))),
                })
              }
            />
          </label>
        ))}
      </div>
      <p className={`text-xs ${energyValid && scatterValid ? 'text-emerald-700 dark:text-emerald-400' : 'text-destructive'}`}>
        Reflectance + Loss = { (value.reflectance + value.loss).toFixed(3) } · Specular + Diffuse = { (value.specularRatio + value.diffuseRatio).toFixed(3) }
      </p>
    </div>
  )
}

function CompiledPreview({
  baseMaterialId,
  surfaceId,
  opticalOverride,
}: {
  baseMaterialId: string
  surfaceId: string
  opticalOverride?: OpticalValueOverride
}) {
  const catalogProfile = compileOpticalProfile(baseMaterialId, surfaceId)
  const compiledProfile = opticalOverride
    ? {
        ...catalogProfile,
        ...opticalOverride,
      }
    : catalogProfile
  const selectedBase = findBaseMaterial(baseMaterialId)
  const selectedSurface = findSurfaceProperty(surfaceId)

  return (
    <section className="rounded-xl border border-primary/20 bg-primary/5 p-3">
      <div className="flex items-center gap-2 text-sm font-semibold">
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
            <div className="text-sm text-muted-foreground">
              {label}
            </div>
            <div className="mt-1 font-mono text-base font-semibold">
              {value}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs leading-4 text-muted-foreground">
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
  const fieldId = useId()
  return (
    <div className="space-y-1.5 text-sm font-medium">
      <div className="flex items-center gap-1.5">
        <label htmlFor={fieldId}>Surface Property</label>
        <HelpTooltip label="Surface Property 도움말">
          표면 마감(광택도)입니다. Base Material의 반사율을 얼마나 정반사
          방향으로 집중시킬지(Gloss) vs 사방으로 흩뿌릴지(Matte)를
          결정합니다. 선택한 Base Material의 Category(Metal/Resin/Tape/Foam)에
          맞는 항목만 표시됩니다.
        </HelpTooltip>
      </div>
      <select
        id={fieldId}
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
    </div>
  )
}

interface FaceEditorState {
  /** null while composing a brand-new face group that hasn't been applied yet. */
  assignmentId: string | null
  surfaceId: string
}

export function MaterialEditorDialog({
  open,
  onOpenChange,
  component,
  scene,
  componentName,
  returnFocusRef,
}: MaterialEditorDialogProps) {
  const profileFieldId = useId()
  const baseMaterialFieldId = useId()
  const surfacePropertyFieldId = useId()
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
  const customValuesValid =
    !partDraft.opticalOverride ||
    (Math.abs(
      partDraft.opticalOverride.reflectance + partDraft.opticalOverride.loss - 1,
    ) <= 0.001 &&
      Math.abs(
        partDraft.opticalOverride.specularRatio +
          partDraft.opticalOverride.diffuseRatio -
          1,
      ) <= 0.001)

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
  const cadFaceCount = (faceIds: number[]) => {
    const sourceIds = scene?.mesh.face_source_ids
    if (!sourceIds) return faceIds.length
    return new Set(
      faceIds.map((faceId) => sourceIds[faceId] ?? faceId),
    ).size
  }
  const selectedCadFaceCount = cadFaceCount(targetFaceIds)
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
    if (!component || !customValuesValid) return
    const assignment: MaterialAssignment = {
      assignmentId: buildAssignmentId(component.component_id, 'part', []),
      componentId: component.component_id,
      targetType: 'part',
      faceIds: [],
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: partDraft.surfaceId,
      profileId: partDraft.profileId,
      bsdfAssetId: '',
      opticalOverride: partDraft.opticalOverride,
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
    if (!name || !customValuesValid) return
    const profile: SavedOpticalProfile = {
      id: `custom-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      name,
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: partDraft.surfaceId,
      bsdfAssetId: '',
      opticalOverride: partDraft.opticalOverride,
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

  // Starts composing a brand-new face group: opens the editor panel right
  // away (rather than after a separate "선택 완료" step) so the same
  // "뷰어에서 CAD Face 선택" toggle inside it can be pressed again at any
  // time to keep adding/removing faces - including after Apply-ing once,
  // by re-opening via the Edit icon (see startEditFaceAssignment below).
  const openNewFaceGroup = () => {
    if (!component) return
    actions.setSelectedComponentIds([component.component_id])
    actions.setSelectedFaceIds([])
    actions.setMaterialFacePickArmed(true)
    setFaceEditor({ assignmentId: null, surfaceId: partDraft.surfaceId })
  }

  // While the editor is open (new or existing group), the same button just
  // pauses/resumes picking - it never discards the editor itself.
  const toggleFacePick = () => {
    if (!faceEditor) {
      openNewFaceGroup()
      return
    }
    actions.setMaterialFacePickArmed(!materialFacePickArmed)
  }

  const closeFaceEditor = () => {
    actions.setMaterialFacePickArmed(false)
    actions.setSelectedFaceIds([])
    setFaceEditor(null)
  }

  const startEditFaceAssignment = (assignment: MaterialAssignment) => {
    if (!component) return
    // Arm picking immediately, same as openNewFaceGroup - otherwise a click
    // in the viewer right after pressing Edit falls through to the default
    // (non-armed) handler, which *replaces* the selection with a single
    // raw triangle instead of adding a coplanar patch to the existing
    // group. The toggle button still lets the user pause picking.
    actions.setMaterialFacePickArmed(true)
    actions.setSelectedComponentIds([component.component_id])
    actions.setSelectedFaceIds(assignment.faceIds)
    setFaceEditor({
      assignmentId: assignment.assignmentId,
      surfaceId: assignment.surfaceId,
    })
  }

  const handleApplyFaceEditor = () => {
    if (!component || !faceEditor || targetFaceIds.length === 0) return
    const nextId = buildAssignmentId(
      component.component_id,
      'faces',
      targetFaceIds,
    )
    // The assignment id embeds its face list, so a face set change means a
    // new id - drop the old one first or it would linger as a stale
    // duplicate alongside the updated group.
    if (faceEditor.assignmentId && faceEditor.assignmentId !== nextId) {
      actions.removeMaterialAssignment(faceEditor.assignmentId)
    }
    const assignment: MaterialAssignment = {
      assignmentId: nextId,
      componentId: component.component_id,
      targetType: 'faces',
      faceIds: targetFaceIds,
      // Base material always follows the part - only the surface finish can
      // differ per face group.
      baseMaterialId: partDraft.baseMaterialId,
      surfaceId: faceEditor.surfaceId,
      profileId: '',
      bsdfAssetId: '',
      enabled: true,
    }
    actions.upsertMaterialAssignment(assignment)
    closeFaceEditor()
  }

  const handleRemoveFaceAssignment = (assignmentId: string) => {
    actions.removeMaterialAssignment(assignmentId)
    if (faceEditor?.assignmentId === assignmentId) closeFaceEditor()
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title="Material Assignment"
      help={
        component
          ? `${componentName}의 Base Material · Surface Property를 지정하고, 필요하면 특정 Face만 다른 Surface Property로 바꿉니다.`
          : 'Material 대상을 선택하세요.'
      }
      size="lg"
      contentClassName="w-[36rem] sm:max-w-[36rem]"
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
              <div className="text-xs tracking-wide text-muted-foreground uppercase">
                Target
              </div>
              <div className="mt-1 text-sm font-semibold">
                {componentName || 'No component'}
              </div>
            </div>
            {hasActiveRoiScope ? <Badge variant="outline">ROI</Badge> : null}
          </div>
        </section>

        {/* Base material and its default Surface property - one
            always-applicable pair for the whole part, no section title
            (the fields below are self-explanatory once the Target card
            above establishes what's being edited). Same card treatment
            (border/background/padding) as Target and the section below, so
            the dialog reads as one consistent stack of cards. */}
        <section className="rounded-xl border border-border bg-background/45 p-3 space-y-3">
          <div className="space-y-1.5">
            <div className="space-y-1.5 text-sm font-medium">
              <div className="flex items-center gap-1.5">
                <label htmlFor={profileFieldId}>Saved optical profile</label>
                <HelpTooltip label="Saved optical profile 도움말">
                  Base Material + Surface Property 조합을 이름 붙여
                  저장해두고 나중에 다시 고를 수 있습니다. 아래 저장 아이콘
                  (💾)으로 현재 조합을 새 프로필로 저장하고, 내가 만든
                  프로필을 선택 중일 때만 삭제 아이콘이 나타납니다.
                </HelpTooltip>
              </div>
              <div className="flex gap-1.5">
                <select
                  id={profileFieldId}
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
                      opticalOverride: profile.opticalOverride,
                    })
                  }}
                >
                  <option value="">None · use current draft</option>
                  {opticalProfilePresets.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
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
            </div>

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
                  disabled={!profileNameDraft.trim() || !customValuesValid}
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
            <div className="space-y-1.5 text-sm font-medium">
              <div className="flex items-center gap-1.5">
                <label htmlFor={baseMaterialFieldId}>Base Material</label>
                <HelpTooltip label="Base Material 도움말">
                  부품의 소재입니다. Metal(Aluminum/SECC 계열)과
                  Resin(PC/ABS/HIPS × Black/Gray/White)으로 나뉘고, 소재별
                  기본 반사율을 결정합니다. 고른 소재의 category에 따라
                  아래 Surface Property 선택지가 달라집니다.
                </HelpTooltip>
              </div>
              <select
                id={baseMaterialFieldId}
                className={selectClassName}
                value={partDraft.baseMaterialId}
                onChange={(event) => {
                  const nextBase = findBaseMaterial(event.currentTarget.value)
                  setPartDraft({
                    baseMaterialId: nextBase.id,
                    surfaceId: nextBase.defaultSurfaceId,
                    profileId: '',
                    opticalOverride: undefined,
                  })
                }}
              >
                {baseMaterials.map((material) => (
                  <option key={material.id} value={material.id}>
                    {material.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5 text-sm font-medium">
              <div className="flex items-center gap-1.5">
                <label htmlFor={surfacePropertyFieldId}>
                  Surface Property
                </label>
                <HelpTooltip label="Surface Property 도움말">
                  표면 마감(광택도)입니다. Base Material의 반사율을 얼마나
                  정반사 방향으로 집중시킬지(Gloss) vs 사방으로
                  흩뿌릴지(Matte)를 결정합니다. Metal은 Low gloss/Normal/
                  Gloss, Resin은 Matte/Normal/High-gloss 3단계입니다.
                </HelpTooltip>
              </div>
              <select
                id={surfacePropertyFieldId}
                className={selectClassName}
                value={partDraft.surfaceId}
                onChange={(event) =>
                  setPartDraft({
                    ...partDraft,
                    surfaceId: event.currentTarget.value,
                    profileId: '',
                    opticalOverride: undefined,
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
            </div>
          </div>

          <CompiledPreview
            baseMaterialId={partDraft.baseMaterialId}
            surfaceId={partDraft.surfaceId}
            opticalOverride={partDraft.opticalOverride}
          />

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                checked={Boolean(partDraft.opticalOverride)}
                onChange={(event) =>
                  setPartDraft({
                    ...partDraft,
                    profileId: '',
                    opticalOverride: event.currentTarget.checked
                      ? catalogValues(partDraft.baseMaterialId, partDraft.surfaceId)
                      : undefined,
                  })
                }
              />
              Use Custom Optical Values
            </label>
            {partDraft.opticalOverride ? (
              <OpticalValueEditor
                value={partDraft.opticalOverride}
                onChange={(opticalOverride) =>
                  setPartDraft({ ...partDraft, profileId: '', opticalOverride })
                }
              />
            ) : null}
          </div>

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
            <Button
              size="sm"
              disabled={!component || !customValuesValid}
              onClick={handleApplyPart}
            >
              Apply to part
            </Button>
          </div>
        </section>

        {/* 3: optional per-face Surface property override - base material
            is never independently chosen here, it always follows the part. */}
        <section className="rounded-xl border border-border bg-background/45 p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <div className="text-sm font-semibold">Add Surface Property</div>
              <HelpTooltip label="Add Surface Property 도움말">
                기본값은 위 부품 Surface Property를 따라갑니다. 특정 Face만
                다른 마감으로 바꾸고 싶을 때만 추가하세요. Base Material은
                항상 부품과 동일합니다. 이미 만든 항목도 연필 아이콘으로
                다시 열어 면을 추가·제거할 수 있습니다.
              </HelpTooltip>
            </div>
            <Badge variant="outline">{faceAssignments.length}</Badge>
          </div>

          {faceAssignments.length > 0 ? (
            <div className="space-y-1.5">
              {faceAssignments.map((assignment, index) => {
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
                      <div className="truncate text-sm font-semibold">
                        {surface.name}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        CAD 면 {cadFaceCount(assignment.faceIds)}개
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Edit face group ${index + 1} surface property`}
                        onClick={() => startEditFaceAssignment(assignment)}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Remove face group ${index + 1} surface property`}
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

          {faceEditor ? (
            <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold">
                  {faceEditor.assignmentId
                    ? 'Surface Property 편집'
                    : '새 Surface Property'}
                </div>
                <Button variant="ghost" size="sm" onClick={closeFaceEditor}>
                  취소
                </Button>
              </div>
              {/* Same constant-label toggle button as the other pickers -
                  staying inside the editor (rather than disappearing once a
                  group exists) is what lets faces be added to or removed
                  from an already-applied group, not just a brand-new one. */}
              <ViewerFacePickControl
                armed={materialFacePickArmed}
                assigned={selectedCadFaceCount > 0}
                kind="surface"
                cadFaceCount={selectedCadFaceCount}
                onToggle={toggleFacePick}
              />
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
                <Button
                  size="sm"
                  disabled={targetFaceIds.length === 0}
                  onClick={handleApplyFaceEditor}
                >
                  Apply
                </Button>
              </div>
            </div>
          ) : (
            <ViewerFacePickControl
              armed={false}
              assigned={false}
              kind="surface"
              onToggle={openNewFaceGroup}
            />
          )}
        </section>
      </div>
    </AppDialog>
  )
}
