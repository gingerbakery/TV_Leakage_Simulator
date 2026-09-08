#!/usr/bin/env node
/**
 * Package a synthetic closed-enclosure scene/setup/result as a UI-native BITSAM.
 * Requires Node 24 and the existing frontend dependencies. No bundling or CAD
 * parsing is needed: the application's own store, serializer, parser and scene
 * fingerprint are imported through Node's native TypeScript support.
 *
 * node scripts/closed_enclosure_project_helper.mjs --scene scene.json \
 *   --request setup.json --result result.json --cad-name gap_0p2.step \
 *   --output samples/closed_enclosure/gap_0p2.bitsam
 *
 * The scene must be the viewer scene returned when importing that STEP file.
 * This helper deliberately requires a full-domain CPU request, datum-plane
 * emitters/receivers, and a material assignment for every scene component.
 */
import { readFile, writeFile } from 'node:fs/promises'
import { basename, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import {
  createWorkspaceStore,
} from '../frontend/src/stores/workspace-store.ts'
import {
  compareBitsamProjectScene,
  createBitsamProject,
  parseBitsamProject,
  serializeBitsamProject,
} from '../frontend/src/features/projects/bitsam-project.ts'
import {
  compileOpticalProfile,
} from '../frontend/src/features/materials/material-catalog.ts'

const matte = compileOpticalProfile('pc_black', 'matte_black_resin')

function required(condition, message) {
  if (!condition) throw new Error(message)
}

function normalizeEmitter(emitter) {
  required(emitter.emitter_type === 'datum_plane', 'Only datum-plane sample emitters are supported')
  return {
    face_indices: [], source_face_indices: [], normal_mode: 'face_normal',
    normal_flip: false, custom_normal: null, direction_distribution: 'lambertian',
    gaussian_sigma_deg: 12, power_mode: 'total', power_lumen: 1,
    power_density_lm_per_m2: 100, luminance_nit: 500, center: null,
    u_axis: null, v_axis: null, width_mm: null, height_mm: null,
    reference_mode: null, surface_construction: 'rectangular_fit',
    polygon_vertices: [], reference_vertex_indices: [],
    reference_edge_vertex_indices: [], reference_vertex_points: [],
    reference_edge_points: [], ray_count: 10000, seed: null, enabled: true,
    ...emitter,
  }
}

function normalizeReceiver(receiver) {
  required((receiver.placement_mode ?? 'datum_plane') === 'datum_plane', 'Only datum-plane sample receivers are supported')
  return {
    receiver_type: 'rectangle', display_name: receiver.receiver_id,
    placement_mode: 'datum_plane', source_face_indices: [],
    center: [0, 0, 0], normal: [0, 0, 1], u_axis: null, v_axis: null,
    width_mm: 100, height_mm: 30, resolution: [80, 24],
    acceptance_angle_deg: 90, normal_flip: false, reference_mode: null,
    reference_vertex_indices: [], reference_edge_vertex_indices: [],
    reference_vertex_points: [], reference_edge_points: [], view_distance_mm: null,
    base_center: null, base_u_axis: null, base_v_axis: null, base_normal: null,
    position_offset_mm: [0, 0, 0], tilt_xyz_deg: [0, 0, 0], pivot: null,
    enabled: true, ...receiver,
  }
}

function materialAssignment(assignment, profiles) {
  required(assignment.target_type === 'part', 'Sample optical assignments must target whole components')
  const profile = profiles.get(assignment.profile_id)
  required(profile, `Missing optical profile: ${assignment.profile_id}`)
  required(profile.scatter_model === matte.scatterModel, `Profile ${profile.profile_id} must use ${matte.scatterModel}`)
  required(Math.abs(profile.roughness - matte.roughness) < 1e-12,
    `Profile ${profile.profile_id} roughness must be ${matte.roughness} to survive UI reload`)
  required(Math.abs(profile.gaussian_sigma_deg - matte.scatterSigmaDeg) < 1e-12,
    `Profile ${profile.profile_id} gaussian_sigma_deg must be ${matte.scatterSigmaDeg} to survive UI reload`)
  return {
    assignmentId: assignment.assignment_id,
    componentId: assignment.component_id, targetType: 'part', faceIds: [],
    baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin',
    profileId: profile.profile_id, bsdfAssetId: profile.bsdf_asset_id ?? '',
    opticalOverride: {
      reflectance: profile.reflectance,
      loss: profile.absorption ?? 1 - profile.reflectance,
      specularRatio: profile.specular_ratio, diffuseRatio: profile.diffuse_ratio,
    },
    enabled: assignment.enabled !== false,
  }
}

export function createClosedEnclosureProject({ scene, request, result, cadName, savedAt }) {
  required(scene.schema_version === 'mesh-scene.v1', 'Expected the imported viewer ScenePayload')
  required(request.config?.compute_backend === 'cpu', 'Sample packaging requires an explicit CPU request')
  required(!request.roi_faces?.length, 'Samples must retain the full optical transport domain')
  required(!request.excluded_component_ids?.length, 'Sample enclosure components must not be excluded')
  required(!request.transform_rules?.length, 'Bake sample geometry into STEP before packaging')
  required(request.emitters?.length && request.receivers?.length, 'Sample requires emitters and receivers')
  const store = createWorkspaceStore()
  const actions = store.getState().actions
  const displayName = basename(cadName)
  actions.setActiveCad({ path: displayName, displayName })
  actions.setRayTraceConfig({ ...store.getState().rayTraceConfig, ...request.config })
  for (const emitter of request.emitters) actions.upsertEmitter(normalizeEmitter(emitter))
  for (const receiver of request.receivers) actions.upsertReceiver(normalizeReceiver(receiver))
  const profiles = new Map(request.optical_profiles.map((profile) => [profile.profile_id, profile]))
  const assignments = [...(request.optical_assignments ?? [])]
    .sort((left, right) => (left.priority ?? 0) - (right.priority ?? 0))
    .map((assignment) => materialAssignment(assignment, profiles))
  const covered = new Set(assignments.filter((assignment) => assignment.enabled).map((assignment) => assignment.componentId))
  for (const component of scene.components) {
    required(covered.has(component.component_id), `Component ${component.component_id} has no optical assignment`)
  }
  for (const assignment of assignments) actions.upsertMaterialAssignment(assignment)
  const normalizedResult = result ? {
    ...result,
    config: { ...store.getState().rayTraceConfig, ...result.config },
    emitters: result.emitters.map(normalizeEmitter),
    receivers: result.receivers.map(normalizeReceiver),
  } : undefined
  const project = createBitsamProject(scene, store.getState(), savedAt ?? new Date(), normalizedResult)
  const source = serializeBitsamProject(project)
  const restored = parseBitsamProject(source)
  const compatibility = compareBitsamProjectScene(restored, scene, store.getState().activeCad)
  required(compatibility.compatible, `CAD fingerprint check failed: ${compatibility.reasons.join('; ')}`)
  const reloadedStore = createWorkspaceStore()
  reloadedStore.getState().actions.restoreProjectState(restored.workspace)
  required(reloadedStore.getState().roiScopes.length === 0, 'Reload unexpectedly enabled an ROI')
  required(reloadedStore.getState().rayTraceConfig.compute_backend === 'cpu', 'Reload changed the compute backend')
  required(reloadedStore.getState().materialAssignments.length === assignments.length, 'Reload changed optical assignments')
  return { source, project: restored }
}

async function main(argv) {
  if (argv.includes('--help') || argv.length === 0) {
    console.log('Usage: node scripts/closed_enclosure_project_helper.mjs --scene SCENE.json --request SETUP.json [--result RESULT.json] --cad-name MODEL.step --output MODEL.bitsam [--saved-at ISO_DATE]')
    return
  }
  const args = {}
  const allowed = new Set(['--scene', '--request', '--result', '--cad-name', '--output', '--saved-at'])
  for (let index = 0; index < argv.length; index += 2) {
    required(allowed.has(argv[index]) && argv[index + 1], `Unknown or incomplete argument: ${argv[index]}`)
    args[argv[index].slice(2)] = argv[index + 1]
  }
  for (const key of ['scene', 'request', 'cad-name', 'output']) required(args[key], `Missing --${key}`)
  const readJson = async (path) => JSON.parse((await readFile(path, 'utf8')).replace(/^\uFEFF/, ''))
  const [scene, request, result] = await Promise.all([
    readJson(args.scene), readJson(args.request), args.result ? readJson(args.result) : undefined,
  ])
  const packaged = createClosedEnclosureProject({
    scene, request, result, cadName: args['cad-name'],
    savedAt: args['saved-at'] ? new Date(args['saved-at']) : undefined,
  })
  await writeFile(args.output, packaged.source, 'utf8')
  console.log(JSON.stringify({
    output: resolve(args.output), cad: packaged.project.cad.display_name,
    fingerprint: packaged.project.cad.fingerprint,
    emitters: packaged.project.workspace.emitters.length,
    receivers: packaged.project.workspace.receivers.length,
    material_assignments: packaged.project.workspace.materialAssignments.length,
    has_saved_result: Boolean(packaged.project.analysis_result),
    parser_roundtrip: true, geometry_compatible: true, compute_backend: 'cpu',
  }, null, 2))
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch((error) => { console.error(error.message); process.exitCode = 1 })
}
