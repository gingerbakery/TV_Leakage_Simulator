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

  isPointVisible(point: Vector3, camera: Camera): boolean {
    camera.updateWorldMatrix(true, false)
    const projected = point.clone().project(camera)
    this.raycaster.setFromCamera(new Vector2(projected.x, projected.y), camera)
    this.raycaster.near = 0
    this.raycaster.far = Math.max(0, this.raycaster.ray.origin.distanceTo(point) - 1e-4)
    this.scene.updateMatrixWorld(true)
    return this.raycaster.intersectObjects([...this.proxies.values()], false).length === 0
  }

  dispose(): void {
    for (const proxy of this.proxies.values()) proxy.removeFromParent()
    this.proxies.clear()
    // Geometry belongs to the source CAD/Block and must not be disposed here.
    this.depthMaterial.dispose()
  }
}
