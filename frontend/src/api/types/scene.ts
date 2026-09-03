import type { Vec3 } from './common'

export interface SceneFeatureEdgeSegment {
  start: Vec3
  end: Vec3
  component_id: number | null
}

export interface SceneMesh {
  vertices: Vec3[]
  faces: [number, number, number][]
  face_ids: number[]
  face_component_ids: (number | null)[]
  face_material_ids: string[]
  /** Original CAD/B-rep face id. Many tessellation triangles can share it. */
  face_source_ids?: number[]
  face_normals: Vec3[]
  face_centroids: Vec3[]
  face_areas_mm2: number[]
  feature_edge_segments: SceneFeatureEdgeSegment[]
}

export interface SceneComponent {
  object_id: number
  component_id: number
  object_name: string
  component_name: string
  face_indices: number[]
  face_count: number
  area_mm2: number
  bbox_min: Vec3
  bbox_max: Vec3
  is_truncated: boolean
  /** Component's own display color as authored in the CAD file (e.g. NX body color), hex "#rrggbb". Null if the STEP file had none. */
  color: string | null
}

/** Lightweight Component identity used for cross-Case setup matching.
 * Deliberately excludes face_indices and all tessellated mesh arrays. */
export type SceneComponentMatchMetadata = Pick<
  SceneComponent,
  | 'component_id'
  | 'component_name'
  | 'object_name'
  | 'face_count'
  | 'area_mm2'
  | 'bbox_min'
  | 'bbox_max'
>

export interface ScenePayload {
  schema_version: 'mesh-scene.v1'
  units: {
    length: 'mm'
  }
  coordinate_system: {
    handedness: 'right'
    axes: {
      x: 'model_x'
      y: 'model_y'
      z: 'model_z'
    }
  }
  mesh: SceneMesh
  /**
   * Legacy alias of `components`. New code should prefer `components`.
   */
  objects: SceneComponent[]
  components: SceneComponent[]
  metadata: {
    face_count: number
    vertex_count: number
    component_count: number
    source_file: string
    synthetic: boolean
    import_note: string
    import_timings_sec?: Record<string, number>
    receiver_face_hint: number[]
    scene_token: string
  }
}

export interface CadUploadResponse {
  ok: true
  display_name: string
  path: string
}

export interface SectionCapContour {
  component_id: number | null
  points: Vec3[]
}

export interface SectionCapRequest {
  scene_token: string
  axis: 'x' | 'y' | 'z'
  position: number
  hidden_component_ids: number[]
  transform_rules: unknown[]
}

export interface SectionCapResponse {
  axis: 'x' | 'y' | 'z'
  position: number
  contours: SectionCapContour[]
  open_chain_count: number
}
