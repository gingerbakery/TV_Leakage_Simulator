import type {
  RayTraceRequest,
  RayTraceResult,
  RayTraceResultSourceContext,
  ScenePayload,
} from '@/api'

const sceneSignatureCache = new WeakMap<ScenePayload, string>()
const pendingSourceContexts = new Map<string, RayTraceResultSourceContext>()
const maxPendingSourceContexts = 32

interface HashState {
  first: number
  second: number
}

function hashByte(state: HashState, value: number): void {
  const byte = value & 0xff
  state.first = Math.imul(state.first ^ byte, 0x01000193)
  state.second = Math.imul(state.second ^ byte, 0x85ebca6b)
}

function hashUint32(state: HashState, value: number): void {
  const normalized = value >>> 0
  hashByte(state, normalized)
  hashByte(state, normalized >>> 8)
  hashByte(state, normalized >>> 16)
  hashByte(state, normalized >>> 24)
}

const floatBuffer = new ArrayBuffer(8)
const floatView = new DataView(floatBuffer)

function hashFloat64(state: HashState, value: number): void {
  floatView.setFloat64(0, value, true)
  for (let index = 0; index < 8; index += 1) {
    hashByte(state, floatView.getUint8(index))
  }
}

function hashNumberList(
  state: HashState,
  tag: number,
  values: readonly number[],
  kind: 'float64' | 'int32',
): void {
  hashUint32(state, tag)
  hashUint32(state, values.length)
  for (const value of values) {
    if (kind === 'float64') hashFloat64(state, value)
    else hashUint32(state, value)
  }
}

function hashNullableIntList(
  state: HashState,
  tag: number,
  values: readonly (number | null)[],
): void {
  hashUint32(state, tag)
  hashUint32(state, values.length)
  for (const value of values) hashUint32(state, value ?? -1)
}

/**
 * Hash the complete trace mesh and its face/component association without
 * allocating a second serialized mesh. This is an identity guard, not a
 * cryptographic authenticity proof.
 */
export function createRayTraceSceneMeshSignature(scene: ScenePayload): string {
  const cached = sceneSignatureCache.get(scene)
  if (cached) return cached

  const state: HashState = {
    first: 0x811c9dc5,
    second: 0x9e3779b9,
  }
  hashUint32(state, scene.mesh.vertices.length)
  for (const vertex of scene.mesh.vertices) {
    hashNumberList(state, 1, vertex, 'float64')
  }
  hashUint32(state, scene.mesh.faces.length)
  for (const face of scene.mesh.faces) {
    hashNumberList(state, 2, face, 'int32')
  }
  hashNumberList(state, 3, scene.mesh.face_ids, 'int32')
  hashNullableIntList(state, 4, scene.mesh.face_component_ids)
  hashNumberList(
    state,
    5,
    scene.mesh.face_source_ids ?? [],
    'int32',
  )

  const signature = `mesh-fnv-pair-v1:${(state.first >>> 0)
    .toString(16)
    .padStart(8, '0')}${(state.second >>> 0)
    .toString(16)
    .padStart(8, '0')}`
  sceneSignatureCache.set(scene, signature)
  return signature
}

export function createRayTraceResultSourceContext(
  scene: ScenePayload,
  request: RayTraceRequest,
  cadCaseId: string | null = null,
): RayTraceResultSourceContext {
  return {
    schema_version: 'ray-result-source.v1',
    cad_case_id: cadCaseId,
    cad_display_name: request.project_name,
    scene: {
      schema_version: 'ray-result-scene.v1',
      scene_token: request.scene_token,
      scene_schema_version: scene.schema_version,
      face_count: scene.metadata.face_count,
      vertex_count: scene.metadata.vertex_count,
      component_count: scene.metadata.component_count,
      mesh_signature: createRayTraceSceneMeshSignature(scene),
    },
    requests: [structuredClone(request)],
  }
}

export function attachRayTraceResultSourceContext(
  result: RayTraceResult,
  sourceContext: RayTraceResultSourceContext,
): RayTraceResult {
  return {
    ...structuredClone(result),
    source_context: structuredClone(sourceContext),
  }
}

/** Keep launch-time context outside the Ray Tracing panel lifecycle. Accordion
 * navigation may unmount that panel while its background job is still active. */
export function registerPendingRayTraceResultSourceContext(
  jobId: string,
  sourceContext: RayTraceResultSourceContext,
): void {
  pendingSourceContexts.delete(jobId)
  pendingSourceContexts.set(jobId, structuredClone(sourceContext))
  while (pendingSourceContexts.size > maxPendingSourceContexts) {
    const oldestJobId = pendingSourceContexts.keys().next().value
    if (typeof oldestJobId !== 'string') break
    pendingSourceContexts.delete(oldestJobId)
  }
}

export function findPendingRayTraceResultSourceContext(
  jobId: string | null | undefined,
): RayTraceResultSourceContext | undefined {
  if (!jobId) return undefined
  const sourceContext = pendingSourceContexts.get(jobId)
  return sourceContext ? structuredClone(sourceContext) : undefined
}

export function takePendingRayTraceResultSourceContext(
  jobId: string | null | undefined,
): RayTraceResultSourceContext | undefined {
  if (!jobId) return undefined
  const sourceContext = pendingSourceContexts.get(jobId)
  pendingSourceContexts.delete(jobId)
  return sourceContext ? structuredClone(sourceContext) : undefined
}

function rayTraceGeometryBasis(request: RayTraceRequest | undefined): string {
  if (!request) return 'missing-request'
  return JSON.stringify({
    transform_rules: request.transform_rules.map((rule) => ({
      rule_id: rule.rule_id,
      target_type: rule.target_type,
      object_id: rule.object_id,
      enabled: rule.enabled,
      move: [rule.move.x, rule.move.y, rule.move.z],
      tilt: [rule.tilt.x, rule.tilt.y, rule.tilt.z],
      pivot: rule.pivot
        ? [rule.pivot.x, rule.pivot.y, rule.pivot.z]
        : null,
    })),
    excluded_component_ids: [...request.excluded_component_ids].sort(
      (left, right) => left - right,
    ),
    roi_faces: [...(request.roi_faces ?? [])].sort(
      (left, right) => left - right,
    ),
  })
}

export function mergeRayTraceResultSourceContexts(
  previous: RayTraceResultSourceContext | undefined,
  current: RayTraceResultSourceContext | undefined,
): RayTraceResultSourceContext | undefined {
  if (!previous) return current ? structuredClone(current) : undefined
  if (!current) return structuredClone(previous)
  if (
    previous.cad_case_id !== current.cad_case_id ||
    previous.cad_display_name !== current.cad_display_name ||
    previous.scene.scene_token !== current.scene.scene_token ||
    previous.scene.mesh_signature !== current.scene.mesh_signature ||
    rayTraceGeometryBasis(previous.requests.at(-1)) !==
      rayTraceGeometryBasis(current.requests.at(-1))
  ) {
    throw new Error('Ray result source changed during convergence')
  }
  const currentAlreadyContainsPrevious =
    current.requests.length > previous.requests.length &&
    previous.requests.every(
      (request, index) =>
        JSON.stringify(request) === JSON.stringify(current.requests[index]),
    )
  return {
    ...structuredClone(current),
    requests: currentAlreadyContainsPrevious
      ? current.requests.map((request) => structuredClone(request))
      : [
          ...previous.requests.map((request) => structuredClone(request)),
          ...current.requests.map((request) => structuredClone(request)),
        ],
  }
}
