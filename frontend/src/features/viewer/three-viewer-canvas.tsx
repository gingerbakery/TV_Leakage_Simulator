import { useEffect, useRef, useState } from 'react'
import {
  ACESFilmicToneMapping,
  Box3,
  CanvasTexture,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DirectionalLight,
  DoubleSide,
  EdgesGeometry,
  GridHelper,
  Group,
  HemisphereLight,
  LineBasicMaterial,
  LineSegments,
  MathUtils,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  MOUSE,
  OrthographicCamera,
  Plane,
  PerspectiveCamera,
  Raycaster,
  Scene,
  SRGBColorSpace,
  Sprite,
  SpriteMaterial,
  Vector2,
  Vector3,
  WebGLRenderer,
  type BufferGeometry,
  type Material,
  type Object3D,
} from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'

import type { SceneComponent, ScenePayload } from '@/api'
import {
  findBaseMaterial,
  findSurfaceProperty,
} from '@/features/materials'
import { buildRoiClippedGeometries } from '@/features/roi/roi-clipped-geometry'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type ComponentTransformRule,
  type MaterialAssignment,
  type RoiScope,
} from '@/stores'

import {
  createComponentGeometry,
  createFaceGeometry,
  createFeatureEdgeGeometry,
  getSceneBounds,
} from './scene-geometry'

export type ViewerCameraPreset = 'Fit' | 'Iso' | 'XY' | '-XY'
export type ViewerRenderMode =
  | 'Wireframe'
  | 'Surface'
  | 'Surface + Edge'

export interface RoiBoxSelectionResult {
  clipBox: {
    xMin: number
    xMax: number
    yMin: number
    yMax: number
  }
  view: 'front_xy' | 'back_neg_xy'
}

interface ThreeViewerCanvasProps {
  scene: ScenePayload
  axisScalePercent: number
  cameraPreset: ViewerCameraPreset
  cameraRequestId: number
  renderMode: ViewerRenderMode
  roiBoxSelectionArmed: boolean
  roiFaceIds: number[]
  roiScopes: RoiScope[]
  onRoiBoxSelection(result: RoiBoxSelectionResult): void
  onStatusMessage(message: string): void
}

interface ComponentRenderNode {
  center: Vector3
  component: SceneComponent
  depthPriority: number
  edges: LineSegments<BufferGeometry, LineBasicMaterial>
  group: Group
  materialOverlayRoot: Group
  roiOverlayRoot: Group
  surface: Mesh<BufferGeometry, MeshStandardMaterial>
  transformOverlayRoot: Group
}

interface ViewerRuntime {
  axisScalePercent: number
  camera: PerspectiveCamera
  controls: TrackballControls
  grid: GridHelper
  modelRoot: Group
  nodes: Map<number, ComponentRenderNode>
  raycaster: Raycaster
  renderer: WebGLRenderer
  roiSelectionCameraUp: Vector3 | null
  roiSelectionViewDirection: Vector3 | null
  roiPreviewKey: string
  roiPreviewRoot: Group
  scene: Scene
  showGrid: boolean
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

const componentPalette = [
  0x64748b, 0x526b7a, 0x475569, 0x5b6473, 0x45606d, 0x667085,
]

const wireframeSurfaceOpacity = 0.65
const selectedWireframeSurfaceOpacity = 0.78

function surfaceDepthUnits(depthPriority: number): number {
  return 4 + depthPriority * 4
}

const materialColors: Record<string, number> = {
  black_powder_coated_aluminum: 0x394552,
  black_pc_resin: 0x202a35,
  anodized_aluminum: 0x8a99a8,
  matte_black_abs: 0x2c3744,
  black_tape_general: 0x111827,
  foam_absorber_general: 0x17202b,
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

function createAxisLabel(text: string, color: string): Sprite {
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
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  label.scale.set(0.38, 0.38, 1)
  return label
}

function createOrientationGizmo(): Group {
  const gizmo = new Group()
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
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    })
    const shaft = new Mesh(
      new CylinderGeometry(0.022, 0.022, 1, 14),
      material,
    )
    shaft.position.copy(axis.direction).multiplyScalar(0.5)
    shaft.quaternion.setFromUnitVectors(up, axis.direction)
    shaft.renderOrder = 200
    gizmo.add(shaft)

    const head = new Mesh(
      new ConeGeometry(0.065, 0.2, 18),
      material.clone(),
    )
    head.position.copy(axis.direction)
    head.quaternion.setFromUnitVectors(up, axis.direction)
    head.renderOrder = 201
    gizmo.add(head)

    const label = createAxisLabel(axis.name, axis.color)
    label.position.copy(axis.direction).multiplyScalar(1.28)
    label.renderOrder = 202
    gizmo.add(label)
  }

  return gizmo
}

function clearGroup(group: Group): void {
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
    color: new Color(
      materialColors[assignment.baseMaterialId] ?? fallbackColor,
    ),
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
    side: DoubleSide,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 1,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  })
}

function applyComponentTransform(
  node: ComponentRenderNode,
  transformRules: ComponentTransformRule[],
): void {
  node.group.position.copy(node.center)
  node.group.rotation.set(0, 0, 0)

  const rule = transformRules.find(
    (candidate) =>
      candidate.enabled &&
      candidate.componentId === node.component.component_id &&
      candidate.targetType === 'component',
  )
  if (!rule) return

  node.group.position.add(
    new Vector3(rule.move.x, rule.move.y, rule.move.z),
  )
  node.group.rotation.set(
    MathUtils.degToRad(rule.tilt.x),
    MathUtils.degToRad(rule.tilt.y),
    MathUtils.degToRad(rule.tilt.z),
  )
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
  } else if (preset === 'XY') {
    direction.set(0, 0, 1)
  } else if (preset === '-XY') {
    direction.set(0, 0, -1)
  }

  if (preset === 'XY') {
    runtime.camera.up.set(0, 1, 0)
  } else if (preset === '-XY') {
    runtime.camera.up.set(0, -1, 0)
  } else if (preset === 'Iso') {
    runtime.camera.up.set(0, 0, 1)
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

function restoreRoiSelectionCameraPose(
  runtime: ViewerRuntime,
): boolean {
  const direction = runtime.roiSelectionViewDirection
  const up = runtime.roiSelectionCameraUp
  if (!direction || !up) return false

  const distance = Math.max(
    runtime.camera.position.distanceTo(runtime.controls.target),
    1,
  )
  runtime.camera.up.copy(up)
  runtime.camera.position
    .copy(runtime.controls.target)
    .add(direction.clone().multiplyScalar(distance))
  runtime.controls.update()
  runtime.roiSelectionViewDirection = null
  runtime.roiSelectionCameraUp = null
  return true
}

function createComponentNode(
  scene: ScenePayload,
  component: SceneComponent,
  index: number,
): ComponentRenderNode {
  const bundle = createComponentGeometry(scene, component)
  const surfaceMaterial = new MeshStandardMaterial({
    color: componentPalette[index % componentPalette.length],
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

  const materialOverlayRoot = new Group()
  const roiOverlayRoot = new Group()
  const transformOverlayRoot = new Group()
  const group = new Group()
  group.name = `component-${component.component_id}`
  group.position.copy(bundle.center)
  group.add(
    surface,
    edges,
    materialOverlayRoot,
    roiOverlayRoot,
    transformOverlayRoot,
  )

  return {
    center: bundle.center,
    component,
    depthPriority: index,
    edges,
    group,
    materialOverlayRoot,
    roiOverlayRoot,
    surface,
    transformOverlayRoot,
  }
}

export function ThreeViewerCanvas({
  scene,
  axisScalePercent,
  cameraPreset,
  cameraRequestId,
  renderMode,
  roiBoxSelectionArmed,
  roiFaceIds,
  roiScopes,
  onRoiBoxSelection,
  onStatusMessage,
}: ThreeViewerCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<ViewerRuntime | null>(null)
  const roiBoxSelectionArmedRef = useRef(roiBoxSelectionArmed)
  const onRoiBoxSelectionRef = useRef(onRoiBoxSelection)
  const boxDragRef = useRef<ViewerBoxDrag | null>(null)
  const [rendererError, setRendererError] = useState('')
  const [boxDrag, setBoxDrag] = useState<ViewerBoxDrag | null>(null)
  const selectedComponentIds = useWorkspaceStore(
    workspaceSelectors.selectedComponentIds,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const materialAssignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const transformRules = useWorkspaceStore(
    workspaceSelectors.transformRules,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  useEffect(() => {
    roiBoxSelectionArmedRef.current = roiBoxSelectionArmed
  }, [roiBoxSelectionArmed])

  useEffect(() => {
    onRoiBoxSelectionRef.current = onRoiBoxSelection
  }, [onRoiBoxSelection])

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
    const camera = new PerspectiveCamera(42, 1, 0.01, 100000)
    camera.up.set(0, 0, 1)
    const controls = new TrackballControls(camera, canvas)
    controls.staticMoving = true
    controls.rotateSpeed = 1.15
    controls.zoomSpeed = 1.2
    controls.panSpeed = 0.6
    controls.mouseButtons = {
      LEFT: MOUSE.ROTATE,
      MIDDLE: MOUSE.DOLLY,
      RIGHT: MOUSE.PAN,
    }

    const modelRoot = new Group()
    const roiPreviewRoot = new Group()
    roiPreviewRoot.name = 'roi-preview-root'
    roiPreviewRoot.visible = false
    threeScene.add(modelRoot, roiPreviewRoot)
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
    const grid = new GridHelper(
      maxDimension * 1.8,
      18,
      0x334155,
      0x1e293b,
    )
    grid.rotation.x = Math.PI / 2
    grid.position.set(
      bounds.center.x,
      bounds.center.y,
      bounds.center.z -
        bounds.size.z / 2 -
        maxDimension * 0.0125,
    )
    const gridMaterial = grid.material as LineBasicMaterial
    gridMaterial.transparent = true
    gridMaterial.opacity = 0.28
    gridMaterial.depthWrite = false
    grid.renderOrder = -100
    threeScene.add(grid)

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
      grid,
      modelRoot,
      nodes,
      raycaster: new Raycaster(),
      renderer,
      roiSelectionCameraUp: null,
      roiSelectionViewDirection: null,
      roiPreviewKey: '',
      roiPreviewRoot,
      scene: threeScene,
      showGrid: false,
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

    let animationFrame = 0
    const animate = () => {
      controls.update()
      runtime.grid.visible =
        runtime.showGrid && camera.position.z > grid.position.z
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
      const xValues = points.map((point) => point.x)
      const yValues = points.map((point) => point.y)
      return {
        clipBox: {
          xMin: Math.min(...xValues),
          xMax: Math.max(...xValues),
          yMin: Math.min(...yValues),
          yMax: Math.max(...yValues),
        },
        view:
          camera.position.z < controls.target.z
            ? 'back_neg_xy'
            : 'front_xy',
      }
    }

    let pointerDown: { x: number; y: number } | null = null
    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return
      if (roiBoxSelectionArmedRef.current) {
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
      if (event.button !== 0) return
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

      const rect = canvas.getBoundingClientRect()
      const pointer = new Vector2(
        ((event.clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1,
        -((event.clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1,
      )
      runtime.raycaster.setFromCamera(pointer, camera)
      const candidates = [...nodes.values()]
        .filter((node) => node.group.visible)
        .map((node) => node.surface)
      const hit = runtime.raycaster.intersectObjects(candidates, false)[0]
      const additive = event.ctrlKey || event.metaKey || event.shiftKey
      const hitFaceIndex = hit?.faceIndex

      if (!hit || hitFaceIndex === null || hitFaceIndex === undefined) {
        if (!additive) {
          actions.setSelectedComponentIds([])
          actions.setSelectedFaceIds([])
          onStatusMessage('Viewer selection을 해제했습니다.')
        }
        return
      }

      const componentId = Number(hit.object.userData.componentId)
      const sourceFaceIds = hit.object.userData.sourceFaceIds as
        | number[]
        | undefined
      const faceId = sourceFaceIds?.[hitFaceIndex]
      if (
        !Number.isSafeInteger(componentId) ||
        faceId === undefined ||
        !Number.isSafeInteger(faceId)
      ) {
        return
      }

      if (additive) {
        actions.toggleSelectedComponentId(componentId)
        actions.toggleSelectedFaceId(faceId)
      } else {
        actions.setSelectedComponentIds([componentId])
        actions.setSelectedFaceIds([faceId])
      }
      onStatusMessage(
        `Viewer picking · Component ${componentId} · Face ${faceId}`,
      )
    }
    const handleDoubleClick = () => {
      fitCamera(runtime, 'Fit')
      onStatusMessage('Camera preset · Fit')
    }
    const handlePointerCancel = () => {
      pointerDown = null
      boxDragRef.current = null
      setBoxDrag(null)
    }
    const preventContextMenu = (event: MouseEvent) =>
      event.preventDefault()

    canvas.addEventListener('pointerdown', handlePointerDown)
    canvas.addEventListener('pointermove', handlePointerMove)
    canvas.addEventListener('pointerup', handlePointerUp)
    canvas.addEventListener('pointercancel', handlePointerCancel)
    canvas.addEventListener('dblclick', handleDoubleClick)
    canvas.addEventListener('contextmenu', preventContextMenu)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      canvas.removeEventListener('pointerdown', handlePointerDown)
      canvas.removeEventListener('pointermove', handlePointerMove)
      canvas.removeEventListener('pointerup', handlePointerUp)
      canvas.removeEventListener('pointercancel', handlePointerCancel)
      canvas.removeEventListener('dblclick', handleDoubleClick)
      canvas.removeEventListener('contextmenu', preventContextMenu)
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
  }, [cameraPreset, cameraRequestId, scene])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    runtime.axisScalePercent = axisScalePercent
  }, [axisScalePercent])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return

    runtime.controls.noRotate = roiBoxSelectionArmed
    if (roiBoxSelectionArmed) {
      if (!runtime.roiSelectionViewDirection) {
        runtime.roiSelectionViewDirection = runtime.camera.position
          .clone()
          .sub(runtime.controls.target)
          .normalize()
        runtime.roiSelectionCameraUp = runtime.camera.up
          .clone()
          .normalize()
      }
      runtime.roiPreviewRoot.visible = false
      runtime.modelRoot.visible = true
      const preset =
        runtime.camera.position.z < runtime.controls.target.z
          ? '-XY'
          : 'XY'
      fitCamera(runtime, preset)
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
    })

    if (showRoiPreview && runtime.roiPreviewKey !== previewKey) {
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
              flatShading: true,
              transparent: false,
              opacity: 1,
              side: DoubleSide,
              depthTest: true,
              depthWrite: true,
            })
        const surface = new Mesh(
          clipped.surfaceGeometry,
          surfaceMaterial,
        )
        surface.name = 'roi-clipped-surface'
        runtime.roiPreviewRoot.add(surface)

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
                transparent: false,
                opacity: 1,
                side: DoubleSide,
                depthTest: true,
                depthWrite: true,
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
          const featureEdges = new LineSegments(
            clipped.featureEdgeGeometry,
            new LineBasicMaterial({
              color: 0xd7edf8,
              transparent: true,
              opacity: isWireframe ? 0.96 : 0.74,
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
              opacity: 0.9,
              depthTest: true,
              depthWrite: false,
            }),
          )
          capEdges.name = 'roi-cap-edges'
          capEdges.renderOrder = 4
          runtime.roiPreviewRoot.add(capEdges)
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
        restoreRoiSelectionCameraPose(runtime)
        fitCamera(runtime, 'Fit')
        onStatusMessage(
          `ROI isolated solid · ${clipped.clippedTriangleCount.toLocaleString()} triangles · ${clipped.capLoopCount} section caps`,
        )
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
        fitCamera(runtime, 'Fit')
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
      if (restoreRoiSelectionCameraPose(runtime)) {
        fitCamera(runtime, 'Fit')
      }
    } else if (!showRoiPreview) {
      runtime.roiPreviewRoot.visible = false
      runtime.modelRoot.visible = true
      if (
        !roiBoxSelectionArmed &&
        restoreRoiSelectionCameraPose(runtime)
      ) {
        fitCamera(runtime, 'Fit')
      }
    }

    const roiFaceSet = new Set(roiFaceIds)
    for (const [componentId, node] of runtime.nodes) {
      const isSelected = selectedComponentIds.includes(componentId)
      const isUnavailable =
        hiddenComponentIds.includes(componentId) ||
        deletedComponentIds.includes(componentId)
      node.group.visible = !isUnavailable
      applyComponentTransform(node, transformRules)

      const partAssignment = materialAssignments.find(
        (assignment) =>
          assignment.enabled &&
          assignment.componentId === componentId &&
          assignment.targetType === 'part',
      )
      const fallbackColor =
        componentPalette[
          Math.max(0, scene.components.indexOf(node.component)) %
            componentPalette.length
        ]
      const style = viewerMaterialStyle(partAssignment, fallbackColor)
      const displayColor = style.color.clone()
      if (isSelected) displayColor.lerp(new Color(0x38bdf8), 0.58)

      node.surface.material.color.copy(displayColor)
      node.surface.material.emissive.set(isSelected ? 0x082f49 : 0x000000)
      node.surface.material.emissiveIntensity = isSelected ? 0.72 : 0
      node.surface.material.metalness = style.metalness
      node.surface.material.roughness = style.roughness
      const isWireframe = renderMode === 'Wireframe'
      if (node.surface.material.transparent !== isWireframe) {
        node.surface.material.transparent = isWireframe
        node.surface.material.needsUpdate = true
      }
      node.surface.material.opacity = isWireframe
        ? isSelected
          ? selectedWireframeSurfaceOpacity
          : wireframeSurfaceOpacity
        : 1
      node.surface.material.depthWrite = true
      node.surface.material.polygonOffsetFactor = 0
      node.surface.material.polygonOffsetUnits =
        surfaceDepthUnits(node.depthPriority)
      node.surface.visible = true
      node.edges.visible = renderMode !== 'Surface'
      node.edges.material.color.set(isSelected ? 0x38bdf8 : 0xb9d5e8)
      node.edges.material.opacity = isSelected
        ? 1
        : isWireframe
          ? 1
          : 0.72

      clearGroup(node.materialOverlayRoot)
      clearGroup(node.roiOverlayRoot)
      clearGroup(node.transformOverlayRoot)
      node.materialOverlayRoot.visible = renderMode !== 'Wireframe'

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
            viewerMaterialStyle(assignment, fallbackColor),
            0.96,
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
        overlay.position.set(rule.move.x, rule.move.y, rule.move.z)
        overlay.rotation.set(
          MathUtils.degToRad(rule.tilt.x),
          MathUtils.degToRad(rule.tilt.y),
          MathUtils.degToRad(rule.tilt.z),
        )
        overlay.renderOrder = 3
        node.transformOverlayRoot.add(overlay)
      }
    }
    runtime.showGrid = renderMode !== 'Wireframe'
  }, [
    deletedComponentIds,
    hiddenComponentIds,
    materialAssignments,
    renderMode,
    roiBoxSelectionArmed,
    roiFaceIds,
    roiScopes,
    scene,
    selectedComponentIds,
    transformRules,
    onStatusMessage,
  ])

  return (
    <div
      className="absolute inset-0 overflow-hidden rounded-[inherit]"
      data-testid="three-viewer"
    >
      <canvas
        ref={canvasRef}
        className={`absolute inset-0 size-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset ${
          roiBoxSelectionArmed ? 'cursor-crosshair' : ''
        }`}
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
      <div
        id="three-viewer-controls"
        className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border/70 bg-background/70 px-2.5 py-1.5 text-[0.62rem] text-muted-foreground backdrop-blur"
      >
        {roiBoxSelectionArmed
          ? 'ROI mode · Left drag select · Wheel zoom · Right drag pan'
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
