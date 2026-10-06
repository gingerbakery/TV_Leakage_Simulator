import { useState, type RefObject } from 'react'
import { AppDialog } from '@/components/common'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, type OpticalValueOverride } from '@/stores'
import { findBaseMaterial, findSurfaceProperty, type CompiledOpticalProfile } from './material-catalog'
import { OpticalProfilePreview, OpticalValueEditor } from './optical-profile-editor'
import { isCustomOpticalValid, resolveOpticalProfile } from './optical-profile'

interface CustomOpticalDialogProps {
  componentName: string
  baseMaterialId: string
  surfaceId: string
  initialValue: CompiledOpticalProfile
  returnFocusRef: RefObject<HTMLButtonElement | null>
  onClose(): void
  onSave(value: OpticalValueOverride): void
}

// Mounted for one editing session; changes stay local until Save.
export function CustomOpticalDialog({
  componentName, baseMaterialId, surfaceId, initialValue,
  returnFocusRef, onClose, onSave,
}: CustomOpticalDialogProps) {
  const [draft, setDraft] = useState<OpticalValueOverride>(() => ({ ...initialValue }))
  const angleDependent = useWorkspaceStore((state) => state.rayTraceConfig.angle_dependent_reflectance !== false)
  const valid = isCustomOpticalValid(draft)
  const preview = resolveOpticalProfile(baseMaterialId, surfaceId, draft)
  const save = () => {
    if (valid) onSave({ ...preview })
  }

  return <AppDialog open onOpenChange={(open) => { if (!open) onClose() }}
    title="Surface Property · 커스텀 편집" size="md"
    help="Save하면 부품 기본값과 Material Assignment의 미리보기에 바로 반영됩니다."
    keepOpenOnInteractOutside returnFocusRef={returnFocusRef}
    footer={<>
      <Button variant="outline" onClick={onClose}>Cancel</Button>
      <Button disabled={!valid} onClick={save}>Save</Button>
    </>}>
    <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
      <div className="rounded-lg border border-border bg-muted/20 p-3">
        <div className="font-semibold">{componentName}</div>
        <div className="mt-1 text-xs text-muted-foreground">
          {findBaseMaterial(baseMaterialId).name} · {findSurfaceProperty(surfaceId).name}
        </div>
      </div>
      <OpticalValueEditor value={draft} onChange={setDraft} />
      {!valid ? <p role="alert" className="text-xs text-destructive">
        비율은 0–1, σ는 0보다 큰 값이어야 합니다. None의 반사율은 0입니다.
      </p> : null}
      <OpticalProfilePreview profile={preview} label="적용 예정 광학 값" />
      <div className="text-xs text-muted-foreground">
        입사각 반사율 보정: {angleDependent ? 'ON · 실제 반사율은 각도에 따라 달라짐' : 'OFF · 입력 반사율 고정'}
      </div>
    </div>
  </AppDialog>
}
