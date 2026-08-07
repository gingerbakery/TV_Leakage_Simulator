import {
  useEffect,
  useMemo,
  useState,
  type RefObject,
} from 'react'
import type { SceneComponent } from '@/api'
import { Box, Crosshair, MousePointer2, Rotate3D } from 'lucide-react'

import { AppDialog, HelpTooltip } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type ComponentTransformRule,
  type Vector3Value,
} from '@/stores'

interface TransformEditorDialogProps {
  open: boolean
  onOpenChange(open: boolean): void
  component: SceneComponent | null
  componentName: string
  returnFocusRef?: RefObject<HTMLElement | null>
}

type Axis = keyof Vector3Value

const zeroVector = (): Vector3Value => ({ x: 0, y: 0, z: 0 })
const inputClassName =
  'h-9 w-full rounded-lg border border-input bg-background px-2.5 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20'

function buildRuleId(componentId: number): string {
  return `transform-component-${componentId}`
}

function vectorMagnitude(vector: Vector3Value): number {
  return Math.sqrt(vector.x ** 2 + vector.y ** 2 + vector.z ** 2)
}

function componentBoundsCenter(
  component: SceneComponent | null,
): Vector3Value {
  if (!component) return zeroVector()
  return {
    x: (component.bbox_min[0] + component.bbox_max[0]) / 2,
    y: (component.bbox_min[1] + component.bbox_max[1]) / 2,
    z: (component.bbox_min[2] + component.bbox_max[2]) / 2,
  }
}

type PivotMode = 'center' | 'custom'

export function TransformEditorDialog({
  open,
  onOpenChange,
  component,
  componentName,
  returnFocusRef,
}: TransformEditorDialogProps) {
  const transformRules = useWorkspaceStore(
    workspaceSelectors.transformRules,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const pivotPickArmed = useWorkspaceStore(
    workspaceSelectors.pivotPickArmed,
  )
  const pivotPickPoint = useWorkspaceStore(
    workspaceSelectors.pivotPickPoint,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [move, setMove] = useState<Vector3Value>(zeroVector)
  const [tilt, setTilt] = useState<Vector3Value>(zeroVector)
  const [pivotMode, setPivotMode] = useState<PivotMode>('center')
  const [pivot, setPivot] = useState<Vector3Value>(zeroVector)

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
  const ruleId = component ? buildRuleId(component.component_id) : ''
  const currentRule =
    transformRules.find((rule) => rule.ruleId === ruleId) ?? null

  useEffect(() => {
    if (!open || !component) return
    // A legacy Local-faces rule (from a project saved before that feature
    // was removed) can share this component but never this ruleId - only
    // the component-level rule feeds this dialog now.
    const componentRule = transformRules.find(
      (rule) =>
        rule.componentId === component.component_id &&
        rule.targetType === 'component',
    )
    setMove(componentRule?.move ?? zeroVector())
    setTilt(componentRule?.tilt ?? zeroVector())
    setPivotMode(componentRule?.pivot ? 'custom' : 'center')
    setPivot(componentRule?.pivot ?? componentBoundsCenter(component))
  }, [component, open, transformRules])

  // A pick made in the viewer while this dialog was open lands here as a
  // point in the shared store (the viewer has no direct reference back to
  // this dialog's local state) - consume it once, then clear it so it
  // can't be replayed if the dialog closes and reopens later.
  useEffect(() => {
    if (!open || !pivotPickPoint) return
    setPivotMode('custom')
    setPivot(pivotPickPoint)
    actions.setPivotPickPoint(null)
  }, [actions, open, pivotPickPoint])

  // Closing the dialog mid-pick would otherwise strand the viewer in an
  // armed, crosshair-cursor state with nothing left to receive the result.
  useEffect(() => {
    if (open) return
    actions.setPivotPickArmed(false)
  }, [actions, open])

  // Mirror the draft pivot into the viewer as a marker so the user can see
  // where it actually sits - both right after picking and while nudging
  // the X/Y/Z fields by hand, before Apply is ever pressed.
  useEffect(() => {
    actions.setPivotPreviewPoint(
      open && pivotMode === 'custom' ? pivot : null,
    )
  }, [actions, open, pivotMode, pivot])

  useEffect(() => {
    return () => {
      actions.setPivotPreviewPoint(null)
    }
  }, [actions])

  const updateVector = (
    setter: (value: Vector3Value) => void,
    vector: Vector3Value,
    axis: Axis,
    value: number,
  ) => {
    setter({
      ...vector,
      [axis]: Number.isFinite(value) ? value : 0,
    })
  }

  const canApply = component !== null

  const handleApply = () => {
    if (!component || !canApply) return
    const rule: ComponentTransformRule = {
      ruleId: buildRuleId(component.component_id),
      componentId: component.component_id,
      targetType: 'component',
      selectionMethod: 'click',
      faceIds: [],
      move,
      tilt,
      pivot: pivotMode === 'custom' ? pivot : null,
      enabled: true,
    }
    actions.upsertTransformRule(rule)
    onOpenChange(false)
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title="Transform editor"
      help={
        component
          ? `${componentName}의 move·tilt rule을 편집합니다.`
          : 'Transform 대상을 선택하세요.'
      }
      size="lg"
      returnFocusRef={returnFocusRef}
      onSubmit={handleApply}
      footer={
        <>
          {currentRule ? (
            <Button
              variant="destructive"
              onClick={() => {
                actions.removeTransformRule(currentRule.ruleId)
                onOpenChange(false)
              }}
            >
              Remove rule
            </Button>
          ) : null}
          <Button
            variant="outline"
            onClick={() => {
              setMove(zeroVector())
              setTilt(zeroVector())
              setPivotMode('center')
              setPivot(componentBoundsCenter(component))
            }}
          >
            Reset
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canApply} onClick={handleApply}>
            Apply transform
          </Button>
        </>
      }
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <section className="rounded-xl border border-border bg-background/45 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-1.5 text-[0.65rem] tracking-wide text-muted-foreground uppercase">
                Target
                <HelpTooltip label="Target 도움말">
                  선택한 부품 전체를 이동·회전합니다.
                </HelpTooltip>
              </div>
              <div className="mt-1 text-sm font-semibold">
                {componentName || 'No component'}
              </div>
            </div>
            {hasActiveRoiScope ? <Badge variant="outline">ROI</Badge> : null}
          </div>
        </section>

        <VectorEditor
          title="Move"
          unit="mm"
          help="선택한 부품을 X/Y/Z 방향으로 평행이동합니다. Tilt보다 먼저 적용되는 것으로 간주해 계산합니다."
          vector={move}
          onChange={(axis, value) =>
            updateVector(setMove, move, axis, value)
          }
        />
        <VectorEditor
          title="Tilt"
          unit="deg"
          help="선택한 부품을 X/Y/Z축 기준으로 회전합니다. 회전 기준점은 아래 Tilt pivot에서 정합니다."
          vector={tilt}
          onChange={(axis, value) =>
            updateVector(setTilt, tilt, axis, value)
          }
        />

        <fieldset className="rounded-xl border border-border bg-background/35 p-3">
          <legend className="flex items-center gap-1.5 px-1 text-xs font-semibold">
            Tilt pivot
            <HelpTooltip label="Tilt pivot 도움말">
              Tilt 회전의 기준점입니다. Component center는 대상의 bounding
              box 중심을 기준으로 회전하고, Custom point는 직접 좌표를
              입력하거나 Viewer에서 표면을 클릭해 임의의 지점을 기준점으로
              지정합니다.
            </HelpTooltip>
          </legend>
          <div className="mt-1 grid grid-cols-2 gap-2">
            <Button
              type="button"
              size="sm"
              variant={pivotMode === 'center' ? 'secondary' : 'ghost'}
              aria-pressed={pivotMode === 'center'}
              onClick={() => {
                setPivotMode('center')
                setPivot(componentBoundsCenter(component))
              }}
            >
              <Box />
              Component center
            </Button>
            <Button
              type="button"
              size="sm"
              variant={pivotMode === 'custom' ? 'secondary' : 'ghost'}
              aria-pressed={pivotMode === 'custom'}
              onClick={() => setPivotMode('custom')}
            >
              <Crosshair />
              Custom point
            </Button>
          </div>
          {pivotMode === 'custom' ? (
            <div className="mt-3 grid grid-cols-3 gap-2">
              {(['x', 'y', 'z'] as const).map((axis) => (
                <label
                  key={axis}
                  className="space-y-1 text-[0.68rem] font-medium"
                >
                  <span className="uppercase">{axis}</span>
                  <NumberInput
                    aria-label={`Pivot ${axis}`}
                    step={0.1}
                    decimals={1}
                    className={inputClassName}
                    value={pivot[axis]}
                    onValueChange={(value) =>
                      updateVector(setPivot, pivot, axis, value)
                    }
                  />
                </label>
              ))}
            </div>
          ) : null}
          {pivotMode === 'custom' ? (
            <Button
              type="button"
              size="sm"
              variant={pivotPickArmed ? 'secondary' : 'outline'}
              aria-pressed={pivotPickArmed}
              className="mt-2 w-full"
              onClick={() =>
                actions.setPivotPickArmed(!pivotPickArmed)
              }
            >
              <MousePointer2 />
              {pivotPickArmed
                ? '뷰어에서 표면을 클릭하세요…'
                : '뷰어에서 좌표 선택'}
            </Button>
          ) : null}
        </fieldset>

        <section className="rounded-xl border border-primary/20 bg-primary/5 p-3">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <Rotate3D className="size-3.5 text-primary" />
            Transform preview
            <HelpTooltip label="Transform preview 도움말">
              적용한 move·tilt는 Three.js Viewer에 즉시 반영됩니다.
            </HelpTooltip>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border bg-background/45 p-2">
              <div className="text-[0.62rem] text-muted-foreground">
                Move magnitude
              </div>
              <div className="mt-1 font-mono text-sm font-semibold">
                {vectorMagnitude(move).toFixed(3)} mm
              </div>
            </div>
            <div className="rounded-lg border border-border bg-background/45 p-2">
              <div className="text-[0.62rem] text-muted-foreground">
                Tilt magnitude
              </div>
              <div className="mt-1 font-mono text-sm font-semibold">
                {vectorMagnitude(tilt).toFixed(3)}°
              </div>
            </div>
          </div>
        </section>
      </div>
    </AppDialog>
  )
}

interface VectorEditorProps {
  title: string
  unit: string
  help: string
  vector: Vector3Value
  onChange(axis: Axis, value: number): void
}

function VectorEditor({
  title,
  unit,
  help,
  vector,
  onChange,
}: VectorEditorProps) {
  return (
    <fieldset className="rounded-xl border border-border bg-background/35 p-3">
      <legend className="flex items-center gap-1.5 px-1 text-xs font-semibold">
        {title} · {unit}
        <HelpTooltip label={`${title} 도움말`}>{help}</HelpTooltip>
      </legend>
      <div className="mt-1 grid grid-cols-3 gap-2">
        {(['x', 'y', 'z'] as const).map((axis) => (
          <label key={axis} className="space-y-1 text-[0.68rem] font-medium">
            <span className="uppercase">
              {title === 'Tilt' ? 'R' : ''}
              {axis}
            </span>
            <NumberInput
              step={0.1}
              decimals={1}
              className={inputClassName}
              value={vector[axis]}
              onValueChange={(value) => onChange(axis, value)}
            />
          </label>
        ))}
      </div>
    </fieldset>
  )
}
