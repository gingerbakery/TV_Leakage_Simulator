import { CheckCircle2, CircleAlert, Cpu, Gauge, TriangleAlert } from 'lucide-react'

import type { ComputeBackend } from '@/api'
import { Badge } from '@/components/ui/badge'
import { resolveComputeExecution } from './compute-execution-model'

export function ComputeExecutionStatus({
  configuredBackend,
  performance,
}: {
  configuredBackend: ComputeBackend
  performance: Record<string, unknown>
}) {
  const summary = resolveComputeExecution(configuredBackend, performance)
  const warning = summary.state === 'gpu-fallback' || summary.state === 'gpu-zero'
  const active = summary.state === 'gpu-active' || summary.state === 'gpu-mixed'
  const Icon = active
    ? CheckCircle2
    : warning
      ? TriangleAlert
      : summary.state === 'cpu'
        ? Cpu
        : CircleAlert

  return (
    <section
      aria-label="Compute execution status"
      role={warning ? 'alert' : 'status'}
      className={
        active
          ? 'rounded-lg border border-emerald-500/40 bg-emerald-500/8 p-3'
          : warning
            ? 'rounded-lg border border-orange-400/50 bg-orange-500/8 p-3'
            : 'rounded-lg border border-border bg-muted/20 p-3'
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Icon
          className={active ? 'size-5 text-emerald-600' : warning ? 'size-5 text-orange-600' : 'size-5 text-primary'}
          aria-hidden="true"
        />
        <span className="font-semibold">Compute device · {summary.title}</span>
        <Badge variant={active ? 'default' : warning ? 'destructive' : 'outline'}>
          {summary.requested === 'gpu_cuda' ? 'GPU requested' : 'CPU requested'}
        </Badge>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        {summary.deviceName ? <Badge variant="outline">{summary.deviceName}</Badge> : null}
        <Badge variant="outline">Provider · {summary.provider}</Badge>
        {summary.requested === 'gpu_cuda' ? (
          <Badge variant={summary.gpuSuccesses > 0 ? 'secondary' : 'destructive'}>
            CUDA batches · {summary.gpuSuccesses}/{summary.gpuAttempts}
          </Badge>
        ) : null}
        {summary.cpuSmallWaveSuccesses > 0 ? (
          <Badge variant="outline">CPU small waves · {summary.cpuSmallWaveSuccesses}</Badge>
        ) : null}
      </div>
      {summary.reason ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-orange-800 dark:text-orange-200">
          <Gauge className="size-3.5" aria-hidden="true" />
          {summary.reason}
        </p>
      ) : null}
    </section>
  )
}
