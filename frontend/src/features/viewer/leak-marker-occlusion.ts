import { Camera, DoubleSide, Group, Mesh, MeshBasicMaterial, Raycaster, Scene, Vector2, Vector3, type WebGLRenderer } from 'three'

/** CAD display visibility and transparency must not reveal hidden leak markers.
 * Render shared physical geometry into depth only, then the marker layer.
 * Never change the materials, visibility, clipping or ownership of source CAD.
 */
export class LeakMarkerOcclusion {
  readonly markers = new Group()
  readonly blockers = new Group()
  private readonly scene = new Scene()
  private readonly depthMaterial = new MeshBasicMaterial({
    side: DoubleSide, colorWrite: false, depthTest: true, depthWrite: true,
  })
  private readonly proxies = new Map<Mesh, Mesh>()
  private readonly raycaster = new Raycaster()

  constructor() {
    this.scene.add(this.markers)
  }

  sync(sources: Mesh[]): void {
    const remaining = new Set(this.proxies.keys())
    for (const source of sources) {
      remaining.delete(source)
      let proxy = this.proxies.get(source)
      if (!proxy) {
        proxy = new Mesh(source.geometry, this.depthMaterial)
        proxy.matrixAutoUpdate = false
        this.proxies.set(source, proxy)
        this.scene.add(proxy)
      }
      // Hidden ancestors still own the physical transform. Refresh it explicitly.
      source.updateWorldMatrix(true, false)
      proxy.geometry = source.geometry
      proxy.matrix.copy(source.matrixWorld)
      proxy.matrixWorldNeedsUpdate = true
    }
    for (const source of remaining) {
      this.proxies.get(source)!.removeFromParent()
      this.proxies.delete(source)
    }
  }

  render(renderer: WebGLRenderer, camera: Camera): void {
    if (!this.markers.visible || this.markers.children.length === 0) return
    const autoClear = renderer.autoClear
    try {
      renderer.autoClear = false
      // Preserve the CAD colour buffer; only markers use the physical depth.
      renderer.clearDepth()
      renderer.render(this.scene, camera)
    } finally {
      renderer.autoClear = autoClear
    }
  }

  isPointVisible(point: Vector3, camera: Camera, pixel?: { x: number; y: number; width: number; height: number }): boolean {
    if (pixel) return this.pixelVisibilityTest(camera, pixel)(point)
    camera.updateWorldMatrix(true, false)
    const projected = point.clone().project(camera)
    // A fixed-size sprite may be visible through a slot at the clicked pixel
    // even when its centre is covered by a rim. Match rasterized sprite depth
    // at that pixel, rather than testing the centre for every halo click.
    const screen = new Vector2(projected.x, projected.y)
    this.raycaster.setFromCamera(screen, camera)
    this.raycaster.near = 0
    const forward = camera.getWorldDirection(new Vector3())
    const denominator = this.raycaster.ray.direction.dot(forward)
    if (denominator <= 0) return false
    const distance = point.clone().sub(this.raycaster.ray.origin).dot(forward) / denominator
    this.raycaster.far = Math.max(0, distance - 1e-4)
    this.scene.updateMatrixWorld(true)
    return this.raycaster.intersectObjects([...this.proxies.values()], false).length === 0
  }

  /** One lazy CAD intersection for a whole click, shared by overlapping dots. */
  pixelVisibilityTest(camera: Camera, pixel: { x: number; y: number; width: number; height: number }): (point: Vector3) => boolean {
    camera.updateWorldMatrix(true, false)
    const raycaster = new Raycaster()
    raycaster.setFromCamera(new Vector2(pixel.x / pixel.width * 2 - 1, 1 - pixel.y / pixel.height * 2), camera)
    const forward = camera.getWorldDirection(new Vector3())
    const denominator = raycaster.ray.direction.dot(forward)
    let nearest: number | undefined
    return point => {
      if (denominator <= 0) return false
      const distance = point.clone().sub(raycaster.ray.origin).dot(forward) / denominator
      if (distance <= 0) return false
      if (nearest === undefined) {
        this.scene.updateMatrixWorld(true)
        nearest = raycaster.intersectObjects([...this.proxies.values()], false)[0]?.distance ?? Infinity
      }
      return nearest >= distance - 1e-4
    }
  }

  dispose(): void {
    for (const proxy of this.proxies.values()) proxy.removeFromParent()
    this.proxies.clear()
    // Geometry belongs to the source CAD/Block and must not be disposed here.
    this.depthMaterial.dispose()
  }
}
