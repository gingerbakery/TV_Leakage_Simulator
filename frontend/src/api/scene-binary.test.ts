import { describe, expect, it } from 'vitest'

import { decodeSceneBinary } from './scene-binary'

function createBinaryFixture(): ArrayBuffer {
  const arrays = [
    { name: 'vertices', dtype: 'float64', width: 3, values: [0, 0, 0, 1, 0, 0, 0, 1, 0] },
    { name: 'faces', dtype: 'uint32', width: 3, values: [0, 1, 2] },
    { name: 'face_component_ids', dtype: 'int32', width: 1, values: [3] },
    { name: 'face_source_ids', dtype: 'uint32', width: 1, values: [99] },
    { name: 'face_areas_mm2', dtype: 'float64', width: 1, values: [0.5] },
    { name: 'feature_edge_points', dtype: 'float64', width: 6, values: [0, 0, 0, 1, 0, 0] },
    { name: 'feature_edge_component_ids', dtype: 'int32', width: 1, values: [3] },
    { name: 'component_face_indices', dtype: 'uint32', width: 1, values: [0] },
  ] as const
  let offset = 0
  const descriptors: Record<string, object> = {}
  for (const block of arrays) {
    offset = (offset + 7) & ~7
    const bytes = block.values.length * (block.dtype === 'float64' ? 8 : 4)
    descriptors[block.name] = {
      dtype: block.dtype,
      width: block.width,
      count: block.values.length / block.width,
      byte_offset: offset,
      byte_length: bytes,
    }
    offset += bytes
  }
  const manifest = {
    schema_version: 'mesh-scene.v2-binary',
    units: { length: 'mm' },
    coordinate_system: { handedness: 'right', axes: { x: 'model_x', y: 'model_y', z: 'model_z' } },
    components: [{
      object_id: 3, component_id: 3, object_name: 'Panel', component_name: 'Panel',
      face_count: 1, area_mm2: 0.5, bbox_min: [0, 0, 0], bbox_max: [1, 1, 0],
      is_truncated: false, color: '#abcdef', binary_face_encoding: 'range', binary_face_start: 0, binary_face_count: 1,
    }],
    metadata: {
      face_count: 1, vertex_count: 3, component_count: 1, source_file: 'fixture.step',
      synthetic: false, import_note: '', receiver_face_hint: [], scene_token: 'scene-test',
    },
    binary: { version: 1, byte_order: 'little', byte_length: offset, arrays: descriptors, face_material_table: ['PC'] },
  }
  const encoder = new TextEncoder()
  let manifestBytes = encoder.encode(JSON.stringify(manifest))
  const padded = new Uint8Array(manifestBytes.length + ((-manifestBytes.length) & 7))
  padded.fill(32)
  padded.set(manifestBytes)
  manifestBytes = padded
  const buffer = new ArrayBuffer(16 + manifestBytes.length + offset)
  const bytes = new Uint8Array(buffer)
  bytes.set(encoder.encode('BITSAMSC'), 0)
  const header = new DataView(buffer)
  header.setUint32(8, 1, true)
  header.setUint32(12, manifestBytes.length, true)
  bytes.set(manifestBytes, 16)
  let dataOffset = 16 + manifestBytes.length
  for (const block of arrays) {
    dataOffset = (dataOffset + 7) & ~7
    const View = block.dtype === 'float64' ? Float64Array : block.dtype === 'int32' ? Int32Array : Uint32Array
    new View(buffer, dataOffset, block.values.length).set(block.values)
    dataOffset += block.values.length * (block.dtype === 'float64' ? 8 : 4)
  }
  return buffer
}

describe('decodeSceneBinary', () => {
  it('keeps CAD names, colors, faces and authored face ids', () => {
    const scene = decodeSceneBinary(createBinaryFixture())
    expect(scene.components[0].component_name).toBe('Panel')
    expect(scene.components[0].color).toBe('#abcdef')
    expect([...scene.components[0].face_indices]).toEqual([0])
    expect(scene.mesh.vertices[1]).toEqual([1, 0, 0])
    expect(scene.mesh.faces[0]).toEqual([0, 1, 2])
    expect(scene.mesh.face_source_ids?.[0]).toBe(99)
    expect(scene.mesh.face_ids[0]).toBe(0)
    expect(scene.mesh.face_centroids[0]).toEqual([1 / 3, 1 / 3, 0])
    expect(scene.mesh.face_normals[0]).toEqual([0, 0, 1])
    expect(scene.mesh.face_material_ids[0]).toBe('PC')
    expect(scene.mesh.feature_edge_segments[0].component_id).toBe(3)
    expect(scene.objects).toBe(scene.components)
    expect(scene.mesh.faces.includes([0, 1, 2])).toBe(false)
    expect(scene.mesh.faces.map((face) => face[2])).toEqual([2])
    expect(scene.components[0].face_indices.filter((faceId) => faceId === 0)).toEqual([0])
  })
})
