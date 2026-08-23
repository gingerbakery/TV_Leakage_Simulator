import type { GpuCudaStatus } from '@/api'

export function isGpuCudaStatusReady(
  status: GpuCudaStatus | undefined,
): boolean {
  return Boolean(
    status?.available === true &&
      status.strict_float64 === true &&
      status.kernel_executed === true &&
      status.kernel_verified === true &&
      status.preflight_scope === 'production_ray_bvh' &&
      status.provider_contract === 'strict_float64_bvh_v1',
  )
}
