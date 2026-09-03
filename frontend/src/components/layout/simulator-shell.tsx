import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {
  apiClient,
  useRayTraceJobQuery,
  useSceneQuery,
  type RayTraceResult,
} from '@/api'
import {
  Box,
  BookOpen,
  CircleDot,
  Copy,
  FolderOpen,
  Moon,
  Save,
  Sun,
} from 'lucide-react'

import { AppDialog, ConfirmationDialog } from '@/components/common'
import { ViewerWorkspace } from '@/components/layout/viewer-workspace'
import {
  WorkflowSidebar,
  type WorkflowSectionId,
} from '@/components/layout/workflow-sidebar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ComponentEditorRequest } from '@/features/components'
import { getComponentDisplayName } from '@/features/components'
import { MaterialEditorDialog } from '@/features/materials'
import {
  BitsamProjectError,
  compareBitsamProjectScene,
  createBitsamSettingsOnlyState,
  createBitsamProject,
  saveBitsamProject,
  readBitsamProjectFile,
  type BitsamProject,
} from '@/features/projects'
import {
  matchSetupComponents,
  sceneComponentMatchMetadata,
} from '@/features/projects/copy-analysis-setup'
import type {
  RayObjectEditRequest,
  ViewerCameraFrame,
} from '@/features/raytracing'
import { TransformEditorDialog } from '@/features/transforms'
import {
  groupRoiFacesByComponent,
  resolveFacesInRoiBox,
  resolveNearestVisibleFace,
} from '@/features/roi'
import {
  mergeRayTraceReceiverResults,
  useWorkspaceStore,
  workspaceSelectors,
  workspaceStore,
  type CopySetupTarget,
} from '@/stores'

type ComponentDialogType = 'material' | 'transform' | 'delete'

const WORKFLOW_SIDEBAR_STORAGE_KEY = 'tv-leakage-workflow-sidebar-width'
const WORKFLOW_SIDEBAR_MIN_WIDTH = 320
const WORKFLOW_SIDEBAR_DEFAULT_WIDTH = 384
const WORKFLOW_SIDEBAR_WIDE_WIDTH = 464
const WORKFLOW_SIDEBAR_MAX_WIDTH = 560

function workflowSidebarMaxWidth(viewportWidth = window.innerWidth) {
  if (viewportWidth < 1024) return WORKFLOW_SIDEBAR_MAX_WIDTH
  return Math.max(
    WORKFLOW_SIDEBAR_MIN_WIDTH,
    Math.min(
      WORKFLOW_SIDEBAR_MAX_WIDTH,
      Math.floor(viewportWidth * 0.46),
    ),
  )
}

function clampWorkflowSidebarWidth(
  width: number,
  viewportWidth = window.innerWidth,
) {
  return Math.round(
    Math.min(
      Math.max(width, WORKFLOW_SIDEBAR_MIN_WIDTH),
      workflowSidebarMaxWidth(viewportWidth),
    ),
  )
}

export function SimulatorShell() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    window.localStorage.getItem('tv-leakage-theme') === 'dark'
      ? 'dark'
      : 'light',
  )
  const [activeSection, setActiveSection] =
    useState<WorkflowSectionId>('model-import')
  const [workflowSidebarWidth, setWorkflowSidebarWidth] = useState(() => {
    const storedWidth = Number(
      window.localStorage.getItem(WORKFLOW_SIDEBAR_STORAGE_KEY),
    )
    return Number.isFinite(storedWidth) && storedWidth > 0
      ? clampWorkflowSidebarWidth(storedWidth)
      : clampWorkflowSidebarWidth(WORKFLOW_SIDEBAR_DEFAULT_WIDTH)
  })
  const [isWorkflowSidebarResizing, setIsWorkflowSidebarResizing] =
    useState(false)
  const workflowSidebarResizeStartRef = useRef<{
    pointerId: number
    clientX: number
    width: number
  } | null>(null)
  const [viewerCameraFrame, setViewerCameraFrame] =
    useState<ViewerCameraFrame | null>(null)
  const [rayTraceResultOpen, setRayTraceResultOpen] = useState(false)
  const [autoConvergenceCancelToken, setAutoConvergenceCancelToken] =
    useState(0)
  const [displayedRayTraceResult, setDisplayedRayTraceResult] =
    useState<RayTraceResult | null>(null)
  const [rayObjectEditRequest, setRayObjectEditRequest] =
    useState<RayObjectEditRequest | null>(null)
  const [componentDialog, setComponentDialog] = useState<{
    type: ComponentDialogType
    componentId: number
  } | null>(null)
  const [noticeDialog, setNoticeDialog] = useState<{
    title: string
    description: string
    action?: 'select-gpu'
  } | null>(null)
  const [copySetupOpen, setCopySetupOpen] = useState(false)
  const [copySetupTargetIds, setCopySetupTargetIds] = useState<string[]>([])
  const [copySetupPending, setCopySetupPending] = useState(false)
  const [copySetupProgress, setCopySetupProgress] = useState('')
  const [pendingProject, setPendingProject] =
    useState<BitsamProject | null>(null)
  const noticeReturnFocusRef = useRef<HTMLElement>(null)
  const componentReturnFocusRef = useRef<HTMLElement>(null)
  const projectFileInputRef = useRef<HTMLInputElement>(null)
  const projectLoadAttemptRef = useRef('')
  const copySetupAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
    window.localStorage.setItem('tv-leakage-theme', theme)
  }, [theme])

  useEffect(() => {
    window.localStorage.setItem(
      WORKFLOW_SIDEBAR_STORAGE_KEY,
      String(workflowSidebarWidth),
    )
  }, [workflowSidebarWidth])

  useEffect(() => {
    const clampToViewport = () =>
      setWorkflowSidebarWidth((current) =>
        clampWorkflowSidebarWidth(current),
      )

    window.addEventListener('resize', clampToViewport)
    return () => window.removeEventListener('resize', clampToViewport)
  }, [])
  const lastOpenedResultRunIdRef = useRef('')
  const previousActiveCaseIdRef = useRef<string | null>(null)
  const suppressedCaseJobIdRef = useRef<string | null>(null)
  const suppressedCaseRunIdRef = useRef<string | null>(null)
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const activeCadCaseVisible =
    cadCases.find((item) => item.caseId === activeCadCaseId)?.visible ?? true
  const nameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const activeRayTraceJobId = useWorkspaceStore(
    workspaceSelectors.activeRayTraceJobId,
  )
  const emitters = useWorkspaceStore(workspaceSelectors.emitters)
  const receivers = useWorkspaceStore(workspaceSelectors.receivers)
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const rayTraceConfig = useWorkspaceStore(workspaceSelectors.rayTraceConfig)
  const restoredRayTraceResult = useWorkspaceStore(
    workspaceSelectors.restoredRayTraceResult,
  )
  const sceneQuery = useSceneQuery(activeCad?.path ?? '')
  const rayTraceJobQuery = useRayTraceJobQuery(activeRayTraceJobId)
  const rayTraceJob = rayTraceJobQuery.data
  const rawRayTraceResult =
    rayTraceJob?.status === 'completed'
      ? rayTraceJob.result
      : restoredRayTraceResult
  const savedActiveCaseResult = cadCases.find(
    (item) => item.caseId === activeCadCaseId,
  )?.latestResult
  const rayTraceResult = useMemo(() => {
    if (!rawRayTraceResult) return rawRayTraceResult
    if (savedActiveCaseResult?.run_id === rawRayTraceResult.run_id) {
      return savedActiveCaseResult
    }
    return mergeRayTraceReceiverResults(
      savedActiveCaseResult,
      rawRayTraceResult,
      receivers,
    )
  }, [rawRayTraceResult, receivers, savedActiveCaseResult])
  const scene = sceneQuery.data
  const sceneErrorMessage = sceneQuery.error?.message

  useEffect(() => {
    if (!scene || !activeCadCaseId) return
    actions.setCadCaseComponentMatchMetadata(
      activeCadCaseId,
      sceneComponentMatchMetadata(scene),
    )
  }, [actions, activeCadCaseId, scene])

  const activeComponent =
    scene?.components.find(
      (component) =>
        component.component_id === componentDialog?.componentId,
    ) ?? null
  const activeComponentName = activeComponent
    ? getComponentDisplayName(activeComponent, nameOverrides)
    : ''

  useEffect(() => {
    const previousCaseId = previousActiveCaseIdRef.current
    previousActiveCaseIdRef.current = activeCadCaseId
    if (previousCaseId === null || previousCaseId === activeCadCaseId) return
    const targetCase = cadCases.find(
      (item) => item.caseId === activeCadCaseId,
    )
    suppressedCaseJobIdRef.current = targetCase?.latestJobId ?? null
    suppressedCaseRunIdRef.current = targetCase?.latestResult?.run_id ?? null
    setRayTraceResultOpen(false)
  }, [activeCadCaseId, cadCases])

  useEffect(() => {
    if (!scene || !roiScopes.some((scope) => scope.components.length === 0)) {
      return
    }
    let changed = false
    const remapped = roiScopes.map((scope) => {
      if (scope.components.length > 0) return scope
      const faceIds = scope.clipBox
        ? resolveFacesInRoiBox(scene, scope.clipBox, [])
        : scope.point
          ? [resolveNearestVisibleFace(scene, scope.point, [])].filter(
              (faceId): faceId is number => faceId !== null,
            )
          : []
      const components = groupRoiFacesByComponent(
        scene,
        faceIds,
        nameOverrides,
      )
      if (components.length > 0) changed = true
      return {
        ...scope,
        components,
      }
    })
    if (changed) actions.setRoiScopes(remapped)
  }, [actions, nameOverrides, roiScopes, scene])

  useEffect(() => {
    const savedCaseResult = cadCases.find(
      (item) => item.caseId === activeCadCaseId,
    )?.latestResult
    setDisplayedRayTraceResult(
      savedCaseResult ?? restoredRayTraceResult ?? null,
    )
  }, [activeCadCaseId, cadCases, restoredRayTraceResult])

  useEffect(() => {
    if (!rayTraceResult) {
      return
    }
    setDisplayedRayTraceResult(rayTraceResult)
    if (savedActiveCaseResult?.run_id !== rayTraceResult.run_id) {
      actions.setActiveCadCaseResult(rayTraceResult)
    }
    const restoredByCaseSwitch =
      (activeRayTraceJobId !== null &&
        activeRayTraceJobId === suppressedCaseJobIdRef.current) ||
      rayTraceResult.run_id === suppressedCaseRunIdRef.current
    if (restoredByCaseSwitch) {
      suppressedCaseJobIdRef.current = null
      suppressedCaseRunIdRef.current = null
      lastOpenedResultRunIdRef.current = rayTraceResult.run_id
      return
    }
    if (lastOpenedResultRunIdRef.current === rayTraceResult.run_id) return
    lastOpenedResultRunIdRef.current = rayTraceResult.run_id
    if (rayTraceConfig.auto_convergence) {
      const target = rayTraceConfig.convergence_target_percent ?? 5
      const enabledReceiverMetrics = receivers
        .filter((receiver) => receiver.enabled)
        .map((receiver) => {
          const value = rayTraceResult.metrics[receiver.receiver_id]
          return value && typeof value === 'object'
            ? (value as Record<string, unknown>)
            : {}
        })
      const metricNumber = (value: unknown) =>
        Number.isFinite(Number(value)) ? Number(value) : Number.POSITIVE_INFINITY
      const converged =
        enabledReceiverMetrics.length > 0 &&
        enabledReceiverMetrics.every(
          (metric) =>
            (Number(metric.hit_count) || 0) >= 30 &&
            metricNumber(metric.error_estimate_percent) <= target &&
            metricNumber(metric.peak_area_error_estimate_percent) <= target,
        )
      const baseRayCount = emitters
        .filter((emitter) => emitter.enabled)
        .reduce((sum, emitter) => sum + Math.max(1, emitter.ray_count), 0)
      const currentMultiplier =
        baseRayCount > 0 ? rayTraceResult.total_rays / baseRayCount : 1
      const reachedMaximum =
        currentMultiplier >= (rayTraceConfig.max_convergence_multiplier ?? 8)
      if (!converged && !reachedMaximum) {
        setActiveSection('ray-tracing')
        setRayTraceResultOpen(true)
        return
      }
    }
    setActiveSection('result')
    setRayTraceResultOpen(true)
  }, [actions, activeRayTraceJobId, emitters, rayTraceConfig, rayTraceResult, receivers, savedActiveCaseResult?.run_id])

  const openFeatureNotice = (
    title: string,
    description: string,
    action?: 'select-gpu',
  ) => {
    if (document.activeElement instanceof HTMLElement) {
      noticeReturnFocusRef.current = document.activeElement
    }
    setNoticeDialog({ title, description, action })
  }

  useEffect(() => {
    if (!pendingProject || !activeCad || !scene) return

    const restoredLegacyCpu =
      pendingProject.workspace.rayTraceConfig.compute_backend === undefined

    const compatibility = compareBitsamProjectScene(
      pendingProject,
      scene,
      activeCad,
    )
    if (!compatibility.compatible) {
      const attemptKey = `${pendingProject.saved_at}:${scene.metadata.scene_token}`
      if (projectLoadAttemptRef.current === attemptKey) return
      projectLoadAttemptRef.current = attemptKey
      const settingsOnly = createBitsamSettingsOnlyState(pendingProject)
      actions.restoreProjectState(settingsOnly.workspace)
      const sameCadFamily =
        activeCad.displayName.toLocaleLowerCase() ===
          pendingProject.cad.display_name.toLocaleLowerCase() &&
        scene.metadata.component_count ===
          pendingProject.cad.fingerprint.component_count
      const restoredArchivedResult =
        sameCadFamily && pendingProject.analysis_result
          ? pendingProject.analysis_result
          : null
      actions.setRestoredRayTraceResult(restoredArchivedResult)
      setPendingProject(null)
      openFeatureNotice(
        restoredLegacyCpu
          ? 'CPU 모드로 설정 조건을 불러왔습니다'
          : '설정 조건만 불러왔습니다',
        [
          '저장 당시 CAD와 현재 CAD의 Surface 구조가 달라 형상 연결 항목은 제외했습니다.',
          'Ray 개수, 최대 반사 횟수, 종료 조건, Stored path 저장 한도 및 표시 조건을 복원했습니다.',
          `Datum emitter ${settingsOnly.restoredDatumEmitters}개 / Datum receiver ${settingsOnly.restoredDatumReceivers}개를 복원했습니다.`,
          `CAD Component·Face·ROI 연결 항목 ${settingsOnly.skippedGeometryItems}개는 잘못된 면 연결을 방지하기 위해 제외했습니다.`,
          restoredArchivedResult
            ? '동일한 CAD 파일명과 Component 구성의 저장 결과 및 Stored Ray는 과거 결과 확인용으로 복원했습니다. 현재 형상과 Surface 번호가 다를 수 있으므로 Surface 기여도는 다시 계산해 주세요.'
            : pendingProject.analysis_result
              ? 'CAD 파일명이 달라 저장 결과와 Stored Ray는 현재 형상에 연결하지 않았습니다.'
              : '저장된 분석 결과는 없습니다.',
          ...compatibility.reasons.map((reason) => `불일치: ${reason}`),
          ...(restoredLegacyCpu
            ? ['이 프로젝트에는 계산 장치 설정이 없어 CPU로 복원했습니다.']
            : []),
        ].join('\n'),
        restoredLegacyCpu ? 'select-gpu' : undefined,
      )
      return
    }

    actions.restoreProjectState(pendingProject.workspace)
    actions.setRestoredRayTraceResult(
      pendingProject.analysis_result ?? null,
    )
    setPendingProject(null)
    projectLoadAttemptRef.current = ''
    openFeatureNotice(
      restoredLegacyCpu
        ? 'CPU 모드로 프로젝트를 불러왔습니다'
        : 'BITSAM 프로젝트 불러오기 완료',
      [
        `${pendingProject.project_name} 설정을 현재 CAD에 복원했습니다.`,
        'ROI, Component 상태, Transform, Material, Emitter, Receiver와 Ray Tracing 설정이 적용되었습니다.',
        ...compatibility.warnings,
        ...(restoredLegacyCpu
          ? ['이전 프로젝트에 계산 장치 설정이 없어 CPU로 안전하게 복원했습니다. GPU를 사용하려면 아래 버튼으로 명시적으로 선택하세요.']
          : []),
      ].join('\n'),
      restoredLegacyCpu ? 'select-gpu' : undefined,
    )
  }, [actions, activeCad, pendingProject, scene])

  const handleSaveProject = async () => {
    if (!activeCad || !scene) {
      openFeatureNotice(
        '저장할 CAD가 없습니다',
        'CAD 모델을 불러온 뒤 BITSAM 프로젝트를 저장해 주세요.',
      )
      return
    }

    try {
      const project = createBitsamProject(
        scene,
        workspaceStore.getState(),
        new Date(),
        displayedRayTraceResult,
      )
      const saveResult = await saveBitsamProject(project)
      if (saveResult === 'cancelled') return
      openFeatureNotice(
        'BITSAM 프로젝트 저장 완료',
        saveResult === 'picked'
          ? `${project.project_name}.bitsam 파일을 선택한 위치에 저장했습니다. 원본 CAD 파일은 포함되지 않으므로 함께 보관해 주세요.`
          : saveResult === 'fallback-downloaded'
            ? `저장 위치 선택 기능을 사용할 수 없어 ${project.project_name}.bitsam 파일을 다운로드 폴더에 안전하게 저장했습니다.`
            : `${project.project_name}.bitsam 파일을 다운로드 폴더에 저장했습니다. 이 브라우저에서는 저장 위치 선택을 지원하지 않습니다.`,
      )
    } catch (error) {
      openFeatureNotice(
        'BITSAM 프로젝트 저장 실패',
        error instanceof Error
          ? error.message
          : '알 수 없는 오류가 발생했습니다.',
      )
    }
  }

  const handleLoadProject = async (
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return

    try {
      const project = await readBitsamProjectFile(file)
      projectLoadAttemptRef.current = ''
      setPendingProject(project)
      if (!activeCad || !scene) {
        openFeatureNotice(
          'BITSAM 프로젝트를 읽었습니다',
          `${project.cad.display_name} CAD 파일을 Import하면 저장된 설정을 자동으로 복원합니다.`,
        )
      }
    } catch (error) {
      setPendingProject(null)
      openFeatureNotice(
        'BITSAM 프로젝트 불러오기 실패',
        error instanceof BitsamProjectError
          ? error.message
          : '파일을 읽는 중 알 수 없는 오류가 발생했습니다.',
      )
    }
  }

  const openComponentDialog = (
    type: ComponentDialogType,
    request: ComponentEditorRequest,
  ) => {
    componentReturnFocusRef.current = request.returnFocusElement
    setComponentDialog({
      type,
      componentId: request.componentId,
    })
  }

  const handleCopyAnalysisSetup = async () => {
    if (!scene || copySetupTargetIds.length === 0) return
    const abortController = new AbortController()
    copySetupAbortRef.current = abortController
    setCopySetupPending(true)
    setCopySetupProgress('Preparing Component metadata...')
    try {
      const selectedCases = cadCases.filter((item) =>
        copySetupTargetIds.includes(item.caseId),
      )
      const sourceMetadata =
        cadCases.find((item) => item.caseId === activeCadCaseId)
          ?.componentMatchMetadata ?? sceneComponentMatchMetadata(scene)
      const successful: Array<{
        item: (typeof selectedCases)[number]
        match: ReturnType<typeof matchSetupComponents>
      }> = []
      const failedCases: string[] = []

      for (let index = 0; index < selectedCases.length; index += 1) {
        if (abortController.signal.aborted) return
        const item = selectedCases[index]
        const caseName =
          item.name || item.cad.displayName || `Case ${index + 1}`
        setCopySetupProgress(
          `${index + 1} / ${selectedCases.length} · ${caseName}`,
        )
        // Yield once so the progress label paints before any legacy Scene
        // fallback. Normal imported Cases already have lightweight metadata.
        await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
        try {
          let targetMetadata = item.componentMatchMetadata
          if (!targetMetadata || targetMetadata.length === 0) {
            // Compatibility path for a Case created before metadata capture.
            // Process one full Scene at a time so large meshes never coexist.
            const targetScene = await apiClient.getScene(item.cad.path, {
              signal: abortController.signal,
            })
            targetMetadata = sceneComponentMatchMetadata(targetScene)
            actions.setCadCaseComponentMatchMetadata(
              item.caseId,
              targetMetadata,
            )
          }
          successful.push({
            item,
            match: matchSetupComponents(
              { components: sourceMetadata },
              { components: targetMetadata },
            ),
          })
        } catch (error) {
          if (abortController.signal.aborted) return
          failedCases.push(caseName)
          console.warn(`Copy Setup metadata failed for ${caseName}`, error)
        }
      }
      if (successful.length === 0) {
        openFeatureNotice(
          'Copy Setup Failed',
          `대상 CAD Scene을 불러오지 못했습니다.${
            failedCases.length > 0
              ? `\n확인 필요: ${failedCases.join(', ')}`
              : ''
          }`,
        )
        return
      }

      const copyTargets: CopySetupTarget[] = successful.map(
        ({ item, match }) => ({
          caseId: item.caseId,
          componentIdMap: match.componentIdMap,
        }),
      )
      actions.copyActiveSetupToCases(copyTargets)
      const sourceState = workspaceStore.getState()
      const matchedComponents = successful.reduce(
        (sum, item) => sum + item.match.matched,
        0,
      )
      const unmatchedComponents = successful.reduce(
        (sum, item) => sum + item.match.unmatched,
        0,
      )
      const matchBreakdown = successful.reduce(
        (totals, item) => ({
          name: totals.name + item.match.breakdown.name,
          geometry: totals.geometry + item.match.breakdown.geometry,
          componentId:
            totals.componentId + item.match.breakdown.componentId,
          orderedFallback:
            totals.orderedFallback + item.match.breakdown.orderedFallback,
        }),
        { name: 0, geometry: 0, componentId: 0, orderedFallback: 0 },
      )
      const faceSpecificSkipped =
        successful.length *
        (sourceState.materialAssignments.filter(
          (assignment) => assignment.targetType === 'faces',
        ).length +
          sourceState.transformRules.filter(
            (rule) => rule.targetType === 'faces',
          ).length)
      const surfaceEmittersToReselect =
        successful.length *
        sourceState.emitters.filter(
          (emitter) => emitter.emitter_type === 'face',
        ).length
      setCopySetupOpen(false)
      setCopySetupTargetIds([])
      openFeatureNotice(
        'Copy Setup Complete',
        [
          `${successful.length}개 Case에 안전한 전체 설정 복사를 완료했습니다.`,
          `Component 연결: ${matchedComponents}개 일치 / ${unmatchedComponents}개 불일치 제외`,
          `매칭 기준: 이름 ${matchBreakdown.name} / 형상 ${matchBreakdown.geometry} / Component ID ${matchBreakdown.componentId} / 동일 순서 ${matchBreakdown.orderedFallback}`,
          `Face 종속 Material·Transform: ${faceSpecificSkipped}개 안전을 위해 제외`,
          `CAD Surface Emitter: ${surfaceEmittersToReselect}개 Face 재선택 필요`,
          'ROI는 좌표 기준으로 재매핑되며 Hidden/Delete 상태와 기존 Ray 결과는 복사하지 않습니다.',
          ...(failedCases.length > 0
            ? [`Scene 로드 실패로 제외된 Case: ${failedCases.join(', ')}`]
            : []),
        ].join('\n'),
      )
    } catch (error) {
      openFeatureNotice(
        'Copy Setup Failed',
        error instanceof Error
          ? error.message
          : '설정을 복사하는 중 알 수 없는 오류가 발생했습니다.',
      )
    } finally {
      if (copySetupAbortRef.current === abortController) {
        copySetupAbortRef.current = null
      }
      setCopySetupPending(false)
      setCopySetupProgress('')
    }
  }

  const updateWorkflowSidebarWidth = (width: number) => {
    setWorkflowSidebarWidth(clampWorkflowSidebarWidth(width))
  }

  const handleWorkflowSidebarWidthToggle = () => {
    setWorkflowSidebarWidth((current) =>
      clampWorkflowSidebarWidth(
        current > WORKFLOW_SIDEBAR_DEFAULT_WIDTH + 24
          ? WORKFLOW_SIDEBAR_DEFAULT_WIDTH
          : WORKFLOW_SIDEBAR_WIDE_WIDTH,
      ),
    )
  }

  const handleWorkflowSidebarResizePointerDown = (
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    workflowSidebarResizeStartRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      width: workflowSidebarWidth,
    }
    setIsWorkflowSidebarResizing(true)
  }

  const handleWorkflowSidebarResizePointerMove = (
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    const start = workflowSidebarResizeStartRef.current
    if (!start || start.pointerId !== event.pointerId) return
    updateWorkflowSidebarWidth(start.width + event.clientX - start.clientX)
  }

  const finishWorkflowSidebarResize = (
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    const start = workflowSidebarResizeStartRef.current
    if (!start || start.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    workflowSidebarResizeStartRef.current = null
    setIsWorkflowSidebarResizing(false)
  }

  const handleWorkflowSidebarResizeKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) => {
    const step = event.shiftKey ? 32 : 16
    let nextWidth: number | undefined

    if (event.key === 'ArrowLeft') {
      nextWidth = workflowSidebarWidth - step
    } else if (event.key === 'ArrowRight') {
      nextWidth = workflowSidebarWidth + step
    } else if (event.key === 'Home') {
      nextWidth = WORKFLOW_SIDEBAR_MIN_WIDTH
    } else if (event.key === 'End') {
      nextWidth = workflowSidebarMaxWidth()
    }

    if (nextWidth === undefined) return
    event.preventDefault()
    updateWorkflowSidebarWidth(nextWidth)
  }

  return (
    <div className="grid min-h-svh w-full min-w-0 max-w-full grid-cols-[minmax(0,1fr)] bg-background text-foreground lg:h-svh lg:grid-rows-[3.25rem_minmax(0,1fr)] lg:overflow-hidden">
      <header className="sticky top-0 z-30 flex h-13 items-center justify-between border-b border-border bg-background/92 px-3 backdrop-blur-xl lg:static lg:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary">
            <Box className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">
              TV Leakage Simulator
            </div>
            <div className="hidden text-xs text-muted-foreground sm:block">
              React workspace shell
            </div>
          </div>
          <Badge
            variant="outline"
            className="hidden border-primary/30 bg-primary/10 text-primary md:inline-flex"
          >
            v1.0.0 · React
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={projectFileInputRef}
            type="file"
            accept=".bitsam,application/vnd.bitsam+json"
            className="hidden"
            aria-label="BITSAM project file"
            onChange={handleLoadProject}
          />
          <Button
            variant="outline"
            size="sm"
            aria-label="Save BITSAM project"
            disabled={!activeCad || !scene}
            title={
              activeCad && scene
                ? '현재 시뮬레이션을 .bitsam 파일로 저장'
                : 'CAD를 먼저 불러와 주세요'
            }
            onClick={handleSaveProject}
          >
            <Save data-icon="inline-start" />
            <span className="hidden sm:inline">Save</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Copy Setup"
            disabled={!activeCadCaseId || cadCases.length < 2}
            title="현재 Case의 해석 설정을 다른 Case로 복사"
            onClick={() => {
              setCopySetupTargetIds([])
              setCopySetupOpen(true)
            }}
          >
            <Copy data-icon="inline-start" />
            <span className="hidden sm:inline">Copy Setup</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Load BITSAM project"
            title=".bitsam 시뮬레이션 파일 불러오기"
            onClick={() => projectFileInputRef.current?.click()}
          >
            <FolderOpen data-icon="inline-start" />
            <span className="hidden sm:inline">Load</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={
              theme === 'light'
                ? 'Switch to dark mode'
                : 'Switch to light mode'
            }
            title={
              theme === 'light'
                ? 'Dark mode로 변경'
                : 'Light mode로 변경'
            }
            onClick={() =>
              setTheme((current) =>
                current === 'light' ? 'dark' : 'light',
              )
            }
          >
            {theme === 'light' ? (
              <Moon data-icon="inline-start" />
            ) : (
              <Sun data-icon="inline-start" />
            )}
            <span className="hidden xl:inline">
              {theme === 'light' ? 'Dark' : 'Light'}
            </span>
          </Button>
          <div className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
            <CircleDot className="size-3 text-primary" aria-hidden="true" />
            API layer ready
          </div>
          <Button
            variant="outline"
            size="sm"
            aria-label="Manual Guide"
            onClick={() =>
              openFeatureNotice(
                'Manual Guide',
                '시뮬레이터 완성 후 전체 작업 순서, 각 Step과 버튼의 기능, 입력 조건 및 결과 해석 가이드를 연결할 예정입니다.',
              )
            }
          >
            <BookOpen data-icon="inline-start" />
            <span className="hidden sm:inline">Manual Guide</span>
          </Button>
        </div>
      </header>

      <div
        className="relative grid min-h-0 min-w-0 grid-cols-1 lg:grid-cols-[var(--workflow-sidebar-width)_minmax(0,1fr)]"
        data-simulator-workspace-layout
        style={
          {
            '--workflow-sidebar-width': `${workflowSidebarWidth}px`,
          } as CSSProperties
        }
      >
        <WorkflowSidebar
          activeSection={activeSection}
          onActiveSectionChange={setActiveSection}
          isWide={
            workflowSidebarWidth > WORKFLOW_SIDEBAR_DEFAULT_WIDTH + 24
          }
          onToggleWidth={handleWorkflowSidebarWidthToggle}
          scene={scene}
          cameraFrame={viewerCameraFrame}
          isSceneLoading={sceneQuery.isPending && activeCad !== null}
          sceneErrorMessage={sceneErrorMessage}
          rayTraceJob={rayTraceJob}
          autoConvergenceCancelToken={autoConvergenceCancelToken}
          rayObjectEditRequest={rayObjectEditRequest}
          onRayObjectEditRequestHandled={() =>
            setRayObjectEditRequest(null)
          }
          onOpenRayTraceResult={() => {
            if (displayedRayTraceResult) setRayTraceResultOpen(true)
          }}
          onEditMaterial={(request) =>
            openComponentDialog('material', request)
          }
          onEditTransform={(request) =>
            openComponentDialog('transform', request)
          }
          onDeleteComponent={(request) =>
            openComponentDialog('delete', request)
          }
        />
        <div
          className={`group absolute inset-y-0 z-20 hidden w-3 -translate-x-1/2 cursor-col-resize touch-none select-none outline-none lg:block ${
            isWorkflowSidebarResizing ? 'bg-primary/8' : ''
          }`}
          style={{ left: 'var(--workflow-sidebar-width)' }}
          role="separator"
          aria-label="왼쪽 메뉴 너비 조절"
          aria-orientation="vertical"
          aria-controls="workflow-sidebar"
          aria-valuemin={WORKFLOW_SIDEBAR_MIN_WIDTH}
          aria-valuemax={workflowSidebarMaxWidth()}
          aria-valuenow={workflowSidebarWidth}
          aria-valuetext={`${workflowSidebarWidth}px`}
          tabIndex={0}
          title="드래그하거나 방향키로 왼쪽 메뉴 너비 조절 · 더블클릭으로 기본 폭"
          onPointerDown={handleWorkflowSidebarResizePointerDown}
          onPointerMove={handleWorkflowSidebarResizePointerMove}
          onPointerUp={finishWorkflowSidebarResize}
          onPointerCancel={finishWorkflowSidebarResize}
          onLostPointerCapture={() => {
            workflowSidebarResizeStartRef.current = null
            setIsWorkflowSidebarResizing(false)
          }}
          onKeyDown={handleWorkflowSidebarResizeKeyDown}
          onDoubleClick={() =>
            updateWorkflowSidebarWidth(WORKFLOW_SIDEBAR_DEFAULT_WIDTH)
          }
        >
          <span
            className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-[width,background-color,box-shadow] group-hover:w-0.5 group-hover:bg-primary/70 group-focus-visible:w-0.5 group-focus-visible:bg-primary group-focus-visible:shadow-[0_0_0_2px_color-mix(in_oklab,var(--primary)_25%,transparent)] ${
              isWorkflowSidebarResizing
                ? 'w-0.5 bg-primary'
                : 'bg-border'
            }`}
            aria-hidden="true"
          />
        </div>
        <ViewerWorkspace
          scene={scene}
          cadModelVisible={activeCadCaseVisible}
          isSceneLoading={sceneQuery.isPending && activeCad !== null}
          sceneErrorMessage={sceneErrorMessage}
          onCameraFrameChange={setViewerCameraFrame}
          rayTraceResult={displayedRayTraceResult}
          rayTraceResultOpen={rayTraceResultOpen}
          onRayTraceResultOpenChange={(open) => {
            setRayTraceResultOpen(open)
            if (!open) {
              setAutoConvergenceCancelToken((current) => current + 1)
            }
          }}
          editingComponentId={
            componentDialog?.type === 'material' ||
            componentDialog?.type === 'transform'
              ? componentDialog.componentId
              : null
          }
          editingComponentMode={
            componentDialog?.type === 'material' ||
            componentDialog?.type === 'transform'
              ? componentDialog.type
              : null
          }
          onEditMaterial={(request) =>
            openComponentDialog('material', request)
          }
          onEditTransform={(request) =>
            openComponentDialog('transform', request)
          }
          onDeleteComponent={(request) =>
            openComponentDialog('delete', request)
          }
          onEditRayObject={(request) => {
            setActiveSection('ray-tracing')
            setRayObjectEditRequest(request)
          }}
        />
      </div>

      <AppDialog
        open={copySetupOpen}
        onOpenChange={(open) => {
          if (!open) copySetupAbortRef.current?.abort()
          setCopySetupOpen(open)
        }}
        title="Copy Analysis Setup"
        description="현재 활성 Case의 해석 조건을 선택한 Case에 복사합니다. 대상 Case의 기존 설정과 Ray 결과는 교체됩니다."
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                copySetupAbortRef.current?.abort()
                setCopySetupOpen(false)
              }}
            >
              {copySetupPending ? 'Cancel Copy' : 'Cancel'}
            </Button>
            <Button
              disabled={copySetupTargetIds.length === 0 || copySetupPending}
              onClick={() => void handleCopyAnalysisSetup()}
            >
              {copySetupPending
                ? 'Checking Components...'
                : `Copy to ${copySetupTargetIds.length} Cases`}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-3">
            <div className="text-sm text-muted-foreground">Source Case</div>
            <div className="mt-1 text-base font-semibold">
              {cadCases.find((item) => item.caseId === activeCadCaseId)?.name ||
                cadCases.find((item) => item.caseId === activeCadCaseId)?.cad.displayName ||
                'Active Case'}
            </div>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">Target Cases</legend>
            {cadCases
              .filter((item) => item.caseId !== activeCadCaseId)
              .map((item) => (
                <label
                  key={item.caseId}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-border p-3 hover:border-primary/40 hover:bg-primary/5"
                >
                  <input
                    type="checkbox"
                    checked={copySetupTargetIds.includes(item.caseId)}
                    onChange={(event) => {
                      const checked = event.currentTarget.checked
                      setCopySetupTargetIds((current) =>
                        checked
                          ? [...current, item.caseId]
                          : current.filter((caseId) => caseId !== item.caseId),
                      )
                    }}
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">
                      {item.name || `CASE ${String(item.order).padStart(2, '0')}`}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {item.cad.displayName}
                    </span>
                  </span>
                </label>
              ))}
          </fieldset>
          {copySetupPending && copySetupProgress ? (
            <div
              className="rounded-lg border border-primary/25 bg-primary/5 p-3 text-sm font-medium text-foreground"
              role="status"
            >
              Copying Setup · {copySetupProgress}
            </div>
          ) : null}
          <div className="rounded-lg border border-orange-300/60 bg-orange-50/70 p-3 text-xs leading-5 text-orange-950 dark:border-orange-800 dark:bg-orange-950/30 dark:text-orange-100">
            전체 해석 설정을 안전하게 복사합니다. Component 설정은 CAD 이름이 일치하는 부품에만 연결하고, ROI는 동일 공간 좌표로 재매핑합니다. Face 종속 Material·Transform은 제외하며 CAD Surface Emitter는 Face를 다시 선택해야 합니다.
          </div>
        </div>
      </AppDialog>

      <AppDialog
        open={noticeDialog !== null}
        onOpenChange={(open) => {
          if (!open) setNoticeDialog(null)
        }}
        title={noticeDialog?.title ?? 'Notice'}
        description={noticeDialog?.description}
        returnFocusRef={noticeReturnFocusRef}
        footer={
          <>
            <Button variant="outline" onClick={() => setNoticeDialog(null)}>
              Close
            </Button>
            {noticeDialog?.action === 'select-gpu' ? (
              <Button
                onClick={() => {
                  const current = workspaceStore.getState().rayTraceConfig
                  actions.setRayTraceConfig({
                    ...current,
                    compute_backend: 'gpu_cuda',
                    intersection_backend:
                      current.intersection_backend === 'brute_force'
                        ? 'bvh'
                        : current.intersection_backend,
                  })
                  setActiveSection('ray-tracing')
                  setNoticeDialog(null)
                }}
              >
                NVIDIA CUDA GPU 선택
              </Button>
            ) : null}
          </>
        }
      />

      <MaterialEditorDialog
        open={componentDialog?.type === 'material'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        component={activeComponent}
        scene={scene}
        componentName={activeComponentName}
        returnFocusRef={componentReturnFocusRef}
      />

      <TransformEditorDialog
        open={componentDialog?.type === 'transform'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        component={activeComponent}
        componentName={activeComponentName}
        returnFocusRef={componentReturnFocusRef}
      />

      <ConfirmationDialog
        open={componentDialog?.type === 'delete'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        title={`Delete ${activeComponentName || 'component'}?`}
        description="Viewer와 Ray Tracing 대상에서 제외하며 연결된 Material Assignment와 Transform Rule도 함께 정리합니다. CAD를 다시 Import하면 복원됩니다."
        confirmLabel="Delete component"
        cancelLabel="Cancel"
        destructive
        returnFocusRef={componentReturnFocusRef}
        onConfirm={() => {
          if (!activeComponent) return
          actions.deleteComponent(
            activeComponent.component_id,
            activeComponent.face_indices,
          )
          setComponentDialog(null)
        }}
      />
    </div>
  )
}
