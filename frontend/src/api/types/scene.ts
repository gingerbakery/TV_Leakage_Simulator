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
}

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
    receiver_face_hint: number[]
    scene_token: string
  }
}

export interface CadUploadResponse {
  ok: true
  display_name: string
  path: string
}
