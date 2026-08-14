import { useMemo, useState } from 'react'
import {
  BoxSelect,
  Crosshair,
  MapPin,
  Power,
  Trash2,
} from 'lucide-react'

import type { ScenePayload } from '@/api'
import { HelpTooltip } from '@/components/common'
import { Badge } from '@/components/ui/badge'
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
  resolveNearestVisibleFace,
  summarizeActiveRoiScopes,
} from './roi-selection'

interface RoiSelectionPanelProps {
  scene?: ScenePayload
}

function formatArea(value: number): string {
  return new Intl.NumberFormat('ko-KR', {
    maximumFractionDigits: 2,
  }).format(value)
}

export function RoiSelectionPanel({
  scene,
}: RoiSelectionPanelProps) {
  const [coordinate, setCoordinate] = useState<Vector3Value>({
    x: 0,
    y: 0,
    z: 0,
  })
  const [coordinateResult, setCoordinateResult] = useState('')
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
  const summary = useMemo(
    () => summarizeActiveRoiScopes(roiScopes),
    [roiScopes],
  )

  const resolveCoordinate = () => {
    if (!scene) {
      setCoordinateResult('먼저 CAD를 Import하세요.')
      return
    }
    const point = coordinate
    if (!Object.values(point).every(Number.isFinite)) {
      setCoordinateResult('X, Y, Z 좌표를 모두 숫자로 입력하세요.')
      return
    }

    const faceId = resolveNearestVisibleFace(
      scene,
      point,
      hiddenComponentIds,
      deletedComponentIds,
    )
    if (faceId === null) {
      setCoordinateResult(
        '보이는 컴포넌트에서 선택 가능한 face를 찾지 못했습니다.',
      )
      return
    }

    actions.addRoiScope({
      label: roiDraftLabel,
      source: 'point',
      view: 'coordinate',
      point,
      components: groupRoiFacesByComponent(
        scene,
        [faceId],
        componentNameOverrides,
      ),
    })
    setCoordinateResult('선택한 face를 ROI List에 추가했습니다.')
    setCoordinate({ x: 0, y: 0, z: 0 })
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
          <div className="text-sm font-semibold">좌표로 Face 찾기</div>
          <HelpTooltip label="좌표로 Face 찾기 도움말">
            입력한 X/Y/Z 좌표에서 가장 가까운, 현재 보이는 컴포넌트의
            face를 찾아 ROI로 추가합니다.
          </HelpTooltip>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-1.5">
          {(['x', 'y', 'z'] as const).map((axis) => (
            <label
              key={axis}
              className="text-sm font-medium text-muted-foreground uppercase"
            >
              {axis} (mm)
              <NumberInput
                aria-label={`ROI ${axis.toUpperCase()} coordinate`}
                value={coordinate[axis]}
                decimals={1}
                className="mt-1 h-8 w-full rounded-md border border-border bg-background/60 px-2 text-xs text-foreground outline-none focus:border-primary/60"
                onValueChange={(value) => {
                  setCoordinate((current) => ({
                    ...current,
                    [axis]: value,
                  }))
                }}
              />
            </label>
          ))}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!scene}
          className="mt-2.5 w-full border-blue-200 bg-blue-100/70 text-blue-900 hover:bg-blue-200/70 dark:border-blue-700/70 dark:bg-blue-900/35 dark:text-sky-200 dark:hover:bg-blue-800/50"
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
              const areaMm2 = scope.components.reduce(
                (sum, component) => sum + component.areaMm2,
                0,
              )

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
                          {' · '}
                          {formatArea(areaMm2)} mm²
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
                  <div className="mt-2 space-y-1 border-t border-border/60 pt-2">
                    {scope.components.map((component) => (
                      <div
                        key={component.componentId}
                        className="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                      >
                        <span className="truncate">
                          {component.componentName}
                        </span>
                        <span className="shrink-0">
                          {formatArea(component.areaMm2)} mm²
                        </span>
                      </div>
                    ))}
                  </div>
                </article>
              )
            })
          )}
        </div>
      </section>

      <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Power className="size-3.5 text-primary" />
            활성 ROI
            <HelpTooltip label="활성 ROI 도움말">
              박스 ROI는 경계에서 triangle을 실제 절단하고 새 vertex와
              폐곡선 section cap을 만든 뒤 ROI solid만 표시합니다.
              좌표 선택은 단일 face 보완 경로라 절단 cap을 만들지
              않습니다.
            </HelpTooltip>
          </div>
          <Badge variant="outline" className="border-primary/25 text-primary">
            {summary.scopeCount} scopes
          </Badge>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2 text-center">
          {[
            ['Component', summary.componentCount.toLocaleString()],
            ['Area', `${formatArea(summary.areaMm2)} mm²`],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-md bg-background/45 px-1 py-2"
            >
              <div className="text-xs text-muted-foreground">
                {label}
              </div>
              <div className="mt-0.5 truncate text-base font-semibold">
                {value}
              </div>
            </div>
          ))}
        </div>
        {summary.bboxMin && summary.bboxMax ? (
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            Bounds · X {summary.bboxMin.x.toFixed(1)}~
            {summary.bboxMax.x.toFixed(1)} · Y{' '}
            {summary.bboxMin.y.toFixed(1)}~
            {summary.bboxMax.y.toFixed(1)} · Z{' '}
            {summary.bboxMin.z.toFixed(1)}~
            {summary.bboxMax.z.toFixed(1)} mm
          </p>
        ) : null}
      </div>
    </div>
  )
}
