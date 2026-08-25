import type {
  SceneComponent,
  SceneFeatureEdgeSegment,
  ScenePayload,
} from './types'

const MAGIC = 'BITSAMSC'
const HEADER_BYTES = 16

type NumericView = Float64Array | Uint32Array | Int32Array

interface BinaryDescriptor {
  dtype: 'float64' | 'uint32' | 'int32'
  width: number
  count: number
  byte_offset: number
  byte_length: number
}

interface BinaryManifest {
  schema_version: 'mesh-scene.v2-binary'
  units: ScenePayload['units']
  coordinate_system: ScenePayload['coordinate_system']
  components: Array<
    Omit<SceneComponent, 'face_indices'> & {
      binary_face_offset: number
      binary_face_count: number
    }
  >
  metadata: ScenePayload['metadata']
  binary: {
    version: number
    byte_order: 'little'
    byte_length: number
    arrays: Record<string, BinaryDescriptor>
    face_material_table: string[]
  }
}

function numericView(
  buffer: ArrayBuffer,
  dataOffset: number,
  descriptor: BinaryDescriptor,
): NumericView {
  const byteOffset = dataOffset + descriptor.byte_offset
  const length = descriptor.count * descriptor.width
  if (descriptor.dtype === 'float64') {
    return new Float64Array(buffer, byteOffset, length)
  }
  if (descriptor.dtype === 'int32') {
    return new Int32Array(buffer, byteOffset, length)
  }
  return new Uint32Array(buffer, byteOffset, length)
}

function arrayFacade<T>(
  length: number,
  read: (index: number) => T,
): T[] {
  const target: T[] = []
  target.length = length
  return new Proxy(target, {
    get(current, property, receiver) {
      if (typeof property === 'string' && /^\d+$/.test(property)) {
        const index = Number(property)
        return index < length ? read(index) : undefined
      }
      if (property === Symbol.iterator) {
        return function* iterator() {
          for (let index = 0; index < length; index += 1) yield read(index)
        }
      }
      return Reflect.get(current, property, receiver)
    },
    has(current, property) {
      if (typeof property === 'string' && /^\d+$/.test(property)) {
        return Number(property) < length
      }
      return Reflect.has(current, property)
    },
  })
}

function scalarFacade<T>(view: NumericView, map: (value: number) => T): T[] {
  return arrayFacade(view.length, (index) => map(view[index]))
}

function tuple3Facade(view: NumericView): [number, number, number][] {
  return arrayFacade(view.length / 3, (index) => {
    const offset = index * 3
    return [view[offset], view[offset + 1], view[offset + 2]]
  })
}

function edgeFacade(
  points: NumericView,
  componentIds: NumericView,
): SceneFeatureEdgeSegment[] {
  return arrayFacade(componentIds.length, (index) => {
    const offset = index * 6
    const componentId = componentIds[index]
    return {
      start: [points[offset], points[offset + 1], points[offset + 2]],
      end: [points[offset + 3], points[offset + 4], points[offset + 5]],
      component_id: componentId < 0 ? null : componentId,
    }
  })
}

export function decodeSceneBinary(buffer: ArrayBuffer): ScenePayload {
  if (buffer.byteLength < HEADER_BYTES) {
    throw new Error('CAD Binary 응답 헤더가 손상되었습니다.')
  }
  const bytes = new Uint8Array(buffer)
  const magic = new TextDecoder().decode(bytes.subarray(0, 8))
  if (magic !== MAGIC) {
    throw new Error('지원하지 않는 CAD Binary 형식입니다.')
  }
  const header = new DataView(buffer, 8, 8)
  const version = header.getUint32(0, true)
  const manifestLength = header.getUint32(4, true)
  if (version !== 1 || HEADER_BYTES + manifestLength > buffer.byteLength) {
    throw new Error('CAD Binary 버전 또는 Manifest 길이가 올바르지 않습니다.')
  }
  const manifest = JSON.parse(
    new TextDecoder().decode(
      bytes.subarray(HEADER_BYTES, HEADER_BYTES + manifestLength),
    ),
  ) as BinaryManifest
  const dataOffset = HEADER_BYTES + manifestLength
  if (dataOffset + manifest.binary.byte_length !== buffer.byteLength) {
    throw new Error('CAD Binary 배열 길이가 Manifest와 일치하지 않습니다.')
  }

  const views = Object.fromEntries(
    Object.entries(manifest.binary.arrays).map(([name, descriptor]) => [
      name,
      numericView(buffer, dataOffset, descriptor),
    ]),
  ) as Record<string, NumericView>
  const componentFaces = views.component_face_indices
  const components = manifest.components.map((component) => {
    const { binary_face_offset, binary_face_count, ...metadata } = component
    return {
      ...metadata,
      face_indices: scalarFacade(
        componentFaces.subarray(
          binary_face_offset,
          binary_face_offset + binary_face_count,
        ) as NumericView,
        (value) => value,
      ),
    }
  })
  const materials = manifest.binary.face_material_table

  return {
    schema_version: 'mesh-scene.v1',
    units: manifest.units,
    coordinate_system: manifest.coordinate_system,
    mesh: {
      vertices: tuple3Facade(views.vertices),
      faces: tuple3Facade(views.faces),
      face_ids: scalarFacade(views.face_ids, (value) => value),
      face_component_ids: scalarFacade(
        views.face_component_ids,
        (value) => (value < 0 ? null : value),
      ),
      face_material_ids: scalarFacade(
        views.face_material_codes,
        (value) => materials[value] ?? '',
      ),
      face_source_ids: scalarFacade(views.face_source_ids, (value) => value),
      face_normals: tuple3Facade(views.face_normals),
      face_centroids: tuple3Facade(views.face_centroids),
      face_areas_mm2: scalarFacade(views.face_areas_mm2, (value) => value),
      feature_edge_segments: edgeFacade(
        views.feature_edge_points,
        views.feature_edge_component_ids,
      ),
    },
    components,
    objects: components,
    metadata: manifest.metadata,
  }
}

