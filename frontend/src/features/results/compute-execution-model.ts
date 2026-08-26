import type { ComputeBackend } from '@/api'

export type ComputeExecutionState =
  | 'cpu'
  | 'gpu-active'
  | 'gpu-mixed'
  | 'gpu-fallback'
  | 'gpu-zero'

export interface ComputeExecutionSummary {
  state: ComputeExecutionState
  title: string
  requested: ComputeBackend
  provider: string
  deviceName: string | null
  gpuAttempts: number
  gpuSuccesses: number
  cpuSmallWaveSuccesses: number
  accuracyContract: string | null
  accuracyParityVerified: boolean
  reason: string | null
}

function count(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function readableReason(reason: string | null): string | null {
  if (!reason) return null
  const labels: Record<string, string> = {
    cuda_driver_unavailable: 'NVIDIA 드라이버 사용 불가',
    cuda_toolkit_not_found: 'CUDA Toolkit 누락',
    cuda_runtime_unavailable: 'CUDA runtime 사용 불가',
    cuda_preflight_kernel_failed: 'CUDA 검증 kernel 실행 실패',
    gpu_cuda_requires_bvh: 'BVH 가속 구조 필요',
    numba_not_installed: 'GPU Python 패키지 누락',
    numba_cuda_import_failed: 'Numba CUDA 로드 실패',
    gpu_cuda_scalar_uses_python_cpu:
      '이 Emitter 형식은 CUDA batch를 지원하지 않아 CPU scalar로 실행됨',
    gpu_cuda_below_hybrid_threshold:
      '작은 wave만 있어 CUDA 대신 CPU 최적화 경로로 실행됨',
  }
  return labels[reason] ?? reason.replaceAll('_', ' ')
}

export function resolveComputeExecution(
  configuredBackend: ComputeBackend,
  performance: Record<string, unknown>,
): ComputeExecutionSummary {
  const explicitState = text(performance.compute_execution_state)
  const requested =
    explicitState === 'cpu'
      ? 'cpu'
      : performance.compute_backend === 'gpu_cuda' ||
          configuredBackend === 'gpu_cuda'
        ? 'gpu_cuda'
        : 'cpu'
  const provider = text(performance.intersection_provider) ?? 'python_cpu'
  const deviceName = text(performance.gpu_cuda_device_name)
  const gpuAttempts = count(performance.gpu_cuda_gpu_attempt_count)
  const gpuSuccesses = count(performance.gpu_cuda_gpu_success_count)
  const cpuSmallWaveSuccesses = count(
    performance.gpu_cuda_hybrid_cpu_success_count,
  )
  const gpuUsed = performance.gpu_cuda_used === true || gpuSuccesses > 0
  const reason =
    text(performance.compute_execution_reason) ??
    text(performance.intersection_fallback_reason) ??
    text(performance.intersection_provider_unavailable_reason)
  const accuracyContract = text(performance.monte_carlo_contract)
  const accuracyParityVerified =
    accuracyContract === 'cpu_gpu_deterministic_batch_v1'

  if (explicitState === 'cpu' || requested === 'cpu') {
    return {
      state: 'cpu',
      title: 'CPU 실행',
      requested,
      provider,
      deviceName: null,
      gpuAttempts,
      gpuSuccesses,
      cpuSmallWaveSuccesses,
      accuracyContract,
      accuracyParityVerified,
      reason: null,
    }
  }

  if (
    explicitState === 'gpu_active' ||
    explicitState === 'gpu_mixed' ||
    gpuUsed
  ) {
    const mixed =
      explicitState === 'gpu_mixed' ||
      (explicitState !== 'gpu_active' &&
        (cpuSmallWaveSuccesses > 0 || provider === 'mixed'))
    return {
      state: mixed ? 'gpu-mixed' : 'gpu-active',
      title: mixed ? 'GPU 활성 · CPU 보조' : 'GPU 활성',
      requested,
      provider,
      deviceName,
      gpuAttempts,
      gpuSuccesses,
      cpuSmallWaveSuccesses,
      accuracyContract,
      accuracyParityVerified,
      reason: readableReason(reason),
    }
  }

  if (
    explicitState === 'gpu_requested_cpu_only' ||
    reason ||
    provider.includes('cpu')
  ) {
    return {
      state: 'gpu-fallback',
      title: 'CPU 대체 실행 · GPU 미사용',
      requested,
      provider,
      deviceName,
      gpuAttempts,
      gpuSuccesses,
      cpuSmallWaveSuccesses,
      accuracyContract,
      accuracyParityVerified,
      reason: readableReason(reason) ?? 'CUDA batch가 실행되지 않음',
    }
  }

  return {
    state: 'gpu-zero',
    title: 'GPU 미실행 · CUDA batch 0',
    requested,
    provider,
    deviceName,
    gpuAttempts,
    gpuSuccesses,
    cpuSmallWaveSuccesses,
    accuracyContract,
    accuracyParityVerified,
    reason: 'GPU를 요청했지만 CUDA 실행 기록이 없습니다.',
  }
}
