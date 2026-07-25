import type { RayTraceJob } from '@/api'
import {
  Activity,
  BarChart3,
  Eye,
  Route,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
  onOpenAnalysis(): void
}

const allFilters = (visible: boolean): RayPathDisplayFilters => ({
  receiver_direct: visible,
  receiver_reflected: visible,
  direct: visible,
  specular: visible,
  lambertian: visible,
  gaussian: visible,
})

export function ResultPanel({
  job,
  onOpenAnalysis,
}: ResultPanelProps) {
  const filters = useWorkspaceStore(
    workspaceSelectors.rayPathDisplayFilters,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const result = job?.status === 'completed' ? job.result : null
  const paths = result?.stored_paths ?? []
  const visiblePathCount = paths.reduce(
    (count, path) =>
      count + Number(isRayPathVisible(path, filters)),
    0,
  )
  const hitRatio =
    result && result.total_rays > 0
      ? result.receiver_hit_count / result.total_rays
      : 0

  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
            <Activity className="size-3.5" />
            Result status
          </div>
          <Badge
            variant="outline"
            className={
              job?.status === 'completed'
                ? 'border-primary/25 bg-primary/8 text-primary'
                : undefined
            }
          >
            {job?.status ?? 'not run'}
          </Badge>
        </div>
        {!job ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-[0.68rem] leading-4 text-muted-foreground">
            Ray tracing을 실행하면 결과와 저장 경로가 표시됩니다.
          </p>
        ) : job.status === 'failed' ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/8 p-2 text-[0.68rem] text-destructive">
            {job.error}
          </p>
        ) : job.status !== 'completed' ? (
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-[0.68rem]">
            <div className="flex justify-between">
              <span className="font-semibold">{job.phase}</span>
              <span>{(job.progress * 100).toFixed(1)}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${job.progress * 100}%` }}
              />
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-1.5">
              {[
                ['Total rays', result!.total_rays.toLocaleString()],
                [
                  'Receiver hits',
                  result!.receiver_hit_count.toLocaleString(),
                ],
                ['Hit ratio', `${(hitRatio * 100).toFixed(3)}%`],
                ['Runtime', `${result!.runtime_sec.toFixed(3)} s`],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-lg border border-border bg-background/40 p-2"
                >
                  <div className="text-[0.58rem] text-muted-foreground">
                    {label}
                  </div>
                  <div className="mt-0.5 text-xs font-semibold">{value}</div>
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
        )}
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
            <Route className="size-3.5" />
            3D Ray path 표시
          </div>
          <Badge variant="outline">
            {visiblePathCount}/{paths.length}
          </Badge>
        </div>
        <div className="space-y-1">
          {rayPathFilterOrder.map((filter, index) => {
            const style = rayPathStyles[filter]
            return (
              <label
                key={filter}
                className={`flex items-center gap-2 rounded-md border px-2 py-1.5 text-[0.68rem] ${
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
        <p className="text-[0.62rem] leading-4 text-muted-foreground">
          필터 변경은 재계산 없이 Viewer overlay에 즉시 반영됩니다.
        </p>
      </section>
    </div>
  )
}
