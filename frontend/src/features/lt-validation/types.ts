export type Vec3 = [number, number, number]
export interface ValidationIssue { code: string; severity: 'error' | 'warning'; message: string }
export interface ValidationSource {
  id: string
  name: string
  total_flux_lm: number | null
  flux_reference: 'aim_region' | 'emitted' | null
  center_mm: Vec3 | null
  u_axis: Vec3 | null
  v_axis: Vec3 | null
  width_mm: number | null
  height_mm: number | null
  normal: Vec3 | null
  angular_distribution: string | null
  aim: null | {
    center_mm: Vec3 | null; u_axis: Vec3 | null; v_axis: Vec3 | null
    width_mm: number | null; height_mm: number | null; distribution: string | null
  }
}
export interface ValidationReceiver {
  id: string
  name: string
  quantity: 'photometric' | 'radiometric' | 'unknown'
  raw: boolean | null
  flux_lm: number[][] | null
  u_edges_mm: number[] | null
  v_edges_mm: number[] | null
  center_mm: Vec3 | null
  u_axis: Vec3 | null
  v_axis: Vec3 | null
  normal: Vec3 | null
  acceptance_angle_deg: number | null
  aperture: 'none' | null
  total_flux_lm: number | null
  total_flux_tolerance_lm?: number
  issues: ValidationIssue[]
  provenance?: Record<string, unknown>
}
export interface ValidationDataset {
  schema: 'lt-validation.v1'
  kind: 'bitsam' | 'lighttools' | 'manual'
  filename: string
  receivers: ValidationReceiver[]
  sources: ValidationSource[]
  issues: ValidationIssue[]
  provenance?: Record<string, unknown>
}
export interface ValidationCase {
  id: string
  name: string
  sim: ValidationDataset | null
  lt: ValidationDataset | null
  sim_receiver_id: string | null
  lt_receiver_id: string | null
}
export interface ValidationPlotData {
  u_mm: number[]
  v_mm: number[]
  u_edges_mm: number[]
  v_edges_mm: number[]
  sim_pct: number[][]
  lt_pct: number[][]
  difference_pp: number[][]
  column_sim_pct: number[]
  column_lt_pct: number[]
  column_difference_pp: number[]
  row_sim_pct: number[]
  row_lt_pct: number[]
  row_difference_pp: number[]
  sim_flux_lm?: number[][]
  lt_flux_lm?: number[][]
  sim_illuminance_lx?: number[][]
  lt_illuminance_lx?: number[][]
}
export interface ValidationCaseResult {
  id: string
  name: string
  status: 'ready' | 'blocked'
  issues: ValidationIssue[]
  sim_total_lm: number | null
  lt_total_lm: number | null
  flux_deviation_pct: number | null
  pearson: number | null
  overlap_pct: number | null
  sim_relative_pct: number | null
  lt_relative_pct: number | null
  sim_change_pct: number | null
  lt_change_pct: number | null
  plots: ValidationPlotData | null
}
export interface ValidationResponse { cases: ValidationCaseResult[]; baseline_id: string }
