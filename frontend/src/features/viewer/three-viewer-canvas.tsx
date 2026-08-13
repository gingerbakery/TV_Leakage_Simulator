import { useEffect, useRef, useState } from 'react'
import {
  ACESFilmicToneMapping,
  Box3,
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DirectionalLight,
  DoubleSide,
  EdgesGeometry,
  Euler,
  Float32BufferAttribute,
  Group,
  HemisphereLight,
  LineBasicMaterial,
  LineSegments,
  MathUtils,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  MOUSE,
  OrthographicCamera,
  Plane,
  PerspectiveCamera,
  Quaternion,
  Raycaster,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  Sprite,
  SpriteMaterial,
  Vector2,
  Vector3,
  WebGLRenderer,
  type Material,
  type Object3D,
} from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'

import type {
  EmitterSpec,
  RayTraceResult,
  SceneComponent,
  ScenePayload,
} from '@/api'
import type { ViewerCameraFrame } from '@/features/raytracing'
import {
  buildRayPathVisualization,
  rayPathFilterOrder,
  rayPathStyles,
} from '@/features/results/ray-paths'
import {
  findBaseMaterial,
  findSurfaceProperty,
} from '@/features/materials'
import {
  buildRoiClippedGeometries,
  type RoiComponentPointTransform,
} from '@/features/roi/roi-clipped-geometry'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type ComponentTransformRule,
  type MaterialAssignment,
  type RoiClipBox,
  type RoiProjectionPlane,
  type RoiScope,
  type RoiView,
} from '@/stores'

import {
  createComponentGeometry,
  createFaceGeometry,
  createFeatureEdgeGeometry,
  findCadSurfaceFaceIds,
  getSceneBounds,
  resolveComponentColor,
} from './scene-geometry'
import {
  cameraFovForPreset,
  DEFAULT_CAMERA_FOV_DEGREES,
  getAxisCameraPresetAxes,
  surfaceOpacityFromTransparency,
  type AxisCameraPreset,
  type DisplayCameraPreset,
} from './viewer-display'

export type ViewerCameraPreset = DisplayCameraPreset
type RoiCameraPreset = AxisCameraPreset
export type ViewerRenderMode =
  | 'Wireframe'
  | 'Surface'
  | 'Surface + Edge'

export interface RoiBoxSelectionResult {
  clipBox: RoiClipBox
  view: RoiView
}

export interface ViewerComponentContextTarget {
  clientX: number
  clientY: number
  componentId: number
  returnFocusElement: HTMLElement | null
}

export interface ViewerRayObjectContextTarget {
  clientX: number
  clientY: number
  id: string
  kind: 'emitter' | 'receiver'
  returnFocusElement: HTMLElement | null
}

interface ThreeViewerCanvasProps {
  scene: ScenePayload
  cadModelVisible?: boolean
  axisScalePercent: number
  surfaceTransparencyPercent: number
  cameraPreset: ViewerCameraPreset
  cameraRequestId: number
  renderMode: ViewerRenderMode
  roiBoxSelectionArmed: boolean
  roiFaceIds: number[]
  roiScopes: RoiScope[]
  rayTraceResult?: RayTraceResult | null
  editingComponentId?: number | null
  editingComponentMode?: 'material' | 'transform' | null
  onRoiBoxSelection(result: RoiBoxSelectionResult): void
  onCameraFrameChange?(frame: ViewerCameraFrame): void
  onCameraPresetChange?(preset: ViewerCameraPreset): void
  onComponentContextMenu?(target: ViewerComponentContextTarget): void
  onRayObjectContextMenu?(target: ViewerRayObjectContextTarget): void
  onStatusMessage(message: string): void
}

interface ComponentRenderNode {
  center: Vector3
  component: SceneComponent
  depthPriority: number
  edges: LineSegments<BufferGeometry, LineBasicMaterial>
  emitterOverlayRoot: Group
  group: Group
  hiddenEdges: LineSegments<BufferGeometry, LineBasicMaterial>
  materialOverlayRoot: Group
  roiOverlayRoot: Group
  selectionOverlayRoot: Group
  surface: Mesh<BufferGeometry, MeshStandardMaterial>
  transformOverlayRoot: Group
  wireframeFill: Mesh<BufferGeometry, MeshBasicMaterial>
}

interface ViewerRuntime {
  axisScalePercent: number
  camera: PerspectiveCamera
  controls: TrackballControls
  globalOriginAxes: Group
  modelRoot: Group
  nodes: Map<number, ComponentRenderNode>
  originAxisBaseScale: number
  pipCamera: PerspectiveCamera
  pipDistance: number
  pipTarget: Vector3
  pipUserAdjusted: boolean
  pipViewportRect: { x: number; y: number; width: number; height: number } | null
  pivotMarkerRoot: Group
  placementRoot: Group
  rayPathRoot: Group
  raycaster: Raycaster
  renderer: WebGLRenderer
  roiBoundsMarker: Group
  roiSelectionCameraPose: CameraPose | null
  roiSelectionPreset: RoiCameraPreset | null
  roiSelectionRoot: Group
  roiPreviewKey: string
  roiPreviewRoot: Group
  scene: Scene
}

interface CameraPose {
  far: number
  fov: number
  near: number
  position: Vector3
  target: Vector3
  up: Vector3
}

interface ViewerMaterialStyle {
  color: Color
  metalness: number
  roughness: number
}

interface ViewerBoxDrag {
  startX: number
  startY: number
  currentX: number
  currentY: number
}

interface FacePlacementFrame {
  center: [number, number, number]
  height: number
  normal: [number, number, number]
  uAxis: [number, number, number]
  vAxis: [number, number, number]
  width: number
}

const wireframeSurfaceOpacity = 0.75
const selectedWireframeSurfaceOpacity = 0.82
const emitterOverlayColor = 0xfacc15
const emitterDirectionColor = 0xffb000
// The "this part is selected / being edited" tint is amber/gold
// (0xfacc15 and friends, see `highlightColor` below) - a face selected
// *within* that part needs to read as a clearly separate highlight instead
// of blending into the part's own glow, so these use the complementary
// blue side of the wheel instead. Three shades mirror the original amber
// trio's relative saturation/darkness (armed pick > editing > plain select).
const selectedFaceHighlightColorArmed = 0x2563eb
const selectedFaceHighlightColorEditing = 0x3b82f6
const selectedMaterialFaceHighlightColor = 0xff8a00
// NX-style whole-component selection: a bright orange surface wash plus a
// crisp orange CAD-edge outline stays visible regardless of the part's own
// authored/imported display color.
const selectedComponentSurfaceColor = 0xff8a00
const selectedComponentEdgeColor = 0xffb000
// A saturated cyan reads clearly against both the neutral CAD grays and
// the warm emitter yellow/orange palette, unlike the previous lavender
// purple which tended to wash out against similarly light surfaces.
const receiverOverlayColor = 0x22d3ee

function cameraPresetVectors(preset: RoiCameraPreset): {
  direction: Vector3
  up: Vector3
} {
  const axes = getAxisCameraPresetAxes(preset)
  return {
    direction: new Vector3(...axes.direction),
    up: new Vector3(...axes.up),
  }
}

const roiCameraPresetConfig: Record<
  RoiCameraPreset,
  {
    direction: Vector3
    plane: RoiProjectionPlane
    up: Vector3
    view: Exclude<RoiView, 'coordinate'>
  }
> = {
  XY: {
    ...cameraPresetVectors('XY'),
    plane: 'xy',
    view: 'front_xy',
  },
  '-XY': {
    ...cameraPresetVectors('-XY'),
    plane: 'xy',
    view: 'back_neg_xy',
  },
  YZ: {
    ...cameraPresetVectors('YZ'),
    plane: 'yz',
    view: 'front_yz',
  },
  '-YZ': {
    ...cameraPresetVectors('-YZ'),
    plane: 'yz',
    view: 'back_neg_yz',
  },
  ZX: {
    ...cameraPresetVectors('ZX'),
    plane: 'zx',
    view: 'front_zx',
  },
  '-ZX': {
    ...cameraPresetVectors('-ZX'),
    plane: 'zx',
    view: 'back_neg_zx',
  },
}

function surfaceDepthUnits(depthPriority: number): number {
  return 4 + depthPriority * 4
}

function disposeMaterial(material: Material | Material[]): void {
  if (Array.isArray(material)) {
    material.forEach((item) => item.dispose())
  } else {
    material.dispose()
  }
}

function disposeObject(object: Object3D): void {
  object.traverse((child) => {
    if (child instanceof Mesh || child instanceof LineSegments) {
      child.geometry.dispose()
      disposeMaterial(child.material)
    } else if (child instanceof Sprite) {
      child.material.map?.dispose()
      child.material.dispose()
    }
  })
}

function viewerCameraFrame(runtime: ViewerRuntime): ViewerCameraFrame {
  runtime.camera.updateMatrixWorld(true)
  const normal = runtime.camera.getWorldDirection(new Vector3()).normalize()
  const uAxis = new Vector3()
    .setFromMatrixColumn(runtime.camera.matrixWorld, 0)
    .normalize()
  const vAxis = new Vector3()
    .crossVectors(normal, uAxis)
    .normalize()
  return {
    target: runtime.controls.target.toArray(),
    normal: normal.toArray(),
    uAxis: uAxis.toArray(),
    vAxis: vAxis.toArray(),
  }
}

function createPlacementPlane(
  name: string,
  centerValues: [number, number, number],
  uValues: [number, number, number],
  vValues: [number, number, number],
  normalValues: [number, number, number],
  width: number,
  height: number,
  color: number,
  directionColor: number,
  normalFlip: boolean,
  fillOpacity: number,
  alwaysVisible = false,
): Group {
  const root = new Group()
  root.name = name
  const center = new Vector3(...centerValues)
  const uAxis = new Vector3(...uValues).normalize()
  const vAxis = new Vector3(...vValues).normalize()
  const normal = new Vector3(...normalValues)
    .normalize()
    .multiplyScalar(normalFlip ? -1 : 1)
  const halfU = uAxis.clone().multiplyScalar(width / 2)
  const halfV = vAxis.clone().multiplyScalar(height / 2)
  const corners = [
    center.clone().sub(halfU).sub(halfV),
    center.clone().add(halfU).sub(halfV),
    center.clone().add(halfU).add(halfV),
    center.clone().sub(halfU).add(halfV),
  ]
  const surfaceGeometry = new BufferGeometry()
  surfaceGeometry.setFromPoints([
    corners[0],
    corners[1],
    corners[2],
    corners[0],
    corners[2],
    corners[3],
  ])
  const surface = new Mesh(
    surfaceGeometry,
    new MeshBasicMaterial({
      color,
      side: DoubleSide,
      transparent: true,
      opacity: fillOpacity,
      depthTest: !alwaysVisible,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  surface.renderOrder = alwaysVisible ? 82 : 20

  const edgeGeometry = new BufferGeometry()
  edgeGeometry.setFromPoints([
    corners[0],
    corners[1],
    corners[1],
    corners[2],
    corners[2],
    corners[3],
    corners[3],
    corners[0],
  ])
  const edges = new LineSegments(
    edgeGeometry,
    new LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.96,
      depthTest: !alwaysVisible,
      depthWrite: false,
    }),
  )
  edges.renderOrder = alwaysVisible ? 83 : 21

  const normalLength = MathUtils.clamp(
    Math.min(Math.abs(width), Math.abs(height)) * 0.18,
    2,
    18,
  )
  root.add(
    surface,
    edges,
    createDirectionArrow(
      `${name}-direction`,
      center,
      normal,
      normalLength,
      directionColor,
    ),
  )
  return root
}

function createDirectionArrow(
  name: string,
  center: Vector3,
  normal: Vector3,
  normalLength: number,
  color: number,
): Group {
  const root = new Group()
  root.name = name
  const direction = normal.clone().normalize()
  const arrowLength = MathUtils.clamp(
    normalLength * 0.28,
    0.45,
    5.5,
  )
  const arrowHeadLength = MathUtils.clamp(
    arrowLength * 0.2,
    0.12,
    0.85,
  )
  const arrowHeadWidth = MathUtils.clamp(
    arrowLength * 0.11,
    0.07,
    0.48,
  )
  const reference =
    Math.abs(direction.z) < 0.9
      ? new Vector3(0, 0, 1)
      : new Vector3(1, 0, 0)
  const side = new Vector3()
    .crossVectors(direction, reference)
    .normalize()
  const tip = center.clone().addScaledVector(direction, arrowLength)
  const headBase = tip
    .clone()
    .addScaledVector(direction, -arrowHeadLength)
  const left = headBase
    .clone()
    .addScaledVector(side, arrowHeadWidth)
  const right = headBase
    .clone()
    .addScaledVector(side, -arrowHeadWidth)
  const arrow = new LineSegments(
    new BufferGeometry().setFromPoints([
      center,
      tip,
      tip,
      left,
      tip,
      right,
    ]),
    new LineBasicMaterial({
      color,
      transparent: false,
      opacity: 1,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  arrow.renderOrder = 23
  root.add(arrow)
  return root
}

function createFacePatchBoundary(
  scene: ScenePayload,
  faceIds: Iterable<number>,
  center: Vector3,
  normal: Vector3,
  alwaysVisible = true,
): LineSegments<BufferGeometry, LineBasicMaterial> | null {
  const edges = new Map<
    string,
    { count: number; first: number; second: number }
  >()
  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    for (let edge = 0; edge < 3; edge += 1) {
      const first = face[edge]
      const second = face[(edge + 1) % 3]
      const key =
        first < second ? `${first}:${second}` : `${second}:${first}`
      const existing = edges.get(key)
      if (existing) existing.count += 1
      else edges.set(key, { count: 1, first, second })
    }
  }

  const positions: number[] = []
  const offset = normal.clone().multiplyScalar(0.02)
  for (const edge of edges.values()) {
    if (edge.count !== 1) continue
    const first = scene.mesh.vertices[edge.first]
    const second = scene.mesh.vertices[edge.second]
    if (!first || !second) continue
    positions.push(
      first[0] - center.x + offset.x,
      first[1] - center.y + offset.y,
      first[2] - center.z + offset.z,
      second[0] - center.x + offset.x,
      second[1] - center.y + offset.y,
      second[2] - center.z + offset.z,
    )
  }
  if (positions.length === 0) return null
  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )
  const boundary = new LineSegments(
    geometry,
    new LineBasicMaterial({
      color: 0xfbbf24,
      transparent: true,
      opacity: 1,
      depthTest: !alwaysVisible,
      depthWrite: false,
    }),
  )
  boundary.name = 'emitter-face-boundary'
  boundary.renderOrder = 22
  return boundary
}

function resolveFacePlacementFrame(
  scene: ScenePayload,
  faceIds: Iterable<number>,
): FacePlacementFrame | null {
  const points: Vector3[] = []
  const normalSum = new Vector3()
  const centerSum = new Vector3()
  let totalWeight = 0
  let referenceNormal: Vector3 | null = null
  let longestEdge = new Vector3(1, 0, 0)
  let longestEdgeLengthSq = 0

  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    const normalValues = scene.mesh.face_normals[faceId]
    if (!face || !normalValues) continue
    const vertices = face
      .map((vertexId) => scene.mesh.vertices[vertexId])
      .filter((vertex): vertex is [number, number, number] =>
        Boolean(vertex),
      )
      .map((vertex) => new Vector3(...vertex))
    if (vertices.length !== 3) continue

    const normal = new Vector3(...normalValues).normalize()
    if (!referenceNormal) referenceNormal = normal.clone()
    if (normal.dot(referenceNormal) < 0) normal.multiplyScalar(-1)
    const weight = Math.max(
      scene.mesh.face_areas_mm2[faceId] ?? 0,
      1e-6,
    )
    const centroid = vertices[0]
      .clone()
      .add(vertices[1])
      .add(vertices[2])
      .multiplyScalar(1 / 3)
    normalSum.addScaledVector(normal, weight)
    centerSum.addScaledVector(centroid, weight)
    totalWeight += weight
    points.push(...vertices)

    for (let edge = 0; edge < 3; edge += 1) {
      const edgeVector = vertices[(edge + 1) % 3]
        .clone()
        .sub(vertices[edge])
      if (edgeVector.lengthSq() > longestEdgeLengthSq) {
        longestEdge = edgeVector
        longestEdgeLengthSq = edgeVector.lengthSq()
      }
    }
  }

  if (points.length === 0 || totalWeight <= 0) return null
  const normal = normalSum.normalize()
  if (normal.lengthSq() < 0.5) return null
  const center = centerSum.multiplyScalar(1 / totalWeight)
  const uAxis = longestEdge
    .sub(normal.clone().multiplyScalar(longestEdge.dot(normal)))
    .normalize()
  if (uAxis.lengthSq() < 0.5) {
    uAxis
      .crossVectors(
        Math.abs(normal.z) < 0.9
          ? new Vector3(0, 0, 1)
          : new Vector3(0, 1, 0),
        normal,
      )
      .normalize()
  }
  const vAxis = new Vector3().crossVectors(normal, uAxis).normalize()
  let minU = Infinity
  let maxU = -Infinity
  let minV = Infinity
  let maxV = -Infinity
  for (const point of points) {
    const relative = point.clone().sub(center)
    const u = relative.dot(uAxis)
    const v = relative.dot(vAxis)
    minU = Math.min(minU, u)
    maxU = Math.max(maxU, u)
    minV = Math.min(minV, v)
    maxV = Math.max(maxV, v)
  }
  const width = Math.max(maxU - minU, 0.5)
  const height = Math.max(maxV - minV, 0.5)
  const planeCenter = center
    .clone()
    .addScaledVector(uAxis, (minU + maxU) / 2)
    .addScaledVector(vAxis, (minV + maxV) / 2)
    .addScaledVector(normal, Math.max(Math.hypot(width, height) * 0.001, 0.015))

  return {
    center: planeCenter.toArray(),
    height,
    normal: normal.toArray(),
    uAxis: uAxis.toArray(),
    vAxis: vAxis.toArray(),
    width,
  }
}

function createAxisLabel(
  text: string,
  color: string,
  depthTest = false,
): Sprite {
  const canvas = document.createElement('canvas')
  canvas.width = 96
  canvas.height = 96
  const context = canvas.getContext('2d')
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height)
    context.font = '800 52px Geist, Segoe UI, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.lineWidth = 8
    context.strokeStyle = 'rgba(2, 6, 23, 0.96)'
    context.strokeText(text, 48, 47)
    context.fillStyle = color
    context.fillText(text, 48, 47)
  }

  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  const label = new Sprite(
    new SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  label.scale.set(0.38, 0.38, 1)
  return label
}

function createOrientationGizmo(depthTest = false): Group {
  const gizmo = new Group()
  const renderOrder = depthTest ? 10 : 200
  const up = new Vector3(0, 1, 0)
  const axes = [
    {
      name: 'X',
      color: '#ef4444',
      hex: 0xef4444,
      direction: new Vector3(1, 0, 0),
    },
    {
      name: 'Y',
      color: '#22c55e',
      hex: 0x22c55e,
      direction: new Vector3(0, 1, 0),
    },
    {
      name: 'Z',
      color: '#3b82f6',
      hex: 0x3b82f6,
      direction: new Vector3(0, 0, 1),
    },
  ]

  for (const axis of axes) {
    const material = new MeshBasicMaterial({
      color: axis.hex,
      depthTest,
      depthWrite: false,
      toneMapped: false,
    })
    const shaft = new Mesh(
      new CylinderGeometry(0.022, 0.022, 1, 14),
      material,
    )
    shaft.position.copy(axis.direction).multiplyScalar(0.5)
    shaft.quaternion.setFromUnitVectors(up, axis.direction)
    shaft.renderOrder = renderOrder
    gizmo.add(shaft)

    const head = new Mesh(
      new ConeGeometry(0.065, 0.2, 18),
      material.clone(),
    )
    head.position.copy(axis.direction)
    head.quaternion.setFromUnitVectors(up, axis.direction)
    head.renderOrder = renderOrder + 1
    gizmo.add(head)

    const label = createAxisLabel(axis.name, axis.color, depthTest)
    label.position.copy(axis.direction).multiplyScalar(1.28)
    label.renderOrder = renderOrder + 2
    gizmo.add(label)
  }

  return gizmo
}

// Small sphere + 3-axis crosshair marking a picked/typed tilt pivot point,
// so the user can see exactly where it sits in the model - drawn with
// depthTest off (always on top) since a pivot buried inside solid geometry
// would otherwise be invisible.
function createPivotMarker(armLength: number): Group {
  const marker = new Group()
  marker.name = 'pivot-marker'
  const color = 0xf472b6
  const sphereMaterial = new MeshBasicMaterial({
    color,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  })
  const sphere = new Mesh(
    new SphereGeometry(Math.max(armLength * 0.18, 1e-3), 16, 12),
    sphereMaterial,
  )
  sphere.renderOrder = 220
  marker.add(sphere)

  const lineMaterial = new LineBasicMaterial({
    color,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    opacity: 0.9,
    toneMapped: false,
  })
  const axisDirections = [
    new Vector3(1, 0, 0),
    new Vector3(0, 1, 0),
    new Vector3(0, 0, 1),
  ]
  for (const direction of axisDirections) {
    const positions = new Float32Array([
      -direction.x * armLength,
      -direction.y * armLength,
      -direction.z * armLength,
      direction.x * armLength,
      direction.y * armLength,
      direction.z * armLength,
    ])
    const geometry = new BufferGeometry()
    geometry.setAttribute(
      'position',
      new Float32BufferAttribute(positions, 3),
    )
    const line = new LineSegments(geometry, lineMaterial)
    line.renderOrder = 220
    marker.add(line)
  }
  return marker
}

function clearGroup(group: Group | undefined): void {
  if (!group) return
  for (const child of [...group.children]) {
    group.remove(child)
    disposeObject(child)
  }
}

function viewerMaterialStyle(
  assignment: MaterialAssignment | undefined,
  fallbackColor: number,
): ViewerMaterialStyle {
  if (!assignment) {
    return {
      color: new Color(fallbackColor),
      metalness: 0.12,
      roughness: 0.72,
    }
  }

  const base = findBaseMaterial(assignment.baseMaterialId)
  const surface = findSurfaceProperty(assignment.surfaceId)
  return {
    // Display color is independent from the optical material catalog.
    color: new Color(fallbackColor),
    metalness: base.category === 'Metal' ? 0.58 : 0.04,
    roughness: surface.roughness,
  }
}

function faceOverlayMaterial(
  style: ViewerMaterialStyle,
  opacity: number,
): MeshStandardMaterial {
  return new MeshStandardMaterial({
    color: style.color,
    metalness: style.metalness,
    roughness: style.roughness,
    // Material overlays follow CAD triangle normals so hard edges remain
    // crisp after ROI clipping instead of showing interpolated bands.
    flatShading: true,
    side: DoubleSide,
    transparent: opacity < 1,
    opacity,
    // ROI component/material layers are coplanar display overlays. Let the
    // underlying clipped CAD surface own the depth buffer; writing depth
    // here intermittently occludes the real B-rep edge lines rendered
    // afterwards and produces broken, noisy internal-line artifacts.
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  })
}

// Component geometry is baked with its local origin at `center` (the
// component's own bounding-box center), and a Group only ever rotates
// around its own local origin. So rotating around a different pivot P
// means solving for the position that reproduces
// worldPos = P + move + R*(originalWorld - P) given a fixed local vertex
// (originalWorld - center): position = P + move + R*(center - P).
// With no override (P = center) this reduces to `center + move`, i.e.
// unchanged from the previous center-only behavior.
function pivotAdjustedPosition(
  center: Vector3,
  pivot: Vector3,
  move: Vector3,
  rotation: Euler,
): Vector3 {
  const centerOffset = new Vector3()
    .subVectors(center, pivot)
    .applyEuler(rotation)
  return new Vector3().copy(pivot).add(move).add(centerOffset)
}

function resolveTransformPivot(
  rule: ComponentTransformRule,
  fallbackCenter: Vector3,
): Vector3 {
  return rule.pivot
    ? new Vector3(rule.pivot.x, rule.pivot.y, rule.pivot.z)
    : fallbackCenter
}

function applyComponentTransform(
  node: ComponentRenderNode,
  transformRules: ComponentTransformRule[],
): void {
  const rule = transformRules.find(
    (candidate) =>
      candidate.enabled &&
      candidate.componentId === node.component.component_id &&
      candidate.targetType === 'component',
  )
  if (!rule) {
    node.group.position.copy(node.center)
    node.group.rotation.set(0, 0, 0)
    return
  }

  const rotation = new Euler(
    MathUtils.degToRad(rule.tilt.x),
    MathUtils.degToRad(rule.tilt.y),
    MathUtils.degToRad(rule.tilt.z),
  )
  const pivot = resolveTransformPivot(rule, node.center)
  const move = new Vector3(rule.move.x, rule.move.y, rule.move.z)
  node.group.rotation.copy(rotation)
  node.group.position.copy(
    pivotAdjustedPosition(node.center, pivot, move, rotation),
  )
}

function createRoiPointTransform(
  runtime: ViewerRuntime,
  transformRules: ComponentTransformRule[],
): RoiComponentPointTransform | undefined {
  const matrices = new Map<number, Matrix4>()
  for (const rule of transformRules) {
    if (
      !rule.enabled ||
      rule.targetType !== 'component' ||
      matrices.has(rule.componentId)
    ) {
      continue
    }
    const node = runtime.nodes.get(rule.componentId)
    if (!node) continue
    const rotation = new Matrix4().makeRotationFromEuler(
      new Euler(
        MathUtils.degToRad(rule.tilt.x),
        MathUtils.degToRad(rule.tilt.y),
        MathUtils.degToRad(rule.tilt.z),
      ),
    )
    const pivot = resolveTransformPivot(rule, node.center)
    const matrix = new Matrix4()
      .makeTranslation(
        pivot.x + rule.move.x,
        pivot.y + rule.move.y,
        pivot.z + rule.move.z,
      )
      .multiply(rotation)
      .multiply(
        new Matrix4().makeTranslation(-pivot.x, -pivot.y, -pivot.z),
      )
    matrices.set(rule.componentId, matrix)
  }
  if (matrices.size === 0) return undefined

  return (componentId, point) => {
    const matrix = matrices.get(componentId)
    if (!matrix) return [point[0], point[1], point[2]]
    const transformed = new Vector3(
      point[0],
      point[1],
      point[2],
    ).applyMatrix4(matrix)
    return [transformed.x, transformed.y, transformed.z]
  }
}

function fitCamera(
  runtime: ViewerRuntime,
  preset: ViewerCameraPreset,
): void {
  const fitRoot = runtime.roiPreviewRoot.visible
    ? runtime.roiPreviewRoot
    : runtime.modelRoot
  fitRoot.updateMatrixWorld(true)
  const bounds = new Box3().setFromObject(fitRoot)
  if (bounds.isEmpty()) return

  const center = bounds.getCenter(new Vector3())
  const size = bounds.getSize(new Vector3())
  const maxDimension = Math.max(size.x, size.y, size.z, 1)
  runtime.camera.fov = cameraFovForPreset(
    preset,
    runtime.camera.fov,
  )
  const verticalFov = MathUtils.degToRad(runtime.camera.fov)
  const horizontalFov =
    2 *
    Math.atan(
      Math.tan(verticalFov / 2) * Math.max(runtime.camera.aspect, 0.1),
    )
  const distance =
    Math.max(
      maxDimension / (2 * Math.tan(verticalFov / 2)),
      maxDimension / (2 * Math.tan(horizontalFov / 2)),
    ) * 1.35

  let direction = new Vector3(1, -1, 0.78)
  if (preset === 'Fit') {
    direction
      .subVectors(runtime.camera.position, runtime.controls.target)
      .normalize()
    if (direction.lengthSq() < 0.01) {
      direction.set(1, -1, 0.78)
    }
  } else if (preset === 'Iso') {
    runtime.camera.up.set(0, 0, 1)
  } else {
    const config = roiCameraPresetConfig[preset]
    direction.copy(config.direction)
    runtime.camera.up.copy(config.up)
  }

  runtime.camera.position
    .copy(center)
    .add(direction.normalize().multiplyScalar(distance))
  runtime.camera.near = Math.max(distance / 1000, 0.01)
  runtime.camera.far = Math.max(distance * 20, 1000)
  runtime.camera.updateProjectionMatrix()
  runtime.controls.target.copy(center)
  runtime.controls.update()
}

// Rotates the camera's "up" vector around the current view axis (rather
// than orbiting around the target), i.e. rolls the horizon. Used while ROI
// box-drag is armed, where normal orbit is locked so a plain drag always
// draws the box - Shift/Alt+drag still needs some way to reorient.
function rollCamera(runtime: ViewerRuntime, angleRad: number): void {
  const viewAxis = new Vector3()
    .subVectors(runtime.camera.position, runtime.controls.target)
    .normalize()
  runtime.camera.up.applyAxisAngle(viewAxis, -angleRad).normalize()
  runtime.camera.lookAt(runtime.controls.target)
  runtime.controls.update()
}

// Free-orbits the PIP "Full View" camera around its fixed target - the PIP
// has no TrackballControls instance of its own (it shares the main canvas
// with the primary camera), so drags starting inside the PIP rect are
// routed here instead.
function orbitPipCamera(
  runtime: ViewerRuntime,
  dx: number,
  dy: number,
): void {
  if (!dx && !dy) return
  const camera = runtime.pipCamera
  const target = runtime.pipTarget
  const offset = new Vector3().subVectors(camera.position, target)
  const yawQuat = new Quaternion().setFromAxisAngle(
    camera.up.clone().normalize(),
    -dx * 0.008,
  )
  offset.applyQuaternion(yawQuat)
  camera.updateMatrixWorld()
  const rightAxis = new Vector3()
    .setFromMatrixColumn(camera.matrixWorld, 0)
    .normalize()
  if (rightAxis.lengthSq() > 1e-10) {
    const pitchQuat = new Quaternion().setFromAxisAngle(
      rightAxis,
      -dy * 0.008,
    )
    offset.applyQuaternion(pitchQuat)
    camera.up.applyQuaternion(pitchQuat).normalize()
  }
  camera.position.copy(target).add(offset)
  camera.lookAt(target)
}

function restoreRoiSelectionCameraPose(
  runtime: ViewerRuntime,
): boolean {
  const pose = runtime.roiSelectionCameraPose
  if (!pose) return false

  runtime.camera.position.copy(pose.position)
  runtime.camera.up.copy(pose.up)
  runtime.camera.fov = pose.fov
  runtime.camera.near = pose.near
  runtime.camera.far = pose.far
  runtime.camera.updateProjectionMatrix()
  runtime.controls.target.copy(pose.target)
  runtime.controls.update()
  runtime.roiSelectionCameraPose = null
  runtime.roiSelectionPreset = null
  return true
}

function createComponentNode(
  scene: ScenePayload,
  component: SceneComponent,
  index: number,
): ComponentRenderNode {
  const bundle = createComponentGeometry(scene, component)
  const surfaceMaterial = new MeshStandardMaterial({
    color: resolveComponentColor(component, index),
    metalness: 0.12,
    roughness: 0.72,
    flatShading: false,
    side: DoubleSide,
    polygonOffset: true,
    // A slope-scaled factor creates visible seams where CAD faces meet.
    // Constant depth units keep coplanar components deterministic without
    // moving steep faces farther behind their shared feature edges.
    polygonOffsetFactor: 0,
    polygonOffsetUnits: surfaceDepthUnits(index),
  })
  const surface = new Mesh(bundle.geometry, surfaceMaterial)
  surface.name = `component-surface-${component.component_id}`
  surface.userData.componentId = component.component_id
  surface.userData.sourceFaceIds = bundle.faceIds
  surface.renderOrder = index

  const featureSegments = scene.mesh.feature_edge_segments.filter(
    (segment) => segment.component_id === component.component_id,
  )
  const edgeGeometry =
    featureSegments.length > 0
      ? createFeatureEdgeGeometry(featureSegments, bundle.center)
      : new EdgesGeometry(bundle.geometry, 24)
  const edges = new LineSegments(
    edgeGeometry,
    new LineBasicMaterial({
      color: 0xb9d5e8,
      transparent: true,
      opacity: 0.72,
      depthTest: true,
      depthWrite: false,
    }),
  )
  edges.name = `component-edges-${component.component_id}`
  edges.renderOrder = 100 + index
  const hiddenEdges = new LineSegments(
    edgeGeometry.clone(),
    new LineBasicMaterial({
      color: 0x8aa4b8,
      transparent: true,
      opacity: 0.16,
      depthTest: false,
      depthWrite: false,
    }),
  )
  hiddenEdges.name = `component-hidden-edges-${component.component_id}`
  hiddenEdges.renderOrder = 80 + index
  hiddenEdges.visible = false
  const wireframeFill = new Mesh(
    bundle.geometry.clone(),
    new MeshBasicMaterial({
      color: 0x263b4d,
      transparent: true,
      opacity: wireframeSurfaceOpacity,
      side: DoubleSide,
      depthTest: true,
      depthWrite: true,
      toneMapped: false,
    }),
  )
  wireframeFill.name = `component-wirefill-${component.component_id}`
  wireframeFill.renderOrder = index
  wireframeFill.visible = false

  const emitterOverlayRoot = new Group()
  const materialOverlayRoot = new Group()
  const roiOverlayRoot = new Group()
  const selectionOverlayRoot = new Group()
  const transformOverlayRoot = new Group()
  const group = new Group()
  group.name = `component-${component.component_id}`
  group.position.copy(bundle.center)
  group.add(
    surface,
    wireframeFill,
    hiddenEdges,
    edges,
    emitterOverlayRoot,
    materialOverlayRoot,
    roiOverlayRoot,
    selectionOverlayRoot,
    transformOverlayRoot,
  )

  return {
    center: bundle.center,
    component,
    depthPriority: index,
    edges,
    emitterOverlayRoot,
    group,
    hiddenEdges,
    materialOverlayRoot,
    roiOverlayRoot,
    selectionOverlayRoot,
    surface,
    transformOverlayRoot,
    wireframeFill,
  }
}

export function ThreeViewerCanvas({
  scene,
  cadModelVisible = true,
  axisScalePercent,
  surfaceTransparencyPercent,
  cameraPreset,
  cameraRequestId,
  renderMode,
  roiBoxSelectionArmed,
  roiFaceIds,
  roiScopes,
  rayTraceResult,
  editingComponentId,
  editingComponentMode,
  onRoiBoxSelection,
  onCameraFrameChange,
  onCameraPresetChange,
  onComponentContextMenu,
  onRayObjectContextMenu,
  onStatusMessage,
}: ThreeViewerCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<ViewerRuntime | null>(null)
  const roiBoxSelectionArmedRef = useRef(roiBoxSelectionArmed)
  const emitterFaceSelectionArmedRef = useRef(false)
  const materialFacePickArmedRef = useRef(false)
  const pivotPickArmedRef = useRef(false)
  const datumFacePickArmedRef = useRef(false)
  const selectedFaceIdsRef = useRef<number[]>([])
  const roiFaceIdsRef = useRef<number[]>(roiFaceIds)
  const roiScopesRef = useRef<RoiScope[]>(roiScopes)
  const selectedComponentIdsRef = useRef<number[]>([])
  const emittersRef = useRef<EmitterSpec[]>([])
  const onRoiBoxSelectionRef = useRef(onRoiBoxSelection)
  const onCameraFrameChangeRef = useRef(onCameraFrameChange)
  const onCameraPresetChangeRef = useRef(onCameraPresetChange)
  const onComponentContextMenuRef = useRef(onComponentContextMenu)
  const onRayObjectContextMenuRef = useRef(onRayObjectContextMenu)
  const boxDragRef = useRef<ViewerBoxDrag | null>(null)
  const fullViewCameraSyncRef = useRef(false)
  const fullViewSyncBaseMainDistanceRef = useRef<number | null>(null)
  const fullViewSyncBasePipDistanceRef = useRef<number | null>(null)
  const [rendererError, setRendererError] = useState('')
  const [boxDrag, setBoxDrag] = useState<ViewerBoxDrag | null>(null)
  const [fullViewCameraSync, setFullViewCameraSync] = useState(false)
  const selectedComponentIds = useWorkspaceStore(
    workspaceSelectors.selectedComponentIds,
  )
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const emitterFaceSelectionArmed = useWorkspaceStore(
    workspaceSelectors.emitterFaceSelectionArmed,
  )
  const materialFacePickArmed = useWorkspaceStore(
    workspaceSelectors.materialFacePickArmed,
  )
  const pivotPickArmed = useWorkspaceStore(
    workspaceSelectors.pivotPickArmed,
  )
  const datumFacePickArmed = useWorkspaceStore(
    workspaceSelectors.datumFacePickArmed,
  )
  const pivotPreviewPoint = useWorkspaceStore(
    workspaceSelectors.pivotPreviewPoint,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const rayPathDisplayFilters = useWorkspaceStore(
    workspaceSelectors.rayPathDisplayFilters,
  )
  const materialAssignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const componentColorOverrides = useWorkspaceStore(
    workspaceSelectors.componentColorOverrides,
  )
  const transformRules = useWorkspaceStore(
    workspaceSelectors.transformRules,
  )
  const emitters = useWorkspaceStore(workspaceSelectors.emitters)
  const receivers = useWorkspaceStore(workspaceSelectors.receivers)
  const placementPreviewEmitter = useWorkspaceStore(
    workspaceSelectors.placementPreviewEmitter,
  )
  const placementPreviewReceiver = useWorkspaceStore(
    workspaceSelectors.placementPreviewReceiver,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const surfaceOpacity = surfaceOpacityFromTransparency(
    surfaceTransparencyPercent,
  )

  useEffect(() => {
    emitterFaceSelectionArmedRef.current = emitterFaceSelectionArmed
  }, [emitterFaceSelectionArmed])

  useEffect(() => {
    materialFacePickArmedRef.current = materialFacePickArmed
  }, [materialFacePickArmed])

  useEffect(() => {
    pivotPickArmedRef.current = pivotPickArmed
  }, [pivotPickArmed])

  useEffect(() => {
    datumFacePickArmedRef.current = datumFacePickArmed
  }, [datumFacePickArmed])

  useEffect(() => {
    selectedFaceIdsRef.current = selectedFaceIds
  }, [selectedFaceIds])

  useEffect(() => {
    roiFaceIdsRef.current = roiFaceIds
  }, [roiFaceIds])

  useEffect(() => {
    roiScopesRef.current = roiScopes
  }, [roiScopes])

  useEffect(() => {
    selectedComponentIdsRef.current = selectedComponentIds
  }, [selectedComponentIds])

  useEffect(() => {
    emittersRef.current = emitters
  }, [emitters])

  useEffect(() => {
    roiBoxSelectionArmedRef.current = roiBoxSelectionArmed
  }, [roiBoxSelectionArmed])

  useEffect(() => {
    onRoiBoxSelectionRef.current = onRoiBoxSelection
  }, [onRoiBoxSelection])

  useEffect(() => {
    onCameraFrameChangeRef.current = onCameraFrameChange
  }, [onCameraFrameChange])

  useEffect(() => {
    onCameraPresetChangeRef.current = onCameraPresetChange
  }, [onCameraPresetChange])

  useEffect(() => {
    onComponentContextMenuRef.current = onComponentContextMenu
  }, [onComponentContextMenu])

  useEffect(() => {
    onRayObjectContextMenuRef.current = onRayObjectContextMenu
  }, [onRayObjectContextMenu])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer({
        canvas,
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
      })
    } catch {
      setRendererError(
        'WebGL 초기화에 실패했습니다. 그래픽 가속 설정을 확인하세요.',
      )
      return
    }

    setRendererError('')
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.outputColorSpace = SRGBColorSpace
    renderer.toneMapping = ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.05
    renderer.setClearColor(0x000000, 0)
    renderer.autoClear = false

    const threeScene = new Scene()
    const orientationScene = new Scene()
    const orientationCamera = new OrthographicCamera(
      -1.45,
      1.45,
      1.45,
      -1.45,
      0.1,
      10,
    )
    orientationScene.add(createOrientationGizmo())
    const camera = new PerspectiveCamera(
      DEFAULT_CAMERA_FOV_DEGREES,
      1,
      0.01,
      100000,
    )
    camera.up.set(0, 0, 1)
    // Fixed-angle camera for the "Full View" picture-in-picture inset shown
    // in the corner while an ROI is active - independent of the main
    // camera so the PIP always frames the whole model, not the ROI.
    const pipCamera = new PerspectiveCamera(45, 1, 0.01, 100000)
    pipCamera.up.set(0, 0, 1)
    const controls = new TrackballControls(camera, canvas)
    controls.staticMoving = true
    controls.rotateSpeed = 2.3
    controls.zoomSpeed = 1.2
    controls.panSpeed = 0.2
    controls.mouseButtons = {
      LEFT: MOUSE.ROTATE,
      MIDDLE: MOUSE.DOLLY,
      RIGHT: MOUSE.PAN,
    }

    const modelRoot = new Group()
    const placementRoot = new Group()
    placementRoot.name = 'ray-tracing-placement-root'
    const rayPathRoot = new Group()
    rayPathRoot.name = 'ray-path-overlay-root'
    const roiPreviewRoot = new Group()
    roiPreviewRoot.name = 'roi-preview-root'
    roiPreviewRoot.visible = false
    const roiSelectionRoot = new Group()
    roiSelectionRoot.name = 'roi-selection-overlay-root'
    roiSelectionRoot.visible = false
    const roiBoundsMarker = new Group()
    roiBoundsMarker.name = 'roi-bounds-marker-root'
    roiBoundsMarker.visible = false
    const pivotMarkerRoot = new Group()
    pivotMarkerRoot.name = 'pivot-marker-root'
    pivotMarkerRoot.visible = false
    threeScene.add(
      modelRoot,
      roiPreviewRoot,
      roiSelectionRoot,
      roiBoundsMarker,
      pivotMarkerRoot,
      placementRoot,
      rayPathRoot,
    )
    threeScene.add(new HemisphereLight(0xe7f5ff, 0x182337, 2.5))
    const keyLight = new DirectionalLight(0xffffff, 3.2)
    keyLight.position.set(1.5, -2.2, 3.4)
    threeScene.add(keyLight)
    const fillLight = new DirectionalLight(0x7dd3fc, 1.25)
    fillLight.position.set(-2, 1, 0.8)
    threeScene.add(fillLight)

    const bounds = getSceneBounds(scene)
    const maxDimension = Math.max(
      bounds.size.x,
      bounds.size.y,
      bounds.size.z,
      1,
    )
    const originAxisBaseScale = maxDimension * 0.1
    const globalOriginAxes = createOrientationGizmo(true)
    globalOriginAxes.name = 'global-origin-coordinate-axes'
    globalOriginAxes.position.set(0, 0, 0)
    globalOriginAxes.scale.setScalar(originAxisBaseScale)
    threeScene.add(globalOriginAxes)
    const nodes = new Map<number, ComponentRenderNode>()
    scene.components.forEach((component, index) => {
      const node = createComponentNode(scene, component, index)
      nodes.set(component.component_id, node)
      modelRoot.add(node.group)
    })

    const runtime: ViewerRuntime = {
      axisScalePercent: 50,
      camera,
      controls,
      globalOriginAxes,
      modelRoot,
      nodes,
      originAxisBaseScale,
      pipCamera,
      pipDistance: 0,
      pipTarget: new Vector3(),
      pipUserAdjusted: false,
      pipViewportRect: null,
      pivotMarkerRoot,
      placementRoot,
      rayPathRoot,
      raycaster: new Raycaster(),
      renderer,
      roiBoundsMarker,
      roiSelectionCameraPose: null,
      roiSelectionPreset: null,
      roiSelectionRoot,
      roiPreviewKey: '',
      roiPreviewRoot,
      scene: threeScene,
    }
    runtimeRef.current = runtime

    let viewportWidth = 1
    let viewportHeight = 1
    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const width = Math.max(Math.floor(rect.width), 1)
      const height = Math.max(Math.floor(rect.height), 1)
      viewportWidth = width
      viewportHeight = height
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      controls.handleResize()
    }
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(canvas)
    resize()
    fitCamera(runtime, 'Iso')
    onCameraFrameChangeRef.current?.(viewerCameraFrame(runtime))

    const emitCameraFrame = () => {
      onCameraFrameChangeRef.current?.(viewerCameraFrame(runtime))
    }
    controls.addEventListener('end', emitCameraFrame)

    let animationFrame = 0
    const animate = () => {
      controls.update()
      renderer.setScissorTest(false)
      renderer.setViewport(0, 0, viewportWidth, viewportHeight)
      renderer.clear()
      renderer.render(threeScene, camera)

      const gizmoSize = Math.max(
        44,
        Math.min(
          Math.round(168 * (runtime.axisScalePercent / 50)),
          Math.floor(viewportWidth * 0.5),
          Math.floor(viewportHeight * 0.5),
        ),
      )
      const gizmoX = 14
      const gizmoY = 46
      const cameraDirection = orientationCamera.position
        .subVectors(camera.position, controls.target)
        .normalize()
        .multiplyScalar(3)
      orientationCamera.position.copy(cameraDirection)
      orientationCamera.up.copy(camera.up).normalize()
      orientationCamera.lookAt(0, 0, 0)
      orientationCamera.updateMatrixWorld()

      renderer.clearDepth()
      renderer.setViewport(gizmoX, gizmoY, gizmoSize, gizmoSize)
      renderer.setScissor(gizmoX, gizmoY, gizmoSize, gizmoSize)
      renderer.setScissorTest(true)
      renderer.render(orientationScene, orientationCamera)
      renderer.setScissorTest(false)

      if (runtime.roiPreviewRoot.visible) {
        const pipWidth = Math.min(
          220,
          Math.floor(viewportWidth * 0.34),
        )
        const pipHeight = Math.min(
          160,
          Math.floor(viewportHeight * 0.34),
        )
        if (pipWidth > 24 && pipHeight > 24) {
          const pipMargin = 14
          const pipX = viewportWidth - pipWidth - pipMargin
          const pipY = pipMargin
          runtime.pipViewportRect = {
            x: pipX,
            y: viewportHeight - pipY - pipHeight,
            width: pipWidth,
            height: pipHeight,
          }

          pipCamera.aspect = pipWidth / pipHeight
          if (!runtime.pipUserAdjusted) {
            runtime.modelRoot.updateMatrixWorld(true)
            const fullBounds = new Box3().setFromObject(runtime.modelRoot)
            if (!fullBounds.isEmpty()) {
              const center = fullBounds.getCenter(new Vector3())
              const size = fullBounds.getSize(new Vector3())
              const maxDimension = Math.max(size.x, size.y, size.z, 1)
              const verticalFov = MathUtils.degToRad(pipCamera.fov)
              const horizontalFov =
                2 *
                Math.atan(
                  Math.tan(verticalFov / 2) *
                    Math.max(pipCamera.aspect, 0.1),
                )
              const distance =
                Math.max(
                  maxDimension / (2 * Math.tan(verticalFov / 2)),
                  maxDimension / (2 * Math.tan(horizontalFov / 2)),
                ) * 1.35
              runtime.pipTarget.copy(center)
              runtime.pipDistance = distance
              pipCamera.position
                .copy(center)
                .addScaledVector(
                  new Vector3(1, -1, 0.78).normalize(),
                  distance,
                )
              pipCamera.up.set(0, 0, 1)
              pipCamera.lookAt(center)
            }
          }
          if (fullViewCameraSyncRef.current) {
            const mainOffset = new Vector3().subVectors(
              camera.position,
              controls.target,
            )
            const mainDistance = Math.max(mainOffset.length(), 1e-6)
            if (fullViewSyncBaseMainDistanceRef.current === null) {
              fullViewSyncBaseMainDistanceRef.current = mainDistance
              fullViewSyncBasePipDistanceRef.current = runtime.pipDistance
            }
            const baseMainDistance = Math.max(
              fullViewSyncBaseMainDistanceRef.current,
              1e-6,
            )
            const basePipDistance = Math.max(
              fullViewSyncBasePipDistanceRef.current ?? runtime.pipDistance,
              1e-6,
            )
            runtime.pipDistance = MathUtils.clamp(
              basePipDistance * (mainDistance / baseMainDistance),
              Math.max(runtime.originAxisBaseScale * 0.1, 1e-3),
              runtime.originAxisBaseScale * 1000,
            )
            pipCamera.position
              .copy(runtime.pipTarget)
              .addScaledVector(
                mainOffset.normalize(),
                runtime.pipDistance,
              )
            pipCamera.up.copy(camera.up).normalize()
            pipCamera.lookAt(runtime.pipTarget)
          }
          pipCamera.near = Math.max(runtime.pipDistance / 1000, 0.01)
          pipCamera.far = Math.max(runtime.pipDistance * 20, 1000)
          pipCamera.updateProjectionMatrix()

          runtime.modelRoot.visible = true
          runtime.roiPreviewRoot.visible = false
          runtime.roiBoundsMarker.visible = true

          renderer.setScissorTest(true)
          renderer.setScissor(pipX, pipY, pipWidth, pipHeight)
          renderer.setViewport(pipX, pipY, pipWidth, pipHeight)
          renderer.clear(true, true, false)
          renderer.render(threeScene, pipCamera)
          renderer.setScissorTest(false)

          runtime.modelRoot.visible = false
          runtime.roiPreviewRoot.visible = true
          runtime.roiBoundsMarker.visible = false
        } else {
          runtime.pipViewportRect = null
        }
      } else {
        runtime.pipViewportRect = null
      }

      animationFrame = window.requestAnimationFrame(animate)
    }
    animationFrame = window.requestAnimationFrame(animate)

    const canvasPoint = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      return {
        x: Math.min(
          Math.max(event.clientX - rect.left, 0),
          Math.max(rect.width, 1),
        ),
        y: Math.min(
          Math.max(event.clientY - rect.top, 0),
          Math.max(rect.height, 1),
        ),
      }
    }
    const resolveBoxSelection = (
      selection: ViewerBoxDrag,
    ): RoiBoxSelectionResult | null => {
      const rect = canvas.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) return null
      const minX = Math.min(selection.startX, selection.currentX)
      const maxX = Math.max(selection.startX, selection.currentX)
      const minY = Math.min(selection.startY, selection.currentY)
      const maxY = Math.max(selection.startY, selection.currentY)
      const viewDirection = camera.getWorldDirection(new Vector3())
      const projectionPlane = new Plane().setFromNormalAndCoplanarPoint(
        viewDirection,
        controls.target,
      )
      const boxRaycaster = new Raycaster()
      const points: Vector3[] = []

      for (const [x, y] of [
        [minX, minY],
        [maxX, minY],
        [maxX, maxY],
        [minX, maxY],
      ]) {
        const pointer = new Vector2(
          (x / rect.width) * 2 - 1,
          -(y / rect.height) * 2 + 1,
        )
        boxRaycaster.setFromCamera(pointer, camera)
        const point = boxRaycaster.ray.intersectPlane(
          projectionPlane,
          new Vector3(),
        )
        if (point) points.push(point)
      }

      if (points.length !== 4) return null
      const preset = runtime.roiSelectionPreset
      if (!preset) return null
      const config = roiCameraPresetConfig[preset]
      const xValues = points.map((point) => point.x)
      const yValues = points.map((point) => point.y)
      const zValues = points.map((point) => point.z)
      const modelMinimum = bounds.center
        .clone()
        .sub(bounds.size.clone().multiplyScalar(0.5))
      const modelMaximum = bounds.center
        .clone()
        .add(bounds.size.clone().multiplyScalar(0.5))
      return {
        clipBox: {
          plane: config.plane,
          xMin:
            config.plane === 'yz'
              ? modelMinimum.x
              : Math.min(...xValues),
          xMax:
            config.plane === 'yz'
              ? modelMaximum.x
              : Math.max(...xValues),
          yMin:
            config.plane === 'zx'
              ? modelMinimum.y
              : Math.min(...yValues),
          yMax:
            config.plane === 'zx'
              ? modelMaximum.y
              : Math.max(...yValues),
          zMin:
            config.plane === 'xy'
              ? modelMinimum.z
              : Math.min(...zValues),
          zMax:
            config.plane === 'xy'
              ? modelMaximum.z
              : Math.max(...zValues),
        },
        view: config.view,
      }
    }

    const resolveSurfaceHit = (clientX: number, clientY: number) => {
      const rect = canvas.getBoundingClientRect()
      const pointer = new Vector2(
        ((clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1,
        -((clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1,
      )
      runtime.raycaster.setFromCamera(pointer, camera)
      const candidates = runtime.roiPreviewRoot.visible
        ? runtime.roiPreviewRoot.children.filter(
            (child): child is Mesh<BufferGeometry, Material> =>
              child instanceof Mesh &&
              (Array.isArray(
                child.geometry.userData.sourceFaceIds,
              ) ||
                Array.isArray(
                  child.geometry.userData.componentIds,
                )),
          )
        : [...nodes.values()]
            .filter((node) => node.group.visible)
            .map((node) => node.surface)
      const hits = runtime.raycaster.intersectObjects(candidates, false)

      for (const hit of hits) {
        if (!(hit.object instanceof Mesh)) continue
        const hitFaceIndex = hit.faceIndex
        if (hitFaceIndex === null || hitFaceIndex === undefined) continue
        const sourceFaceIds =
          (hit.object.userData.sourceFaceIds as number[] | undefined) ??
          (hit.object.geometry.userData.sourceFaceIds as
            | number[]
            | undefined)
        const componentIds =
          (hit.object.userData.componentIds as number[] | undefined) ??
          (hit.object.geometry.userData.componentIds as
            | number[]
            | undefined)
        const sourceFaceId = sourceFaceIds?.[hitFaceIndex]
        const faceId =
          typeof sourceFaceId === 'number' &&
          Number.isSafeInteger(sourceFaceId)
          ? sourceFaceId
          : null
        const objectComponentId = Number(
          hit.object.userData.componentId,
        )
        const fallbackComponentId =
          componentIds?.[hitFaceIndex] ??
          (faceId === null
            ? null
            : scene.mesh.face_component_ids[faceId])
        const componentId =
          Number.isSafeInteger(objectComponentId)
            ? objectComponentId
            : typeof fallbackComponentId === 'number' &&
                Number.isSafeInteger(fallbackComponentId)
              ? fallbackComponentId
              : null
        if (
          componentId === null ||
          !Number.isSafeInteger(componentId)
        ) {
          continue
        }
        const worldNormal = hit.face
          ? hit.face.normal
              .clone()
              .transformDirection(hit.object.matrixWorld)
          : null
        return {
          componentId,
          faceId,
          isRoiCap: hit.object.name === 'roi-section-caps',
          normal: worldNormal
            ? ([worldNormal.x, worldNormal.y, worldNormal.z] as [
                number,
                number,
                number,
              ])
            : null,
          point: [hit.point.x, hit.point.y, hit.point.z] as [
            number,
            number,
            number,
          ],
        }
      }
      return null
    }

    // NX-style pivot point snapping: within a small on-screen radius of the
    // click, prefer an edge endpoint (corner/intersection) first, then an
    // edge midpoint, over the exact raycast point - a rotation pivot is
    // almost always one of those two, rarely an arbitrary point on a face.
    const pivotSnapToleranceCss = 22
    const resolveEdgePivotSnap = (
      clientX: number,
      clientY: number,
      componentId: number,
    ): [number, number, number] | null => {
      const rect = canvas.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) return null

      let bestPoint: [number, number, number] | null = null
      let bestPriority = Infinity
      let bestPixelDistance = Infinity

      const consider = (point: [number, number, number], priority: number) => {
        const projected = new Vector3(
          point[0],
          point[1],
          point[2],
        ).project(camera)
        if (projected.z < -1 || projected.z > 1) return
        const screenX = rect.left + (projected.x + 1) * 0.5 * rect.width
        const screenY = rect.top + (1 - projected.y) * 0.5 * rect.height
        const pixelDistance = Math.hypot(
          screenX - clientX,
          screenY - clientY,
        )
        if (pixelDistance > pivotSnapToleranceCss) return
        if (
          priority < bestPriority ||
          (priority === bestPriority && pixelDistance < bestPixelDistance)
        ) {
          bestPoint = point
          bestPriority = priority
          bestPixelDistance = pixelDistance
        }
      }

      for (const segment of scene.mesh.feature_edge_segments) {
        if (segment.component_id !== componentId) continue
        consider(segment.start, 0)
        consider(segment.end, 0)
        consider(
          [
            (segment.start[0] + segment.end[0]) / 2,
            (segment.start[1] + segment.end[1]) / 2,
            (segment.start[2] + segment.end[2]) / 2,
          ],
          1,
        )
      }

      return bestPoint
    }

    const resolveRayObjectHit = (
      clientX: number,
      clientY: number,
    ): { id: string; kind: 'emitter' | 'receiver' } | null => {
      const rect = canvas.getBoundingClientRect()
      const pointer = new Vector2(
        ((clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1,
        -((clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1,
      )
      runtime.raycaster.setFromCamera(pointer, camera)
      const hits = runtime.raycaster.intersectObjects(
        runtime.placementRoot.children,
        true,
      )
      for (const hit of hits) {
        let object: Object3D | null = hit.object
        while (object && object !== runtime.placementRoot) {
          const kind = object.userData.rayObjectKind
          const id = object.userData.rayObjectId
          if (
            (kind === 'emitter' || kind === 'receiver') &&
            typeof id === 'string' &&
            !id.startsWith('__placement_preview_')
          ) {
            return { id, kind }
          }
          object = object.parent
        }
      }
      return null
    }

    let pointerDown: { x: number; y: number } | null = null
    let rightPointerDown: { x: number; y: number } | null = null
    let rightPointerMoved = false
    let rollDrag: { lastX: number } | null = null
    let pipDrag: { lastX: number; lastY: number } | null = null
    const pointInPipViewport = (point: { x: number; y: number }) => {
      const rect = runtime.pipViewportRect
      if (!rect) return false
      return (
        point.x >= rect.x &&
        point.x <= rect.x + rect.width &&
        point.y >= rect.y &&
        point.y <= rect.y + rect.height
      )
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (event.button === 2) {
        rightPointerDown = { x: event.clientX, y: event.clientY }
        rightPointerMoved = false
        return
      }
      if (event.button !== 0) return
      if (
        runtime.roiPreviewRoot.visible &&
        pointInPipViewport(canvasPoint(event))
      ) {
        event.preventDefault()
        pointerDown = null
        pipDrag = { lastX: event.clientX, lastY: event.clientY }
        runtime.pipUserAdjusted = true
        canvas.setPointerCapture(event.pointerId)
        return
      }
      if (roiBoxSelectionArmedRef.current) {
        // Shift/Alt+drag stays free to roll the camera even while armed -
        // orbit is locked so a plain drag always draws the box, but the
        // user still needs some way to reorient without disarming.
        if (event.shiftKey || event.altKey) {
          event.preventDefault()
          pointerDown = null
          rollDrag = { lastX: event.clientX }
          canvas.setPointerCapture(event.pointerId)
          return
        }
        event.preventDefault()
        pointerDown = null
        const point = canvasPoint(event)
        const selection = {
          startX: point.x,
          startY: point.y,
          currentX: point.x,
          currentY: point.y,
        }
        boxDragRef.current = selection
        setBoxDrag(selection)
        canvas.setPointerCapture(event.pointerId)
        return
      }
      pointerDown = { x: event.clientX, y: event.clientY }
    }
    const handlePointerMove = (event: PointerEvent) => {
      if (
        rightPointerDown &&
        Math.hypot(
          event.clientX - rightPointerDown.x,
          event.clientY - rightPointerDown.y,
        ) > 5
      ) {
        rightPointerMoved = true
      }
      if (rollDrag) {
        const dx = event.clientX - rollDrag.lastX
        rollDrag.lastX = event.clientX
        rollCamera(runtime, dx * 0.012)
        emitCameraFrame()
        return
      }
      if (pipDrag) {
        const dx = event.clientX - pipDrag.lastX
        const dy = event.clientY - pipDrag.lastY
        pipDrag.lastX = event.clientX
        pipDrag.lastY = event.clientY
        orbitPipCamera(runtime, dx, dy)
        return
      }
      const selection = boxDragRef.current
      if (!selection) return
      const point = canvasPoint(event)
      const nextSelection = {
        ...selection,
        currentX: point.x,
        currentY: point.y,
      }
      boxDragRef.current = nextSelection
      setBoxDrag(nextSelection)
    }
    const handlePointerUp = (event: PointerEvent) => {
      if (event.button === 2) {
        if (
          rightPointerDown &&
          Math.hypot(
            event.clientX - rightPointerDown.x,
            event.clientY - rightPointerDown.y,
          ) > 5
        ) {
          rightPointerMoved = true
        }
        rightPointerDown = null
        return
      }
      if (event.button !== 0) return
      if (rollDrag) {
        rollDrag = null
        if (canvas.hasPointerCapture(event.pointerId)) {
          canvas.releasePointerCapture(event.pointerId)
        }
        return
      }
      if (pipDrag) {
        pipDrag = null
        if (canvas.hasPointerCapture(event.pointerId)) {
          canvas.releasePointerCapture(event.pointerId)
        }
        return
      }
      const selection = boxDragRef.current
      if (selection) {
        const point = canvasPoint(event)
        const completedSelection = {
          ...selection,
          currentX: point.x,
          currentY: point.y,
        }
        boxDragRef.current = null
        setBoxDrag(null)
        if (canvas.hasPointerCapture(event.pointerId)) {
          canvas.releasePointerCapture(event.pointerId)
        }
        const movement =
          Math.abs(completedSelection.currentX - completedSelection.startX) +
          Math.abs(completedSelection.currentY - completedSelection.startY)
        if (movement <= 8) {
          actions.setRoiBoxSelectionArmed(false)
          onStatusMessage('ROI 박스 선택을 취소했습니다.')
          return
        }

        const result = resolveBoxSelection(completedSelection)
        if (!result) {
          actions.setRoiBoxSelectionArmed(false)
          onStatusMessage('ROI 박스의 모델 좌표를 계산하지 못했습니다.')
          return
        }
        onRoiBoxSelectionRef.current(result)
        return
      }
      if (!pointerDown) return
      const movement = Math.hypot(
        event.clientX - pointerDown.x,
        event.clientY - pointerDown.y,
      )
      pointerDown = null
      if (movement > 5) return

      const hit = resolveSurfaceHit(event.clientX, event.clientY)
      const additive = event.ctrlKey || event.metaKey || event.shiftKey

      if (pivotPickArmedRef.current) {
        if (!hit) {
          onStatusMessage(
            'Pivot picking · 모델 표면을 클릭해 회전 기준점을 선택하세요.',
          )
          return
        }
        const snapped = resolveEdgePivotSnap(
          event.clientX,
          event.clientY,
          hit.componentId,
        )
        const [x, y, z] = snapped ?? hit.point
        actions.setPivotPickPoint({ x, y, z })
        actions.setPivotPickArmed(false)
        onStatusMessage(
          snapped
            ? `Pivot picking · edge 지점에 스냅 · (${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)})`
            : `Pivot picking · (${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)}) 선택됨`,
        )
        return
      }

      if (datumFacePickArmedRef.current) {
        if (!hit) {
          onStatusMessage(
            'Datum face picking · 기구 도면의 face를 클릭하세요.',
          )
          return
        }
        if (hit.faceId === null) {
          if (!hit.isRoiCap || !hit.normal) {
            onStatusMessage(
              'Datum face picking · 선택할 수 없는 표면입니다.',
            )
            return
          }
          // ROI section caps aren't original CAD faces (no faceId to
          // flood-fill from), but they're still a valid, well-defined flat
          // plane - offsetting a receiver from the cut plane is a normal
          // workflow, so accept the pick instead of rejecting it outright.
          const axisIndex = [0, 1, 2].reduce((best, axis) =>
            Math.abs(hit.normal![axis]) > Math.abs(hit.normal![best])
              ? axis
              : best,
          )
          const snappedNormal: [number, number, number] = [0, 0, 0]
          snappedNormal[axisIndex] = hit.normal[axisIndex] >= 0 ? 1 : -1
          // Snap to the center of the ROI box's own rectangular face on
          // this axis (not the raw click point) - the same way picking a
          // real CAD face always lands on that face's center regardless of
          // where on it you click, instead of tracking the cursor.
          const boxAxisRanges = (
            box: RoiClipBox,
          ): [
            [number, number],
            [number, number],
            [number, number] | undefined,
          ] => [
            [box.xMin, box.xMax],
            [box.yMin, box.yMax],
            box.zMin !== undefined && box.zMax !== undefined
              ? [box.zMin, box.zMax]
              : undefined,
          ]
          const capTolerance = 0.5
          const matchingBox = roiScopesRef.current
            .filter((scope) => scope.active)
            .map((scope) => scope.clipBox)
            .find((box): box is RoiClipBox => {
              if (!box) return false
              const ranges = boxAxisRanges(box)
              const cutRange = ranges[axisIndex]
              if (!cutRange) return false
              const onPlane =
                Math.abs(hit.point[axisIndex] - cutRange[0]) <=
                  capTolerance ||
                Math.abs(hit.point[axisIndex] - cutRange[1]) <= capTolerance
              if (!onPlane) return false
              return [0, 1, 2].every((axis) => {
                if (axis === axisIndex) return true
                const range = ranges[axis]
                if (!range) return true
                return (
                  hit.point[axis] >= range[0] - capTolerance &&
                  hit.point[axis] <= range[1] + capTolerance
                )
              })
            })
          const capCenter: [number, number, number] = [...hit.point]
          if (matchingBox) {
            const ranges = boxAxisRanges(matchingBox)
            for (const axis of [0, 1, 2]) {
              if (axis === axisIndex) continue
              const range = ranges[axis]
              if (range) capCenter[axis] = (range[0] + range[1]) / 2
            }
          }
          actions.setDatumFacePickResult({
            center: { x: capCenter[0], y: capCenter[1], z: capCenter[2] },
            normal: {
              x: snappedNormal[0],
              y: snappedNormal[1],
              z: snappedNormal[2],
            },
            faceIds: [],
          })
          actions.setSelectedFaceIds([])
          actions.setDatumFacePickArmed(false)
          onStatusMessage(
            matchingBox
              ? 'Datum face picking · ROI 절단면 선택됨 (절단면 중심 기준)'
              : 'Datum face picking · ROI 절단면 선택됨 (클릭 지점 기준)',
          )
          return
        }
        const datumComponent = scene.components.find(
          (candidate) => candidate.component_id === hit.componentId,
        )
        const componentFaceIds =
          datumComponent?.face_indices ?? [hit.faceId]
        // When an ROI clip is active, a face can be cut off mid-surface -
        // the coplanar patch must stay within the faces actually included
        // in the ROI, not flood-fill into the original (uncut) face and
        // center on geometry the user can't even see right now.
        const roiFaceIdSet = runtime.roiPreviewRoot.visible
          ? new Set(roiFaceIdsRef.current)
          : null
        const candidateFaceIds = roiFaceIdSet
          ? componentFaceIds.filter((faceId) => roiFaceIdSet.has(faceId))
          : componentFaceIds
        const patchFaceIds = findCadSurfaceFaceIds(
          scene,
          candidateFaceIds,
          hit.faceId,
        )
        let weightedX = 0
        let weightedY = 0
        let weightedZ = 0
        let totalArea = 0
        for (const patchFaceId of patchFaceIds) {
          const centroid = scene.mesh.face_centroids[patchFaceId]
          const area = scene.mesh.face_areas_mm2[patchFaceId] ?? 0
          if (!centroid || area <= 0) continue
          weightedX += centroid[0] * area
          weightedY += centroid[1] * area
          weightedZ += centroid[2] * area
          totalArea += area
        }
        const center =
          totalArea > 0
            ? [
                weightedX / totalArea,
                weightedY / totalArea,
                weightedZ / totalArea,
              ]
            : hit.point
        const normalVector = scene.mesh.face_normals[hit.faceId]
        actions.setDatumFacePickResult({
          center: { x: center[0], y: center[1], z: center[2] },
          normal: {
            x: normalVector[0],
            y: normalVector[1],
            z: normalVector[2],
          },
          faceIds: patchFaceIds,
        })
        actions.setSelectedFaceIds(patchFaceIds)
        actions.setSelectedComponentIds([hit.componentId])
        actions.setDatumFacePickArmed(false)
        onStatusMessage('Datum face picking · surface 중심 선택됨')
        return
      }

      if (!hit) {
        if (!additive) {
          actions.setSelectedComponentIds([])
          actions.setSelectedFaceIds([])
          onStatusMessage('Viewer selection을 해제했습니다.')
        }
        return
      }

      const { componentId, faceId } = hit

      if (emitterFaceSelectionArmedRef.current) {
        if (faceId === null) {
          onStatusMessage(
            `Emitter surface picking · Component ${componentId}의 ROI 절단면은 원본 CAD face가 아니므로 발광면으로 선택할 수 없습니다.`,
          )
          return
        }
        // Same coplanar-patch, toggle-add/remove behavior as the Material
        // editor's Face 지정 picker and Transform's Local faces picker - a
        // click grabs/releases the whole CAD surface as drawn, regardless
        // of modifier keys, so all four "면 지정" pickers in the app behave
        // identically.
        const component = scene.components.find(
          (candidate) => candidate.component_id === componentId,
        )
        const patchFaceIds = findCadSurfaceFaceIds(
          scene,
          component?.face_indices ?? [faceId],
          faceId,
        )
        const nextFaceIds = new Set(selectedFaceIdsRef.current)
        const removePatch = patchFaceIds.every((id) => nextFaceIds.has(id))
        for (const id of patchFaceIds) {
          if (removePatch) nextFaceIds.delete(id)
          else nextFaceIds.add(id)
        }
        actions.setSelectedFaceIds(nextFaceIds)
        actions.setSelectedComponentIds([
          ...new Set([...selectedComponentIdsRef.current, componentId]),
        ])
        onStatusMessage(
          `Emitter surface picking · Component ${componentId} · surface ${removePatch ? '해제' : '추가'}`,
        )
        return
      }

      if (materialFacePickArmedRef.current) {
        if (faceId === null) {
          onStatusMessage(
            `Material face picking · Component ${componentId}의 ROI 절단면은 원본 CAD face가 아니므로 선택할 수 없습니다.`,
          )
          return
        }
        // A click should select the whole flat/curved CAD surface the user
        // sees, not the single underlying mesh triangle - expand to the
        // coplanar patch (same helper the Emitter surface picker uses), and
        // toggle add/remove regardless of modifier keys so several surfaces
        // can be gathered without holding Ctrl/Shift for each one.
        const component = scene.components.find(
          (candidate) => candidate.component_id === componentId,
        )
        const patchFaceIds = findCadSurfaceFaceIds(
          scene,
          component?.face_indices ?? [faceId],
          faceId,
        )
        const nextFaceIds = new Set(selectedFaceIdsRef.current)
        const removePatch = patchFaceIds.every((id) => nextFaceIds.has(id))
        for (const id of patchFaceIds) {
          if (removePatch) nextFaceIds.delete(id)
          else nextFaceIds.add(id)
        }
        actions.setSelectedFaceIds(nextFaceIds)
        actions.setSelectedComponentIds([
          ...new Set([...selectedComponentIdsRef.current, componentId]),
        ])
        onStatusMessage(
          `Material face picking · Component ${componentId} · surface ${removePatch ? '해제' : '추가'}`,
        )
        return
      }

      if (additive) {
        actions.toggleSelectedComponentId(componentId)
        if (faceId !== null) actions.toggleSelectedFaceId(faceId)
      } else {
        actions.setSelectedComponentIds([componentId])
        actions.setSelectedFaceIds(
          faceId === null ? [] : [faceId],
        )
      }
      onStatusMessage(
        faceId === null
          ? `Viewer picking · Component ${componentId} · ROI section cap`
          : `Viewer picking · Component ${componentId} · face selected`,
      )
    }
    const handleDoubleClick = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const point = {
        x: Math.min(
          Math.max(event.clientX - rect.left, 0),
          Math.max(rect.width, 1),
        ),
        y: Math.min(
          Math.max(event.clientY - rect.top, 0),
          Math.max(rect.height, 1),
        ),
      }
      if (runtime.roiPreviewRoot.visible && pointInPipViewport(point)) {
        runtime.pipUserAdjusted = false
        onStatusMessage('Full View PIP · 다시 전체 맞춤')
        return
      }
      fitCamera(runtime, 'Fit')
      emitCameraFrame()
      onStatusMessage('Camera preset · Fit')
    }
    const handlePipWheel = (event: WheelEvent) => {
      if (!runtime.roiPreviewRoot.visible) return
      const rect = canvas.getBoundingClientRect()
      const point = {
        x: Math.min(
          Math.max(event.clientX - rect.left, 0),
          Math.max(rect.width, 1),
        ),
        y: Math.min(
          Math.max(event.clientY - rect.top, 0),
          Math.max(rect.height, 1),
        ),
      }
      if (!pointInPipViewport(point)) return
      // Registered on the capture phase so this runs before
      // TrackballControls' own wheel listener would otherwise zoom the
      // main camera instead.
      event.preventDefault()
      event.stopImmediatePropagation()
      const camera = runtime.pipCamera
      const offset = new Vector3().subVectors(
        camera.position,
        runtime.pipTarget,
      )
      const zoomFactor = Math.exp(event.deltaY * 0.001)
      const minDistance = Math.max(runtime.originAxisBaseScale * 0.1, 1e-3)
      const maxDistance = runtime.originAxisBaseScale * 1000
      const nextDistance = MathUtils.clamp(
        offset.length() * zoomFactor,
        minDistance,
        maxDistance,
      )
      offset.setLength(nextDistance)
      camera.position.copy(runtime.pipTarget).add(offset)
      runtime.pipDistance = nextDistance
      runtime.pipUserAdjusted = true
    }
    let pointerOverCanvas = false
    const handlePointerEnter = () => {
      pointerOverCanvas = true
    }
    const handlePointerLeave = () => {
      pointerOverCanvas = false
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== 'f') return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (!pointerOverCanvas) return
      const activeTag = document.activeElement?.tagName
      if (
        activeTag === 'INPUT' ||
        activeTag === 'TEXTAREA' ||
        activeTag === 'SELECT'
      ) {
        return
      }
      event.preventDefault()
      fitCamera(runtime, 'Fit')
      emitCameraFrame()
      onStatusMessage('Camera preset · Fit (F)')
    }
    const handlePointerCancel = () => {
      pointerDown = null
      rightPointerDown = null
      rightPointerMoved = false
      rollDrag = null
      pipDrag = null
      boxDragRef.current = null
      setBoxDrag(null)
    }
    const handleContextMenu = (event: MouseEvent) => {
      const suppressMenu =
        rightPointerMoved ||
        roiBoxSelectionArmedRef.current ||
        emitterFaceSelectionArmedRef.current ||
        materialFacePickArmedRef.current
      rightPointerDown = null
      rightPointerMoved = false
      if (suppressMenu) {
        event.preventDefault()
        event.stopPropagation()
        return
      }

      const rayObjectHit = resolveRayObjectHit(
        event.clientX,
        event.clientY,
      )
      if (rayObjectHit) {
        onRayObjectContextMenuRef.current?.({
          clientX: event.clientX,
          clientY: event.clientY,
          ...rayObjectHit,
          returnFocusElement: canvas,
        })
        onStatusMessage(
          `${rayObjectHit.kind === 'emitter' ? 'Emitter' : 'Receiver'} menu · ${rayObjectHit.id}`,
        )
        event.preventDefault()
        event.stopPropagation()
        return
      }

      const hit = resolveSurfaceHit(event.clientX, event.clientY)
      if (!hit) {
        event.preventDefault()
        event.stopPropagation()
        return
      }

      const faceEmitter =
        hit.faceId === null
          ? null
          : emittersRef.current.find(
              (emitter) =>
                emitter.enabled &&
                emitter.emitter_type === 'face' &&
                emitter.face_indices.includes(hit.faceId as number),
            ) ?? null
      if (faceEmitter) {
        onRayObjectContextMenuRef.current?.({
          clientX: event.clientX,
          clientY: event.clientY,
          id: faceEmitter.emitter_id,
          kind: 'emitter',
          returnFocusElement: canvas,
        })
        onStatusMessage(`Emitter menu · ${faceEmitter.emitter_id}`)
        event.preventDefault()
        event.stopPropagation()
        return
      }

      actions.setSelectedComponentIds([hit.componentId])
      actions.setSelectedFaceIds([])
      onComponentContextMenuRef.current?.({
        clientX: event.clientX,
        clientY: event.clientY,
        componentId: hit.componentId,
        returnFocusElement: canvas,
      })
      onStatusMessage(
        `Component menu · Component ${hit.componentId}`,
      )
      event.preventDefault()
      event.stopPropagation()
    }

    canvas.addEventListener('pointerdown', handlePointerDown)
    canvas.addEventListener('pointermove', handlePointerMove)
    canvas.addEventListener('pointerup', handlePointerUp)
    canvas.addEventListener('pointercancel', handlePointerCancel)
    canvas.addEventListener('dblclick', handleDoubleClick)
    canvas.addEventListener('contextmenu', handleContextMenu)
    canvas.addEventListener('pointerenter', handlePointerEnter)
    canvas.addEventListener('pointerleave', handlePointerLeave)
    canvas.addEventListener('wheel', handlePipWheel, {
      capture: true,
      passive: false,
    })
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      canvas.removeEventListener('pointerdown', handlePointerDown)
      canvas.removeEventListener('pointermove', handlePointerMove)
      canvas.removeEventListener('pointerup', handlePointerUp)
      canvas.removeEventListener('pointercancel', handlePointerCancel)
      canvas.removeEventListener('dblclick', handleDoubleClick)
      canvas.removeEventListener('contextmenu', handleContextMenu)
      canvas.removeEventListener('pointerenter', handlePointerEnter)
      canvas.removeEventListener('pointerleave', handlePointerLeave)
      canvas.removeEventListener('wheel', handlePipWheel, {
        capture: true,
      })
      window.removeEventListener('keydown', handleKeyDown)
      controls.removeEventListener('end', emitCameraFrame)
      controls.dispose()
      disposeObject(threeScene)
      disposeObject(orientationScene)
      renderer.dispose()
      runtimeRef.current = null
    }
  }, [actions, onStatusMessage, scene])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    fitCamera(runtime, cameraPreset)
    onCameraFrameChangeRef.current?.(viewerCameraFrame(runtime))
    if (
      roiBoxSelectionArmed &&
      Object.prototype.hasOwnProperty.call(
        roiCameraPresetConfig,
        cameraPreset,
      )
    ) {
      // The armed-selection plane (which axis the drag box is unbounded
      // on) follows whichever ROI preset is active - switching views
      // mid-arm must update it too, or a box drawn from the new view
      // gets resolved against the old view's plane.
      const preset = cameraPreset as RoiCameraPreset
      runtime.roiSelectionPreset = preset
      onStatusMessage(
        `ROI 박스 선택 · ${preset} view · 왼쪽 드래그로 범위를 지정하세요.`,
      )
    }
  }, [cameraPreset, cameraRequestId, onStatusMessage, roiBoxSelectionArmed, scene])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    runtime.axisScalePercent = axisScalePercent
    runtime.globalOriginAxes.scale.setScalar(
      runtime.originAxisBaseScale * (axisScalePercent / 50),
    )
  }, [axisScalePercent])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    clearGroup(runtime.pivotMarkerRoot)
    if (!pivotPreviewPoint) {
      runtime.pivotMarkerRoot.visible = false
      return
    }
    const marker = createPivotMarker(runtime.originAxisBaseScale * 0.6)
    marker.position.set(
      pivotPreviewPoint.x,
      pivotPreviewPoint.y,
      pivotPreviewPoint.z,
    )
    runtime.pivotMarkerRoot.add(marker)
    runtime.pivotMarkerRoot.visible = true
  }, [pivotPreviewPoint])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return

    runtime.controls.noRotate = roiBoxSelectionArmed
    if (roiBoxSelectionArmed) {
      if (!runtime.roiSelectionCameraPose) {
        runtime.roiSelectionCameraPose = {
          far: runtime.camera.far,
          fov: runtime.camera.fov,
          near: runtime.camera.near,
          position: runtime.camera.position.clone(),
          target: runtime.controls.target.clone(),
          up: runtime.camera.up.clone(),
        }
        // Always start ROI box selection from the top-down plan view
        // rather than whichever axis happens to be nearest to the current
        // camera angle - for thin/elongated models (e.g. a flat panel),
        // "nearest" can land on a near-degenerate edge-on view that's
        // useless for drawing a box, and users have no way to predict it.
        runtime.roiSelectionPreset = 'XY'
      }
      runtime.roiPreviewRoot.visible = false
      runtime.modelRoot.visible = true
      const preset = runtime.roiSelectionPreset ?? 'XY'
      fitCamera(runtime, preset)
      // The auto-snap-to-nearest-axis above moves the camera directly on
      // the runtime without going through the cameraPreset prop, so the
      // toolbar button/pill would otherwise keep showing whatever preset
      // was active before arming (e.g. "Iso") while the view is actually
      // locked to a different one - report the real preset back so the UI
      // stays in sync.
      onCameraPresetChangeRef.current?.(preset)
      onStatusMessage(
        `ROI 박스 선택 · ${preset} view · 왼쪽 드래그로 범위를 지정하세요.`,
      )
    }
  }, [onStatusMessage, roiBoxSelectionArmed, scene])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return

    const activeBoxScopes = roiScopes.filter(
      (scope) => scope.active && scope.clipBox,
    )
    const showRoiPreview =
      activeBoxScopes.length > 0 && !roiBoxSelectionArmed
    if (!showRoiPreview) {
      // Next time an ROI preview appears, reframe the PIP fresh instead of
      // keeping wherever the user last dragged it.
      runtime.pipUserAdjusted = false
    }
    // Wireframe box(es) marking where the active ROI sits within the full
    // model - only ever rendered in the "Full View" PIP inset, not the
    // main ROI-isolated view.
    clearGroup(runtime.roiBoundsMarker)
    for (const scope of activeBoxScopes) {
      if (scope.components.length === 0) continue
      const min = new Vector3(Infinity, Infinity, Infinity)
      const max = new Vector3(-Infinity, -Infinity, -Infinity)
      for (const component of scope.components) {
        min.x = Math.min(min.x, component.bboxMin.x)
        min.y = Math.min(min.y, component.bboxMin.y)
        min.z = Math.min(min.z, component.bboxMin.z)
        max.x = Math.max(max.x, component.bboxMax.x)
        max.y = Math.max(max.y, component.bboxMax.y)
        max.z = Math.max(max.z, component.bboxMax.z)
      }
      if (!Number.isFinite(min.x) || !Number.isFinite(max.x)) continue
      const size = new Vector3().subVectors(max, min)
      const center = new Vector3().addVectors(min, max).multiplyScalar(0.5)
      const boxGeometry = new BoxGeometry(
        Math.max(size.x, 1e-3),
        Math.max(size.y, 1e-3),
        Math.max(size.z, 1e-3),
      )
      const markerEdges = new EdgesGeometry(boxGeometry)
      boxGeometry.dispose()
      const marker = new LineSegments(
        markerEdges,
        new LineBasicMaterial({
          color: 0xfacc15,
          transparent: true,
          opacity: 0.95,
          depthTest: false,
        }),
      )
      marker.position.copy(center)
      marker.renderOrder = 200
      runtime.roiBoundsMarker.add(marker)
    }

    const roiPointTransform = createRoiPointTransform(
      runtime,
      transformRules,
    )
    const previewKey = JSON.stringify({
      scopes: activeBoxScopes.map((scope) => ({
        id: scope.id,
        box: scope.clipBox,
        faces: scope.components.flatMap(
          (component) => component.faceIds,
        ),
      })),
      hiddenComponentIds,
      deletedComponentIds,
      renderMode,
      surfaceOpacity,
      componentColorOverrides,
      componentTransforms: transformRules
        .filter(
          (rule) =>
            rule.enabled && rule.targetType === 'component',
        )
        .map((rule) => ({
          componentId: rule.componentId,
          move: rule.move,
          tilt: rule.tilt,
        })),
      materialAssignments: materialAssignments
        .filter((assignment) => assignment.enabled)
        .map((assignment) => ({
          assignmentId: assignment.assignmentId,
          componentId: assignment.componentId,
          targetType: assignment.targetType,
          faceIds: assignment.faceIds,
          baseMaterialId: assignment.baseMaterialId,
          surfaceId: assignment.surfaceId,
        })),
    })

    if (
      showRoiPreview &&
      (runtime.roiPreviewKey !== previewKey ||
        runtime.roiPreviewRoot.children.length === 0)
    ) {
      const shouldInitialFit = runtime.roiPreviewKey.length === 0
      clearGroup(runtime.roiPreviewRoot)
      const boxFaceIds = [
        ...new Set(
          activeBoxScopes.flatMap((scope) =>
            scope.components.flatMap(
              (component) => component.faceIds,
            ),
          ),
        ),
      ]
      const clipBoxes = activeBoxScopes.flatMap((scope) =>
        scope.clipBox ? [scope.clipBox] : [],
      )
      const clipped = buildRoiClippedGeometries(
        scene,
        boxFaceIds,
        clipBoxes,
        [...hiddenComponentIds, ...deletedComponentIds],
        roiPointTransform,
      )
      if (clipped && clipped.openChainCount === 0) {
        const isWireframe = renderMode === 'Wireframe'
        const surfaceMaterial = isWireframe
          ? new MeshBasicMaterial({
              color: 0x263b4d,
              transparent: true,
              opacity: wireframeSurfaceOpacity,
              side: DoubleSide,
              depthTest: true,
              depthWrite: true,
              toneMapped: false,
            })
          : new MeshStandardMaterial({
              color: 0x8fb3c7,
              roughness: 0.72,
              metalness: 0.04,
              // The ROI surface is still the original CAD skin. Smooth its
              // tessellation normals; only the newly generated section caps
              // below should remain flat shaded.
              flatShading: false,
              transparent: surfaceOpacity < 1,
              opacity: surfaceOpacity,
              side: DoubleSide,
              depthTest: true,
              depthWrite: surfaceOpacity >= 1,
            })
        const surface = new Mesh(
          clipped.surfaceGeometry,
          surfaceMaterial,
        )
        surface.name = 'roi-clipped-surface'
        runtime.roiPreviewRoot.add(surface)

        // ROI geometry is rebuilt as a clipped solid, so it cannot reuse the
        // Full View component meshes. Reapply each component's authored/user
        // display color as clipped overlays to keep both views consistent.
        if (!isWireframe) {
          const boxFaceIdSet = new Set(boxFaceIds)
          for (const [componentIndex, component] of scene.components.entries()) {
            const componentFaceIds = component.face_indices.filter((faceId) =>
              boxFaceIdSet.has(faceId),
            )
            if (componentFaceIds.length === 0) continue
            const componentGeometry = buildRoiClippedGeometries(
              scene,
              componentFaceIds,
              clipBoxes,
              [...hiddenComponentIds, ...deletedComponentIds],
              roiPointTransform,
            )
            if (!componentGeometry) continue
            const customColor = componentColorOverrides[component.component_id]
            const componentColor = customColor
              ? Number.parseInt(customColor.slice(1), 16)
              : resolveComponentColor(component, componentIndex)
            const componentOverlay = new Mesh(
              componentGeometry.surfaceGeometry,
              faceOverlayMaterial(
                viewerMaterialStyle(undefined, componentColor),
                surfaceOpacity,
              ),
            )
            componentOverlay.name = `roi-component-color-${component.component_id}`
            componentOverlay.renderOrder = 5 + componentIndex
            runtime.roiPreviewRoot.add(componentOverlay)
            componentGeometry.capGeometry?.dispose()
            componentGeometry.capEdgeGeometry?.dispose()
            componentGeometry.featureEdgeGeometry?.dispose()
          }
        }

        if (clipped.capGeometry) {
          const capMaterial = isWireframe
            ? new MeshBasicMaterial({
                color: 0x314a5c,
                transparent: true,
                opacity: 0.75,
                side: DoubleSide,
                depthTest: true,
                depthWrite: true,
                toneMapped: false,
              })
            : new MeshStandardMaterial({
                color: 0x6f9fb5,
                roughness: 0.78,
                metalness: 0.02,
                flatShading: true,
                transparent: surfaceOpacity < 1,
                opacity: surfaceOpacity,
                side: DoubleSide,
                depthTest: true,
                depthWrite: surfaceOpacity >= 1,
              })
          const caps = new Mesh(
            clipped.capGeometry,
            capMaterial,
          )
          caps.name = 'roi-section-caps'
          caps.renderOrder = 1
          runtime.roiPreviewRoot.add(caps)
        }

        const showEdges = renderMode !== 'Surface'
        if (showEdges && clipped.featureEdgeGeometry) {
          if (isWireframe) {
            const hiddenFeatureEdges = new LineSegments(
              clipped.featureEdgeGeometry.clone(),
              new LineBasicMaterial({
                color: 0x8aa4b8,
                transparent: true,
                opacity: 0.16,
                depthTest: false,
                depthWrite: false,
              }),
            )
            hiddenFeatureEdges.name = 'roi-hidden-feature-edges'
            hiddenFeatureEdges.renderOrder = 2
            runtime.roiPreviewRoot.add(hiddenFeatureEdges)
          }
          const featureEdges = new LineSegments(
            clipped.featureEdgeGeometry,
            new LineBasicMaterial({
              color: 0xd7edf8,
              transparent: true,
              opacity: isWireframe ? 0.82 : 0.74,
              depthTest: true,
              depthWrite: false,
            }),
          )
          featureEdges.name = 'roi-feature-edges'
          featureEdges.renderOrder = 3
          runtime.roiPreviewRoot.add(featureEdges)
        }
        if (showEdges && clipped.capEdgeGeometry) {
          const capEdges = new LineSegments(
            clipped.capEdgeGeometry,
            new LineBasicMaterial({
              color: 0xe0f2fe,
              transparent: true,
              opacity: isWireframe ? 0.72 : 0.9,
              depthTest: true,
              depthWrite: false,
            }),
          )
          capEdges.name = 'roi-cap-edges'
          capEdges.renderOrder = 4
          runtime.roiPreviewRoot.add(capEdges)
        }

        if (renderMode !== 'Wireframe') {
          const boxFaceIdSet = new Set(boxFaceIds)
          const roiMaterialAssignments = materialAssignments
            .filter((assignment) => assignment.enabled)
            .sort((left, right) => {
              if (left.targetType === right.targetType) return 0
              return left.targetType === 'part' ? -1 : 1
            })
          for (const [
            assignmentIndex,
            assignment,
          ] of roiMaterialAssignments.entries()) {
            const assignmentFaceIds =
              assignment.targetType === 'part'
                ? boxFaceIds.filter(
                    (faceId) =>
                      scene.mesh.face_component_ids[faceId] ===
                      assignment.componentId,
                  )
                : assignment.faceIds.filter((faceId) =>
                    boxFaceIdSet.has(faceId),
                  )
            if (assignmentFaceIds.length === 0) continue

            const assignmentGeometry = buildRoiClippedGeometries(
              scene,
              assignmentFaceIds,
              clipBoxes,
              [...hiddenComponentIds, ...deletedComponentIds],
              roiPointTransform,
            )
            if (!assignmentGeometry) continue

            const componentIndex = scene.components.findIndex(
              (component) =>
                component.component_id === assignment.componentId,
            )
            const fallbackColor = resolveComponentColor(
              componentIndex >= 0 ? scene.components[componentIndex] : undefined,
              componentIndex,
            )
            const customColor =
              componentColorOverrides[assignment.componentId]
            const assignmentDisplayColor = customColor
              ? Number.parseInt(customColor.slice(1), 16)
              : fallbackColor
            const overlay = new Mesh(
              assignmentGeometry.surfaceGeometry,
              faceOverlayMaterial(
                viewerMaterialStyle(assignment, assignmentDisplayColor),
                surfaceOpacity,
              ),
            )
            overlay.name = `roi-material-${assignment.assignmentId}`
            overlay.renderOrder = 10 + assignmentIndex
            runtime.roiPreviewRoot.add(overlay)

            assignmentGeometry.capGeometry?.dispose()
            assignmentGeometry.capEdgeGeometry?.dispose()
            assignmentGeometry.featureEdgeGeometry?.dispose()
          }
        }

        runtime.roiPreviewRoot.userData.capLoopCount =
          clipped.capLoopCount
        runtime.roiPreviewRoot.userData.clippedTriangleCount =
          clipped.clippedTriangleCount
        runtime.roiPreviewRoot.userData.clippedVertexCount =
          clipped.clippedVertexCount
        runtime.roiPreviewRoot.visible = true
        runtime.modelRoot.visible = false
        runtime.roiPreviewKey = previewKey
        if (
          !restoreRoiSelectionCameraPose(runtime) &&
          shouldInitialFit
        ) {
          fitCamera(runtime, 'Fit')
        }
        onStatusMessage('ROI isolated solid 생성됨')
      } else {
        clipped?.surfaceGeometry.dispose()
        clipped?.capGeometry?.dispose()
        clipped?.capEdgeGeometry?.dispose()
        clipped?.featureEdgeGeometry?.dispose()
        clearGroup(runtime.roiPreviewRoot)
        runtime.roiPreviewRoot.visible = false
        runtime.modelRoot.visible = true
        runtime.roiPreviewKey = previewKey
        restoreRoiSelectionCameraPose(runtime)
        onStatusMessage(
          clipped
            ? `ROI section cap 무결성 오류 · 열린 경계 ${clipped.openChainCount}개`
            : 'ROI clipping geometry를 생성하지 못했습니다.',
        )
      }
    } else if (
      showRoiPreview &&
      runtime.roiPreviewRoot.children.length > 0
    ) {
      runtime.roiPreviewRoot.visible = true
      runtime.modelRoot.visible = false
      restoreRoiSelectionCameraPose(runtime)
    } else if (!showRoiPreview) {
      runtime.roiPreviewRoot.visible = false
      runtime.modelRoot.visible = true
      if (!roiBoxSelectionArmed) {
        restoreRoiSelectionCameraPose(runtime)
      }
    }

    if (!runtime.roiSelectionRoot) {
      runtime.roiSelectionRoot = new Group()
      runtime.roiSelectionRoot.name = 'roi-selection-overlay-root'
      runtime.scene.add(runtime.roiSelectionRoot)
    }
    clearGroup(runtime.roiSelectionRoot)
    runtime.roiSelectionRoot.visible = false
    const selectedRoiComponentIds = new Set(
      editingComponentId === null ||
        editingComponentId === undefined
        ? selectedComponentIds
        : [...selectedComponentIds, editingComponentId],
    )
    const activeRoiFaceIds = [
      ...new Set(
        activeBoxScopes.flatMap((scope) =>
          scope.components.flatMap(
            (component) => component.faceIds,
          ),
        ),
      ),
    ]
    const activeRoiFaceSet = new Set(activeRoiFaceIds)
    const selectionFaceIds =
      emitterFaceSelectionArmed || materialFacePickArmed || datumFacePickArmed
      ? selectedFaceIds.filter((faceId) =>
          activeRoiFaceSet.has(faceId),
        )
      : [
          ...new Set(
            activeBoxScopes.flatMap((scope) =>
              scope.components
                .filter((component) =>
                  selectedRoiComponentIds.has(
                    component.componentId,
                  ),
                )
                .flatMap((component) => component.faceIds),
            ),
          ),
        ]

    if (
      showRoiPreview &&
      runtime.roiPreviewRoot.visible &&
      selectionFaceIds.length > 0
    ) {
      const selectionClipBoxes = activeBoxScopes.flatMap((scope) =>
        scope.clipBox ? [scope.clipBox] : [],
      )
      const selectedComponentIdsForOverlay = new Set(
        selectionFaceIds.flatMap((faceId) => {
          const componentId =
            scene.mesh.face_component_ids[faceId]
          return componentId === null ? [] : [componentId]
        }),
      )
      const unavailableSelectionComponentIds = [
        ...hiddenComponentIds,
        ...deletedComponentIds,
        ...scene.components
          .map((component) => component.component_id)
          .filter(
            (componentId) =>
              !selectedComponentIdsForOverlay.has(componentId),
          ),
      ]
      const selectedClipped = buildRoiClippedGeometries(
        scene,
        selectionFaceIds,
        selectionClipBoxes,
        unavailableSelectionComponentIds,
        roiPointTransform,
      )
      if (selectedClipped) {
        const selectionColor = datumFacePickArmed
          ? selectedComponentSurfaceColor
          : materialFacePickArmed
          ? selectedMaterialFaceHighlightColor
          : emitterFaceSelectionArmed
            ? selectedFaceHighlightColorArmed
            : selectedComponentSurfaceColor
        const selectionOpacity = datumFacePickArmed
          ? 0.72
          : materialFacePickArmed
          ? 0.62
          : emitterFaceSelectionArmed
            ? 0.52
            : 0.36
        const createSelectionMaterial = () =>
          new MeshBasicMaterial({
            color: selectionColor,
            side: DoubleSide,
            transparent: true,
            opacity: selectionOpacity,
            depthTest: true,
            depthWrite: false,
            polygonOffset: true,
            polygonOffsetFactor: -4,
            polygonOffsetUnits: -4,
            toneMapped: false,
          })
        const selectedSurface = new Mesh(
          selectedClipped.surfaceGeometry,
          createSelectionMaterial(),
        )
        selectedSurface.name = 'roi-selected-surface'
        selectedSurface.renderOrder = emitterFaceSelectionArmed
          ? 94
          : 90
        runtime.roiSelectionRoot.add(selectedSurface)

        if (selectedClipped.capGeometry) {
          const selectedCaps = new Mesh(
            selectedClipped.capGeometry,
            createSelectionMaterial(),
          )
          selectedCaps.name = 'roi-selected-section-caps'
          selectedCaps.renderOrder = selectedSurface.renderOrder
          runtime.roiSelectionRoot.add(selectedCaps)
        }

        if (
          emitterFaceSelectionArmed &&
          selectedClipped.featureEdgeGeometry
        ) {
          selectedClipped.featureEdgeGeometry.dispose()
        }
        const suppressMaterialTargetEdges =
          editingComponentMode === 'material' &&
          !materialFacePickArmed &&
          !emitterFaceSelectionArmed
        const selectionEdgeGeometries = suppressMaterialTargetEdges
          ? []
          : [
              emitterFaceSelectionArmed
                ? null
                : selectedClipped.featureEdgeGeometry,
              selectedClipped.capEdgeGeometry,
            ].filter(
              (
                geometry,
              ): geometry is BufferGeometry => geometry !== null,
            )
        if (suppressMaterialTargetEdges) {
          selectedClipped.featureEdgeGeometry?.dispose()
          selectedClipped.capEdgeGeometry?.dispose()
        }
        for (const [
          edgeIndex,
          edgeGeometry,
        ] of selectionEdgeGeometries.entries()) {
          const selectedEdges = new LineSegments(
            edgeGeometry,
            new LineBasicMaterial({
              color: selectionColor,
              transparent: true,
              opacity:
                emitterFaceSelectionArmed || materialFacePickArmed || datumFacePickArmed
                  ? 1
                  : 0.88,
              depthTest:
                emitterFaceSelectionArmed || materialFacePickArmed || datumFacePickArmed,
              depthWrite: false,
              toneMapped: false,
            }),
          )
          selectedEdges.name = `roi-selected-edges-${edgeIndex}`
          selectedEdges.renderOrder =
            selectedSurface.renderOrder + 1
          runtime.roiSelectionRoot.add(selectedEdges)
        }

        if (emitterFaceSelectionArmed) {
          const frame = resolveFacePlacementFrame(
            scene,
            selectionFaceIds,
          )
          const selectedBounds =
            selectedClipped.surfaceGeometry.boundingBox
          if (frame && selectedBounds) {
            const direction = createDirectionArrow(
              'roi-selected-emitter-direction',
              selectedBounds.getCenter(new Vector3()),
              new Vector3(...frame.normal),
              MathUtils.clamp(
                Math.min(frame.width, frame.height) * 0.18,
                2,
                18,
              ),
              emitterDirectionColor,
            )
            direction.traverse((child) => {
              child.renderOrder = Math.max(
                child.renderOrder,
                96,
              )
            })
            runtime.roiSelectionRoot.add(direction)
          }
        }
        runtime.roiSelectionRoot.visible = true
      }
    }

    const enabledFaceEmitters = emitters.filter(
      (emitter) =>
        emitter.enabled && emitter.emitter_type === 'face',
    )
    const enabledFaceEmitterSets = enabledFaceEmitters.map((emitter) => ({
      emitter,
      faceIds: new Set(emitter.face_indices),
    }))

    clearGroup(runtime.placementRoot)
    const placementEmitters = placementPreviewEmitter
      ? [
          ...emitters.filter(
            (emitter) =>
              emitter.emitter_id !==
              placementPreviewEmitter.emitter_id,
          ),
          placementPreviewEmitter,
        ]
      : emitters
    const placementReceivers = placementPreviewReceiver
      ? [
          ...receivers.filter(
            (receiver) =>
              receiver.receiver_id !==
              placementPreviewReceiver.receiver_id,
          ),
          placementPreviewReceiver,
        ]
      : receivers
    for (const emitter of placementEmitters) {
      if (
        !emitter.enabled ||
        emitter.emitter_type === 'face' ||
        !emitter.center ||
        !emitter.u_axis ||
        !emitter.v_axis ||
        !emitter.width_mm ||
        !emitter.height_mm
      ) {
        continue
      }
      const fallbackNormal = new Vector3(...emitter.u_axis)
        .cross(new Vector3(...emitter.v_axis))
        .normalize()
        .toArray()
      const emitterPlane = createPlacementPlane(
        `emitter-plane-${emitter.emitter_id}`,
        emitter.center,
        emitter.u_axis,
        emitter.v_axis,
        emitter.custom_normal ?? fallbackNormal,
        emitter.width_mm,
        emitter.height_mm,
        emitterOverlayColor,
        emitterDirectionColor,
        emitter.normal_flip,
        emitter === placementPreviewEmitter ? 0.4 : 0.2,
        emitter === placementPreviewEmitter,
      )
      emitterPlane.userData.rayObjectKind = 'emitter'
      emitterPlane.userData.rayObjectId = emitter.emitter_id
      runtime.placementRoot.add(emitterPlane)
    }
    for (const receiver of placementReceivers) {
      if (
        !receiver.enabled ||
        !receiver.u_axis ||
        !receiver.v_axis
      ) {
        continue
      }
      const receiverPlane = createPlacementPlane(
        `receiver-plane-${receiver.receiver_id}`,
        receiver.center,
        receiver.u_axis,
        receiver.v_axis,
        receiver.normal,
        receiver.width_mm,
        receiver.height_mm,
        receiverOverlayColor,
        receiverOverlayColor,
        receiver.normal_flip,
        receiver === placementPreviewReceiver ? 0.34 : 0.14,
        receiver === placementPreviewReceiver,
      )
      receiverPlane.userData.rayObjectKind = 'receiver'
      receiverPlane.userData.rayObjectId = receiver.receiver_id
      runtime.placementRoot.add(receiverPlane)
    }

    if (showRoiPreview && runtime.roiPreviewRoot.visible) {
      const roiClipBoxes = activeBoxScopes.flatMap((scope) =>
        scope.clipBox ? [scope.clipBox] : [],
      )
      for (const { emitter, faceIds } of enabledFaceEmitterSets) {
        const componentFaceGroups = new Map<number, number[]>()
        for (const faceId of faceIds) {
          if (!activeRoiFaceSet.has(faceId)) continue
          const componentId =
            scene.mesh.face_component_ids[faceId]
          if (
            componentId === null ||
            !Number.isSafeInteger(componentId)
          ) {
            continue
          }
          const group = componentFaceGroups.get(componentId) ?? []
          group.push(faceId)
          componentFaceGroups.set(componentId, group)
        }

        for (const [componentId, emitterFaceIds] of componentFaceGroups) {
          const unavailableComponentIds = [
            ...hiddenComponentIds,
            ...deletedComponentIds,
            ...scene.components
              .map((component) => component.component_id)
              .filter((candidateId) => candidateId !== componentId),
          ]
          const clippedEmitter = buildRoiClippedGeometries(
            scene,
            emitterFaceIds,
            roiClipBoxes,
            unavailableComponentIds,
            roiPointTransform,
          )
          if (!clippedEmitter) continue

          const emitterRoot = new Group()
          emitterRoot.name = `roi-emitter-reference-${emitter.emitter_id}-${componentId}`
          emitterRoot.userData.rayObjectKind = 'emitter'
          emitterRoot.userData.rayObjectId = emitter.emitter_id
          const emitterSurface = new Mesh(
            clippedEmitter.surfaceGeometry,
            new MeshStandardMaterial({
              color: emitterOverlayColor,
              emissive: 0x713f12,
              emissiveIntensity: 0.32,
              roughness: 0.5,
              side: DoubleSide,
              transparent: true,
              opacity: renderMode === 'Wireframe' ? 0.16 : 0.52,
              depthTest: true,
              depthWrite: false,
              polygonOffset: true,
              polygonOffsetFactor: -5,
              polygonOffsetUnits: -5,
            }),
          )
          emitterSurface.name = `${emitterRoot.name}-surface`
          emitterSurface.renderOrder = 38
          const emitterBoundary = new LineSegments(
            new EdgesGeometry(clippedEmitter.surfaceGeometry, 24),
            new LineBasicMaterial({
              color: 0xfef08a,
              transparent: true,
              opacity: 0.96,
              depthTest: true,
              depthWrite: false,
              toneMapped: false,
            }),
          )
          emitterBoundary.name = `${emitterRoot.name}-boundary`
          emitterBoundary.renderOrder = 39
          emitterRoot.add(emitterSurface, emitterBoundary)

          const frame = resolveFacePlacementFrame(
            scene,
            emitterFaceIds,
          )
          const emitterBounds =
            clippedEmitter.surfaceGeometry.boundingBox
          if (frame && emitterBounds) {
            let directionNormal = new Vector3(...frame.normal)
            if (roiPointTransform) {
              const transformedCenter = roiPointTransform(
                componentId,
                frame.center,
              )
              const transformedNormalEnd = roiPointTransform(
                componentId,
                [
                  frame.center[0] + frame.normal[0],
                  frame.center[1] + frame.normal[1],
                  frame.center[2] + frame.normal[2],
                ],
              )
              directionNormal = new Vector3(
                transformedNormalEnd[0] - transformedCenter[0],
                transformedNormalEnd[1] - transformedCenter[1],
                transformedNormalEnd[2] - transformedCenter[2],
              ).normalize()
            }
            directionNormal.multiplyScalar(
              emitter.normal_flip ? -1 : 1,
            )
            const direction = createDirectionArrow(
              `${emitterRoot.name}-direction`,
              emitterBounds.getCenter(new Vector3()),
              directionNormal,
              MathUtils.clamp(
                Math.min(frame.width, frame.height) * 0.22,
                3,
                22,
              ),
              emitterDirectionColor,
            )
            direction.traverse((child) => {
              child.renderOrder = Math.max(child.renderOrder, 97)
            })
            emitterRoot.add(direction)
          }

          clippedEmitter.capGeometry?.dispose()
          clippedEmitter.capEdgeGeometry?.dispose()
          clippedEmitter.featureEdgeGeometry?.dispose()
          runtime.placementRoot.add(emitterRoot)
        }
      }
    }

    const emitterFaceSet = new Set(
      enabledFaceEmitters.flatMap((emitter) => emitter.face_indices),
    )
    const selectedFaceSet = new Set(selectedFaceIds)
    const roiFaceSet = new Set(roiFaceIds)
    for (const [componentId, node] of runtime.nodes) {
      const isEditing = editingComponentId === componentId
      const isSelected =
        isEditing ||
        (!emitterFaceSelectionArmed &&
          !datumFacePickArmed &&
          selectedComponentIds.includes(componentId))
      const isUnavailable =
        hiddenComponentIds.includes(componentId) ||
        deletedComponentIds.includes(componentId)
      node.group.visible = !isUnavailable
      applyComponentTransform(node, transformRules)
      if (!node.wireframeFill) {
        node.wireframeFill = new Mesh(
          node.surface.geometry.clone(),
          new MeshBasicMaterial({
            color: 0x263b4d,
            transparent: true,
            opacity: wireframeSurfaceOpacity,
            side: DoubleSide,
            depthTest: true,
            depthWrite: true,
            toneMapped: false,
          }),
        )
        node.wireframeFill.name = `component-wirefill-${componentId}`
        node.wireframeFill.renderOrder = node.depthPriority
        node.group.add(node.wireframeFill)
      }
      if (!node.hiddenEdges) {
        node.hiddenEdges = new LineSegments(
          node.edges.geometry.clone(),
          new LineBasicMaterial({
            color: 0x8aa4b8,
            transparent: true,
            opacity: 0.16,
            depthTest: false,
            depthWrite: false,
          }),
        )
        node.hiddenEdges.name = `component-hidden-edges-${componentId}`
        node.hiddenEdges.renderOrder = 80 + node.depthPriority
        node.group.add(node.hiddenEdges)
      }

      const partAssignment = materialAssignments.find(
        (assignment) =>
          assignment.enabled &&
          assignment.componentId === componentId &&
          assignment.targetType === 'part',
      )
      const authoredColor = resolveComponentColor(
        node.component,
        scene.components.indexOf(node.component),
      )
      const customColor = componentColorOverrides[componentId]
      const displayBaseColor = customColor
        ? Number.parseInt(customColor.slice(1), 16)
        : authoredColor
      const style = viewerMaterialStyle(partAssignment, displayBaseColor)
      const displayColor = style.color.clone()
      const highlightColor = selectedComponentEdgeColor
      const showHighlightedEdges =
        isSelected &&
        !(isEditing && editingComponentMode === 'material')
      node.surface.material.color.copy(displayColor)
      // Selection is communicated by edges/overlays only. Keeping the
      // surface untouched makes CAD and user-assigned display colors remain
      // immediately visible while the component is selected or edited.
      node.surface.material.emissive.set(0x000000)
      node.surface.material.emissiveIntensity = 0
      node.surface.material.metalness = style.metalness
      node.surface.material.roughness = style.roughness
      const isWireframe = renderMode === 'Wireframe'
      const isTransparentSurface = !isWireframe && surfaceOpacity < 1
      if (
        node.surface.material.transparent !== isTransparentSurface
      ) {
        node.surface.material.transparent = isTransparentSurface
        node.surface.material.needsUpdate = true
      }
      node.surface.material.opacity = surfaceOpacity
      node.surface.material.depthWrite = !isTransparentSurface
      node.surface.material.polygonOffsetFactor = 0
      node.surface.material.polygonOffsetUnits =
        surfaceDepthUnits(node.depthPriority)
      node.surface.visible = !isWireframe
      node.wireframeFill.visible = isWireframe
      node.wireframeFill.material.color.set(
        isSelected ? selectedComponentSurfaceColor : 0x263b4d,
      )
      node.wireframeFill.material.opacity = isSelected
        ? selectedWireframeSurfaceOpacity
        : wireframeSurfaceOpacity
      node.hiddenEdges.visible = isWireframe
      node.hiddenEdges.material.opacity = 0.16
      node.edges.visible = renderMode !== 'Surface'
      node.edges.material.color.set(
        showHighlightedEdges ? highlightColor : 0xb9d5e8,
      )
      node.edges.material.opacity = showHighlightedEdges
        ? 1
        : isWireframe
          ? 0.82
          : 0.72

      clearGroup(node.emitterOverlayRoot)
      clearGroup(node.materialOverlayRoot)
      clearGroup(node.roiOverlayRoot)
      clearGroup(node.selectionOverlayRoot)
      clearGroup(node.transformOverlayRoot)
      node.materialOverlayRoot.visible = renderMode !== 'Wireframe'

      if (isSelected && !emitterFaceSelectionArmed && !datumFacePickArmed) {
        const targetSurface = new Mesh(
          node.surface.geometry.clone(),
          new MeshBasicMaterial({
            color: selectedComponentSurfaceColor,
            side: DoubleSide,
            transparent: true,
            opacity: 0.36,
            depthTest: true,
            depthWrite: false,
            polygonOffset: true,
            polygonOffsetFactor: -4,
            polygonOffsetUnits: -4,
            toneMapped: false,
          }),
        )
        targetSurface.name = `editor-target-surface-${componentId}`
        targetSurface.renderOrder = 88

        node.selectionOverlayRoot.add(targetSurface)
        const targetEdges = new LineSegments(
          node.edges.geometry.clone(),
          new LineBasicMaterial({
            color: selectedComponentEdgeColor,
            transparent: true,
            opacity: 1,
            // NX-style selection must highlight only edges that are visible
            // from the current camera. Disabling the depth test made every
            // rear/internal B-rep edge bleed through the solid surface as an
            // irregular line overlay after selecting a component.
            depthTest: true,
            depthWrite: false,
            toneMapped: false,
          }),
        )
        targetEdges.name = `component-selection-edges-${componentId}`
        targetEdges.renderOrder = 89
        node.selectionOverlayRoot.add(targetEdges)
      }

      const componentEmitterFaceIds = node.component.face_indices.filter(
        (faceId) => emitterFaceSet.has(faceId),
      )
      if (componentEmitterFaceIds.length > 0) {
        const bundle = createFaceGeometry(
          scene,
          componentEmitterFaceIds,
          node.center,
        )
        if (bundle.faceIds.length > 0) {
          const overlay = new Mesh(
            bundle.geometry,
            new MeshStandardMaterial({
              color: emitterOverlayColor,
              emissive: 0x7c2d12,
              emissiveIntensity: 0.42,
              roughness: 0.5,
              side: DoubleSide,
              transparent: true,
              opacity: renderMode === 'Wireframe' ? 0.16 : 0.54,
              depthTest: true,
              depthWrite: false,
              polygonOffset: true,
              polygonOffsetFactor: -3,
              polygonOffsetUnits: -3,
            }),
          )
          overlay.name = `emitter-highlight-${componentId}`
          overlay.renderOrder = 8
          node.emitterOverlayRoot.add(overlay)
        } else {
          bundle.geometry.dispose()
        }
      }
      for (const { emitter, faceIds } of enabledFaceEmitterSets) {
        const emitterFaceIds = node.component.face_indices.filter(
          (faceId) => faceIds.has(faceId),
        )
        const frame = resolveFacePlacementFrame(scene, emitterFaceIds)
        if (!frame) continue
        const normal = new Vector3(...frame.normal).multiplyScalar(
          emitter.normal_flip ? -1 : 1,
        )
        const localCenter = new Vector3(
          frame.center[0] - node.center.x,
          frame.center[1] - node.center.y,
          frame.center[2] - node.center.z,
        )
        const reference = new Group()
        reference.name = `emitter-face-reference-${emitter.emitter_id}-${componentId}`
        const boundary = createFacePatchBoundary(
          scene,
          emitterFaceIds,
          node.center,
          new Vector3(...frame.normal),
          false,
        )
        if (boundary) reference.add(boundary)
        reference.add(
          createDirectionArrow(
            `${reference.name}-direction`,
            localCenter,
            normal,
            MathUtils.clamp(
              Math.min(frame.width, frame.height) * 0.18,
              2,
              18,
            ),
            emitterDirectionColor,
          ),
        )
        node.emitterOverlayRoot.add(reference)
      }

      const componentSelectedFaceIds = node.component.face_indices.filter(
        (faceId) => selectedFaceSet.has(faceId),
      )
      if (componentSelectedFaceIds.length > 0) {
        const isEmitterSurfaceDraft = emitterFaceSelectionArmed
        const isMaterialSurfaceDraft = materialFacePickArmed
        const isDatumSurfaceDraft = datumFacePickArmed
        const bundle = createFaceGeometry(
          scene,
          componentSelectedFaceIds,
          node.center,
        )
        if (bundle.faceIds.length > 0) {
          const overlay = new Mesh(
            bundle.geometry,
            new MeshBasicMaterial({
              color: isDatumSurfaceDraft
                ? selectedComponentSurfaceColor
                : isMaterialSurfaceDraft
                ? selectedMaterialFaceHighlightColor
                : isEmitterSurfaceDraft
                  ? selectedFaceHighlightColorArmed
                  : selectedFaceHighlightColorEditing,
              side: DoubleSide,
              transparent: true,
              opacity: isDatumSurfaceDraft
                ? 0.76
                : isMaterialSurfaceDraft
                ? 0.72
                : isEmitterSurfaceDraft
                  ? 0.94
                  : 0.86,
              depthTest: !isEmitterSurfaceDraft,
              depthWrite: false,
              polygonOffset: true,
              polygonOffsetFactor: -6,
              polygonOffsetUnits: -6,
              toneMapped: false,
            }),
          )
          overlay.name = `selected-face-highlight-${componentId}`
          // Must draw after (render-order-wise) the whole-part "editing"
          // tint (targetSurface, order 88) - otherwise that amber overlay
          // paints over this face highlight and the blue never shows.
          overlay.renderOrder = isEmitterSurfaceDraft ? 94 : 92
          node.selectionOverlayRoot.add(overlay)

          const frame = resolveFacePlacementFrame(
            scene,
            componentSelectedFaceIds,
          )

          if (!isEmitterSurfaceDraft && frame) {
            // A crisp outline around exactly the selected patch - without
            // this, a selection that happens to cover an entire visible
            // surface reads as a flat color change with no distinguishable
            // boundary, easy to mistake for "the whole part changed" rather
            // than "this specific face is selected".
            const outlineBoundary = createFacePatchBoundary(
              scene,
              componentSelectedFaceIds,
              node.center,
              new Vector3(...frame.normal),
              false,
            )
            if (outlineBoundary) {
              outlineBoundary.name = `selected-face-outline-${componentId}`
              outlineBoundary.material.color.set(
                isDatumSurfaceDraft || isMaterialSurfaceDraft
                  ? 0xffb347
                  : 0xffffff,
              )
              outlineBoundary.material.depthTest = true
              outlineBoundary.renderOrder = 93
              node.selectionOverlayRoot.add(outlineBoundary)
            }
          }

          if (isEmitterSurfaceDraft) {
            if (frame) {
              const normal = new Vector3(...frame.normal)
              const boundary = createFacePatchBoundary(
                scene,
                componentSelectedFaceIds,
                node.center,
                normal,
              )
              if (boundary) {
                boundary.name = `selected-emitter-boundary-${componentId}`
                boundary.renderOrder = 95
                node.selectionOverlayRoot.add(boundary)
              }
              const localCenter = new Vector3(
                frame.center[0] - node.center.x,
                frame.center[1] - node.center.y,
                frame.center[2] - node.center.z,
              )
              const direction = createDirectionArrow(
                `selected-emitter-direction-${componentId}`,
                localCenter,
                normal,
                MathUtils.clamp(
                  Math.min(frame.width, frame.height) * 0.18,
                  2,
                  18,
                ),
                emitterDirectionColor,
              )
              direction.traverse((child) => {
                child.renderOrder = Math.max(child.renderOrder, 96)
              })
              node.selectionOverlayRoot.add(direction)
            }
          }
        } else {
          bundle.geometry.dispose()
        }
      }

      const componentRoiFaceIds = node.component.face_indices.filter(
        (faceId) => roiFaceSet.has(faceId),
      )
      if (componentRoiFaceIds.length > 0) {
        const bundle = createFaceGeometry(
          scene,
          componentRoiFaceIds,
          node.center,
        )
        if (bundle.faceIds.length > 0) {
          const overlay = new Mesh(
            bundle.geometry,
            new MeshStandardMaterial({
              color: 0xfacc15,
              emissive: 0x713f12,
              emissiveIntensity: 0.55,
              roughness: 0.58,
              side: DoubleSide,
              transparent: true,
              opacity: renderMode === 'Wireframe' ? 0.76 : 0.58,
              depthWrite: false,
              polygonOffset: true,
              polygonOffsetFactor: -2,
              polygonOffsetUnits: -2,
            }),
          )
          overlay.name = `roi-highlight-${componentId}`
          overlay.renderOrder = 5
          node.roiOverlayRoot.add(overlay)
        } else {
          bundle.geometry.dispose()
        }
      }

      const faceAssignments = materialAssignments.filter(
        (assignment) =>
          assignment.enabled &&
          assignment.componentId === componentId &&
          assignment.targetType === 'faces' &&
          assignment.faceIds.length > 0,
      )
      for (const assignment of faceAssignments) {
        const bundle = createFaceGeometry(
          scene,
          assignment.faceIds,
          node.center,
        )
        if (bundle.faceIds.length === 0) {
          bundle.geometry.dispose()
          continue
        }
        const overlay = new Mesh(
          bundle.geometry,
          faceOverlayMaterial(
            viewerMaterialStyle(assignment, displayBaseColor),
            Math.min(0.96, surfaceOpacity),
          ),
        )
        overlay.renderOrder = 2
        node.materialOverlayRoot.add(overlay)
      }

      const faceTransformRules = transformRules.filter(
        (rule) =>
          rule.enabled &&
          rule.componentId === componentId &&
          rule.targetType === 'faces' &&
          rule.faceIds.length > 0,
      )
      for (const rule of faceTransformRules) {
        const bundle = createFaceGeometry(scene, rule.faceIds, node.center)
        if (bundle.faceIds.length === 0) {
          bundle.geometry.dispose()
          continue
        }
        const overlay = new Mesh(
          bundle.geometry,
          new MeshStandardMaterial({
            color: 0xf59e0b,
            emissive: 0x78350f,
            emissiveIntensity: 0.35,
            roughness: 0.58,
            side: DoubleSide,
            transparent: true,
            opacity: 0.72,
            depthWrite: false,
          }),
        )
        {
          const rotation = new Euler(
            MathUtils.degToRad(rule.tilt.x),
            MathUtils.degToRad(rule.tilt.y),
            MathUtils.degToRad(rule.tilt.z),
          )
          const pivot = resolveTransformPivot(rule, node.center)
          const move = new Vector3(rule.move.x, rule.move.y, rule.move.z)
          overlay.rotation.copy(rotation)
          overlay.position.copy(
            pivotAdjustedPosition(node.center, pivot, move, rotation),
          )
        }
        overlay.renderOrder = 3
        node.transformOverlayRoot.add(overlay)
      }
    }
    // CAD visibility is a display-only switch. Keep the scene and ray
    // overlay mounted so stored paths remain available while the model is
    // hidden from the Import CAD List.
    if (!cadModelVisible) {
      runtime.modelRoot.visible = false
      runtime.roiPreviewRoot.visible = false
      runtime.roiBoundsMarker.visible = false
    }
    onCameraFrameChangeRef.current?.(viewerCameraFrame(runtime))
  }, [
    deletedComponentIds,
    datumFacePickArmed,
    emitterFaceSelectionArmed,
    emitters,
    editingComponentId,
    editingComponentMode,
    hiddenComponentIds,
    materialFacePickArmed,
    materialAssignments,
    componentColorOverrides,
    placementPreviewEmitter,
    placementPreviewReceiver,
    renderMode,
    roiBoxSelectionArmed,
    roiFaceIds,
    roiScopes,
    receivers,
    scene,
    selectedComponentIds,
    selectedFaceIds,
    surfaceOpacity,
    transformRules,
    cadModelVisible,
    onStatusMessage,
  ])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    clearGroup(runtime.rayPathRoot)
    if (!rayTraceResult) return

    const visualization = buildRayPathVisualization(
      rayTraceResult.stored_paths,
      rayPathDisplayFilters,
    )
    for (const filter of rayPathFilterOrder) {
      const segments = visualization.groups[filter]
      if (segments.length === 0) continue
      const positions = new Float32Array(segments.length * 6)
      let offset = 0
      for (const [start, end] of segments) {
        positions.set(start, offset)
        positions.set(end, offset + 3)
        offset += 6
      }
      const geometry = new BufferGeometry()
      geometry.setAttribute(
        'position',
        new Float32BufferAttribute(positions, 3),
      )
      const style = rayPathStyles[filter]
      const lines = new LineSegments(
        geometry,
        new LineBasicMaterial({
          color: style.color,
          transparent: true,
          opacity: style.opacity,
          depthTest: false,
          depthWrite: false,
          toneMapped: false,
        }),
      )
      lines.name = `ray-path-${filter}`
      lines.renderOrder = 84
      runtime.rayPathRoot.add(lines)
    }
    onStatusMessage(
      `Ray paths · ${visualization.visiblePathCount}/${visualization.totalPathCount} visible`,
    )
  }, [onStatusMessage, rayPathDisplayFilters, rayTraceResult])

  const showFullViewPip =
    !roiBoxSelectionArmed &&
    roiScopes.some((scope) => scope.active && scope.clipBox)

  return (
    <div
      className="absolute inset-0 overflow-hidden rounded-[inherit]"
      data-testid="three-viewer"
    >
      <canvas
        ref={canvasRef}
        className={`absolute inset-0 size-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset ${
          roiBoxSelectionArmed ? 'cursor-crosshair' : ''
        } ${
          emitterFaceSelectionArmed ? 'cursor-crosshair' : ''
        } ${materialFacePickArmed ? 'cursor-crosshair' : ''} ${
          pivotPickArmed ? 'cursor-crosshair' : ''
        } ${datumFacePickArmed ? 'cursor-crosshair' : ''}`}
        aria-label="Interactive 3D CAD viewer"
        aria-describedby="three-viewer-controls"
        data-scene-token={scene.metadata.scene_token}
        tabIndex={0}
      />
      {boxDrag ? (
        <div
          data-testid="roi-box-selection"
          className="pointer-events-none absolute z-20 border border-warning bg-warning/15 shadow-[0_0_0_1px_rgba(250,204,21,0.2)]"
          style={{
            left: Math.min(boxDrag.startX, boxDrag.currentX),
            top: Math.min(boxDrag.startY, boxDrag.currentY),
            width: Math.abs(boxDrag.currentX - boxDrag.startX),
            height: Math.abs(boxDrag.currentY - boxDrag.startY),
          }}
        />
      ) : null}
      {showFullViewPip ? (
        <div
          data-testid="full-view-pip-frame"
          className="pointer-events-none absolute z-10 overflow-hidden rounded-md border border-border/70 shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
          style={{
            right: 14,
            bottom: 14,
            width: 'min(220px, 34%)',
            height: 'min(160px, 34%)',
          }}
        >
          <span className="absolute top-1 left-1.5 rounded bg-background/70 px-1 py-0.5 text-[0.6rem] font-medium tracking-wide text-muted-foreground">
            Full View
          </span>
          <button
            type="button"
            className={`pointer-events-auto absolute top-1 right-1 rounded border px-1.5 py-0.5 text-[0.58rem] font-semibold backdrop-blur transition-colors ${
              fullViewCameraSync
                ? 'border-sky-400/70 bg-sky-500/85 text-white'
                : 'border-border/80 bg-background/80 text-muted-foreground hover:bg-background'
            }`}
            aria-label="Sync Full View camera"
            aria-pressed={fullViewCameraSync}
            title="ROI View 회전·줌을 Full View와 동기화"
            onClick={(event) => {
              event.stopPropagation()
              const next = !fullViewCameraSyncRef.current
              fullViewCameraSyncRef.current = next
              fullViewSyncBaseMainDistanceRef.current = null
              fullViewSyncBasePipDistanceRef.current = null
              if (next) {
                const runtime = runtimeRef.current
                if (runtime) runtime.pipUserAdjusted = true
              }
              setFullViewCameraSync(next)
              onStatusMessage(
                `Full View camera sync · ${next ? 'ON' : 'OFF'}`,
              )
            }}
          >
            {fullViewCameraSync ? 'ON' : 'OFF'}
          </button>
        </div>
      ) : null}
      <div
        id="three-viewer-controls"
        className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border/70 bg-background/70 px-2.5 py-1.5 text-[0.62rem] text-muted-foreground backdrop-blur"
      >
        {roiBoxSelectionArmed
          ? 'ROI mode · Left drag select · Wheel zoom · Right drag pan'
          : emitterFaceSelectionArmed
            ? 'Emitter surface mode · Click a CAD surface to add/remove'
          : 'Drag rotate · Wheel zoom · Right drag pan · Click face · Shift multi-select'}
      </div>
      {rendererError ? (
        <div className="absolute inset-0 flex items-center justify-center bg-background/85 p-6 text-center">
          <div>
            <div className="text-sm font-semibold text-destructive">
              Three.js Viewer unavailable
            </div>
            <p className="mt-2 max-w-sm text-xs leading-5 text-muted-foreground">
              {rendererError}
            </p>
          </div>
        </div>
      ) : null}
    </div>
  )
}
