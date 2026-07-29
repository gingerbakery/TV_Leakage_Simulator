import {
  useEffect,
  useMemo,
  useState,
  type RefObject,
} from 'react'
import type { SceneComponent } from '@/api'
import { Box, MousePointer2, Rotate3D, ScanSearch } from 'lucide-react'

import { AppDialog } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type ComponentTransformRule,
  type TransformSelectionMethod,
  type TransformTargetType,
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

function buildRuleId(
  componentId: number,
  targetType: TransformTargetType,
  faceIds: number[],
): string {
  if (targetType === 'component') return `transform-component-${componentId}`
  return `transform-faces-${componentId}-${faceIds.join('-')}`
}

function vectorMagnitude(vector: Vector3Value): number {
  return Math.sqrt(vector.x ** 2 + vector.y ** 2 + vector.z ** 2)
}

export function TransformEditorDialog({
  open,
  onOpenChange,
  component,
  componentName,
  returnFocusRef,
}: TransformEditorDialogProps) {
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const transformRules = useWorkspaceStore(
    workspaceSelectors.transformRules,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [targetType, setTargetType] =
    useState<TransformTargetType>('component')
  const [selectionMethod, setSelectionMethod] =
    useState<TransformSelectionMethod>('click')
  const [move, setMove] = useState<Vector3Value>(zeroVector)
  const [tilt, setTilt] = useState<Vector3Value>(zeroVector)

  const componentFaceIds = useMemo(
    () => new Set(component?.face_indices ?? []),
    [component],
  )
  const targetFaceIds = useMemo(
    () => selectedFaceIds.filter((faceId) => componentFaceIds.has(faceId)),
    [componentFaceIds, selectedFaceIds],
  )
  const activeRoiFaceCount = useMemo(() => {
    if (!component) return null
    const activeScopes = roiScopes.filter((scope) => scope.active)
    if (activeScopes.length === 0) return null
    return new Set(
      activeScopes.flatMap((scope) =>
        scope.components
          .filter(
            (entry) =>
              entry.componentId === component.component_id,
          )
          .flatMap((entry) => entry.faceIds),
      ),
    ).size
  }, [component, roiScopes])
  const ruleId = component
    ? buildRuleId(component.component_id, targetType, targetFaceIds)
    : ''
  const currentRule =
    transformRules.find((rule) => rule.ruleId === ruleId) ?? null

  useEffect(() => {
    if (!open || !component) return
    const componentRule = transformRules.find(
      (rule) =>
        rule.componentId === component.component_id &&
        rule.targetType === 'component',
    )
    setTargetType('component')
    setSelectionMethod(componentRule?.selectionMethod ?? 'click')
    setMove(componentRule?.move ?? zeroVector())
    setTilt(componentRule?.tilt ?? zeroVector())
  }, [component, open, transformRules])

  const loadTargetRule = (nextTargetType: TransformTargetType) => {
    if (!component) return
    const nextId = buildRuleId(
      component.component_id,
      nextTargetType,
      targetFaceIds,
    )
    const nextRule = transformRules.find((rule) => rule.ruleId === nextId)
    setTargetType(nextTargetType)
    setSelectionMethod(nextRule?.selectionMethod ?? 'click')
    setMove(nextRule?.move ?? zeroVector())
    setTilt(nextRule?.tilt ?? zeroVector())
  }

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

  const canApply =
    component !== null &&
    (targetType === 'component' || targetFaceIds.length > 0)

  const handleApply = () => {
    if (!component || !canApply) return
    const rule: ComponentTransformRule = {
      ruleId: buildRuleId(
        component.component_id,
        targetType,
        targetFaceIds,
      ),
      componentId: component.component_id,
      targetType,
      selectionMethod,
      faceIds: targetType === 'faces' ? targetFaceIds : [],
      move,
      tilt,
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
      description={
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
              <div className="text-[0.65rem] tracking-wide text-muted-foreground uppercase">
                Target
              </div>
              <div className="mt-1 text-sm font-semibold">
                {componentName || 'No component'}
              </div>
            </div>
            <Badge variant="outline">
              {activeRoiFaceCount === null
                ? component?.face_count.toLocaleString() ?? 0
                : activeRoiFaceCount.toLocaleString()}{' '}
              {activeRoiFaceCount === null ? 'faces' : 'ROI faces'}
            </Badge>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={targetType === 'component' ? 'secondary' : 'outline'}
              aria-pressed={targetType === 'component'}
              onClick={() => loadTargetRule('component')}
            >
              <Box />
              Component move
            </Button>
            <Button
              type="button"
              variant={targetType === 'faces' ? 'secondary' : 'outline'}
              aria-pressed={targetType === 'faces'}
              onClick={() => loadTargetRule('faces')}
            >
              <ScanSearch />
              Local faces · {targetFaceIds.length}
            </Button>
          </div>
          {targetType === 'faces' ? (
            <>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={
                    selectionMethod === 'click' ? 'secondary' : 'ghost'
                  }
                  aria-pressed={selectionMethod === 'click'}
                  onClick={() => setSelectionMethod('click')}
                >
                  <MousePointer2 />
                  Click selection
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={
                    selectionMethod === 'box' ? 'secondary' : 'ghost'
                  }
                  aria-pressed={selectionMethod === 'box'}
                  onClick={() => setSelectionMethod('box')}
                >
                  Box selection
                </Button>
              </div>
              {targetFaceIds.length === 0 ? (
                <p className="mt-2 text-[0.68rem] leading-4 text-warning">
                  Local face move는 Viewer에서 face를 선택한 뒤 적용할 수
                  있습니다.
                </p>
              ) : null}
            </>
          ) : null}
        </section>

        <VectorEditor
          title="Move"
          unit="mm"
          vector={move}
          onChange={(axis, value) =>
            updateVector(setMove, move, axis, value)
          }
        />
        <VectorEditor
          title="Tilt"
          unit="deg"
          vector={tilt}
          onChange={(axis, value) =>
            updateVector(setTilt, tilt, axis, value)
          }
        />

        <section className="rounded-xl border border-primary/20 bg-primary/5 p-3">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <Rotate3D className="size-3.5 text-primary" />
            Transform preview
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
          <p className="mt-3 text-[0.68rem] leading-4 text-muted-foreground">
            적용한 component move·tilt와 local face overlay는 Three.js
            Viewer에 즉시 반영됩니다.
          </p>
        </section>
      </div>
    </AppDialog>
  )
}

interface VectorEditorProps {
  title: string
  unit: string
  vector: Vector3Value
  onChange(axis: Axis, value: number): void
}

function VectorEditor({
  title,
  unit,
  vector,
  onChange,
}: VectorEditorProps) {
  return (
    <fieldset className="rounded-xl border border-border bg-background/35 p-3">
      <legend className="px-1 text-xs font-semibold">
        {title} · {unit}
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
