import { Cpu, Monitor } from 'lucide-react'

import type { GpuCudaStatus, RayTraceConfigRequest } from '@/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { GpuCudaHelpDialog } from './gpu-cuda-help-dialog'
import { CpuReadiness, GpuCudaReadiness } from './gpu-cuda-readiness'

type ComputeBackend = RayTraceConfigRequest['compute_backend']

interface ComputeDeviceSelectorProps {
  value: ComputeBackend
  disabled: boolean
  status?: GpuCudaStatus
  pending: boolean
  failed: boolean
  onChange(value: ComputeBackend): void
  onRetry(): void
}

const deviceButtonClassName =
  'h-12 min-w-0 w-full gap-2 text-sm font-semibold shadow-none'

/**
 * The primary compute choice stays structurally identical while its value
 * changes. Readiness details render in the fixed-height row below so switching
 * CPU/GPU never replaces the surrounding Ray Tracing controls.
 */
export function ComputeDeviceSelector({
  value,
  disabled,
  status,
  pending,
  failed,
  onChange,
  onRetry,
}: ComputeDeviceSelectorProps) {
  return (
    <section
      aria-labelledby="compute-device-title"
      className="min-w-0 space-y-2.5 rounded-xl border border-primary/20 bg-primary/5 p-3"
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <h3
          id="compute-device-title"
          className="min-w-0 flex-1 text-sm font-semibold tracking-wide text-foreground"
        >
          연산 장치
        </h3>
        <GpuCudaHelpDialog />
      </div>

      <div
        role="group"
        aria-label="연산 장치 선택"
        className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(8.25rem,100%),1fr))] gap-2"
      >
        <Button
          type="button"
          variant={value === 'cpu' ? 'default' : 'outline'}
          className={cn(
            deviceButtonClassName,
            value === 'cpu' && 'ring-2 ring-primary/20',
          )}
          aria-pressed={value === 'cpu'}
          aria-label="CPU로 연산"
          disabled={disabled}
          onClick={() => onChange('cpu')}
        >
          <Cpu className="size-4" aria-hidden="true" />
          <span className="truncate">CPU</span>
        </Button>
        <Button
          type="button"
          variant={value === 'gpu_cuda' ? 'default' : 'outline'}
          className={cn(
            deviceButtonClassName,
            value === 'gpu_cuda' && 'ring-2 ring-primary/20',
          )}
          aria-pressed={value === 'gpu_cuda'}
          aria-label="NVIDIA GPU로 연산"
          disabled={disabled}
          onClick={() => onChange('gpu_cuda')}
        >
          <Monitor className="size-4" aria-hidden="true" />
          <span className="truncate">NVIDIA GPU</span>
        </Button>
      </div>

      {value === 'gpu_cuda' ? (
        <GpuCudaReadiness
          status={status}
          pending={pending}
          failed={failed}
          onRetry={onRetry}
        />
      ) : (
        <CpuReadiness />
      )}
    </section>
  )
}
