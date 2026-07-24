import { useEffect, useRef, useState } from 'react'
import {
  ACESFilmicToneMapping,
  AxesHelper,
  Box3,
  Color,
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
  MeshStandardMaterial,
  MOUSE,
  PerspectiveCamera,
  Raycaster,
  Scene,
  SRGBColorSpace,
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
import {
  useWorkspaceStore,
  workspaceSelectors,
  type ComponentTransformRule,
  type MaterialAssignment,
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

interface ThreeViewerCanvasProps {
  scene: ScenePayload
  cameraPreset: ViewerCameraPreset
  cameraRequestId: number
  renderMode: ViewerRenderMode
  onStatusMessage(message: string): void
}

interface ComponentRenderNode {
  center: Vector3
  component: SceneComponent
  depthPriority: number
  edges: LineSegments<BufferGeometry, LineBasicMaterial>
  group: Group
  materialOverlayRoot: Group
  surface: Mesh<BufferGeometry, MeshStandardMaterial>
  transformOverlayRoot: Group
}

interface ViewerRuntime {
  camera: PerspectiveCamera
  controls: TrackballControls
  grid: GridHelper
  modelRoot: Group
  nodes: Map<number, ComponentRenderNode>
  raycaster: Raycaster
  renderer: WebGLRenderer
  scene: Scene
  showGrid: boolean
}

interface ViewerMaterialStyle {
  color: Color
  metalness: number
  roughness: number
}

const componentPalette = [
  0x64748b, 0x526b7a, 0x475569, 0x5b6473, 0x45606d, 0x667085,
]

const wireframeSurfaceOpacity = 0.65
const selectedWireframeSurfaceOpacity = 0.78

function surfaceDepthOffset(depthPriority: number): number {
  return 2 + depthPriority * 2
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
    }
  })
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
  runtime.modelRoot.updateMatrixWorld(true)
  const bounds = new Box3().setFromObject(runtime.modelRoot)
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

  runtime.camera.up.set(0, preset === '-XY' ? -1 : 1, 0)
  if (preset === 'Iso' || preset === 'Fit') {
    runtime.camera.up.set(0, 0, 1)
  }

  runtime.camera.position
    .copy(center)
    .add(direction.normalize().multiplyScalar(distance))
  runtime.camera.near = Math.max(distance / 5000, 0.01)
  runtime.camera.far = Math.max(distance * 100, 1000)
  runtime.camera.updateProjectionMatrix()
  runtime.controls.target.copy(center)
  runtime.controls.update()
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
    polygonOffsetFactor: surfaceDepthOffset(index),
    polygonOffsetUnits: surfaceDepthOffset(index),
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
  const transformOverlayRoot = new Group()
  const group = new Group()
  group.name = `component-${component.component_id}`
  group.position.copy(bundle.center)
  group.add(
    surface,
    edges,
    materialOverlayRoot,
    transformOverlayRoot,
  )

  return {
    center: bundle.center,
    component,
    depthPriority: index,
    edges,
    group,
    materialOverlayRoot,
    surface,
    transformOverlayRoot,
  }
}

export function ThreeViewerCanvas({
  scene,
  cameraPreset,
  cameraRequestId,
  renderMode,
  onStatusMessage,
}: ThreeViewerCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<ViewerRuntime | null>(null)
  const [rendererError, setRendererError] = useState('')
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

    const threeScene = new Scene()
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
    threeScene.add(modelRoot)
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

    const axes = new AxesHelper(Math.max(maxDimension * 0.08, 1))
    axes.position.copy(bounds.center)
    threeScene.add(axes)

    const nodes = new Map<number, ComponentRenderNode>()
    scene.components.forEach((component, index) => {
      const node = createComponentNode(scene, component, index)
      nodes.set(component.component_id, node)
      modelRoot.add(node.group)
    })

    const runtime: ViewerRuntime = {
      camera,
      controls,
      grid,
      modelRoot,
      nodes,
      raycaster: new Raycaster(),
      renderer,
      scene: threeScene,
      showGrid: false,
    }
    runtimeRef.current = runtime

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const width = Math.max(Math.floor(rect.width), 1)
      const height = Math.max(Math.floor(rect.height), 1)
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
      renderer.render(threeScene, camera)
      animationFrame = window.requestAnimationFrame(animate)
    }
    animationFrame = window.requestAnimationFrame(animate)

    let pointerDown: { x: number; y: number } | null = null
    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return
      pointerDown = { x: event.clientX, y: event.clientY }
    }
    const handlePointerUp = (event: PointerEvent) => {
      if (event.button !== 0 || !pointerDown) return
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
    }
    const preventContextMenu = (event: MouseEvent) =>
      event.preventDefault()

    canvas.addEventListener('pointerdown', handlePointerDown)
    canvas.addEventListener('pointerup', handlePointerUp)
    canvas.addEventListener('pointercancel', handlePointerCancel)
    canvas.addEventListener('dblclick', handleDoubleClick)
    canvas.addEventListener('contextmenu', preventContextMenu)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      canvas.removeEventListener('pointerdown', handlePointerDown)
      canvas.removeEventListener('pointerup', handlePointerUp)
      canvas.removeEventListener('pointercancel', handlePointerCancel)
      canvas.removeEventListener('dblclick', handleDoubleClick)
      canvas.removeEventListener('contextmenu', preventContextMenu)
      controls.dispose()
      disposeObject(threeScene)
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
      node.surface.material.polygonOffsetFactor =
        surfaceDepthOffset(node.depthPriority)
      node.surface.material.polygonOffsetUnits =
        surfaceDepthOffset(node.depthPriority)
      node.surface.visible = true
      node.edges.visible = renderMode !== 'Surface'
      node.edges.material.color.set(isSelected ? 0x38bdf8 : 0xb9d5e8)
      node.edges.material.opacity = isSelected
        ? 1
        : isWireframe
          ? 1
          : 0.72

      clearGroup(node.materialOverlayRoot)
      clearGroup(node.transformOverlayRoot)
      node.materialOverlayRoot.visible = renderMode !== 'Wireframe'

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
    scene,
    selectedComponentIds,
    transformRules,
  ])

  return (
    <div
      className="absolute inset-0 overflow-hidden rounded-[inherit]"
      data-testid="three-viewer"
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 size-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
        aria-label="Interactive 3D CAD viewer"
        aria-describedby="three-viewer-controls"
        data-scene-token={scene.metadata.scene_token}
        tabIndex={0}
      />
      <div
        id="three-viewer-controls"
        className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border/70 bg-background/70 px-2.5 py-1.5 text-[0.62rem] text-muted-foreground backdrop-blur"
      >
        Drag rotate · Wheel zoom · Right drag pan · Click face · Shift
        multi-select
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
