import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import type {
  RayTraceResult,
  ReceiverGrid,
} from '@/api'
import {
  Activity,
  Aperture,
  Grip,
  Layers3,
  Move,
  X,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type ResultTab =
  | 'summary'
  | 'surface'
  | 'bounce'
  | 'receiver'

interface RayTraceResultWindowProps {
  open: boolean
  result: RayTraceResult | null
  onOpenChange(open: boolean): void
}

interface WindowFrame {
  x: number
  y: number
  width: number
  height: number
}

interface PointerOperation {
  kind: 'drag' | 'resize'
  startX: number
  startY: number
  frame: WindowFrame
}

function numeric(value: unknown): number {
  return Number.isFinite(Number(value)) ? Number(value) : 0
}

function objectValue(
  source: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const value = source[key]
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {}
}

function metricGroup(result: RayTraceResult, key: string) {
  return objectValue(result.metrics, key)
}

function formatMetric(value: unknown, digits = 3) {
  const number = numeric(value)
  const magnitude = Math.abs(number)
  if (magnitude > 0 && (magnitude >= 10_000 || magnitude < 0.001)) {
    return number.toExponential(3)
  }
  return number.toFixed(digits)
}

function ReceiverHeatmap({ grid }: { grid: ReceiverGrid }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return
    const columns = Math.max(1, grid.resolution[0])
    const rows = Math.max(1, grid.resolution[1])
    canvas.width = columns
    canvas.height = rows
    const values = grid.flux_lumen
      .flat()
      .map((value) => Math.max(0, numeric(value)))
    const peak = Math.max(...values, 0)
    const image = context.createImageData(columns, rows)
    for (let index = 0; index < columns * rows; index += 1) {
      const normalized =
        peak > 0 ? Math.sqrt((values[index] || 0) / peak) : 0
      const pixel = index * 4
      image.data[pixel] = Math.round(8 + normalized * 247)
      image.data[pixel + 1] = Math.round(18 + normalized * 195)
      image.data[pixel + 2] = Math.round(50 + (1 - normalized) * 180)
      image.data[pixel + 3] = 255
    }
    context.putImageData(image, 0, 0)
  }, [grid])

  return (
    <canvas
      ref={canvasRef}
      aria-label={`${grid.receiver_id} flux heatmap`}
      className="mt-2 h-36 w-full rounded-lg border border-border bg-[#020617] [image-rendering:pixelated]"
    />
  )
}

function Stat({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="rounded-lg border border-border bg-background/45 p-2.5">
      <div className="text-[0.62rem] text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-semibold">{value}</div>
    </div>
  )
}

export function RayTraceResultWindow({
  open,
  result,
  onOpenChange,
}: RayTraceResultWindowProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const operationRef = useRef<PointerOperation | null>(null)
  const [tab, setTab] = useState<ResultTab>('summary')
  const [frame, setFrame] = useState<WindowFrame>({
    x: 24,
    y: 58,
    width: 760,
    height: 560,
  })

  useEffect(() => {
    if (!open) return
    const parent = rootRef.current?.parentElement
    if (!parent) return
    const bounds = parent.getBoundingClientRect()
    setFrame((current) => ({
      x: Math.max(12, Math.min(current.x, bounds.width - 340)),
      y: Math.max(48, Math.min(current.y, bounds.height - 260)),
      width: Math.min(current.width, Math.max(320, bounds.width - 24)),
      height: Math.min(
        current.height,
        Math.max(260, bounds.height - 64),
      ),
    }))
  }, [open])

  useEffect(() => {
    if (!open) return
    const move = (event: PointerEvent) => {
      const operation = operationRef.current
      const parent = rootRef.current?.parentElement
      if (!operation || !parent) return
      const bounds = parent.getBoundingClientRect()
      const deltaX = event.clientX - operation.startX
      const deltaY = event.clientY - operation.startY
      if (operation.kind === 'drag') {
        setFrame({
          ...operation.frame,
          x: Math.max(
            8,
            Math.min(
              operation.frame.x + deltaX,
              bounds.width - operation.frame.width - 8,
            ),
          ),
          y: Math.max(
            48,
            Math.min(
              operation.frame.y + deltaY,
              bounds.height - operation.frame.height - 8,
            ),
          ),
        })
      } else {
        setFrame({
          ...operation.frame,
          width: Math.max(
            320,
            Math.min(
              operation.frame.width + deltaX,
              bounds.width - operation.frame.x - 8,
            ),
          ),
          height: Math.max(
            260,
            Math.min(
              operation.frame.height + deltaY,
              bounds.height - operation.frame.y - 8,
            ),
          ),
        })
      }
    }
    const stop = () => {
      operationRef.current = null
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [open])

  if (!open || !result) return null

  const begin = (
    event: ReactPointerEvent,
    kind: PointerOperation['kind'],
  ) => {
    operationRef.current = {
      kind,
      startX: event.clientX,
      startY: event.clientY,
      frame,
    }
    event.preventDefault()
  }
  const contribution = result.contribution_summary
  const performance = metricGroup(result, '_performance_summary')
  const reflection = metricGroup(result, '_reflection_summary')
  const optical = metricGroup(result, '_optical_summary')
  const componentRows = Object.entries(contribution.components)
    .map(([name, value]) => ({
      name,
      values:
        value && typeof value === 'object'
          ? (value as Record<string, unknown>)
          : {},
    }))
    .sort(
      (left, right) =>
        numeric(right.values.receiver_flux_lumen) -
        numeric(left.values.receiver_flux_lumen),
    )
    .slice(0, 12)
  const tabs: { id: ResultTab; label: string; icon: typeof Activity }[] = [
    { id: 'summary', label: 'Ray summary', icon: Activity },
    { id: 'surface', label: 'Surface optical', icon: Layers3 },
    { id: 'bounce', label: 'Multi-bounce', icon: Move },
    { id: 'receiver', label: 'Receiver', icon: Aperture },
  ]
  const hitRatio =
    result.total_rays > 0
      ? result.receiver_hit_count / result.total_rays
      : 0

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-label="Ray Tracing Analysis Result"
      className="absolute z-30 flex overflow-hidden rounded-xl border border-border bg-background/96 shadow-2xl shadow-black/55 backdrop-blur-xl"
      style={{
        left: frame.x,
        top: frame.y,
        width: frame.width,
        height: frame.height,
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <div
          className="flex cursor-move items-center justify-between gap-3 border-b border-border bg-muted/25 px-3 py-2.5"
          onPointerDown={(event) => {
            if (
              event.target instanceof HTMLElement &&
              event.target.closest('button')
            ) {
              return
            }
            begin(event, 'drag')
          }}
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">
              Ray Tracing Analysis Result
            </div>
            <div className="text-[0.62rem] text-muted-foreground">
              {result.run_id} · {result.runtime_sec.toFixed(3)} s
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge className="bg-primary/12 text-primary">Complete</Badge>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="Close result window"
              onClick={() => onOpenChange(false)}
            >
              <X />
            </Button>
          </div>
        </div>

        <div
          className="flex gap-1 overflow-x-auto border-b border-border bg-background/80 p-1.5"
          role="tablist"
          aria-label="Result categories"
        >
          {tabs.map(({ id, label, icon: Icon }) => (
            <Button
              key={id}
              role="tab"
              aria-selected={tab === id}
              size="xs"
              variant={tab === id ? 'secondary' : 'ghost'}
              onClick={() => setTab(id)}
            >
              <Icon />
              {label}
            </Button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {tab === 'summary' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label="Total rays"
                  value={result.total_rays.toLocaleString()}
                />
                <Stat
                  label="Receiver hits"
                  value={result.receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Hit ratio"
                  value={`${(hitRatio * 100).toFixed(3)}%`}
                />
                <Stat
                  label="Surface interactions"
                  value={result.surface_hit_count.toLocaleString()}
                />
                <Stat
                  label="Direct flux"
                  value={`${formatMetric(
                    contribution.direct_receiver_flux_lumen,
                  )} lm`}
                />
                <Stat
                  label="Reflected flux"
                  value={`${formatMetric(
                    contribution.reflected_receiver_flux_lumen,
                  )} lm`}
                />
                <Stat
                  label="Ray rate"
                  value={`${Math.round(
                    numeric(performance.rays_per_sec),
                  ).toLocaleString()} /s`}
                />
                <Stat
                  label="Stored paths"
                  value={result.stored_paths.length.toLocaleString()}
                />
              </div>
              <p className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-5 text-muted-foreground">
                Intersection backend ·{' '}
                {String(
                  performance.intersection_backend ??
                    result.config.intersection_backend,
                ).toUpperCase()}
                {' · '}BVH build{' '}
                {formatMetric(performance.bvh_build_sec)} s
              </p>
            </div>
          ) : null}

          {tab === 'surface' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Stat
                  label="Surface hits"
                  value={Math.round(
                    numeric(optical.surface_hit_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Unassigned"
                  value={Math.round(
                    numeric(optical.unassigned_surface_hit_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Components"
                  value={componentRows.length.toLocaleString()}
                />
              </div>
              {componentRows.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                  Detailed contribution mode에서 component 기여도가
                  표시됩니다.
                </p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
                  {componentRows.map(({ name, values }) => (
                    <div
                      key={name}
                      className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-border px-3 py-2 text-xs last:border-b-0"
                    >
                      <span className="truncate font-medium">{name}</span>
                      <span className="text-muted-foreground">
                        {Math.round(
                          numeric(values.receiver_hit_count),
                        ).toLocaleString()}{' '}
                        hits
                      </span>
                      <span className="font-mono">
                        {formatMetric(values.receiver_flux_lumen)} lm
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          {tab === 'bounce' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label="Direct hits"
                  value={contribution.direct_receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Reflected hits"
                  value={contribution.reflected_receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Blocked"
                  value={Math.round(
                    numeric(reflection.reflection_blocked_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Escaped"
                  value={Math.round(
                    numeric(reflection.reflection_escaped_count),
                  ).toLocaleString()}
                />
              </div>
              <div className="overflow-hidden rounded-lg border border-border">
                {(['specular', 'gaussian', 'lambertian'] as const).map(
                  (name) => {
                    const values = objectValue(
                      contribution.lobes,
                      name,
                    )
                    return (
                      <div
                        key={name}
                        className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border px-3 py-2 text-xs capitalize last:border-b-0"
                      >
                        <span className="font-medium">{name}</span>
                        <span className="text-muted-foreground">
                          {Math.round(
                            numeric(values.receiver_hit_count),
                          ).toLocaleString()}{' '}
                          hits
                        </span>
                        <span className="font-mono">
                          {formatMetric(values.receiver_flux_lumen)} lm
                        </span>
                      </div>
                    )
                  },
                )}
              </div>
            </div>
          ) : null}

          {tab === 'receiver' ? (
            <div className="grid gap-3 lg:grid-cols-2">
              {result.receivers.map((receiver) => {
                const values = objectValue(
                  result.metrics,
                  receiver.receiver_id,
                )
                const grid = result.receiver_grids.find(
                  (candidate) =>
                    candidate.receiver_id === receiver.receiver_id,
                )
                return (
                  <section
                    key={receiver.receiver_id}
                    className="rounded-lg border border-border bg-background/40 p-3"
                  >
                    <div className="text-xs font-semibold">
                      {receiver.display_name || receiver.receiver_id}
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1.5">
                      <Stat
                        label="Peak nit_est"
                        value={formatMetric(values.peak_nit_est)}
                      />
                      <Stat
                        label="Mean nit_est"
                        value={formatMetric(values.mean_nit_est)}
                      />
                      <Stat
                        label="Flux"
                        value={`${formatMetric(
                          values.total_flux_lumen,
                        )} lm`}
                      />
                    </div>
                    {grid ? <ReceiverHeatmap grid={grid} /> : null}
                  </section>
                )
              })}
            </div>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        aria-label="Resize result window"
        className="absolute right-0 bottom-0 flex size-6 cursor-nwse-resize items-center justify-center text-muted-foreground hover:text-foreground"
        onPointerDown={(event) => begin(event, 'resize')}
      >
        <Grip className="size-3.5" />
      </button>
    </div>
  )
}
