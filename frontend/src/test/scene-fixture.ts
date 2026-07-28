import type { SceneComponent, ScenePayload } from '@/api'

const components: SceneComponent[] = [
  {
    object_id: 1,
    component_id: 1,
    object_name: 'STEP Solid 1',
    component_name: 'STEP Solid 1',
    face_indices: [0, 1, 2],
    face_count: 3,
    area_mm2: 1200,
    bbox_min: [0, 0, 0],
    bbox_max: [60, 60, 10],
    is_truncated: false,
    color: null,
  },
  {
    object_id: 2,
    component_id: 2,
    object_name: 'STEP Solid 2',
    component_name: 'STEP Solid 2',
    face_indices: [3, 4],
    face_count: 2,
    area_mm2: 480,
    bbox_min: [5, 5, 10],
    bbox_max: [55, 55, 20],
    is_truncated: false,
    color: null,
  },
]

export function createSceneFixture(): ScenePayload {
  return {
    schema_version: 'mesh-scene.v1',
    units: { length: 'mm' },
    coordinate_system: {
      handedness: 'right',
      axes: {
        x: 'model_x',
        y: 'model_y',
        z: 'model_z',
      },
    },
    mesh: {
      vertices: [
        [0, 0, 0],
        [60, 0, 0],
        [60, 60, 0],
        [0, 60, 10],
        [5, 5, 10],
        [55, 5, 10],
        [55, 55, 20],
        [5, 55, 20],
      ],
      faces: [
        [0, 1, 2],
        [0, 2, 3],
        [0, 3, 1],
        [4, 5, 6],
        [4, 6, 7],
      ],
      face_ids: [0, 1, 2, 3, 4],
      face_component_ids: [1, 1, 1, 2, 2],
      face_material_ids: ['', '', '', '', ''],
      face_normals: [
        [0, 0, 1],
        [0, 0, 1],
        [0, 1, 0],
        [0, 0, 1],
        [0, 0, 1],
      ],
      face_centroids: [
        [40, 20, 0],
        [20, 40, 3.33],
        [20, 20, 3.33],
        [38.33, 21.67, 13.33],
        [21.67, 38.33, 16.67],
      ],
      face_areas_mm2: [1800, 1800, 300, 1250, 1250],
      feature_edge_segments: [
        {
          start: [0, 0, 0],
          end: [60, 0, 0],
          component_id: 1,
        },
        {
          start: [5, 5, 10],
          end: [55, 5, 10],
          component_id: 2,
        },
      ],
    },
    objects: components,
    components,
    metadata: {
      face_count: 5,
      vertex_count: 8,
      component_count: 2,
      source_file: 'fixture.step',
      synthetic: false,
      import_note: 'Test fixture',
      receiver_face_hint: [],
      scene_token: 'fixture-token',
    },
  }
}
