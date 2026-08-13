import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { RayTraceResult, ScenePayload } from '@/api'
import {
  BoxSelect,
  CircleDot,
  FileBox,
  LoaderCircle,
  Maximize2,
  Rotate3D,
} from 'lucide-react'

import {
  type ComponentContextAction,
  type RayObjectContextAction,
  ViewerComponentActionMenu,
  ViewerRayObjectActionMenu,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  getComponentDisplayName,
  type ComponentEditorRequest,
} from '@/features/components'
import type {
  RoiBoxSelectionResult,
  ViewerCameraPreset,
  ViewerComponentContextTarget,
  ViewerRayObjectContextTarget,
  ViewerRenderMode,
} from '@/features/viewer'
import type {
  RayObjectEditRequest,
  ViewerCameraFrame,
} from '@/features/raytracing'
import { RayTraceResultWindow } from '@/features/results'
import {
  getActiveRoiFaceIds,
  groupRoiFacesByComponent,
  resolveFacesInRoiBox,
} from '@/features/roi'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

const cameraPresets: ViewerCameraPreset[] = [
  'Fit',
  'Iso',
  'XY',
  '-XY',
  'YZ',
  '-YZ',
  'ZX',
  '-ZX',
]
// While ROI box-drag is armed, orbit is locked to keep drag = draw-box,
// but switching between the axis views still needs to work (matches the
// six RoiCameraPreset planes). Fit/Iso aren't valid ROI projection planes,
// so they stay disabled to avoid drawing a box against an undefined axis.
const roiArmedUsablePresets = new Set<ViewerCameraPreset>([
  'XY',
  '-XY',
  'YZ',
  '-YZ',
  'ZX',
  '-ZX',
])
const renderModes: ViewerRenderMode[] = [
  'Wireframe',
  'Surface',
  'Surface + Edge',
]

const ThreeViewerCanvas = lazy(() =>
  import('@/features/viewer').then((module) => ({
    default: module.ThreeViewerCanvas,
  })),
)

interface ViewerWorkspaceProps {
  cadModelVisible?: boolean
  scene?: ScenePayload
  isSceneLoading?: boolean
  sceneErrorMessage?: string
  onCameraFrameChange?(frame: ViewerCameraFrame): void
  rayTraceResult?: RayTraceResult | null
  rayTraceResultOpen?: boolean
  onRayTraceResultOpenChange?(open: boolean): void
  editingComponentId?: number | null
  editingComponentMode?: 'material' | 'transform' | null
  onEditMaterial?(request: ComponentEditorRequest): void
  onEditTransform?(request: ComponentEditorRequest): void
  onDeleteComponent?(request: ComponentEditorRequest): void
  onEditRayObject?(request: RayObjectEditRequest): void
}

export function ViewerWorkspace({
  cadModelVisible = true,
  scene,
  isSceneLoading = false,
  sceneErrorMessage,
  onCameraFrameChange,
  rayTraceResult,
  rayTraceResultOpen = false,
  onRayTraceResultOpenChange,
  editingComponentId,
  editingComponentMode,
  onEditMaterial,
  onEditTransform,
  onDeleteComponent,
  onEditRayObject,
}: ViewerWorkspaceProps) {
  const [cameraPreset, setCameraPreset] =
    useState<ViewerCameraPreset>('Iso')
  const [cameraRequestId, setCameraRequestId] = useState(0)
  const [renderMode, setRenderMode] =
    useState<ViewerRenderMode>('Surface + Edge')
  const [axisScalePercent, setAxisScalePercent] = useState(50)
  const [surfaceTransparencyPercent, setSurfaceTransparencyPercent] =
    useState(0)
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const reportCases = useMemo(
    () =>
      cadCases.flatMap((item) =>
        item.latestResult
          ? [
              {
                caseId: item.caseId,
                name:
                  item.name || `CASE ${String(item.order).padStart(2, '0')}`,
                cadName: item.cad.displayName,
                result: item.latestResult,
                note: item.note,
              },
            ]
          : [],
      ),
    [cadCases],
  )
  const [sceneLoadingElapsedSec, setSceneLoadingElapsedSec] =
    useState(0)
  const [statusMessage, setStatusMessage] = useState(
    'CAD를 Import하면 Three.js Viewer에서 component와 face를 선택할 수 있습니다.',
  )
  const [contextTarget, setContextTarget] =
    useState<ViewerComponentContextTarget | null>(null)
  const [rayObjectContextTarget, setRayObjectContextTarget] =
    useState<ViewerRayObjectContextTarget | null>(null)
  const selectedComponentIds = useWorkspaceStore(
    workspaceSelectors.selectedComponentIds,
  )
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const excludedComponentIds = useWorkspaceStore(
    workspaceSelectors.excludedComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const componentNameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const emitters = useWorkspaceStore(workspaceSelectors.emitters)
  const receivers = useWorkspaceStore(workspaceSelectors.receivers)
  const roiBoxSelectionArmed = useWorkspaceStore(
    workspaceSelectors.roiBoxSelectionArmed,
  )
  const emitterFaceSelectionArmed = useWorkspaceStore(
    workspaceSelectors.emitterFaceSelectionArmed,
  )
  const roiDraftLabel = useWorkspaceStore(
    workspaceSelectors.roiDraftLabel,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  useEffect(() => {
    if (!isSceneLoading) {
      setSceneLoadingElapsedSec(0)
      return
    }

    const startedAt = Date.now()
    setSceneLoadingElapsedSec(0)
    const timer = window.setInterval(() => {
      setSceneLoadingElapsedSec(
        Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
      )
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isSceneLoading])
  const activeRoiFaceIds = useMemo(
    () => getActiveRoiFaceIds(roiScopes, deletedComponentIds),
    [deletedComponentIds, roiScopes],
  )
  const addBoxRoi = useCallback(
    ({ clipBox, view }: RoiBoxSelectionResult) => {
      if (!scene) return

      const faceIds = resolveFacesInRoiBox(
        scene,
        clipBox,
        hiddenComponentIds,
        deletedComponentIds,
      )
      actions.setRoiBoxSelectionArmed(false)
      if (faceIds.length === 0) {
        setStatusMessage(
          'ROI 선택 결과가 없습니다. 박스 위치와 component 표시 상태를 확인하세요.',
        )
        return
      }

      const components = groupRoiFacesByComponent(
        scene,
        faceIds,
        componentNameOverrides,
      )
      actions.addRoiScope({
        label: roiDraftLabel,
        source: 'box',
        view,
        components,
        clipBox,
      })
      setStatusMessage(`ROI 추가 · ${components.length} components`)
    },
    [
      actions,
      componentNameOverrides,
      deletedComponentIds,
      hiddenComponentIds,
      roiDraftLabel,
      scene,
    ],
  )

  const components = (scene?.components ?? []).filter(
    (component) =>
      !deletedComponentIds.includes(component.component_id),
  )
  const visibleComponentCount = components.filter(
    (component) =>
      !hiddenComponentIds.includes(component.component_id),
  ).length
  const contextComponent = components.find(
    (component) =>
      component.component_id === contextTarget?.componentId,
  )
  const contextComponentId = contextComponent?.component_id
  const contextRayObject =
    rayObjectContextTarget?.kind === 'emitter'
      ? emitters.find(
          (emitter) =>
            emitter.emitter_id === rayObjectContextTarget.id,
        )
      : rayObjectContextTarget?.kind === 'receiver'
        ? receivers.find(
            (receiver) =>
              receiver.receiver_id === rayObjectContextTarget.id,
          )
        : null
  const editingComponent = components.find(
    (component) => component.component_id === editingComponentId,
  )
  const editingComponentName = editingComponent
    ? getComponentDisplayName(
        editingComponent,
        componentNameOverrides,
      )
    : ''
  const handleContextAction = (action: ComponentContextAction) => {
    if (contextComponentId === undefined) return

    if (action === 'visibility') {
      actions.toggleComponentVisibility(contextComponentId)
      setStatusMessage(
        hiddenComponentIds.includes(contextComponentId)
          ? `Component ${contextComponentId} 표시`
          : `Component ${contextComponentId} 숨김`,
      )
      return
    }
    if (action === 'traceability') {
      actions.toggleComponentTraceability(contextComponentId)
      setStatusMessage(
        excludedComponentIds.includes(contextComponentId)
          ? `Component ${contextComponentId} · Traceability On`
          : `Component ${contextComponentId} · Traceability Off`,
      )
      return
    }

    const request = {
      componentId: contextComponentId,
      returnFocusElement: contextTarget?.returnFocusElement ?? null,
    }
    actions.setSelectedComponentIds([contextComponentId])
    actions.setSelectedFaceIds([])
    if (action === 'material') onEditMaterial?.(request)
    else if (action === 'transform') onEditTransform?.(request)
    else onDeleteComponent?.(request)
  }
  const handleRayObjectContextAction = (
    action: RayObjectContextAction,
  ) => {
    if (!rayObjectContextTarget || !contextRayObject) return
    const { id, kind } = rayObjectContextTarget
    if (action === 'edit') {
      onEditRayObject?.({ id, kind })
      return
    }
    if (action === 'enabled') {
      if (kind === 'emitter') {
        actions.setEmitterEnabled(id, !contextRayObject.enabled)
      } else {
        actions.setReceiverEnabled(id, !contextRayObject.enabled)
      }
      setStatusMessage(
        `${kind === 'emitter' ? 'Emitter' : 'Receiver'} ${id} · ${contextRayObject.enabled ? 'Disabled' : 'Enabled'}`,
      )
      return
    }
    if (kind === 'emitter') actions.removeEmitter(id)
    else actions.removeReceiver(id)
    setStatusMessage(
      `${kind === 'emitter' ? 'Emitter' : 'Receiver'} ${id} 삭제`,
    )
  }

  return (
    <main
      data-viewer-workspace
      className="flex min-h-[42rem] min-w-0 flex-col bg-sim-viewer lg:min-h-0"
    >
      <div className="border-b border-border bg-background/65 px-3 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-sm font-semibold">3D Viewer</h1>
            <p className="text-[0.7rem] text-muted-foreground">
              Three.js mesh · ROI, Emitter, Receiver and ray overlays · Step 11
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div
              className="flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50/80 p-1 dark:border-blue-900/70 dark:bg-blue-950/30"
              aria-label="Camera presets"
            >
              {cameraPresets.map((preset) => (
                <Button
                  key={preset}
                  size="xs"
                  variant={
                    cameraPreset === preset ? 'secondary' : 'ghost'
                  }
                  disabled={
                    roiBoxSelectionArmed &&
                    !roiArmedUsablePresets.has(preset)
                  }
                  aria-pressed={cameraPreset === preset}
                  title={preset === 'Fit' ? 'Fit view (F)' : preset}
                  onClick={() => {
                    setCameraPreset(preset)
                    setCameraRequestId((requestId) => requestId + 1)
                    setStatusMessage(`Camera preset · ${preset}`)
                  }}
                >
                  {preset === 'Fit' ? (
                    <Maximize2 aria-hidden="true" />
                  ) : null}
                  {preset}
                </Button>
              ))}
            </div>
            <div
              className="flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50/80 p-1 dark:border-blue-900/70 dark:bg-blue-950/30"
              aria-label="Render modes"
            >
              {renderModes.map((mode) => (
                <Button
                  key={mode}
                  size="xs"
                  variant={renderMode === mode ? 'secondary' : 'ghost'}
                  aria-pressed={renderMode === mode}
                  onClick={() => {
                    setRenderMode(mode)
                    setStatusMessage(`Render mode · ${mode}`)
                  }}
                >
                  {mode}
                </Button>
              ))}
            </div>
            <label className="flex h-8 items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-2 text-[0.65rem] text-muted-foreground dark:border-blue-900/70 dark:bg-blue-950/30">
              <span className="font-medium whitespace-nowrap">
                Axis size
              </span>
              <input
                aria-label="Axis size"
                type="range"
                min="50"
                max="100"
                step="5"
                value={axisScalePercent}
                className="h-1.5 w-20 cursor-pointer accent-primary"
                onChange={(event) => {
                  const nextScale = Number(event.currentTarget.value)
                  setAxisScalePercent(nextScale)
                  setStatusMessage(`Axis size · ${nextScale}%`)
                }}
              />
              <span className="w-8 text-right font-semibold text-foreground">
                {axisScalePercent}%
              </span>
            </label>
            <label
              className="flex h-8 items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-2 text-[0.65rem] text-muted-foreground has-disabled:cursor-not-allowed has-disabled:opacity-45 dark:border-blue-900/70 dark:bg-blue-950/30"
              title={
                renderMode === 'Wireframe'
                  ? 'Surface 또는 Surface + Edge 모드에서 사용할 수 있습니다.'
                  : 'CAD 표면을 투명하게 하여 내부 형상을 확인합니다.'
              }
            >
              <span className="font-medium whitespace-nowrap">
                Transparency
              </span>
              <input
                aria-label="Surface transparency"
                type="range"
                min="0"
                max="85"
                step="5"
                value={surfaceTransparencyPercent}
                disabled={renderMode === 'Wireframe'}
                className="h-1.5 w-20 cursor-pointer accent-primary disabled:cursor-not-allowed"
                onChange={(event) => {
                  const nextTransparency = Number(
                    event.currentTarget.value,
                  )
                  setSurfaceTransparencyPercent(nextTransparency)
                  setStatusMessage(
                    `Surface transparency · ${nextTransparency}%`,
                  )
                }}
              />
              <span className="w-8 text-right font-semibold text-foreground">
                {surfaceTransparencyPercent}%
              </span>
            </label>
          </div>
        </div>
      </div>

      <div className="relative flex min-h-0 flex-1 p-3">
        <div className="relative flex min-h-[30rem] w-full items-center justify-center overflow-hidden rounded-xl border border-border bg-[radial-gradient(circle_at_center,var(--sim-panel-raised)_0,transparent_58%)] lg:min-h-0">
          <div className="pointer-events-none absolute top-3 left-3 z-10 flex items-center gap-2">
            <Badge
              variant="outline"
              className="border-border bg-background/70 text-muted-foreground backdrop-blur"
            >
              <Rotate3D data-icon="inline-start" />
              {cameraPreset}
            </Badge>
            <Badge
              variant="outline"
              className="border-border bg-background/70 text-muted-foreground backdrop-blur"
            >
              {renderMode}
            </Badge>
            {!emitterFaceSelectionArmed &&
            !editingComponent &&
            selectedComponentIds.length > 0 ? (
              <Badge className="border border-amber-400/60 bg-amber-400/20 text-amber-200">
                Component ·{' '}
                {selectedComponentIds
                  .map((componentId) => {
                    const component = components.find(
                      (candidate) =>
                        candidate.component_id === componentId,
                    )
                    return component
                      ? getComponentDisplayName(
                          component,
                          componentNameOverrides,
                        )
                      : componentId
                  })
                  .join(', ')}
              </Badge>
            ) : null}
            {emitterFaceSelectionArmed ? (
              <Badge className="border border-blue-400/50 bg-blue-400/20 text-blue-300">
                {selectedFaceIds.length > 0
                  ? 'Emitter surface · selected'
                  : 'Emitter surface · click a face'}
              </Badge>
            ) : selectedFaceIds.length > 0 ? (
              <Badge className="border border-blue-400/50 bg-blue-400/20 text-blue-300">
                Face selected
              </Badge>
            ) : editingComponent && editingComponentMode ? (
              <Badge className="border border-amber-400/60 bg-amber-400/20 text-amber-200">
                {editingComponentMode === 'transform'
                  ? 'Transform target'
                  : 'Material target'}{' '}
                · {editingComponentName}
              </Badge>
            ) : null}
            {activeRoiFaceIds.length > 0 ? (
              <Badge className="bg-warning/15 text-warning">ROI</Badge>
            ) : null}
          </div>

          {isSceneLoading ? (
            <div className="relative z-10 flex flex-col items-center text-center">
              <LoaderCircle className="size-8 animate-spin text-primary" />
              <div className="mt-3 text-sm font-semibold">
                Loading CAD scene
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Tessellation과 component metadata를 읽는 중입니다.
              </div>
              <div className="mt-2 rounded-full border border-border bg-background/55 px-3 py-1 text-[0.68rem] tabular-nums text-muted-foreground">
                {sceneLoadingElapsedSec < 60
                  ? `${sceneLoadingElapsedSec}s elapsed`
                  : `${Math.floor(sceneLoadingElapsedSec / 60)}m ${sceneLoadingElapsedSec % 60}s elapsed`}
              </div>
              {sceneLoadingElapsedSec >= 30 ? (
                <p className="mt-3 max-w-sm text-[0.68rem] leading-5 text-muted-foreground">
                  회사 PC에서 오래 멈추면 서버 창의 마지막
                  {' [CAD] '}단계를 확인해 주세요. 동일 CAD의 중복 요청은
                  자동으로 하나로 합쳐 처리합니다.
                </p>
              ) : null}
            </div>
          ) : sceneErrorMessage ? (
            <div className="relative z-10 max-w-md rounded-xl border border-destructive/35 bg-destructive/8 p-4 text-center">
              <div className="text-sm font-semibold text-destructive">
                Scene load failed
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {sceneErrorMessage}
              </p>
            </div>
          ) : !scene ? (
            <div className="relative z-10 flex max-w-sm flex-col items-center px-6 text-center">
              <span className="flex size-14 items-center justify-center rounded-2xl border border-border bg-background/50 text-muted-foreground">
                <FileBox className="size-7" />
              </span>
              <div className="mt-4 text-sm font-semibold">
                Empty workspace
              </div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                왼쪽 Model import에서 CAD를 선택하면 실제 Three.js scene이
                생성됩니다.
              </p>
            </div>
          ) : components.length === 0 ? (
            <div className="relative z-10 flex max-w-sm flex-col items-center px-6 text-center">
              <BoxSelect className="size-8 text-muted-foreground" />
              <div className="mt-3 text-sm font-semibold">
                No active components
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                삭제 상태를 복원하려면 CAD를 다시 Import하세요.
              </p>
            </div>
          ) : (
            <>
              <div className="absolute inset-0 rounded-[inherit]">
                <Suspense
                  fallback={
                    <div className="relative z-10 flex h-full flex-col items-center justify-center text-center">
                      <LoaderCircle className="size-8 animate-spin text-primary" />
                      <div className="mt-3 text-sm font-semibold">
                        Starting Three.js Viewer
                      </div>
                    </div>
                  }
                >
                  <ThreeViewerCanvas
                    scene={scene}
                    cadModelVisible={cadModelVisible}
                    axisScalePercent={axisScalePercent}
                    surfaceTransparencyPercent={
                      surfaceTransparencyPercent
                    }
                    cameraPreset={cameraPreset}
                    cameraRequestId={cameraRequestId}
                    renderMode={renderMode}
                    roiBoxSelectionArmed={roiBoxSelectionArmed}
                    roiFaceIds={activeRoiFaceIds}
                    roiScopes={roiScopes}
                    rayTraceResult={rayTraceResult}
                    editingComponentId={editingComponentId}
                    editingComponentMode={editingComponentMode}
                    onRoiBoxSelection={addBoxRoi}
                    onCameraFrameChange={onCameraFrameChange}
                    onCameraPresetChange={setCameraPreset}
                    onComponentContextMenu={(target) => {
                      setRayObjectContextTarget(null)
                      setContextTarget(target)
                    }}
                    onRayObjectContextMenu={(target) => {
                      setContextTarget(null)
                      setRayObjectContextTarget(target)
                    }}
                    onStatusMessage={setStatusMessage}
                  />
                </Suspense>
              </div>
              {contextComponent && contextTarget ? (
                <ViewerComponentActionMenu
                  open
                  componentName={getComponentDisplayName(
                    contextComponent,
                    componentNameOverrides,
                  )}
                  position={{
                    x: contextTarget.clientX,
                    y: contextTarget.clientY,
                  }}
                  visible={
                    !hiddenComponentIds.includes(
                      contextComponent.component_id,
                    )
                  }
                  traceable={
                    !excludedComponentIds.includes(
                      contextComponent.component_id,
                    )
                  }
                  wheelTarget={contextTarget.returnFocusElement}
                  onOpenChange={(open) => {
                    if (!open) setContextTarget(null)
                  }}
                  onAction={handleContextAction}
                />
              ) : null}
              {contextRayObject && rayObjectContextTarget ? (
                <ViewerRayObjectActionMenu
                  open
                  kind={rayObjectContextTarget.kind}
                  objectId={rayObjectContextTarget.id}
                  position={{
                    x: rayObjectContextTarget.clientX,
                    y: rayObjectContextTarget.clientY,
                  }}
                  enabled={contextRayObject.enabled}
                  wheelTarget={
                    rayObjectContextTarget.returnFocusElement
                  }
                  onOpenChange={(open) => {
                    if (!open) setRayObjectContextTarget(null)
                  }}
                  onAction={handleRayObjectContextAction}
                />
              ) : null}
            </>
          )}
          <RayTraceResultWindow
            open={rayTraceResultOpen}
            result={rayTraceResult ?? null}
            scene={scene}
            roiFaceIds={activeRoiFaceIds}
            reportCases={reportCases}
            onCaseMetadataChange={(caseId, name, note) =>
              actions.updateCadCaseMetadata(caseId, name, note)
            }
            onOpenChange={(open) =>
              onRayTraceResultOpenChange?.(open)
            }
          />
        </div>
      </div>

      <footer className="flex min-h-9 items-center justify-between gap-3 border-t border-border bg-background/55 px-3 py-2 text-[0.68rem] text-muted-foreground">
        <span className="truncate">{statusMessage}</span>
        <span className="hidden shrink-0 items-center gap-1 sm:flex">
          <CircleDot className="size-3 text-primary" />
          {scene
            ? `${visibleComponentCount} visible · ${selectedComponentIds.length} component`
            : 'Three.js Viewer · Step 11'}
        </span>
      </footer>
    </main>
  )
}
