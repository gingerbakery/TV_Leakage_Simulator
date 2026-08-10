import {
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  HemisphereLight,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshStandardMaterial,
  OrthographicCamera,
  Plane,
  Scene,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from 'three'

import type { RayHit, ReceiverSpec, ScenePayload } from '@/api'
import type { RayPathDisplayFilter } from '@/stores'

import {
  createFaceGeometry,
  resolveComponentColor,
} from '@/features/viewer/scene-geometry'

import {
  buildRayPathVisualization,
  rayPathFilterOrder,
  rayPathStyles,
} from './ray-paths'
import { computeSectionCapTriangles } from './section-cap-geometry'

const worldUp = new Vector3(0, 0, 1)
const worldX = new Vector3(1, 0, 0)
const minimumAxisLength = 1e-6

export interface SectionPlaneBasis {
  origin: Vector3
  viewNormal: Vector3
  up: Vector3
}

/**
 * A vertical section plane through the receiver's center that contains its
 * normal (boresight) vector - both `viewNormal` (perpendicular to the plane,
 * the direction a camera looks along to see it face-on) and `up` are
 * perpendicular to the boresight by construction.
 */
export function computeSectionPlaneBasis(
  receiver: ReceiverSpec,
): SectionPlaneBasis | null {
  const boresight = new Vector3(...receiver.normal)
  if (boresight.lengthSq() < minimumAxisLength) return null
  boresight.normalize()

  let viewNormal = boresight.clone().cross(worldUp)
  if (viewNormal.lengthSq() < minimumAxisLength) {
    viewNormal = boresight.clone().cross(worldX)
  }
  if (viewNormal.lengthSq() < minimumAxisLength) return null
  viewNormal.normalize()

  return {
    origin: new Vector3(...receiver.center),
    up: worldUp.clone(),
    viewNormal,
  }
}

export interface RaySectionImageOptions {
  scene: ScenePayload
  receiver: ReceiverSpec
  storedPaths: RayHit[][]
  /** Active ROI face ids, if an ROI scope is active - when given (non-
   * empty), the render is scoped to just these faces so it matches what
   * the user is actually looking at in the ROI-clipped viewer instead of
   * the full, uncropped model. */
  roiFaceIds?: number[]
  width?: number
  height?: number
}

// Rendered well above the card's typical on-screen width (the result
// panel can stretch the <img> to 1900px+) so upscaling doesn't turn clean
// edges into a blocky staircase - this is a one-shot static render, so the
// extra render cost is negligible.
const defaultWidth = 1600
const defaultHeight = 1000
// White, so each component's own assigned/authored color (however dark or
// light) reads clearly - unlike the earlier dark report background, which
// made near-black authored CAD colors indistinguishable from the
// background and forced a single flat fallback color for everything.
const backgroundColor = 0xffffff
// A touch darker than a plain mid-gray so the true cut face reads as
// distinct from the receding component surfaces behind it - the same
// visual convention CAD section views use (shaded cut face vs. plain
// surfaces beyond it).
const sectionCapColor = 0x94a3b8
// Report-specific palette, distinct from the interactive viewer's
// dark-background palette (`rayPathStyles`) - light green/yellow read
// fine on a dark canvas but wash out almost completely on white, so this
// image needs its own darker, higher-contrast tones for the same roles.
const reportDirectRayColor = 0x15803d
const reportReflectedRayColor = 0xb45309
const receiverMarkerColor = 0x0e7490

function disposeGeometry(geometry: BufferGeometry) {
  geometry.dispose()
}

/**
 * World-space AABB of exactly the given faces, not the whole scene - used
 * instead of the scene-wide bounds so that, when an ROI is active, framing
 * and the clip-side decision are based on what's actually being rendered
 * (the ROI-clipped subset) rather than the full, uncropped model.
 */
function computeFaceSetBounds(
  scene: ScenePayload,
  faceIds: Iterable<number>,
): { center: Vector3; size: Vector3 } {
  const minimum = new Vector3(Infinity, Infinity, Infinity)
  const maximum = new Vector3(-Infinity, -Infinity, -Infinity)
  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    for (const vertexId of face) {
      const vertex = scene.mesh.vertices[vertexId]
      if (!vertex) continue
      minimum.min(new Vector3(vertex[0], vertex[1], vertex[2]))
      maximum.max(new Vector3(vertex[0], vertex[1], vertex[2]))
    }
  }
  if (!Number.isFinite(minimum.x) || !Number.isFinite(maximum.x)) {
    return { center: new Vector3(), size: new Vector3(1, 1, 1) }
  }
  return {
    center: minimum.clone().add(maximum).multiplyScalar(0.5),
    size: maximum.clone().sub(minimum),
  }
}

/**
 * Renders a static PNG (as a data URL) of the CAD geometry cut open along
 * the receiver's section plane, with only the ray paths that reached that
 * receiver drawn on top. Returns null if the receiver's normal is degenerate
 * or if WebGL rendering isn't available (e.g. a headless/test environment).
 */
export function renderRaySectionImage({
  scene,
  receiver,
  storedPaths,
  roiFaceIds,
  width = defaultWidth,
  height = defaultHeight,
}: RaySectionImageOptions): string | null {
  const roiFaceSet =
    roiFaceIds && roiFaceIds.length > 0 ? new Set(roiFaceIds) : null
  const basis = computeSectionPlaneBasis(receiver)
  if (!basis) return null

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height

  let renderer: WebGLRenderer
  try {
    renderer = new WebGLRenderer({
      canvas,
      antialias: true,
      preserveDrawingBuffer: true,
    })
  } catch {
    return null
  }

  const disposables: { dispose(): void }[] = []
  try {
    renderer.setSize(width, height, false)
    renderer.setPixelRatio(1)
    renderer.outputColorSpace = SRGBColorSpace
    renderer.setClearColor(backgroundColor, 1)
    renderer.localClippingEnabled = true

    const threeScene = new Scene()
    threeScene.background = new Color(backgroundColor)

    // A section/cutaway view mostly shows interior faces that a typical
    // single-direction key light never reaches - without a strong,
    // direction-independent base light those faces render almost pure
    // black (indistinguishable from the background). Lean on a bright
    // hemisphere light plus lights from two opposing directions so every
    // face orientation gets reasonable illumination.
    const ambient = new HemisphereLight(0xffffff, 0x4b5a72, 1.3)
    const keyLight = new DirectionalLight(0xffffff, 0.9)
    keyLight.position.set(1, 1, 1.5)
    const fillLight = new DirectionalLight(0xffffff, 0.6)
    fillLight.position.set(-1, -1, -1.5)
    threeScene.add(ambient, keyLight, fillLight)

    const renderedFaceIdsByComponent = scene.components.map((component, index) => ({
      component,
      index,
      faceIds: roiFaceSet
        ? component.face_indices.filter((id) => roiFaceSet.has(id))
        : component.face_indices,
    }))
    const allRenderedFaceIds = renderedFaceIdsByComponent.flatMap(
      (entry) => entry.faceIds,
    )
    const bounds = computeFaceSetBounds(scene, allRenderedFaceIds)
    const diagonal = Math.max(bounds.size.length(), 1)
    const halfSize = bounds.size.clone().multiplyScalar(0.5)
    const aabbCorners: Vector3[] = []
    for (const signX of [-1, 1]) {
      for (const signY of [-1, 1]) {
        for (const signZ of [-1, 1]) {
          aabbCorners.push(
            bounds.center
              .clone()
              .add(
                new Vector3(
                  halfSize.x * signX,
                  halfSize.y * signY,
                  halfSize.z * signZ,
                ),
              ),
          )
        }
      }
    }

    // Which side of the section plane the bulk of the CAD geometry actually
    // sits on varies a lot - a receiver is often placed right on or beyond a
    // component's surface, not straddling the model symmetrically. Always
    // clipping away a fixed "camera side" can end up removing the entire
    // model (a blank image) if that happens to be the side everything is
    // on. A single bounding-box-centroid sign check isn't reliable either -
    // it degenerates to a coin flip whenever the receiver sits near the
    // model's own center (e.g. mounted on a large flat panel's face, close
    // to that panel's centroid). Instead measure how far the AABB actually
    // extends to each side of the origin and keep whichever side extends
    // further - meaningful even when the origin is near the centroid.
    let positiveExtent = 0
    let negativeExtent = 0
    for (const corner of aabbCorners) {
      const projection = corner.clone().sub(basis.origin).dot(basis.viewNormal)
      positiveExtent = Math.max(positiveExtent, projection)
      negativeExtent = Math.max(negativeExtent, -projection)
    }
    const cameraSide = positiveExtent >= negativeExtent ? -1 : 1

    const clipPlane = new Plane().setFromNormalAndCoplanarPoint(
      basis.viewNormal.clone().multiplyScalar(-cameraSide),
      basis.origin,
    )

    // The section plane can pass a bounding-box extent check (there's
    // real depth to reveal) while still rendering as an almost-empty
    // image: if the rendered faces are mostly flat and oriented so their
    // normal is perpendicular to viewNormal (e.g. only the top/bottom
    // faces of a thin panel got included, no side walls), every triangle
    // is edge-on to this camera and projects to ~zero screen area no
    // matter which direction you view it from. A bounding-box check can't
    // catch this - it has to look at actual per-face orientation.
    let totalFaceArea = 0
    let totalProjectedFaceArea = 0
    for (const faceId of allRenderedFaceIds) {
      const normalValues = scene.mesh.face_normals[faceId]
      const area = scene.mesh.face_areas_mm2?.[faceId]
      if (!normalValues || !area) continue
      totalFaceArea += area
      const normal = new Vector3(...normalValues).normalize()
      totalProjectedFaceArea += area * Math.abs(normal.dot(basis.viewNormal))
    }
    const cadEdgeOnFromThisAngle =
      totalFaceArea > 0 && totalProjectedFaceArea / totalFaceArea < 0.02

    for (const { component, index, faceIds } of renderedFaceIdsByComponent) {
      if (faceIds.length === 0) continue
      const bundle = createFaceGeometry(scene, faceIds, new Vector3())
      if (bundle.geometry.getAttribute('position').count === 0) {
        disposeGeometry(bundle.geometry)
        continue
      }
      const material = new MeshStandardMaterial({
        color: resolveComponentColor(component, index),
        side: DoubleSide,
        metalness: 0.1,
        roughness: 0.75,
        // Ray Summary must remain a real cutaway even for thin products.
        // The old depth heuristic disabled clipping on thin TV assemblies,
        // making the report look like an ordinary aligned CAD projection
        // and hiding the internal ray route behind the front surfaces.
        clippingPlanes: [clipPlane],
      })
      const mesh = new Mesh(bundle.geometry, material)
      threeScene.add(mesh)
      disposables.push(bundle.geometry, material)
    }

    // The true filled cross-section: the actual polygon where the cut
    // plane meets the solid boundary, not just a view of existing surface
    // triangles from a clipped angle. This is what makes the report
    // behave like a real CAD section view - unlike the GPU-clipped
    // surfaces above (which can render nothing if every visible triangle
    // happens to be edge-on to the camera), a computed intersection
    // polygon always faces the viewer squarely, because it *is* the
    // section, not a projection of something else.
    const capRight = new Vector3()
      .crossVectors(
        basis.viewNormal.clone().multiplyScalar(-cameraSide),
        basis.up,
      )
      .normalize()
    const capTriangles = computeSectionCapTriangles(
      scene,
      allRenderedFaceIds,
      basis.origin,
      basis.viewNormal,
      basis.up,
      capRight,
    )
    if (capTriangles) {
      const capGeometry = new BufferGeometry()
      capGeometry.setAttribute(
        'position',
        new Float32BufferAttribute(capTriangles, 3),
      )
      capGeometry.computeVertexNormals()
      const capMaterial = new MeshStandardMaterial({
        color: sectionCapColor,
        side: DoubleSide,
        metalness: 0.05,
        roughness: 0.85,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -1,
      })
      const capMesh = new Mesh(capGeometry, capMaterial)
      capMesh.renderOrder = 5
      threeScene.add(capMesh)
      disposables.push(capGeometry, capMaterial)
    }

    const targetPaths = storedPaths.filter(
      (path) =>
        path.length > 0 &&
        path[path.length - 1]?.receiver_id === receiver.receiver_id,
    )
    const visualization = buildRayPathVisualization(targetPaths, {
      receiver_direct: true,
      receiver_reflected: true,
      direct: false,
      specular: false,
      lambertian: false,
      gaussian: false,
    })
    const reportRayColors: Partial<Record<RayPathDisplayFilter, number>> = {
      receiver_direct: reportDirectRayColor,
      receiver_reflected: reportReflectedRayColor,
    }
    for (const filter of rayPathFilterOrder) {
      const segments = visualization.groups[filter]
      if (segments.length === 0) continue
      const positions = new Float32Array(segments.length * 6)
      segments.forEach(([start, end], index) => {
        positions.set(
          [start[0], start[1], start[2], end[0], end[1], end[2]],
          index * 6,
        )
      })
      const geometry = new BufferGeometry()
      geometry.setAttribute(
        'position',
        new Float32BufferAttribute(positions, 3),
      )
      const material = new LineBasicMaterial({
        color: reportRayColors[filter] ?? rayPathStyles[filter].color,
        transparent: true,
        opacity: 1,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
      })
      const lines = new LineSegments(geometry, material)
      lines.renderOrder = 10
      threeScene.add(lines)
      disposables.push(geometry, material)
    }

    // Draw the receiver's own rectangle (true width/height, in its actual
    // 3D orientation) plus a short stub along its boresight - this is what
    // lets the image be checked at a glance rather than taken on faith:
    // the rectangle should sit exactly where the converging rays end, and
    // the section cut should pass through its center. Not clipped by
    // clipPlane (no clippingPlanes set) so it stays fully visible even
    // where the CAD cutaway removes the surrounding geometry.
    const receiverFramingPoints: Vector3[] = [basis.origin]
    if (receiver.u_axis && receiver.v_axis) {
      const uAxis = new Vector3(...receiver.u_axis).normalize()
      const vAxis = new Vector3(...receiver.v_axis).normalize()
      const halfU = uAxis.clone().multiplyScalar(receiver.width_mm / 2)
      const halfV = vAxis.clone().multiplyScalar(receiver.height_mm / 2)
      const corners = [
        basis.origin.clone().sub(halfU).sub(halfV),
        basis.origin.clone().add(halfU).sub(halfV),
        basis.origin.clone().add(halfU).add(halfV),
        basis.origin.clone().sub(halfU).add(halfV),
      ]
      const boresight = new Vector3(...receiver.normal)
        .normalize()
        .multiplyScalar(receiver.normal_flip ? -1 : 1)
      const boresightLength = Math.min(
        Math.max(
          Math.min(receiver.width_mm, receiver.height_mm) * 0.35,
          diagonal * 0.03,
        ),
        diagonal * 0.25,
      )
      const boresightTip = basis.origin
        .clone()
        .addScaledVector(boresight, boresightLength)
      const markerPositions = new Float32Array(
        [
          [corners[0], corners[1]],
          [corners[1], corners[2]],
          [corners[2], corners[3]],
          [corners[3], corners[0]],
          [basis.origin, boresightTip],
        ].flatMap(([start, end]) => [
          start.x, start.y, start.z,
          end.x, end.y, end.z,
        ]),
      )
      const markerGeometry = new BufferGeometry()
      markerGeometry.setAttribute(
        'position',
        new Float32BufferAttribute(markerPositions, 3),
      )
      const markerMaterial = new LineBasicMaterial({
        color: receiverMarkerColor,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
      })
      const markerLines = new LineSegments(markerGeometry, markerMaterial)
      markerLines.renderOrder = 12
      threeScene.add(markerLines)
      disposables.push(markerGeometry, markerMaterial)
      receiverFramingPoints.push(...corners, boresightTip)
    }

    // Frame an asymmetric frustum around both the scene geometry and the
    // section origin (the receiver's center) - the receiver is often placed
    // some distance off the CAD surface, so a frustum sized/centered purely
    // on the scene's own bounding box can push the geometry off to one edge
    // of the image instead of keeping both subjects in view.
    //
    // `right` must be derived from the camera's actual forward direction
    // (which flips with `cameraSide`) to match how Three's own lookAt()
    // orients the camera - camera.right = forward x up. Using a fixed
    // convention here instead would silently mismatch the frustum against
    // what's actually rendered whenever cameraSide is -1, pushing the
    // asymmetric bounds to the wrong side and clipping the image blank.
    const forward = basis.viewNormal.clone().multiplyScalar(-cameraSide)
    const right = new Vector3()
      .crossVectors(forward, basis.up)
      .normalize()
    let minU = 0
    let maxU = 0
    let minV = 0
    let maxV = 0
    for (const point of [...aabbCorners, ...receiverFramingPoints]) {
      const offset = point.clone().sub(basis.origin)
      const u = offset.dot(right)
      const v = offset.dot(basis.up)
      minU = Math.min(minU, u)
      maxU = Math.max(maxU, u)
      minV = Math.min(minV, v)
      maxV = Math.max(maxV, v)
    }
    const padding = Math.max(maxU - minU, maxV - minV, 1) * 0.12
    const camera = new OrthographicCamera(
      minU - padding,
      maxU + padding,
      maxV + padding,
      minV - padding,
      0.01,
      diagonal * 4,
    )
    camera.position
      .copy(basis.origin)
      .addScaledVector(basis.viewNormal, cameraSide * diagonal * 1.5)
    camera.up.copy(basis.up)
    camera.lookAt(basis.origin)
    camera.updateProjectionMatrix()
    camera.updateMatrixWorld(true)

    renderer.render(threeScene, camera)

    // The filled cap (computed above) already covers the case the
    // GPU-clipped surfaces can't: even when every surface triangle is
    // edge-on to this camera, a real cross-section polygon still faces
    // the viewer. Only warn when *neither* produced anything - the
    // surfaces are edge-on and there was no closed cross-section to fill
    // either (e.g. the ROI-scoped face set is missing the faces needed to
    // close the cut into a loop).
    if (!cadEdgeOnFromThisAngle || capTriangles) {
      return canvas.toDataURL('image/png')
    }

    // Rather than silently hand back an image that looks broken (just
    // rays/receiver floating on a blank background), composite a short
    // explanation onto it. Three's WebGL canvas can't draw 2D text
    // directly, so copy the render into a 2D canvas first.
    const overlayCanvas = document.createElement('canvas')
    overlayCanvas.width = width
    overlayCanvas.height = height
    const ctx = overlayCanvas.getContext('2d')
    if (!ctx) return canvas.toDataURL('image/png')
    ctx.drawImage(canvas, 0, 0)
    const bannerHeight = Math.round(height * 0.09)
    ctx.fillStyle = '#fef2f2'
    ctx.fillRect(0, 0, width, bannerHeight)
    ctx.strokeStyle = '#fecaca'
    ctx.lineWidth = 2
    ctx.strokeRect(1, 1, width - 2, bannerHeight - 2)
    ctx.fillStyle = '#991b1b'
    ctx.font = `${Math.round(bannerHeight * 0.34)}px sans-serif`
    ctx.textBaseline = 'middle'
    ctx.fillText(
      '이 각도에서는 CAD 단면이 거의 보이지 않습니다',
      width * 0.02,
      bannerHeight * 0.38,
    )
    ctx.fillStyle = '#b91c1c'
    ctx.font = `${Math.round(bannerHeight * 0.26)}px sans-serif`
    ctx.fillText(
      '선택된 면이 이 절단 방향과 거의 평행하고, 단면을 채울 만큼 연결된 면도 없습니다 (예: ROI로 옆면 없이 윗면·아랫면만 선택된 경우)',
      width * 0.02,
      bannerHeight * 0.74,
    )
    return overlayCanvas.toDataURL('image/png')
  } catch {
    return null
  } finally {
    for (const disposable of disposables) disposable.dispose()
    // dispose() alone releases Three.js-side resources but doesn't
    // guarantee the browser frees the underlying WebGL context right away -
    // with a context created per receiver (and React StrictMode
    // double-invoking this in dev), leaving that to GC timing risks hitting
    // the browser's concurrent-context limit and evicting the main
    // viewer's context. Force it immediately instead.
    renderer.forceContextLoss()
    renderer.dispose()
  }
}
