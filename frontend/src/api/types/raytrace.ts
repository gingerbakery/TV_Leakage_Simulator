import type { AxisVector, IndexPair, Vec3 } from './common'

export type EmitterType = 'face' | 'datum_plane' | 'reference_plane'
export type EmitterNormalMode = 'face_normal' | 'custom'
export type EmitterDistribution = 'lambertian' | 'isotropic' | 'gaussian'
export type EmitterPowerMode = 'total' | 'power_per_area'
export type EmitterSurfaceConstruction = 'rectangular_fit' | 'polygon_auto'

export interface EmitterSpec {
  emitter_id: string
  emitter_type: EmitterType
  face_indices: number[]
  normal_mode: EmitterNormalMode
  normal_flip: boolean
  custom_normal: Vec3 | null
  direction_distribution: EmitterDistribution
  gaussian_sigma_deg: number
  power_mode: EmitterPowerMode
  power_lumen: number
  power_density_lm_per_m2: number
  center: Vec3 | null
  u_axis: Vec3 | null
  v_axis: Vec3 | null
  width_mm: number | null
  height_mm: number | null
  reference_mode: string | null
  surface_construction: EmitterSurfaceConstruction
  polygon_vertices: Vec3[]
  reference_vertex_indices: number[]
  reference_edge_vertex_indices: IndexPair[]
  reference_vertex_points: Vec3[]
  reference_edge_points: [Vec3, Vec3][]
  ray_count: number
  seed: number | null
  enabled: boolean
}

export type ReceiverType = 'rectangle'
export type ReceiverPlacementMode =
  | 'datum_plane'
  | 'reference_plane'
  | 'current_view'

export interface ReceiverSpec {
  receiver_id: string
  receiver_type: ReceiverType
  display_name: string
  placement_mode: ReceiverPlacementMode
  center: Vec3
  normal: Vec3
  u_axis: Vec3 | null
  v_axis: Vec3 | null
  width_mm: number
  height_mm: number
  resolution: IndexPair
  acceptance_angle_deg: number
  normal_flip: boolean
  reference_mode: string | null
  reference_vertex_indices: number[]
  reference_edge_vertex_indices: IndexPair[]
  reference_vertex_points: Vec3[]
  reference_edge_points: [Vec3, Vec3][]
  view_distance_mm: number | null
  base_center: Vec3 | null
  base_u_axis: Vec3 | null
  base_v_axis: Vec3 | null
  base_normal: Vec3 | null
  position_offset_mm: Vec3
  tilt_xyz_deg: Vec3
  enabled: boolean
}

export type ScatterModel =
  | 'none'
  | 'specular'
  | 'lambertian'
  | 'gaussian'
  | 'mixed'

export interface OpticalProfile {
  profile_id: string
  reflectance: number
  absorption: number | null
  specular_ratio: number
  diffuse_ratio: number
  scatter_model: ScatterModel
  roughness: number
  gaussian_sigma_deg: number
  bsdf_asset_id: string | null
  notes: string
}

export interface OpticalAssignment {
  assignment_id: string
  target_type: 'part' | 'faces'
  component_id: number
  profile_id: string
  face_indices: number[]
  priority: number
  enabled: boolean
}

export interface TransformRule {
  rule_id: string
  target_type: 'component'
  object_id: number
  label: string
  enabled: boolean
  move: AxisVector
  tilt: AxisVector
}

export type TerminationMode = 'threshold' | 'russian_roulette'
export type ContributionMode = 'summary' | 'detailed'
export type IntersectionBackend = 'auto' | 'brute_force' | 'bvh'

export interface RayTraceConfig {
  ray_count: number
  max_depth: number
  seed: number
  min_energy: number
  epsilon_mm: number
  k_abs: number
  k_brdf: number
  termination_mode: TerminationMode
  contribution_mode: ContributionMode
  intersection_backend: IntersectionBackend
  store_ray_paths: boolean
  max_stored_paths: number
}

export type RayTraceConfigRequest = Omit<
  RayTraceConfig,
  'intersection_backend'
> & {
  /**
   * The current legacy UI omits this field and uses the backend's `auto` default.
   */
  intersection_backend?: IntersectionBackend
}

export interface RayTraceRequest {
  scene_token: string
  project_name: string
  emitters: EmitterSpec[]
  receivers: ReceiverSpec[]
  optical_profiles: OpticalProfile[]
  optical_assignments: OpticalAssignment[]
  transform_rules: TransformRule[]
  excluded_component_ids: number[]
  roi_faces?: number[]
  config: RayTraceConfigRequest
}

export interface RayHit {
  face_index: number
  component_id: number | null
  material_id: string | null
  point: Vec3
  normal: Vec3
  distance_mm: number
  incoming_energy_lumen: number
  outgoing_energy_lumen: number
  depth: number
  event_type: string
  receiver_id: string | null
  optical_profile_id: string | null
  reflectance: number | null
  scatter_model: ScatterModel | null
  optical_assignment_source: string | null
  ray_kind: string | null
}

export interface ReceiverGrid {
  receiver_id: string
  resolution: IndexPair
  bin_area_mm2: number
  flux_lumen: number[][]
  hit_count: number
}

type ContributionBreakdown = Record<string, Record<string, unknown>>

export interface RayTraceContributionSummary {
  schema_version: 'rt-contribution.v1'
  direct_receiver_hit_count: number
  direct_receiver_flux_lumen: number
  reflected_receiver_hit_count: number
  reflected_receiver_flux_lumen: number
  receivers: ContributionBreakdown
  components: ContributionBreakdown
  faces: ContributionBreakdown
  materials: ContributionBreakdown
  lobes: ContributionBreakdown
  depths: ContributionBreakdown
}

export interface RayTraceResult {
  run_id: string
  config: RayTraceConfig
  emitters: EmitterSpec[]
  receivers: ReceiverSpec[]
  receiver_grids: ReceiverGrid[]
  optical_profiles: OpticalProfile[]
  total_rays: number
  receiver_hit_count: number
  surface_hit_count: number
  terminated_ray_count: number
  contribution_summary: RayTraceContributionSummary
  runtime_sec: number
  stored_paths: RayHit[][]
  metrics: Record<string, unknown>
}

interface RayTraceJobProgress {
  job_id: string
  processed_rays: number
  total_rays: number
  progress: number
  elapsed_sec: number
  estimated_remaining_sec: number | null
  rays_per_sec: number
  created_at: number
}

export interface QueuedRayTraceJob extends RayTraceJobProgress {
  status: 'queued'
  phase: 'queued'
}

export interface RunningRayTraceJob extends RayTraceJobProgress {
  status: 'running'
  phase: 'preparing' | 'tracing'
}

export interface CompletedRayTraceJob extends RayTraceJobProgress {
  status: 'completed'
  phase: 'completed'
  result: RayTraceResult
  completed_at: number
}

export interface FailedRayTraceJob extends RayTraceJobProgress {
  status: 'failed'
  phase: 'failed'
  error: string
  completed_at: number
}

export type RayTraceJob =
  | QueuedRayTraceJob
  | RunningRayTraceJob
  | CompletedRayTraceJob
  | FailedRayTraceJob
