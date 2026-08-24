import type { GpuCudaStatus } from '@/api'
import {
  CheckCircle2,
  CircleAlert,
  Cpu,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react'

import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'

import { isGpuCudaStatusReady } from './gpu-cuda-status'

function gpuCudaReasonLabel(reasonCode: string | null | undefined) {
  switch (reasonCode) {
    case 'numba_not_installed':
    case 'numba_import_failed':
    case 'numba_cuda_import_failed':
      return 'GPU 실행 구성요소가 준비되지 않음'
    case 'cuda_driver_unavailable':
      return 'NVIDIA 드라이버를 사용할 수 없음'
    case 'cuda_toolkit_not_found':
      return 'CUDA Toolkit을 찾을 수 없음'
    case 'cuda_runtime_unavailable':
      return 'GPU 실행 환경을 사용할 수 없음'
    case 'cuda_device_query_failed':
      return 'NVIDIA GPU 정보를 읽지 못함'
    case 'cuda_preflight_kernel_failed':
      return 'GPU 자체 검사 실패'
    case 'gpu_preflight_scope_incompatible':
      return 'GPU 검사 버전이 호환되지 않음'
    case null:
    case undefined:
    case '':
      return '원인을 확인할 수 없음'
    default:
      return reasonCode.replaceAll('_', ' ')
  }
}

interface GpuCudaReadinessProps {
  status?: GpuCudaStatus
  pending: boolean
  failed: boolean
  onRetry(): void
}

const readinessRowClassName =
  'grid h-14 min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] grid-rows-2 items-center gap-x-2 rounded-lg border px-2.5 py-1.5 text-xs'
const readinessPrimaryClassName =
  'col-start-2 row-start-1 min-w-0 truncate font-semibold leading-4'
const readinessDetailClassName =
  'col-start-2 col-end-4 row-start-2 min-w-0 truncate leading-4'
const readinessActionsClassName =
  'col-start-3 row-start-1 flex h-6 w-14 items-center justify-end gap-1'

function GpuTechnicalDetails({
  status,
  failureReason,
}: {
  status: GpuCudaStatus
  failureReason?: string
}) {
  return (
    <HelpTooltip label="GPU 상세 정보">
      <span className="block space-y-0.5">
        <span className="block font-semibold">GPU 검증 상세</span>
        <span className="block">
          장치 · {status.device_name ?? '확인되지 않음'}
        </span>
        {failureReason ? (
          <span className="block">상태 · {failureReason}</span>
        ) : (
          <span className="block">상태 · 준비 완료</span>
        )}
        <span className="block">
          Compute capability · {status.compute_capability ?? '확인되지 않음'}
        </span>
        <span className="block">
          FP64 · {status.strict_float64 ? 'Strict FP64' : '확인 필요'}
        </span>
        <span className="block">
          Production kernel · {status.kernel_executed && status.kernel_verified ? '검증됨' : '검증 필요'}
        </span>
        <span className="block">
          Scope · {status.preflight_scope ?? '확인되지 않음'}
        </span>
        <span className="block">
          Provider · {status.provider_contract ?? '확인되지 않음'}
        </span>
        <span className="block">
          Numba · {status.numba_version ?? '확인되지 않음'}
        </span>
        {status.reason_code ? (
          <span className="block">상태 코드 · {status.reason_code}</span>
        ) : null}
        {status.toolkit_layout ? (
          <span className="block">Toolkit · {status.toolkit_layout}</span>
        ) : null}
      </span>
    </HelpTooltip>
  )
}

export function GpuCudaReadiness({
  status,
  pending,
  failed,
  onRetry,
}: GpuCudaReadinessProps) {
  if (pending) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label="Selected compute device"
        data-testid="compute-device-status"
        className={`${readinessRowClassName} border-primary/25 bg-background/70`}
      >
        <LoaderCircle className="row-span-2 size-4 animate-spin self-center text-primary" aria-hidden="true" />
        <span className={readinessPrimaryClassName}>
          GPU 준비 상태 확인 중
        </span>
        <span className={readinessDetailClassName}>
          실행 가능 여부를 점검합니다.
        </span>
        <span className={readinessActionsClassName} aria-hidden="true" />
      </div>
    )
  }

  if (failed || !status) {
    return (
      <div
        role="alert"
        aria-label="Selected compute device"
        data-testid="compute-device-status"
        className={`${readinessRowClassName} border-destructive/35 bg-destructive/8 text-destructive`}
      >
        <CircleAlert className="row-span-2 size-4 self-center" aria-hidden="true" />
        <span className={readinessPrimaryClassName}>
          {'GPU 사용 불가 · '}
        </span>
        <span className={readinessDetailClassName}>상태 확인 실패</span>
        <span className={readinessActionsClassName}>
          <Button size="icon-xs" variant="ghost" aria-label="GPU 준비 상태 다시 확인" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
          </Button>
        </span>
      </div>
    )
  }

  if (!isGpuCudaStatusReady(status)) {
    const productionRayBvhVerified =
      status.preflight_scope === 'production_ray_bvh' &&
      status.provider_contract === 'strict_float64_bvh_v1'
    const unavailableReason =
      status.reason_code ??
      (!productionRayBvhVerified
        ? 'gpu_preflight_scope_incompatible'
        : !status.kernel_executed || !status.kernel_verified || !status.strict_float64
          ? 'cuda_preflight_kernel_failed'
          : null)
    return (
      <div
        role="alert"
        aria-label="Selected compute device"
        data-testid="compute-device-status"
        className={`${readinessRowClassName} border-orange-400/45 bg-orange-500/8 text-orange-700 dark:text-orange-300`}
      >
        <TriangleAlert className="row-span-2 size-4 self-center" aria-hidden="true" />
        <span
          className={readinessPrimaryClassName}
          title={`GPU 사용 불가 · ${gpuCudaReasonLabel(unavailableReason)}`}
        >
          {'GPU 사용 불가 · '}
        </span>
        <span
          className={readinessDetailClassName}
          title={gpuCudaReasonLabel(unavailableReason)}
        >
          {gpuCudaReasonLabel(unavailableReason)}
        </span>
        <span className={readinessActionsClassName}>
          <GpuTechnicalDetails
            status={status}
            failureReason={gpuCudaReasonLabel(unavailableReason)}
          />
          <Button size="icon-xs" variant="ghost" aria-label="GPU 준비 상태 다시 확인" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
          </Button>
        </span>
      </div>
    )
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Selected compute device"
      data-testid="compute-device-status"
      className={`${readinessRowClassName} border-emerald-500/35 bg-emerald-500/8 text-emerald-700 dark:text-emerald-300`}
    >
      <CheckCircle2 className="row-span-2 size-4 self-center" aria-hidden="true" />
      <span
        className={readinessPrimaryClassName}
        title={`준비 완료 · ${status.device_name ?? 'NVIDIA GPU'}`}
      >
        {'준비 완료 · '}
      </span>
      <span
        className={readinessDetailClassName}
        data-status-detail="device-name"
        title={status.device_name ?? 'NVIDIA GPU'}
      >
        {status.device_name ?? 'NVIDIA GPU'}
      </span>
      <span className={readinessActionsClassName}>
        <GpuTechnicalDetails status={status} />
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="GPU 준비 상태 다시 확인"
          onClick={onRetry}
        >
          <RefreshCw aria-hidden="true" />
        </Button>
      </span>
    </div>
  )
}

export function CpuReadiness() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Selected compute device"
      data-testid="compute-device-status"
      className={`${readinessRowClassName} border-border bg-background/70 text-muted-foreground`}
    >
      <Cpu className="row-span-2 size-4 self-center" aria-hidden="true" />
      <span className={readinessPrimaryClassName}>
        <strong className="text-foreground">CPU 선택됨</strong>
        {' · '}
      </span>
      <span className={readinessDetailClassName}>호환 모드</span>
      <span className={readinessActionsClassName} aria-hidden="true" />
    </div>
  )
}
