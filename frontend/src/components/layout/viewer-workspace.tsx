import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { RayTraceResult, ScenePayload } from '@/api'
import {
  BoxSelect,
  CircleDot,
  FileBox,
  GalleryHorizontalEnd,
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
import { buildLeakageSurfacePreview } from '@/features/results/leakage-surface-preview'
import type { ViewerCameraSnapshot } from '@/features/viewer/three-viewer-canvas'
import { resolveComponentColorHex } from '@/features/viewer/viewer-display'
import type {
  RayObjectEditRequest,
  ViewerCameraFrame,
} from '@/features/raytracing'
import { rayObjectDisplayName } from '@/features/raytracing/ray-tracing-model'
import {
  buildPrototypeLeakagePreviewData,
  prototypeLeakagePreviewUnavailableReason,
  RayTraceResultWindow,
} from '@/features/results'
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

export interface LeakagePreviewOpenRequest {
  caseId: string | null
  runId: string
  result: RayTraceResult
  initialCameraPreset?: ViewerCameraPreset
  contextDistanceScale?: number
}

interface ViewerWorkspaceProps {
  leakagePreviewRequest?: LeakagePreviewOpenRequest | null
  cadModelVisible?: boolean
  scene?: ScenePayload
  isSceneLoading?: boolean
  sceneErrorMessage?: string
  onRetryScene?(): void
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

interface LeakagePreviewSession {
  caseId: string
  runId: string
  result: RayTraceResult
}

function leakagePreviewReasonMessage(reason: string): string {
  switch (reason) {
    case 'result_source_context_missing':
    case 'result_source_request_missing':
      return '실행 당시 CAD와 해석 조건을 확인할 수 없는 결과입니다.'
    case 'roi_trace_not_supported_by_prototype_aabb':
      return 'ROI 해석 결과는 이번 외곽 경계 시제품에서 표시하지 않습니다.'
    case 'excluded_components_not_supported_by_prototype_aabb':
      return '해석 제외 부품이 있는 결과는 이번 시제품에서 표시하지 않습니다.'
    case 'stored_receiver_paths_not_available':
    case 'receiver_path_samples_missing':
      return '수광 결과는 있지만 3D 위치를 만들 저장 경로 표본이 부족합니다.'
    case 'scene_mesh_signature_mismatch':
      return '실행 당시 CAD 형상과 현재 형상이 일치하지 않습니다.'
    case 'scene_aabb_invalid':
      return '제품 외곽 경계를 계산할 수 없습니다.'
    default:
      return '이 결과는 현재 3D 빛샘 시제품에서 표시할 수 없습니다.'
  }
}

export function ViewerWorkspace({
  leakagePreviewRequest,
  cadModelVisible = true,
  scene,
  isSceneLoading = false,
  sceneErrorMessage,
  onRetryScene,
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
  const handledPreviewRequestRef = useRef('')
  const [leakageInitialView, setLeakageInitialView] = useState<{ id: string; preset: ViewerCameraPreset; distanceScale: number } | null>(null)
  const latestCameraSnapshotRef = useRef<ViewerCameraSnapshot | null>(null)
  const [leakageCameraRestore, setLeakageCameraRestore] = useState<{ id: string; snapshot: ViewerCameraSnapshot } | null>(null)
  const captureCameraSnapshot = useCallback((snapshot: ViewerCameraSnapshot) => {
    latestCameraSnapshotRef.current = snapshot
  }, [])
  const [renderMode, setRenderMode] =
    useState<ViewerRenderMode>('Surface + Edge')
  const [axisScalePercent, setAxisScalePercent] = useState(50)
  const [surfaceTransparencyPercent, setSurfaceTransparencyPercent] =
    useState(0)
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const [leakagePreviewSession, setLeakagePreviewSession] =
    useState<LeakagePreviewSession | null>(null)
  const [leakageExteriorBrightness, setLeakageExteriorBrightness] = useState(60)
  const [leakageExteriorColor, setLeakageExteriorColor] =
    useState<'black' | 'gray' | 'silver'>('black')
  const [leakageExteriorFinish, setLeakageExteriorFinish] =
    useState<'matte' | 'satin'>('matte')
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
  const componentColorOverrides = useWorkspaceStore(
    workspaceSelectors.componentColorOverrides,
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
  const leakagePreviewData = useMemo(() => {
    if (
      !leakagePreviewSession ||
      !scene ||
      activeCadCaseId !== leakagePreviewSession.caseId
    ) {
      return null
    }
    return buildPrototypeLeakagePreviewData(
      leakagePreviewSession.result,
      scene,
    )
  }, [activeCadCaseId, leakagePreviewSession, scene])
  const leakageSurfacePreview = useMemo(() => {
    if (!scene || !leakagePreviewData) return null
    return buildLeakageSurfacePreview(scene, leakagePreviewData,
      leakagePreviewSession?.result.source_context?.requests.at(-1))
  }, [scene, leakagePreviewData, leakagePreviewSession])
  const leakagePreviewHasContinuousField = (leakageSurfacePreview?.fields.length ?? 0) > 0

  useEffect(() => {
    if (!leakagePreviewSession) return
    const sourceCase = cadCases.find(
      (item) => item.caseId === leakagePreviewSession.caseId,
    )
    if (
      activeCadCaseId === leakagePreviewSession.caseId &&
      sourceCase?.latestResult?.run_id === leakagePreviewSession.runId
    ) {
      return
    }
    setLeakagePreviewSession(null)
    setStatusMessage(
      '3D 빛샘 보기를 종료했습니다. 해당 Case의 최신 Ray Tracing Result가 변경되었습니다.',
    )
  }, [activeCadCaseId, cadCases, leakagePreviewSession])

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
  const contextComponentIndex = contextComponent
    ? (scene?.components.indexOf(contextComponent) ?? -1)
    : -1
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
        `${rayObjectDisplayName(
          kind,
          id,
          kind === 'receiver' && 'display_name' in contextRayObject
            ? contextRayObject.display_name
            : undefined,
        )} · ${contextRayObject.enabled ? 'Disabled' : 'Enabled'}`,
      )
      return
    }
    if (kind === 'emitter') actions.removeEmitter(id)
    else actions.removeReceiver(id)
    setStatusMessage(
      `${rayObjectDisplayName(
        kind,
        id,
        kind === 'receiver' && 'display_name' in contextRayObject
          ? contextRayObject.display_name
          : undefined,
      )} 삭제`,
    )
  }

  const openLeakagePreview = useCallback(({
    caseId,
    runId,
    result,
    initialCameraPreset,
    contextDistanceScale,
  }: LeakagePreviewOpenRequest) => {
    const sourceContext = result.source_context
    const previewUnavailableReason =
      prototypeLeakagePreviewUnavailableReason(result)
    const resolvedCaseId = caseId ?? sourceContext?.cad_case_id ?? null
    const targetCase = resolvedCaseId
      ? cadCases.find((item) => item.caseId === resolvedCaseId)
      : null
    if (
      previewUnavailableReason ||
      !sourceContext ||
      !resolvedCaseId ||
      sourceContext.cad_case_id !== resolvedCaseId ||
      !targetCase ||
      targetCase.latestResult?.run_id !== runId
    ) {
      setStatusMessage(
        previewUnavailableReason
          ? `3D 빛샘 보기 · ${leakagePreviewReasonMessage(previewUnavailableReason)}`
          : '3D 빛샘 보기 · 실행 당시 CAD와 연결된 새 Ray Tracing Result가 필요합니다.',
      )
      return
    }

    const previousSnapshot = leakagePreviewSession && !initialCameraPreset ? latestCameraSnapshotRef.current : null
    setLeakageInitialView(initialCameraPreset ? { id: resolvedCaseId + ":" + runId, preset: initialCameraPreset, distanceScale: contextDistanceScale ?? 1 } : null)
    setLeakageCameraRestore(previousSnapshot
      ? { id: resolvedCaseId + ":" + runId, snapshot: structuredClone(previousSnapshot) } : null)
    actions.setCadCaseVisible(resolvedCaseId, true)
    setContextTarget(null)
    setRayObjectContextTarget(null)
    setLeakagePreviewSession({ caseId: resolvedCaseId, runId, result })
    if (!leakagePreviewSession || initialCameraPreset) {
      setCameraPreset(initialCameraPreset ?? 'XY')
      setCameraRequestId((requestId) => requestId + 1)
    }
    onRayTraceResultOpenChange?.(false)
    setStatusMessage(`3D 빛샘 보기 · ${runId}`)
    return true
  }, [actions, cadCases, leakagePreviewSession, onRayTraceResultOpenChange])

  useEffect(() => {
    if (!leakagePreviewRequest || !scene || isSceneLoading || sceneErrorMessage ||
      activeCadCaseId !== leakagePreviewRequest.caseId) return
    const key = leakagePreviewRequest.caseId + ":" + leakagePreviewRequest.runId
    if (handledPreviewRequestRef.current === key) return
    if (openLeakagePreview(leakagePreviewRequest)) handledPreviewRequestRef.current = key
  }, [activeCadCaseId, isSceneLoading, leakagePreviewRequest, openLeakagePreview, scene, sceneErrorMessage])
  return (
    <main
      data-viewer-workspace
      className="flex min-h-[42rem] min-w-0 flex-col bg-sim-viewer lg:min-h-0"
    >
      <div className="border-b border-border bg-background/65 px-3 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-sm font-semibold">
              {leakagePreviewSession ? '3D 빛샘 보기' : '3D Viewer'}
            </h1>
            <p className="text-xs text-muted-foreground">
              {leakagePreviewSession
                ? leakagePreviewData?.status === 'ready'
                  ? `${
                      leakagePreviewHasContinuousField
                        ? '틈 형상 기반 연속광 시제품'
                        : '출구 점 표본 시제품 · 연속 표시 기준 미충족'
                    } · 상대 표시 하한 적용 · 밝기·크기 미보정 · 외관 보조 조명 · ${leakagePreviewSession.runId}`
                  : leakagePreviewData
                    ? leakagePreviewReasonMessage(leakagePreviewData.reason)
                    : '실행 당시 CAD 형상을 불러오는 중입니다.'
                : 'Three.js Mesh · ROI, Emitter, Receiver and Ray Overlays · Step 11'}
            </p>
          </div>
          <div
            className="flex w-full min-w-0 flex-wrap items-center gap-2 min-[390px]:w-auto"
            data-viewer-toolbar
          >
            {leakagePreviewSession ? (
              <>
                <Badge className="border border-cyan-300/30 bg-cyan-400/10 text-cyan-200">
                  {leakagePreviewHasContinuousField ? '시제품 · 평면 틈 범위 확인' : '시제품 · 외곽 경계 추정'}
                </Badge>
                <label className="flex h-8 max-w-full min-w-0 items-center gap-2 rounded-lg border border-border bg-background/70 px-2 text-xs text-muted-foreground">
                  <span className="font-medium whitespace-nowrap">비교 결과</span>
                  <select
                    aria-label="3D 비교 결과"
                    value={leakagePreviewSession.caseId}
                    className="min-w-0 max-w-64 rounded bg-background px-1 py-0.5 text-foreground"
                    onChange={(event) => {
                      const target = cadCases.find((item) => item.caseId === event.currentTarget.value)
                      if (!target?.latestResult) return
                      openLeakagePreview({
                        caseId: target.caseId,
                        runId: target.latestResult.run_id,
                        result: target.latestResult,
                      })
                    }}
                  >
                    {cadCases.filter((item) => item.latestResult).map((item) => {
                      const emitters = item.latestResult!.emitters.filter((emitter) => emitter.enabled)
                      const power = emitters.length > 0 && emitters.every((emitter) =>
                        emitter.power_mode === 'total' && Number.isFinite(emitter.power_lumen))
                        ? emitters.reduce((sum, emitter) => sum + emitter.power_lumen, 0)
                        : null
                      return (
                        <option
                          key={item.caseId}
                          value={item.caseId}
                          disabled={item.latestResult?.source_context?.cad_case_id !== item.caseId ||
                            Boolean(prototypeLeakagePreviewUnavailableReason(item.latestResult!))}
                        >
                          {power === null ? '' : `광원 ${power.toLocaleString()} lm · `}
                          {item.name || item.cad.displayName}
                        </option>
                      )
                    })}
                  </select>
                </label>
                <label className="flex h-8 max-w-full min-w-0 items-center gap-2 rounded-lg border border-border bg-background/70 px-2 text-xs text-muted-foreground">
                  <span className="font-medium whitespace-nowrap">외관 밝기</span>
                  <input
                    aria-label="외관 밝기"
                    type="range"
                    min="0"
                    max="100"
                    step="5"
                    value={leakageExteriorBrightness}
                    className="h-1.5 w-20 cursor-pointer accent-primary"
                    onChange={(event) =>
                      setLeakageExteriorBrightness(Number(event.currentTarget.value))
                    }
                  />
                  <span className="w-8 text-right font-semibold text-foreground">
                    {leakageExteriorBrightness}%
                  </span>
                </label>
                <label className="flex h-8 items-center gap-2 rounded-lg border border-border bg-background/70 px-2 text-xs text-muted-foreground">
                  <span className="font-medium whitespace-nowrap">외관 색상</span>
                  <select
                    aria-label="외관 색상"
                    value={leakageExteriorColor}
                    className="min-w-0 rounded bg-background px-1 py-0.5 text-foreground"
                    onChange={(event) =>
                      setLeakageExteriorColor(
                        event.currentTarget.value as 'black' | 'gray' | 'silver',
                      )
                    }
                  >
                    <option value="black">블랙</option>
                    <option value="gray">그레이</option>
                    <option value="silver">실버</option>
                  </select>
                </label>
                <label className="flex h-8 items-center gap-2 rounded-lg border border-border bg-background/70 px-2 text-xs text-muted-foreground">
                  <span className="font-medium whitespace-nowrap">표면 재질</span>
                  <select
                    aria-label="표면 재질"
                    value={leakageExteriorFinish}
                    className="min-w-0 rounded bg-background px-1 py-0.5 text-foreground"
                    onChange={(event) =>
                      setLeakageExteriorFinish(
                        event.currentTarget.value as 'matte' | 'satin',
                      )
                    }
                  >
                    <option value="matte">무광</option>
                    <option value="satin">반광</option>
                  </select>
                </label>
                <label className="flex h-8 items-center gap-2 rounded-lg border border-border bg-background/70 px-2 text-xs text-muted-foreground">
                  <span className="font-medium whitespace-nowrap">관측 방향</span>
                  <select
                    aria-label="관측 방향"
                    value={cameraPreset === 'Fit' ? '' : cameraPreset}
                    className="min-w-0 rounded bg-background px-1 py-0.5 text-foreground"
                    onChange={(event) => {
                      setCameraPreset(event.currentTarget.value as ViewerCameraPreset)
                      setCameraRequestId((requestId) => requestId + 1)
                      setStatusMessage('3D 빛샘 보기 · 관측 방향 변경')
                    }}
                  >
                    <option value="" disabled>현재 방향</option>
                    <option value="XY">정면 (+Z)</option>
                    <option value="YZ">오른쪽 측면 (+X)</option>
                    <option value="-YZ">왼쪽 측면 (-X)</option>
                    <option value="-XY">뒷면 (-Z)</option>
                    <option value="ZX">위쪽 (+Y)</option>
                    <option value="-ZX">아래쪽 (-Y)</option>
                    <option value="Iso">사선</option>
                  </select>
                </label>
                <Button
                  size="xs"
                  variant="secondary"
                  onClick={() => {
                    setCameraPreset('Fit')
                    setCameraRequestId((requestId) => requestId + 1)
                    setStatusMessage('3D 빛샘 보기 · 빛샘 중심으로 맞춤')
                  }}
                >
                  <Maximize2 aria-hidden="true" />
                  빛샘 맞춤
                </Button>
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => {
                    setLeakagePreviewSession(null)
                    onRayTraceResultOpenChange?.(true)
                    setStatusMessage('Ray Tracing Analysis Result')
                  }}
                >
                  <GalleryHorizontalEnd aria-hidden="true" />
                  Result로 돌아가기
                </Button>
              </>
            ) : (
              <>
            <div
              className="grid w-full min-w-0 grid-cols-4 items-center gap-1 rounded-lg border border-blue-200 bg-blue-50/80 p-1 min-[390px]:flex min-[390px]:w-auto dark:border-blue-900/70 dark:bg-blue-950/30"
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
              className="flex max-w-full min-w-0 flex-wrap items-center gap-1 rounded-lg border border-blue-200 bg-blue-50/80 p-1 dark:border-blue-900/70 dark:bg-blue-950/30"
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
            <label className="flex h-8 max-w-full min-w-0 items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-2 text-xs text-muted-foreground dark:border-blue-900/70 dark:bg-blue-950/30">
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
              className="flex h-8 max-w-full min-w-0 items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-2 text-xs text-muted-foreground has-disabled:cursor-not-allowed has-disabled:opacity-45 dark:border-blue-900/70 dark:bg-blue-950/30"
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
              </>
            )}
          </div>
        </div>
        {leakagePreviewSession ? (
          <p className="mt-2 text-xs text-muted-foreground">
            외관 확인용 조명 · 빛샘 밝기에는 영향 없음
          </p>
        ) : null}
      </div>

      <div className="relative flex min-h-0 flex-1 p-3">
        <div
          className={`relative flex min-h-[30rem] w-full items-center justify-center overflow-hidden rounded-xl border lg:min-h-0 ${
            leakagePreviewSession
              ? 'border-slate-800 bg-black'
              : 'border-border bg-[radial-gradient(circle_at_center,var(--sim-panel-raised)_0,transparent_58%)]'
          }`}
        >
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
                  ? 'Emitter Surface · Selected'
                  : 'Emitter Surface · Click a Face'}
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
              <div className="mt-2 rounded-full border border-border bg-background/55 px-3 py-1 text-xs tabular-nums text-muted-foreground">
                {sceneLoadingElapsedSec < 60
                  ? `${sceneLoadingElapsedSec}s elapsed`
                  : `${Math.floor(sceneLoadingElapsedSec / 60)}m ${sceneLoadingElapsedSec % 60}s elapsed`}
              </div>
              {sceneLoadingElapsedSec >= 30 ? (
                <p className="mt-3 max-w-sm text-xs leading-5 text-muted-foreground">
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
              {onRetryScene ? (
                <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onRetryScene}>
                  다시 연결
                </Button>
              ) : null}
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
                왼쪽 Model Import에서 CAD를 선택하면 실제 Three.js Scene이
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
                      leakagePreviewSession ? 0 : surfaceTransparencyPercent
                    }
                    cameraPreset={cameraPreset}
                    cameraRequestId={cameraRequestId}
                    renderMode={leakagePreviewSession ? 'Surface' : renderMode}
                    roiBoxSelectionArmed={
                      leakagePreviewSession ? false : roiBoxSelectionArmed
                    }
                    roiFaceIds={
                      leakagePreviewSession ? [] : activeRoiFaceIds
                    }
                    roiScopes={leakagePreviewSession ? [] : roiScopes}
                    rayTraceResult={
                      leakagePreviewSession ? null : rayTraceResult
                    }
                    leakagePreviewActive={leakagePreviewSession !== null}
                    leakageCameraRestore={leakageCameraRestore}
                    leakageInitialView={leakageInitialView}
                    onCameraSnapshotChange={captureCameraSnapshot}
                    leakageSurfacePreview={leakageSurfacePreview}
                    leakageExteriorBrightness={leakageExteriorBrightness}
                    leakageExteriorColor={leakageExteriorColor}
                    leakageExteriorFinish={leakageExteriorFinish}
                    leakagePreviewBounds={
                      leakagePreviewData?.status === 'ready'
                        ? leakagePreviewData.bounds
                        : null
                    }
                    leakagePreviewCoverage={
                      leakagePreviewData?.status === 'ready'
                        ? leakagePreviewData.coverage
                        : null
                    }
                    leakagePreviewSamples={
                      leakagePreviewData?.status === 'ready'
                        ? leakagePreviewData.samples
                        : []
                    }
                    leakagePreviewRequest={
                      leakagePreviewSession?.result.source_context?.requests.at(
                        -1,
                      ) ?? null
                    }
                    editingComponentId={
                      leakagePreviewSession ? null : editingComponentId
                    }
                    editingComponentMode={
                      leakagePreviewSession ? null : editingComponentMode
                    }
                    onRoiBoxSelection={addBoxRoi}
                    onCameraFrameChange={onCameraFrameChange}
                    onCameraPresetChange={setCameraPreset}
                    onComponentContextMenu={
                      leakagePreviewSession
                        ? undefined
                        : (target) => {
                            setRayObjectContextTarget(null)
                            setContextTarget(target)
                          }
                    }
                    onRayObjectContextMenu={
                      leakagePreviewSession
                        ? undefined
                        : (target) => {
                            setContextTarget(null)
                            setRayObjectContextTarget(target)
                          }
                    }
                    onStatusMessage={setStatusMessage}
                  />
                </Suspense>
              </div>
              {leakagePreviewSession &&
              leakagePreviewData?.status === 'unsupported' ? (
                <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/85 px-6 text-center">
                  <div className="max-w-md rounded-xl border border-amber-300/25 bg-slate-950/95 p-5 shadow-2xl">
                    <div className="text-sm font-semibold text-amber-200">
                      3D 빛샘 표시 중지
                    </div>
                    <p className="mt-2 text-xs leading-5 text-slate-300">
                      {leakagePreviewReasonMessage(leakagePreviewData.reason)}
                    </p>
                    <p className="mt-2 text-[11px] leading-4 text-slate-500">
                      빈 화면을 빛샘 없음으로 오인하지 않도록 결과를 표시하지
                      않았습니다.
                    </p>
                  </div>
                </div>
              ) : null}
              {leakagePreviewSession &&
              leakagePreviewData?.status === 'ready' &&
              leakagePreviewData.samples.length === 0 ? (
                <div className="pointer-events-none absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full border border-slate-600 bg-black/75 px-3 py-1.5 text-[11px] text-slate-300">
                  표시할 빛샘 광량이 없습니다
                </div>
              ) : null}
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
                  colorOverride={
                    componentColorOverrides[
                      contextComponent.component_id
                    ] ?? null
                  }
                  fallbackColor={resolveComponentColorHex(
                    contextComponent,
                    contextComponentIndex,
                  )}
                  wheelTarget={contextTarget.returnFocusElement}
                  onOpenChange={(open) => {
                    if (!open) setContextTarget(null)
                  }}
                  onColorChange={(color) => {
                    actions.setComponentColor(
                      contextComponent.component_id,
                      color,
                    )
                    setStatusMessage(
                      `${getComponentDisplayName(
                        contextComponent,
                        componentNameOverrides,
                      )} · ${
                        color
                          ? `Display color ${color}`
                          : 'CAD original color'
                      }`,
                    )
                  }}
                  onAction={handleContextAction}
                />
              ) : null}
              {contextRayObject && rayObjectContextTarget ? (
                <ViewerRayObjectActionMenu
                  open
                  kind={rayObjectContextTarget.kind}
                  objectId={rayObjectContextTarget.id}
                  objectLabel={rayObjectDisplayName(
                    rayObjectContextTarget.kind,
                    rayObjectContextTarget.id,
                    rayObjectContextTarget.kind === 'receiver' &&
                      'display_name' in contextRayObject
                      ? contextRayObject.display_name
                      : undefined,
                  )}
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
            componentNameOverrides={componentNameOverrides}
            roiFaceIds={activeRoiFaceIds}
            reportCases={reportCases}
            onCaseMetadataChange={(caseId, name, note) =>
              actions.updateCadCaseMetadata(caseId, name, note)
            }
            onDeleteCaseReceiverResult={(caseId, receiverId) =>
              actions.removeCadCaseReceiverResult(caseId, receiverId)
            }
            onOpenLeakagePreview={openLeakagePreview}
            onOpenChange={(open) =>
              onRayTraceResultOpenChange?.(open)
            }
          />
        </div>
      </div>

      <footer className="flex min-h-9 items-center justify-between gap-3 border-t border-border bg-background/55 px-3 py-2 text-xs text-muted-foreground">
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
