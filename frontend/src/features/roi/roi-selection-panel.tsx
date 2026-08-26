import { useState } from 'react'
import {
  BoxSelect,
  Check,
  ClipboardPaste,
  Copy,
  Crosshair,
  MapPin,
  Trash2,
} from 'lucide-react'

import type { ScenePayload } from '@/api'
import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { cn } from '@/lib/utils'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type Vector3Value,
} from '@/stores'

import {
  groupRoiFacesByComponent,
  resolveFacesInRoiBox,
} from './roi-selection'

interface RoiSelectionPanelProps {
  scene?: ScenePayload
}

function formatCoordinate(value: number): string {
  return Number.isFinite(value) ? value.toFixed(3) : '-'
}

export function RoiSelectionPanel({
  scene,
}: RoiSelectionPanelProps) {
  const [coordinate1, setCoordinate1] = useState<Vector3Value>({
    x: 0,
    y: 0,
    z: 0,
  })
  const [coordinate2, setCoordinate2] = useState<Vector3Value>({
    x: 0,
    y: 0,
    z: 0,
  })
  const [coordinateResult, setCoordinateResult] = useState('')
  const [copiedScopeId, setCopiedScopeId] = useState<string | null>(null)
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const roiBoxSelectionArmed = useWorkspaceStore(
    workspaceSelectors.roiBoxSelectionArmed,
  )
  const roiDraftLabel = useWorkspaceStore(
    workspaceSelectors.roiDraftLabel,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const componentNameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const copyRoiCoordinates = async (
    scopeId: string,
    bounds: {
      xMin: number
      xMax: number
      yMin: number
      yMax: number
      zMin: number
      zMax: number
    },
  ) => {
    const text = [
      `(X1, Y1, Z1)=(${formatCoordinate(bounds.xMin)}, ${formatCoordinate(bounds.yMin)}, ${formatCoordinate(bounds.zMin)})`,
      `(X2, Y2, Z2)=(${formatCoordinate(bounds.xMax)}, ${formatCoordinate(bounds.yMax)}, ${formatCoordinate(bounds.zMax)})`,
    ].join(', ')
    try {
      await navigator.clipboard.writeText(text)
      setCopiedScopeId(scopeId)
      window.setTimeout(() => setCopiedScopeId(null), 1600)
    } catch {
      setCoordinateResult('ROI 좌표를 복사하지 못했습니다. 값을 직접 선택해 복사해 주세요.')
    }
  }

  const resolveCoordinate = () => {
    if (!scene) {
      setCoordinateResult('먼저 CAD를 Import하세요.')
      return
    }
    if (
      ![...Object.values(coordinate1), ...Object.values(coordinate2)].every(
        Number.isFinite,
      )
    ) {
      setCoordinateResult('X1, Y1, Z1, X2, Y2, Z2 좌표를 모두 입력하세요.')
      return
    }

    const clipBox = {
      plane: 'xyz' as const,
      xMin: Math.min(coordinate1.x, coordinate2.x),
      xMax: Math.max(coordinate1.x, coordinate2.x),
      yMin: Math.min(coordinate1.y, coordinate2.y),
      yMax: Math.max(coordinate1.y, coordinate2.y),
      zMin: Math.min(coordinate1.z, coordinate2.z),
      zMax: Math.max(coordinate1.z, coordinate2.z),
    }
    const faceIds = resolveFacesInRoiBox(
      scene,
      clipBox,
      hiddenComponentIds,
      deletedComponentIds,
    )
    if (faceIds.length === 0) {
      setCoordinateResult('입력한 좌표 범위에서 ROI를 찾지 못했습니다.')
      return
    }

    actions.addRoiScope({
      label: roiDraftLabel,
      source: 'box',
      view: 'coordinate',
      clipBox,
      components: groupRoiFacesByComponent(
        scene,
        faceIds,
        componentNameOverrides,
      ),
    })
    setCoordinateResult('좌표 범위를 ROI List에 추가했습니다.')
    setCoordinate1({ x: 0, y: 0, z: 0 })
    setCoordinate2({ x: 0, y: 0, z: 0 })
  }

  const pasteRoiCoordinates = async () => {
    try {
      const text = await navigator.clipboard.readText()
      const numberPattern = '([-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][-+]?\\d+)?)'
      const tuplePattern = new RegExp(
        `\\(\\s*${numberPattern}\\s*,\\s*${numberPattern}\\s*,\\s*${numberPattern}\\s*\\)`,
        'g',
      )
      const tuples = [...text.matchAll(tuplePattern)]
      if (tuples.length < 2) throw new Error('invalid ROI coordinates')
      setCoordinate1({
        x: Number(tuples[0][1]),
        y: Number(tuples[0][2]),
        z: Number(tuples[0][3]),
      })
      setCoordinate2({
        x: Number(tuples[1][1]),
        y: Number(tuples[1][2]),
        z: Number(tuples[1][3]),
      })
      setCoordinateResult('')
    } catch {
      setCoordinateResult('ROI 좌표를 붙여넣지 못했습니다.')
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex items-center gap-1.5">
          <label
            htmlFor="roi-scope-label"
            className="text-xs font-medium text-muted-foreground"
          >
            ROI 이름
          </label>
          <HelpTooltip label="ROI 이름 도움말">
            이 ROI에 부여할 이름입니다. 비워두면 자동으로 번호가
            매겨집니다.
          </HelpTooltip>
        </div>
        <input
          id="roi-scope-label"
          value={roiDraftLabel}
          placeholder="예: bottom-corner"
          className="h-9 w-full rounded-lg border border-border bg-background/60 px-3 text-xs outline-none transition focus:border-primary/60 focus:ring-2 focus:ring-primary/15"
          onChange={(event) =>
            actions.setRoiDraftLabel(event.currentTarget.value)
          }
        />
      </div>

      <div className="rounded-xl border border-blue-200 bg-blue-50/65 p-3 dark:border-blue-800/65 dark:bg-blue-950/28">
        <div className="flex items-center gap-2">
          <BoxSelect className="size-4 shrink-0 text-primary" />
          <div className="text-sm font-semibold">박스 드래그</div>
          <HelpTooltip label="박스 드래그 도움말">
            보이는 컴포넌트만 대상으로 현재 카메라와 가장 가까운
            ±XY·±YZ·±ZX 정면 범위를 선택합니다. 화면 깊이 방향은
            제한하지 않습니다. 버튼을 누른 뒤 Viewer에서 왼쪽 버튼을
            누른 채 영역을 그리면, 완료 후 선택 전 카메라 화면으로
            돌아갑니다.
          </HelpTooltip>
        </div>
        <Button
          type="button"
          size="sm"
          variant={roiBoxSelectionArmed ? 'default' : 'outline'}
          disabled={!scene}
          className="mt-3 w-full border-blue-200 bg-blue-100/70 text-blue-900 hover:bg-blue-200/70 dark:border-blue-700/70 dark:bg-blue-900/35 dark:text-sky-200 dark:hover:bg-blue-800/50"
          onClick={() =>
            actions.setRoiBoxSelectionArmed(!roiBoxSelectionArmed)
          }
        >
          <Crosshair className="size-3.5" />
          {roiBoxSelectionArmed
            ? 'ROI 드래그 취소'
            : '+ ROI 추가 후 드래그'}
        </Button>
      </div>

      <div className="rounded-xl border border-blue-200 bg-blue-50/65 p-3 dark:border-blue-800/65 dark:bg-blue-950/28">
        <div className="flex items-center gap-2">
          <MapPin className="size-4 text-primary" />
          <div className="text-sm font-semibold">좌표로 ROI 추가</div>
        </div>
        <div className="mt-3 space-y-2.5">
          {[
            {
              label: '(X1, Y1, Z1)',
              pointIndex: 1,
              value: coordinate1,
              setter: setCoordinate1,
            },
            {
              label: '(X2, Y2, Z2)',
              pointIndex: 2,
              value: coordinate2,
              setter: setCoordinate2,
            },
          ].map((point) => (
            <div key={point.pointIndex}>
              <div className="text-xs font-semibold text-muted-foreground">
                {point.label}
              </div>
              <div className="mt-1 grid grid-cols-3 gap-1.5">
                {(['x', 'y', 'z'] as const).map((axis) => (
                  <label
                    key={axis}
                    className="text-xs font-medium uppercase text-muted-foreground"
                  >
                    {axis}{point.pointIndex} (mm)
                    <NumberInput
                      aria-label={`ROI ${axis.toUpperCase()}${point.pointIndex} coordinate`}
                      value={point.value[axis]}
                      decimals={3}
                      className="mt-1 h-8 w-full rounded-md border border-border bg-background/60 px-2 text-xs text-foreground outline-none focus:border-primary/60"
                      onValueChange={(value) => {
                        point.setter((current) => ({
                          ...current,
                          [axis]: value,
                        }))
                      }}
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!scene}
          className="mt-2.5 w-full"
          onClick={() => void pasteRoiCoordinates()}
        >
          <ClipboardPaste className="size-3.5" />
          ROI 좌표 붙여넣기
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!scene}
          className="mt-2 w-full border-blue-200 bg-blue-100/70 text-blue-900 hover:bg-blue-200/70 dark:border-blue-700/70 dark:bg-blue-900/35 dark:text-sky-200 dark:hover:bg-blue-800/50"
          onClick={resolveCoordinate}
        >
          좌표로 ROI 추가
        </Button>
        {coordinateResult ? (
          <p
            role="status"
            className="mt-2 text-xs leading-5 text-muted-foreground"
          >
            {coordinateResult}
          </p>
        ) : null}
      </div>

      <section aria-labelledby="roi-list-title">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <div
              id="roi-list-title"
              className="text-sm font-semibold"
            >
              ROI List
            </div>
            <HelpTooltip label="ROI List 도움말">
              활성화한 scope만 분석과 Viewer 격리 표시에 반영됩니다.
            </HelpTooltip>
          </div>
          {roiScopes.length > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => actions.clearRoiScopes()}
            >
              전체 삭제
            </Button>
          ) : null}
        </div>

        <div className="mt-2 space-y-2">
          {roiScopes.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
              아직 만든 ROI가 없습니다.
            </div>
          ) : (
            roiScopes.map((scope) => {
              const boxCoordinates = scope.clipBox
                ? {
                    xMin: scope.clipBox.xMin,
                    xMax: scope.clipBox.xMax,
                    yMin: scope.clipBox.yMin,
                    yMax: scope.clipBox.yMax,
                    zMin:
                      scope.clipBox.zMin ??
                      Math.min(
                        ...scope.components.map(
                          (component) => component.bboxMin.z,
                        ),
                      ),
                    zMax:
                      scope.clipBox.zMax ??
                      Math.max(
                        ...scope.components.map(
                          (component) => component.bboxMax.z,
                        ),
                      ),
                  }
                : null

              return (
                <article
                  key={scope.id}
                  className={cn(
                    'rounded-lg border p-2.5 transition-colors',
                    scope.active
                      ? 'border-primary/35 bg-primary/8'
                      : 'border-border bg-background/30 opacity-70',
                  )}
                >
                  <div className="flex items-start gap-2">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        aria-label={`${scope.scopeId} 활성화`}
                        checked={scope.active}
                        className="mt-0.5 size-3.5 accent-primary"
                        onChange={(event) =>
                          actions.setRoiScopeActive(
                            scope.id,
                            event.currentTarget.checked,
                          )
                        }
                      />
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold">
                          {scope.scopeId}
                        </span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          {scope.source === 'box' ? scope.view : 'coordinate'}
                        </span>
                      </span>
                    </label>
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`${scope.scopeId} 삭제`}
                      onClick={() => actions.removeRoiScope(scope.id)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                  {boxCoordinates ? (
                    <div className="mt-2 rounded-md border border-primary/15 bg-background/55 p-2">
                      <div className="mb-1.5 flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-foreground">
                          ROI 좌표 (mm)
                        </span>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="h-6 gap-1 px-1.5 text-xs"
                          aria-label={`${scope.scopeId} ROI 좌표 복사`}
                          onClick={() =>
                            void copyRoiCoordinates(
                              scope.id,
                              boxCoordinates,
                            )
                          }
                        >
                          {copiedScopeId === scope.id ? (
                            <Check className="size-3" />
                          ) : (
                            <Copy className="size-3" />
                          )}
                          {copiedScopeId === scope.id ? '복사됨' : '복사'}
                        </Button>
                      </div>
                      <div className="space-y-1.5 text-xs">
                        {[
                          {
                            label: '(X1, Y1, Z1)',
                            pointIndex: 1,
                            values: [
                              boxCoordinates.xMin,
                              boxCoordinates.yMin,
                              boxCoordinates.zMin,
                            ],
                          },
                          {
                            label: '(X2, Y2, Z2)',
                            pointIndex: 2,
                            values: [
                              boxCoordinates.xMax,
                              boxCoordinates.yMax,
                              boxCoordinates.zMax,
                            ],
                          },
                        ].map((point) => (
                          <div
                            key={point.pointIndex}
                            className="grid grid-cols-[5.5rem_repeat(3,minmax(0,1fr))] items-center gap-1.5"
                          >
                            <span className="whitespace-nowrap font-semibold text-primary">
                              {point.label}
                            </span>
                            {point.values.map((value, axisIndex) => (
                              <input
                                key={axisIndex}
                                readOnly
                                aria-label={`${scope.scopeId} ${['X', 'Y', 'Z'][axisIndex]}${point.pointIndex}`}
                                value={formatCoordinate(value)}
                                className="h-7 min-w-0 rounded border border-border bg-background px-1.5 text-right font-mono text-xs text-foreground outline-none focus:border-primary focus:ring-1 focus:ring-primary/25"
                                onFocus={(event) =>
                                  event.currentTarget.select()
                                }
                              />
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </article>
              )
            })
          )}
        </div>
      </section>

    </div>
  )
}
