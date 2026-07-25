import {
  useEffect,
  useMemo,
  useState,
  type RefObject,
} from 'react'
import type { SceneComponent } from '@/api'
import { Layers3, Sparkles } from 'lucide-react'

import { AppDialog } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type MaterialAssignment,
  type MaterialTargetType,
} from '@/stores'

import {
  baseMaterials,
  compileOpticalProfile,
  findBaseMaterial,
  findSurfaceProperty,
  opticalProfilePresets,
  surfaceProperties,
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
  const assignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [targetType, setTargetType] =
    useState<MaterialTargetType>('part')
  const [baseMaterialId, setBaseMaterialId] =
    useState('black_pc_resin')
  const [surfaceId, setSurfaceId] = useState('matte_black_resin')
  const [profileId, setProfileId] = useState('')

  const componentFaceIds = useMemo(
    () => new Set(component?.face_indices ?? []),
    [component],
  )
  const targetFaceIds = useMemo(
    () => selectedFaceIds.filter((faceId) => componentFaceIds.has(faceId)),
    [componentFaceIds, selectedFaceIds],
  )

  const assignmentId = component
    ? buildAssignmentId(component.component_id, targetType, targetFaceIds)
    : ''
  const currentAssignment =
    assignments.find(
      (assignment) => assignment.assignmentId === assignmentId,
    ) ?? null

  useEffect(() => {
    if (!open || !component) return

    const partAssignment = assignments.find(
      (assignment) =>
        assignment.componentId === component.component_id &&
        assignment.targetType === 'part',
    )
    const nextBaseMaterialId =
      partAssignment?.baseMaterialId ?? 'black_pc_resin'
    const nextBase = findBaseMaterial(nextBaseMaterialId)

    setTargetType('part')
    setBaseMaterialId(nextBaseMaterialId)
    setSurfaceId(partAssignment?.surfaceId ?? nextBase.defaultSurfaceId)
    setProfileId(partAssignment?.profileId ?? '')
  }, [assignments, component, open])

  const loadTargetAssignment = (nextTargetType: MaterialTargetType) => {
    if (!component) return
    const nextId = buildAssignmentId(
      component.component_id,
      nextTargetType,
      targetFaceIds,
    )
    const assignment = assignments.find(
      (item) => item.assignmentId === nextId,
    )
    const nextBaseMaterialId =
      assignment?.baseMaterialId ?? baseMaterialId
    const nextBase = findBaseMaterial(nextBaseMaterialId)

    setTargetType(nextTargetType)
    setBaseMaterialId(nextBaseMaterialId)
    setSurfaceId(assignment?.surfaceId ?? nextBase.defaultSurfaceId)
    setProfileId(assignment?.profileId ?? '')
  }

  const compiledProfile = compileOpticalProfile(
    baseMaterialId,
    surfaceId,
  )
  const selectedBase = findBaseMaterial(baseMaterialId)
  const selectedSurface = findSurfaceProperty(surfaceId)
  const canApply =
    component !== null &&
    (targetType === 'part' || targetFaceIds.length > 0)

  const handleApply = () => {
    if (!component || !canApply) return
    const assignment: MaterialAssignment = {
      assignmentId: buildAssignmentId(
        component.component_id,
        targetType,
        targetFaceIds,
      ),
      componentId: component.component_id,
      targetType,
      faceIds: targetType === 'faces' ? targetFaceIds : [],
      baseMaterialId,
      surfaceId,
      profileId,
      bsdfAssetId: '',
      enabled: true,
    }

    actions.upsertMaterialAssignment(assignment)
    onOpenChange(false)
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title="Material assignment"
      description={
        component
          ? `${componentName}에 base material과 surface property를 지정합니다.`
          : 'Material 대상을 선택하세요.'
      }
      size="lg"
      returnFocusRef={returnFocusRef}
      footer={
        <>
          {currentAssignment ? (
            <Button
              variant="destructive"
              onClick={() => {
                actions.removeMaterialAssignment(
                  currentAssignment.assignmentId,
                )
                onOpenChange(false)
              }}
            >
              Remove assignment
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canApply} onClick={handleApply}>
            Apply material
          </Button>
        </>
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
            <Badge variant="outline">
              {component?.face_count.toLocaleString() ?? 0} faces
            </Badge>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={targetType === 'part' ? 'secondary' : 'outline'}
              aria-pressed={targetType === 'part'}
              onClick={() => loadTargetAssignment('part')}
            >
              <Layers3 />
              Part assignment
            </Button>
            <Button
              type="button"
              variant={targetType === 'faces' ? 'secondary' : 'outline'}
              aria-pressed={targetType === 'faces'}
              onClick={() => loadTargetAssignment('faces')}
            >
              Face override · {targetFaceIds.length}
            </Button>
          </div>
          {targetType === 'faces' && targetFaceIds.length === 0 ? (
            <p className="mt-2 text-[0.68rem] leading-4 text-warning">
              Face override는 Viewer에서 이 component의 face를 선택한 뒤
              적용할 수 있습니다.
            </p>
          ) : null}
        </section>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1.5 text-xs font-medium">
            <span>Base material</span>
            <select
              className={selectClassName}
              value={baseMaterialId}
              onChange={(event) => {
                const nextBase = findBaseMaterial(
                  event.currentTarget.value,
                )
                setBaseMaterialId(nextBase.id)
                setSurfaceId(nextBase.defaultSurfaceId)
                setProfileId('')
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
              value={surfaceId}
              onChange={(event) => {
                setSurfaceId(event.currentTarget.value)
                setProfileId('')
              }}
            >
              {surfaceProperties.map((surface) => (
                <option key={surface.id} value={surface.id}>
                  {surface.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block space-y-1.5 text-xs font-medium">
          <span>Saved optical profile</span>
          <select
            className={selectClassName}
            value={profileId}
            onChange={(event) => {
              const nextProfileId = event.currentTarget.value
              setProfileId(nextProfileId)
              const profile = opticalProfilePresets.find(
                (item) => item.id === nextProfileId,
              )
              if (!profile) return
              setBaseMaterialId(profile.baseMaterialId)
              setSurfaceId(profile.surfaceId)
            }}
          >
            <option value="">None · use current draft</option>
            {opticalProfilePresets.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>

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
      </div>
    </AppDialog>
  )
}
