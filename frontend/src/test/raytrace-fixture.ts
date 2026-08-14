import type {
  CompletedRayTraceJob,
  RayHit,
  RayTraceResult,
  Vec3,
} from '@/api'
import {
  createDatumEmitter,
  createDatumReceiver,
} from '@/features/raytracing'

function hit(
  point: Vec3,
  eventType: string,
  rayKind: string | null = null,
): RayHit {
  return {
    face_index: eventType === 'surface' ? 0 : -1,
    component_id: eventType === 'surface' ? 1 : null,
    material_id: null,
    point,
    normal: [0, 0, 1],
    distance_mm: 1,
    incoming_energy_lumen: 0.01,
    outgoing_energy_lumen: 0.008,
    depth: eventType === 'surface' ? 1 : 0,
    event_type: eventType,
    receiver_id: eventType === 'receiver' ? 'receiver_001' : null,
    optical_profile_id: null,
    reflectance: null,
    scatter_model: null,
    optical_assignment_source: null,
    ray_kind: rayKind,
  }
}

export function createRayTraceResultFixture(): RayTraceResult {
  const emitter = createDatumEmitter(
    'emitter_001',
    [0, 0, 0],
    [0, 0, 0],
  )
  const receiver = createDatumReceiver(
    'receiver_001',
    [0, 0, 20],
    [0, 0, 0],
  )
  receiver.display_name = 'Main receiver'
  receiver.resolution = [2, 2]

  return {
    run_id: 'run-test-001',
    config: {
      ray_count: 100,
      max_depth: 1,
      seed: 42,
      min_energy: 1e-9,
      epsilon_mm: 1e-4,
      k_abs: 0.12,
      k_brdf: 1,
      termination_mode: 'threshold',
      contribution_mode: 'detailed',
      intersection_backend: 'bvh',
      store_ray_paths: true,
      max_stored_paths: 50,
      auto_convergence: false,
      convergence_target_percent: 5,
      max_convergence_multiplier: 8,
    },
    emitters: [emitter],
    receivers: [receiver],
    receiver_grids: [
      {
        receiver_id: receiver.receiver_id,
        resolution: [2, 2],
        bin_area_mm2: 1,
        flux_lumen: [
          [0.001, 0.002],
          [0.003, 0.004],
        ],
        hit_count: 12,
        flux_squared_lumen2: 0.00001,
        flux_squared_lumen2_grid: [
          [0.0000005, 0.0000015],
          [0.0000035, 0.0000045],
        ],
      },
    ],
    optical_profiles: [],
    total_rays: 100,
    receiver_hit_count: 12,
    surface_hit_count: 30,
    terminated_ray_count: 88,
    contribution_summary: {
      schema_version: 'rt-contribution.v1',
      direct_receiver_hit_count: 8,
      direct_receiver_flux_lumen: 0.008,
      reflected_receiver_hit_count: 4,
      reflected_receiver_flux_lumen: 0.003,
      receivers: {},
      components: {
        '1': {
          receiver_hit_count: 4,
          receiver_flux_lumen: 0.003,
        },
      },
      faces: {},
      materials: {},
      lobes: {
        specular: {
          receiver_hit_count: 4,
          receiver_flux_lumen: 0.003,
        },
      },
      depths: {},
    },
    runtime_sec: 0.125,
    stored_paths: [
      [
        hit([0, 0, 0], 'emitter', 'direct'),
        hit([0, 0, 20], 'receiver', 'direct'),
      ],
      [
        hit([0, 0, 0], 'emitter', 'direct'),
        hit([0, 0, 10], 'surface', 'direct'),
        hit([5, 0, 20], 'receiver', 'specular'),
      ],
    ],
    metrics: {
      _performance_summary: {
        rays_per_sec: 800,
        intersection_backend: 'bvh',
        bvh_build_sec: 0.01,
      },
      _reflection_summary: {
        reflection_blocked_count: 2,
        reflection_escaped_count: 3,
      },
      _optical_summary: {
        surface_hit_count: 30,
        unassigned_surface_hit_count: 0,
      },
      receiver_001: {
        peak_nit_est: 12.5,
        mean_nit_est: 4.25,
        total_flux_lumen: 0.011,
        error_estimate_percent: 2.75,
        peak_area_error_estimate_percent: 3.25,
        hit_count: 40,
      },
    },
  }
}

export function createCompletedRayTraceJobFixture(): CompletedRayTraceJob {
  return {
    job_id: 'job-test-001',
    status: 'completed',
    phase: 'completed',
    processed_rays: 100,
    total_rays: 100,
    progress: 1,
    elapsed_sec: 0.125,
    estimated_remaining_sec: 0,
    rays_per_sec: 800,
    created_at: 1,
    completed_at: 2,
    result: createRayTraceResultFixture(),
  }
}
