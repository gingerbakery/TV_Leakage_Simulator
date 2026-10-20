import type { RayTraceJob, RayTraceResult } from '@/api'
import { useState } from 'react'
import {
  Activity,
  BarChart3,
  Eye,
  FlaskConical,
  Grid3X3,
  Route,
} from 'lucide-react'

import { HelpTooltip } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { LtValidationDialog } from '@/features/lt-validation'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type RayPathDisplayFilters,
} from '@/stores'

import {
  isRayPathVisible,
  rayPathFilterOrder,
  rayPathStyles,
} from './ray-paths'

interface ResultPanelProps {
  job?: RayTraceJob
  result?: RayTraceResult | null
  onOpenAnalysis(): void
  onOpenLtValidation?(): void
}

const allFilters = (visible: boolean): RayPathDisplayFilters => ({
  receiver_direct: visible,
  receiver_reflected: visible,
  direct: visible,
  specular: visible,
  lambertian: visible,
  gaussian: visible,
  bsdf: visible,
})

export function ResultPanel({
  job,
  result: suppliedResult,
  onOpenAnalysis,
  onOpenLtValidation,
}: ResultPanelProps) {
  const [ltValidationOpen, setLtValidationOpen] = useState(false)
  const filters = useWorkspaceStore(
    workspaceSelectors.rayPathDisplayFilters,
  )
  const displayLimit = useWorkspaceStore(
    workspaceSelectors.rayPathDisplayLimit,
  )
  const heatmap3dMode = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dMode,
  )
  const heatmap3dOpacity = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dOpacity,
  )
  const heatmap3dScaleMode = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dScaleMode,
  )
  const heatmap3dCustomMinNit = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dCustomMinNit,
  )
  const heatmap3dCustomMaxNit = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dCustomMaxNit,
  )
  const heatmap3dInterpolation = useWorkspaceStore(
    workspaceSelectors.receiverHeatmap3dInterpolation,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const result =
    suppliedResult !== undefined
      ? suppliedResult
      : job?.status === 'completed'
        ? job.result
        : undefined
  const paths = result?.stored_paths ?? []
  const visiblePathCount = paths.reduce(
    (count, path) =>
      count + Number(isRayPathVisible(path, filters)),
    0,
  )
  const displayedPathCount = Math.min(displayLimit, visiblePathCount)
  const hitRatio =
    result && result.total_rays > 0
      ? result.receiver_hit_count / result.total_rays
      : 0

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1">
        <Button className="flex-1" variant="outline" onClick={onOpenLtValidation ?? (() => setLtValidationOpen(true))}>
          <FlaskConical />LT 검증
        </Button>
        <HelpTooltip label="LT 검증 도움말">.bitsam과 LightTools의 원시 수광 결과를 별도 Case로 비교합니다. CAD나 현재 해석 결과 없이 열 수 있습니다.</HelpTooltip>
      </div>
      {!onOpenLtValidation ? <LtValidationDialog open={ltValidationOpen} onOpenChange={setLtValidationOpen} /> : null}
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Activity className="size-3.5" />
            Result status
            <HelpTooltip label="Result status 도움말">
              현재 Ray Tracing Job의 진행 상태와 저장된 해석 결과를
              보여줍니다. "분석 결과 보기"로 상세 리포트(Result window)를
              다시 열 수 있습니다.
            </HelpTooltip>
          </div>
          <Badge
            variant="outline"
            className={
              result && (!job || job.status === 'completed')
                ? 'border-primary/25 bg-primary/8 text-primary'
                : undefined
            }
          >
            {job?.status ?? (result ? 'saved result' : 'not run')}
          </Badge>
        </div>
        {job?.status === 'failed' ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/8 p-2 text-xs text-destructive">
            {job.error}
          </p>
        ) : job && job.status !== 'completed' ? (
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm">
            <div className="flex justify-between">
              <span className="font-semibold">{job.phase_detail ?? job.phase}</span>
              <span>
                {job.phase === 'aggregating' || job.phase === 'serializing'
                  ? 'Finalizing'
                  : `${(job.progress * 100).toFixed(1)}%`}
              </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${job.progress * 100}%` }}
              />
            </div>
          </div>
        ) : null}
        {result ? (
          <>
            {job && job.status !== 'completed' ? (
              <p className="text-xs leading-4 text-muted-foreground">
                이전 해석 결과를 표시합니다.
              </p>
            ) : null}
            <div className="grid grid-cols-2 gap-1.5">
              {[
                ['Total rays', result.total_rays.toLocaleString()],
                [
                  'Receiver Hits',
                  result.receiver_hit_count.toLocaleString(),
                ],
                ['Hit ratio', `${(hitRatio * 100).toFixed(3)}%`],
                ['Runtime', `${result.runtime_sec.toFixed(3)} s`],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-lg border border-border bg-background/40 p-2"
                >
                  <div className="text-sm text-muted-foreground">
                    {label}
                  </div>
                  <div className="mt-0.5 text-base font-semibold">{value}</div>
                </div>
              ))}
            </div>
            <Button
              className="w-full"
              variant="outline"
              onClick={onOpenAnalysis}
            >
              <BarChart3 />
              분석 결과 보기
            </Button>
          </>
        ) : !job || job.status === 'completed' ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-xs leading-4 text-muted-foreground">
            Ray Tracing을 실행하거나 결과가 저장된 프로젝트를 열면
            결과와 저장 경로가 표시됩니다.
          </p>
        ) : null}
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Grid3X3 className="size-3.5" />
            Receiver Heatmap 3D
            <HelpTooltip label="Receiver Heatmap 3D 도움말">
              계산된 Receiver Heatmap을 실제 3D Receiver Plane 위에
              표시합니다. Physical은 CAD에 가리고, X-ray는 CAD 앞에
              표시됩니다.
            </HelpTooltip>
          </div>
          <Badge variant="outline">
            {result?.receiver_grids.length ?? 0}
          </Badge>
        </div>
        <div className="grid grid-cols-3 gap-1">
          {(['off', 'physical', 'xray'] as const).map((mode) => (
            <Button
              key={mode}
              size="xs"
              variant={heatmap3dMode === mode ? 'default' : 'outline'}
              disabled={!result || result.receiver_grids.length === 0}
              aria-label={`3D Heatmap ${mode === 'off' ? 'OFF' : mode === 'physical' ? 'Physical' : 'X-ray'}`}
              aria-pressed={heatmap3dMode === mode}
              onClick={() => actions.setReceiverHeatmap3dMode(mode)}
            >
              {mode === 'off' ? 'OFF' : mode === 'physical' ? 'Physical' : 'X-ray'}
            </Button>
          ))}
        </div>
        {heatmap3dMode !== 'off' ? (
          <div className="space-y-2 rounded-lg border border-border bg-background/35 p-2">
            <div className="grid grid-cols-3 gap-1">
              {(['auto', 'compare', 'customize'] as const).map((mode) => (
                <Button
                  key={mode}
                  size="xs"
                  variant={heatmap3dScaleMode === mode ? 'secondary' : 'outline'}
                  aria-label={`3D Heatmap scale ${mode}`}
                  aria-pressed={heatmap3dScaleMode === mode}
                  onClick={() => actions.setReceiverHeatmap3dScaleMode(mode)}
                >
                  {mode === 'auto' ? 'Auto' : mode === 'compare' ? 'Compare' : 'Customize'}
                </Button>
              ))}
            </div>
            {heatmap3dScaleMode === 'customize' ? (
              <div className="grid grid-cols-2 gap-1.5">
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>Min nit</span>
                  <input
                    type="number"
                    min={0}
                    step="any"
                    value={heatmap3dCustomMinNit}
                    onChange={(event) =>
                      actions.setReceiverHeatmap3dCustomScale(
                        Number(event.currentTarget.value),
                        heatmap3dCustomMaxNit,
                      )
                    }
                    className="h-7 w-full rounded-md border border-input bg-background px-2 text-right text-sm"
                  />
                </label>
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>Max nit</span>
                  <input
                    type="number"
                    min={0}
                    step="any"
                    value={heatmap3dCustomMaxNit}
                    onChange={(event) =>
                      actions.setReceiverHeatmap3dCustomScale(
                        heatmap3dCustomMinNit,
                        Number(event.currentTarget.value),
                      )
                    }
                    className="h-7 w-full rounded-md border border-input bg-background px-2 text-right text-sm"
                  />
                </label>
              </div>
            ) : null}
            <label className="block space-y-1 text-xs text-muted-foreground">
              <span>Opacity · {Math.round(heatmap3dOpacity * 100)}%</span>
              <input
                type="range"
                min={5}
                max={100}
                step={1}
                value={Math.round(heatmap3dOpacity * 100)}
                onChange={(event) =>
                  actions.setReceiverHeatmap3dOpacity(
                    Number(event.currentTarget.value) / 100,
                  )
                }
                className="w-full accent-primary"
                aria-label="3D Heatmap opacity"
              />
            </label>
            <div className="grid grid-cols-2 gap-1">
              {(['pixel', 'smooth'] as const).map((mode) => (
                <Button
                  key={mode}
                  size="xs"
                  variant={heatmap3dInterpolation === mode ? 'secondary' : 'outline'}
                  aria-label={`3D Heatmap ${mode}`}
                  aria-pressed={heatmap3dInterpolation === mode}
                  onClick={() => actions.setReceiverHeatmap3dInterpolation(mode)}
                >
                  {mode === 'pixel' ? 'Pixel' : 'Smooth'}
                </Button>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Route className="size-3.5" />
            3D Ray path 표시
            <HelpTooltip label="3D Ray path 표시 도움말">
              3D Viewer에 표시할 ray 종류를 선택합니다. 저장된 ray 중
              Max stored paths 설정 내에서 실제 저장된 것만 대상이며,
              통계(Total Rays·Receiver Hits)에는 영향을 주지 않습니다.
            </HelpTooltip>
          </div>
          <Badge variant="outline">
            {visiblePathCount}/{paths.length}
          </Badge>
        </div>
        <div className="rounded-lg border border-border bg-background/35 p-2">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <label htmlFor="ray-path-display-limit" className="text-sm font-medium">
              표시 경로 수 · 현재 {displayedPathCount.toLocaleString()}
            </label>
            <input
              id="ray-path-display-limit"
              type="number"
              min={0}
              max={Math.max(paths.length, 0)}
              step={100}
              value={Math.min(displayLimit, paths.length)}
              disabled={!result || paths.length === 0}
              onChange={(event) =>
                actions.setRayPathDisplayLimit(
                  Math.min(paths.length, Number(event.currentTarget.value) || 0),
                )
              }
              className="h-7 w-24 rounded-md border border-input bg-background px-2 text-right text-sm"
            />
          </div>
          <input
            type="range"
            min={0}
            max={Math.max(paths.length, 1)}
            step={paths.length > 5_000 ? 100 : paths.length > 1_000 ? 50 : 10}
            value={Math.min(displayLimit, paths.length)}
            disabled={!result || paths.length === 0}
            onChange={(event) =>
              actions.setRayPathDisplayLimit(Number(event.currentTarget.value))
            }
            className="w-full accent-primary"
            aria-label="3D Viewer 표시 경로 수"
          />
          <div className="mt-1 flex justify-between text-xs text-muted-foreground">
            <span>0</span>
            <span>저장 {paths.length.toLocaleString()}</span>
          </div>
        </div>
        <div className="space-y-1">
          {rayPathFilterOrder.map((filter, index) => {
            const style = rayPathStyles[filter]
            return (
              <label
                key={filter}
                className={`flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm ${
                  index < 2
                    ? 'border-primary/15 bg-primary/5'
                    : 'border-border bg-background/35'
                }`}
              >
                <input
                  type="checkbox"
                  checked={filters[filter]}
                  disabled={!result || paths.length === 0}
                  onChange={(event) =>
                    actions.setRayPathDisplayFilter(
                      filter,
                      event.currentTarget.checked,
                    )
                  }
                />
                <span
                  className="size-2.5 rounded-full"
                  style={{
                    backgroundColor: `#${style.color
                      .toString(16)
                      .padStart(6, '0')}`,
                  }}
                />
                <span>{style.label}</span>
              </label>
            )
          })}
        </div>
        <div className="grid grid-cols-3 gap-1">
          <Button
            size="xs"
            variant="outline"
            disabled={!result || paths.length === 0}
            onClick={() =>
              actions.setRayPathDisplayFilters({
                ...allFilters(false),
                receiver_direct: true,
                receiver_reflected: true,
              })
            }
          >
            <Eye />
            Receiver
          </Button>
          <Button
            size="xs"
            variant="outline"
            disabled={!result || paths.length === 0}
            onClick={() =>
              actions.setRayPathDisplayFilters(allFilters(true))
            }
          >
            All on
          </Button>
          <Button
            size="xs"
            variant="outline"
            disabled={!result || paths.length === 0}
            onClick={() =>
              actions.setRayPathDisplayFilters(allFilters(false))
            }
          >
            All off
          </Button>
        </div>
        <p className="text-xs leading-4 text-muted-foreground">
          표시 수와 필터는 재계산 없이 Viewer overlay에 즉시 반영됩니다.
          표시는 필터에 맞는 저장 경로 전체에서 균등하게 선택됩니다.
        </p>
      </section>
    </div>
  )
}
