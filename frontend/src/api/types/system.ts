export interface DevStatus {
  ok: true
  web_ui_version: string
  boot_token: string
}

export interface GpuCudaStatus {
  available: boolean
  reason_code: string | null
  device_name: string | null
  compute_capability: string | null
  device_id: number | null
  numba_version: string | null
  toolkit_layout: string | null
  strict_float64: boolean
  kernel_executed: boolean
  kernel_verified: boolean
  preflight_scope: 'production_ray_bvh'
  provider_contract: 'strict_float64_bvh_v1'
}
