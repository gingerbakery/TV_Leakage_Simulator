import { PreviewRunStatus } from './preview-run-status'
import { CollapsibleSection } from '@/components/common/collapsible-section'
import { planObserverSampling } from './observer-sampling'
import { SensorComparisonDialog } from './sensor-comparison-dialog'
import { previewSignature, samePreviewSignature } from './leak-preview-signature'
import { expandBlockerGroups } from './blocker-groups'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Cuboid, Eye, EyeOff, LocateFixed, LoaderCircle, Minus, Play, Plus, ScanSearch, StopCircle, Trash2 } from 'lucide-react'

import {
  apiClient,
  apiQueryKeys,
  type ScenePayload,
  type RayTraceRequest,
  useRayTraceJobQuery,
  useStartRayTraceMutation,
  useStopRayTraceMutation,
} from '@/api'
import { ComputeExecutionStatus } from '@/features/results/compute-execution-status'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { createFaceEmitter, nextSpecId, rayObjectDisplayName } from '@/features/raytracing'
import { buildRayTraceRequest } from '@/features/raytracing/ray-tracing-model'
import { buildPrecisionPreviewConstraints } from '@/features/raytracing/precision-preview-constraints'
import {
  groupRoiFacesByComponent,
} from '@/features/roi'
import { cn } from '@/lib/utils'
import { useWorkspaceStore, workspaceSelectors, workspaceStore } from '@/stores'

import {
  groupObserverCandidates,
  buildLeakPreviewRequest,
  escapeDirectionSummary,
  observerProbeSummary,
  observerNoDetectionMessage,
  createLeakPreviewBodyPlaneEmitters,
  createCandidateReceiver,
  getLeakPreviewBounds,
  candidateRoiWithSources,
  detectLeakPreviewCandidates,
  resolveLeakPreviewRoiFaces,
  type LeakPreviewCandidate,
  type LeakPreviewDirection,
  type LeakPreviewQuality,
} from './leak-preview-model'
import { leakPreviewStore, useLeakPreviewStore } from './leak-preview-store'

interface LeakPreviewPanelProps {
  scene?: ScenePayload
  onOpenPrecision(): void
}

function riskPriority(value: number): 'High' | 'Medium' | 'Low' {
  if (value >= 0.67) return 'High'
  if (value >= 0.33) return 'Medium'
  return 'Low'
}

function coordinate(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : '0.0'
}

function cadFaceCount(scene: ScenePayload | undefined, faceIds: number[]): number {
  if (!scene) return faceIds.length
  const sourceIds = scene.mesh.face_source_ids
  if (!sourceIds) return faceIds.length
  const uniqueSourceIds = new Set<number>()
  for (const faceId of faceIds) uniqueSourceIds.add(sourceIds[faceId] ?? faceId)
  return uniqueSourceIds.size
}

const previewQualityLabel: Record<LeakPreviewQuality, string> = {
  fast: '100,000 Rays · 3 Reflections',
  balanced: '500,000 Rays · 5 Reflections',
  deep: '1,000,000 Rays · 20 Reflections',
}

const previewQualityName: Record<LeakPreviewQuality, string> = {
  fast: 'Fast',
  balanced: 'Balanced',
  deep: 'Deep Scan',
}

const previewDirectionOptions: Array<{ id: LeakPreviewDirection; label: string; axis: string }> = [
  { id: 'pos_z', label: '정면', axis: '+Z' },
  { id: 'neg_z', label: '후면', axis: '-Z' },
  { id: 'neg_x', label: '좌측', axis: '-X' },
  { id: 'pos_x', label: '우측', axis: '+X' },
  { id: 'pos_y', label: '상단', axis: '+Y' },
  { id: 'neg_y', label: '바닥', axis: '-Y' },
]

export function LeakPreviewPanel({ scene, onOpenPrecision }: LeakPreviewPanelProps) {
  const [candidatesCollapsed,setCandidatesCollapsed] = useState(false)
  const [candidatePage, setCandidatePage] = useState(0)
  const [groupDistance, setGroupDistance] = useState(15)
  const [groupUndo, setGroupUndo] = useState<ReturnType<typeof leakPreviewStore.getState> | null>(null)
  const [validatingCandidateId, setValidatingCandidateId] = useState<string | null>(null)
  const [comparisonRequest, setComparisonRequest] = useState<RayTraceRequest | null>(null)
  const candidateListId = useId()
  const candidateRows = useRef(new Map<string, HTMLElement>())
  const queryClient = useQueryClient()
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  useEffect(() => { setComparisonRequest(null) }, [scene?.metadata.scene_token, activeCadCaseId])
  const selectedFaceIds = useWorkspaceStore(workspaceSelectors.selectedFaceIds)
  const selectedComponentIds = useWorkspaceStore(workspaceSelectors.selectedComponentIds)
  const bsdfAssets = useWorkspaceStore(workspaceSelectors.bsdfAssets)
  const materialAssignments = useWorkspaceStore(workspaceSelectors.materialAssignments)
  const transformRules = useWorkspaceStore(workspaceSelectors.transformRules)
  const excludedComponentIds = useWorkspaceStore(workspaceSelectors.excludedComponentIds)
  const deletedComponentIds = useWorkspaceStore(workspaceSelectors.deletedComponentIds)
  const hiddenComponentIds = useWorkspaceStore(workspaceSelectors.hiddenComponentIds)
  const componentNameOverrides = useWorkspaceStore(workspaceSelectors.componentNameOverrides)
  const emitters = useWorkspaceStore(workspaceSelectors.emitters)
  const receivers = useWorkspaceStore(workspaceSelectors.receivers)
  const rayTraceConfig = useWorkspaceStore(workspaceSelectors.rayTraceConfig)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const sourceFaceIds = useLeakPreviewStore((state) => state.sourceFaceIds)
  const sourceEmitterIds = useLeakPreviewStore((state) => state.sourceEmitterIds)
  const setSourceEmitters = useLeakPreviewStore((state) => state.setSourceEmitters)
  const sourceMode = useLeakPreviewStore((state) => state.sourceMode)
  const sourceComponentIds = useLeakPreviewStore((state) => state.sourceComponentIds)
  const sourceBodyFaceCount = useLeakPreviewStore((state) => state.sourceBodyFaceCount)
  const migrateAllowedAreas = useLeakPreviewStore((state) => state.migrateAllowedAreas)
  const ensureScene = useLeakPreviewStore((state) => state.ensureScene)
  const boundSceneToken = useLeakPreviewStore((state) => state.sceneToken)
  const rebindSceneToken = useLeakPreviewStore((state) => state.rebindSceneToken)
  const detectionMode = useLeakPreviewStore((state) => state.detectionMode)
  const observer = useLeakPreviewStore((state) => state.observer)
  const setObserver = useLeakPreviewStore((state) => state.setObserver)
  const setDetectionMode = useLeakPreviewStore((state) => state.setDetectionMode)
  const previewResult = useLeakPreviewStore((state) => state.result)
  const resultRestored = useLeakPreviewStore((state) => state.resultRestored)
  const observerSummary = previewResult ? observerProbeSummary(previewResult) : null
  const [observerSort, setObserverSort] = useState<'brightness' | 'angle'>('brightness')
  const observerViews = useMemo(() => {
    const views = observerSummary?.views.map((view,index) => ({view,index})) ?? []
    if (observerSummary?.measure === 'luminance_cd_m2' && observerSort === 'brightness') {
      views.sort((a,b) => (b.view.peak_luminance_nit ?? 0)-(a.view.peak_luminance_nit ?? 0) || a.index-b.index)
    }
    return views
  }, [observerSummary, observerSort])
  const angularSummary = previewResult ? escapeDirectionSummary(previewResult) : null
  const quality = useLeakPreviewStore((state) => state.quality)
  const directions = useLeakPreviewStore((state) => state.directions)
  const jobId = useLeakPreviewStore((state) => state.jobId)
  const candidates = useLeakPreviewStore((state) => state.candidates)
  const candidatePageCount = Math.ceil(candidates.length / 100)
  const safeCandidatePage = Math.min(candidatePage, Math.max(0, candidatePageCount - 1))
  const previewPointCount = useLeakPreviewStore((state) => state.points.length)
  const visualizationVisible = useLeakPreviewStore((state) => state.visualizationVisible)
  const selectedCandidateId = useLeakPreviewStore((state) => state.selectedCandidateId)
  const selectedCandidate = candidates.find(candidate => candidate.id === selectedCandidateId)
  const candidateRevealRequest = useLeakPreviewStore((state) => state.candidateRevealRequest)
  const ignoreAreas = useLeakPreviewStore((state) => state.ignoreAreas)
  const blockers = useLeakPreviewStore((state) => state.blockers)
  const runSignature = useLeakPreviewStore((state) => state.runSignature)
  const setSourceFaceIds = useLeakPreviewStore((state) => state.setSourceFaceIds)
  const setSourceBody = useLeakPreviewStore((state) => state.setSourceBody)
  const setSourceMode = useLeakPreviewStore((state) => state.setSourceMode)
  const setQuality = useLeakPreviewStore((state) => state.setQuality)
  const toggleDirection = useLeakPreviewStore((state) => state.toggleDirection)
  const setJobId = useLeakPreviewStore((state) => state.setJobId)
  const setRunSignature = useLeakPreviewStore((state) => state.setRunSignature)
  const setDetection = useLeakPreviewStore((state) => state.setDetection)
  const clearDetection = useLeakPreviewStore((state) => state.clearDetection)
  const setVisualizationVisible = useLeakPreviewStore((state) => state.setVisualizationVisible)
  const selectCandidate = useLeakPreviewStore((state) => state.selectCandidate)
  const finishIgnoreAreaSelection = useLeakPreviewStore((state) => state.finishIgnoreAreaSelection)
  const removeCandidate = useLeakPreviewStore((state) => state.removeCandidate)
  const finishBlockerAreaSelection = useLeakPreviewStore((state) => state.finishBlockerAreaSelection)
  const startMutation = useStartRayTraceMutation()
  const stopMutation = useStopRayTraceMutation()
  const jobQuery = useRayTraceJobQuery(jobId)
  const job = jobQuery.data
  const handledRunRef = useRef<string | null>(null)
  const [message, setMessage] = useState('광원으로 사용할 CAD Face를 선택하세요.')
  const [pickingTarget, setPickingTarget] = useState<'source' | null>(null)

  useEffect(() => {
    if (candidateRevealRequest) {
      setCandidatesCollapsed(false)
      const index = candidates.findIndex((candidate) => candidate.id === candidateRevealRequest.candidateId)
      if (index >= 0) setCandidatePage(Math.floor(index / 100))
    }
  }, [candidateRevealRequest, candidates])

  useEffect(() => {
    if (candidateRevealRequest && !candidatesCollapsed) {
      candidateRows.current.get(candidateRevealRequest.candidateId)?.scrollIntoView?.({ block: 'nearest' })
    }
  }, [candidateRevealRequest, candidatesCollapsed, safeCandidatePage])
  useEffect(() => { setCandidatePage(0); setGroupUndo(null) }, [previewResult?.run_id, activeCadCaseId])

  useEffect(() => {
    if (scene) ensureScene(scene.metadata.scene_token)
  }, [ensureScene, scene, boundSceneToken, runSignature])

  useEffect(() => {
    if (scene) migrateAllowedAreas()
  }, [migrateAllowedAreas, scene])

  const activeJob = job?.job_id === jobId ? job : undefined
  // A missing response is an unknown job state, not evidence of completion.
  const isRunning = startMutation.isPending || Boolean(jobId && !activeJob) || activeJob?.status === 'queued' || activeJob?.status === 'running'
  const queryError = jobId && jobQuery.isError ? `작업 상태 조회 실패 · Job ${jobId} · ${jobQuery.error instanceof Error ? jobQuery.error.message : '서버 응답을 확인할 수 없습니다.'}` : null
  const terminalStatus = activeJob?.status === 'failed'
    ? `해석 실패 · ${Math.round(activeJob.progress * 100)}% · Job ${activeJob.job_id} · ${activeJob.error || '알 수 없는 서버 오류'}`
    : activeJob?.status === 'cancelled' || (activeJob?.status === 'completed' && (activeJob.phase === 'stopped' || activeJob.stopped_early))
      ? `중지 · Job ${activeJob.job_id} · ${activeJob.phase_detail ?? '부분 결과'}`
      : activeJob?.status === 'completed' ? `해석 완료 · Job ${activeJob.job_id}` : null
  const isPreparing = job?.status === 'running' && job.phase === 'preparing'
  const isFinalizing = job?.status === 'running' &&
    (job.phase === 'aggregating' || job.phase === 'serializing')
  const availableEmitters = emitters.filter((emitter) => emitter.enabled)
  const selectedEmitters = availableEmitters.filter((emitter) => sourceEmitterIds.includes(emitter.emitter_id))
  const hasPreviewSource = sourceMode === 'emitters'
    ? selectedEmitters.length > 0 && selectedEmitters.length === sourceEmitterIds.length
    : sourceMode === 'body' ? sourceComponentIds.length > 0 : sourceFaceIds.length > 0
  const hasBsdf = materialAssignments.some((assignment) => assignment.enabled && assignment.bsdfAssetId)
  const sourceCadFaceCount = useMemo(
    () => cadFaceCount(scene, sourceFaceIds),
    [scene, sourceFaceIds],
  )
  const observerBounds = scene ? getLeakPreviewBounds(scene, transformRules) : null
  const observerPlan = planObserverSampling(observer, sourceMode === 'emitters' ? selectedEmitters.length : sourceMode === 'body' ? sourceComponentIds.length*2 : 2,
    observerBounds ? { minimum: observerBounds.minimum, maximum: observerBounds.maximum, direction: directions[0] ?? 'neg_z' } : undefined)
  const analysisOnly = <T extends { visible?: boolean; label?: string }>(items: T[]) => items.map(({ visible: _visible, label: _label, ...item }) => item)
  const inputSignatureForSceneToken = (sceneToken: string | null) => previewSignature({
    sceneToken,
    sourceFaceIds,
    sourceMode,
    sourceComponentIds,
    sourceBodyFaceCount,
    sourceEmitterIds,
    existingEmitters: sourceMode === 'emitters' ? analysisOnly(selectedEmitters) : undefined,
    quality,
    detectionMode,
    observer,
    ...(observer.search_mode === 'full_detail' ? { precisionConfig: rayTraceConfig } : {}),
    directions,
    materialAssignments,
    bsdfAssets: bsdfAssets.map((asset) => ({ id: asset.id, transport: asset.transport })),
    transformRules,
    excludedComponentIds,
    deletedComponentIds,
    blockers: analysisOnly(expandBlockerGroups(blockers)),
    allowedAreas: analysisOnly(ignoreAreas),
  })
  const previewInputSignature = inputSignatureForSceneToken(
    scene?.metadata.scene_token ?? null,
  )
  const canResumePreview = Boolean(job?.resume_available && job.job_id === jobId &&
    samePreviewSignature(runSignature, previewInputSignature) && observer.enabled && observer.measurement === 'radiance')

  useEffect(() => {
    return () => actions.setEmitterFaceSelectionArmed(false)
  }, [actions])

  useEffect(() => {
    return () => {
      finishIgnoreAreaSelection()
      finishBlockerAreaSelection()
      actions.setRoiBoxSelectionArmed(false)
    }
  }, [actions, finishBlockerAreaSelection, finishIgnoreAreaSelection])

  useEffect(() => {
    if (!scene || !jobId || job?.job_id !== jobId || job.status !== 'completed') return
    if (!job.result) { setMessage(`결과 누락 · Job ${jobId} · 서버가 완료 결과를 반환하지 않았습니다.`); return }
    if (runSignature && !samePreviewSignature(runSignature, previewInputSignature)) return
    try {
      if (detectionMode === 'escape_directions' && !escapeDirectionSummary(job.result)) {
        setMessage(`결과 확인 실패 · Job ${jobId} · 방향별 유출 집계 결과가 없습니다. 서버와 화면 버전을 확인하세요.`)
        return
      }
      const probe = observerProbeSummary(job.result)
      if (detectionMode === 'escape_directions' && observer.enabled && (!probe ||
        (observer.measurement === 'radiance' && probe.measure !== 'luminance_cd_m2'))) {
        setMessage(`결과 확인 실패 · Job ${jobId} · 역추적 집계가 없거나 유효하지 않습니다. 서버와 화면 버전을 확인하세요.`)
        return
      }
      const detectionKey = `${job.job_id}:${detectionMode}:${JSON.stringify(analysisOnly(ignoreAreas))}`
      if (handledRunRef.current === detectionKey) return
      handledRunRef.current = detectionKey
      const detection = detectLeakPreviewCandidates(scene, job.result, {
        quality,
        ignoreAreas,
        transformRules,
      })
      setDetection(job.result, detection.points, detection.candidates)
      setMessage(
        job.phase === 'stopped' || job.stopped_early || observerProbeSummary(job.result)?.stopped
          ? `Preview를 중지했습니다. 중지 시점까지 빛샘 후보 ${detection.candidates.length}개가 검출되었습니다.`
          : detection.candidates.length > 0
          ? `개구부 경유 ${detection.candidates.filter(candidate => candidate.apertureId).length}개 · 미확정 경로 ${detection.candidates.filter(candidate => !candidate.apertureId).length}개`
          : observerNoDetectionMessage(probe),
      )
    } catch (error) {
      console.error(`[Preview] result processing failed | Job ${jobId}`, error)
      setMessage(`결과 처리 실패 · Job ${jobId} · ${error instanceof Error ? error.message : String(error)}`)
    }
  }, [clearDetection, detectionMode, observer.enabled, observer.measurement, ignoreAreas, job, jobId, previewInputSignature, quality, runSignature, scene, setDetection, transformRules])

  useEffect(() => {
    if (job?.status !== 'cancelled') return
    setMessage('Preview를 중지했습니다.')
  }, [job])

  useEffect(() => {
    // ensureScene above may have just rebound saved evidence to a new token.
    // Read that current signature rather than the pre-effect render snapshot.
    if (!scene) return
    const current = leakPreviewStore.getState()
    if (current.sceneToken !== scene.metadata.scene_token) return
    const currentSignature = current.runSignature
    if (!currentSignature || samePreviewSignature(currentSignature, previewInputSignature)) return
    if (isRunning) { setMessage('설정 변경 · 실행 중인 작업은 기존 조건으로 계속 진행합니다.'); return }
    if (activeJob?.status === 'failed' || activeJob?.status === 'cancelled') return
    clearDetection()
    setMessage(jobId ? `설정 변경으로 결과 초기화 · Job ${jobId} · Preview 재실행 필요` : 'Preview 재실행 필요')
  }, [activeJob, clearDetection, isRunning, jobId, previewInputSignature, runSignature, scene])

  useEffect(() => {
    if (job?.status !== 'failed') return
    setMessage(`Preview 실행 실패 · Job ${job.job_id} · ${job.error}`)
  }, [job])

  const finishFaceSelection = async () => {
    if (sourceMode === 'body') {
      if (selectedComponentIds.length === 0) {
        setMessage('3D Viewer에서 광원 Body를 하나 이상 선택하세요.')
        return
      }
      const selectedIds = new Set(selectedComponentIds)
      const bodyFaceCount = scene?.components
        .filter((component) => selectedIds.has(component.component_id))
        .reduce((sum, component) => sum + Math.max(component.face_count, component.face_indices.length), 0) ?? 0
      if (bodyFaceCount === 0 || !scene?.components.some((component) => selectedIds.has(component.component_id))) {
        setMessage('선택한 Body에서 가상 Plane 기준을 찾지 못했습니다.')
        return
      }
      setSourceBody(selectedComponentIds, bodyFaceCount)
      setMessage(`광원 Body ${selectedComponentIds.length}개 기준 가상 Plane이 등록되었습니다.`)
    } else {
      if (selectedFaceIds.length === 0) {
        setMessage('3D Viewer에서 광원 CAD Face를 하나 이상 선택하세요.')
        return
      }
      setSourceFaceIds(selectedFaceIds)
      setMessage(`광원 Face ${selectedFaceIds.length}개가 등록되었습니다.`)
    }
    setPickingTarget(null)
    actions.setEmitterFaceSelectionArmed(false)
    actions.setSelectedComponentIds([])
    clearDetection()
  }

  const runPreview = async (restart = false) => {
    if (!scene || !hasPreviewSource || isRunning) return
    finishBlockerAreaSelection()
    actions.setRoiBoxSelectionArmed(false)
    clearDetection()
    handledRunRef.current = null
    const expectedResume = canResumePreview && !restart
    setMessage(expectedResume ? '중단한 관찰 휘도 역추적을 이어 실행합니다.' : '전체 세트의 외부 유출광을 탐색하고 있습니다.')
    try {
      const start = (requestScene: ScenePayload) => startMutation.mutateAsync({
        request: { ...buildLeakPreviewRequest({
          scene: requestScene,
          sourceFaceIds,
          sourceComponentIds: sourceMode === 'body' ? sourceComponentIds : [],
          quality,
          maxDepth: rayTraceConfig.max_depth,
          precisionConfig: rayTraceConfig,
          detectionMode,
          observer,
          directions,
          computeBackend: rayTraceConfig.compute_backend,
          materialAssignments,
          bsdfAssets,
          existingEmitters: sourceMode === 'emitters' ? analysisOnly(selectedEmitters) : undefined,
          transformRules,
          excludedComponentIds,
          deletedComponentIds,
          blockers,
          allowedAreas: ignoreAreas,
        }), resume_scope: `preview:${activeCadCaseId ?? ''}`, ...(restart ? { restart_from_beginning: true } : {}) },
      })
      let started
      let runSceneToken = scene.metadata.scene_token
      try {
        started = await start(scene)
      } catch (error) {
        const cacheExpired = error instanceof Error &&
          error.message.includes('CAD scene cache expired')
        if (!cacheExpired || !activeCad?.path) throw error
        setMessage('CAD Scene 캐시를 자동 복구하고 있습니다.')
        const accessoryPaths = scene.metadata.source_files?.slice(1) ?? []
        const refreshed = await apiClient.refreshScene(activeCad.path, undefined, accessoryPaths)
        const refreshedScene: ScenePayload = {
          ...scene,
          metadata: {
            ...scene.metadata,
            scene_token: refreshed.scene_token,
          },
        }
        rebindSceneToken(refreshed.scene_token)
        queryClient.setQueryData(
          apiQueryKeys.scene(activeCad.path, accessoryPaths),
          refreshedScene,
        )
        started = await start(refreshedScene)
        runSceneToken = refreshed.scene_token
        setMessage('CAD Scene 캐시 복구 완료 · Preview를 시작했습니다.')
      }
      setJobId(started.job_id)
      setRunSignature(inputSignatureForSceneToken(runSceneToken))
      if (started.resumed_from_job_id) setMessage(`관찰 휘도 이어 실행 · 처리 표본 ${started.processed_rays.toLocaleString()}개부터 계속합니다.`)
      else if (expectedResume) setMessage('보관 상태가 없거나 입력이 달라 Preview를 처음부터 실행합니다.')
    } catch (error) {
      setRunSignature(null)
      setMessage(`Preview 실행 실패: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const stopPreview = async () => {
    if (!jobId) return
    setMessage('Preview 중지를 요청했습니다.')
    try {
      await stopMutation.mutateAsync({ jobId })
    } catch (error) {
      setMessage(`Preview 중지 실패: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const highlight = useLeakPreviewStore((state) => state.highlight)
  const setHighlight = useLeakPreviewStore((state) => state.setHighlight)

  const ensureCandidateReceiver = (candidate: LeakPreviewCandidate, candidateIndex: number) => {
    const generated = createCandidateReceiver(candidate, candidateIndex, scene, transformRules)
    const existing = receivers.find((receiver) => receiver.reference_mode === 'leak_preview_candidate' &&
      (generated.view_angles ? receiver.view_angles?.axis === generated.view_angles.axis &&
        receiver.view_angles.horizontal_deg === generated.view_angles.horizontal_deg &&
        receiver.view_angles.vertical_deg === generated.view_angles.vertical_deg : !receiver.view_angles) &&
      Math.abs(receiver.normal.reduce((sum, value, axis) => sum + value * generated.normal[axis], 0) - 1) < 1e-6 &&
      Math.hypot(...receiver.center.map((value, axis) => value - generated.center[axis])) < .1)
    if (existing) {
      const receiver = { ...existing, observer_reference_path: generated.observer_reference_path ?? null }
      actions.upsertReceiver(receiver)
      return { receiver, existed: true }
    }
    const receiver = { ...generated,
      receiver_id: nextSpecId('receiver', receivers.map((receiver) => receiver.receiver_id)),
    }
    actions.upsertReceiver(receiver)
    return { receiver, existed: false }
  }

  const createViewReceiver = (candidate: LeakPreviewCandidate) => {
    if (!scene || !candidate.observerEvidence) return
    const index = Math.max(1, candidates.findIndex((item) => item.id === candidate.id) + 1)
    const { receiver } = ensureCandidateReceiver(candidate, index)
    for (const other of receivers) {
      if (other.receiver_id !== receiver.receiver_id && other.enabled) actions.setReceiverEnabled(other.receiver_id, false)
    }
    actions.setReceiverEnabled(receiver.receiver_id, true)
    setMessage('검출 각도의 시야각 Receiver 1개를 활성화했습니다.')
    onOpenPrecision()
  }

  const candidateValidationRequest = (candidate: LeakPreviewCandidate) => {
    if (!scene) throw new Error('CAD를 먼저 불러오세요.')
    const receiver = createCandidateReceiver(candidate, 1, scene, transformRules)
    const workspace = workspaceStore.getState()
    return buildRayTraceRequest({ scene, projectName: 'Preview 센서 검증', emitters: workspace.emitters,
      receivers: [receiver], materialAssignments: workspace.materialAssignments, bsdfAssets: workspace.bsdfAssets,
      transformRules: workspace.transformRules, excludedComponentIds: workspace.excludedComponentIds,
      deletedComponentIds: workspace.deletedComponentIds, roiScopes: workspace.roiScopes, config: workspace.rayTraceConfig,
      auxiliaryBlockers: buildPrecisionPreviewConstraints(blockers, ignoreAreas,
        Boolean(workspace.rayTraceConfig.apply_preview_blockers), Boolean(workspace.rayTraceConfig.apply_allowed_areas)) })
  }

  const validateCandidatePath = async (candidate: LeakPreviewCandidate) => {
    if (!scene || validatingCandidateId || isRunning) return
    if (!candidate.representativePath) { setMessage('경로 검증 실패 · 저장된 대표 경로가 없습니다.'); return }
    setValidatingCandidateId(candidate.id)
    setMessage('대표 경로·정밀해석 Receiver 검증 중')
    try {
      const request = candidateValidationRequest(candidate)
      const response = await apiClient.validateObserverReceiver(request)
      if (leakPreviewStore.getState().sceneToken !== scene.metadata.scene_token || workspaceStore.getState().activeCadCaseId !== activeCadCaseId) return
      const check = response.receiver_checks[0]
      const reasons: Record<string, string> = {
        source_position_or_emission_mismatch: '광원 위치·방출·출력 불일치', source_covered_by_blocker: '발광 위치가 Block에 차폐됨', occluded_segment: '기구물·Block 차폐',
        reflection_vertex_off_surface: '반사점이 실제 면 밖', visible_endpoint_off_surface: '관찰 점이 실제 면 밖',
        reflection_material_mismatch: '반사 재질·방향 불일치', reflection_depth_mismatch: '최대 반사 횟수 초과',
        reflection_disabled: '반사 계산 꺼짐', receiver_position_direction_acceptance_or_roi_mismatch: 'Receiver 위치·방향·Acceptance·ROI 불일치',
      }
      setMessage(check?.path_valid ? check.visible_endpoint_kind === 'virtual_emitter'
        ? '경로 검증 통과 · 가상 발광면 · 외관 위치 미확정'
        : '경로 검증 통과 · 생성될 Receiver 도달' : `경로 검증 실패 · ${reasons[check?.diagnosis ?? ''] ?? '대표 경로 확인 불가'}`)
    } catch (error) {
      setMessage(`경로 검증 실패: ${error instanceof Error ? error.message : String(error)}`)
    } finally { setValidatingCandidateId(null) }
  }

  const createRoi = (candidate: LeakPreviewCandidate, precision: boolean) => {
    if (!scene) return
    setMessage('ROI 후보에 포함되는 CAD Face를 찾고 있습니다.')
    const clipBox = candidateRoiWithSources(scene, candidate, [...(previewResult?.emitters ?? []), ...emitters.filter((item) => item.enabled)], transformRules)
    const faceIds = resolveLeakPreviewRoiFaces(
      scene,
      clipBox,
      hiddenComponentIds,
      deletedComponentIds,
      transformRules,
    )
    const components = groupRoiFacesByComponent(scene, faceIds, componentNameOverrides)
    if (components.length === 0) {
      setMessage('후보 위치에서 ROI로 만들 CAD Face를 찾지 못했습니다.')
      return
    }
    const candidateIndex = Math.max(1, candidates.findIndex((item) => item.id === candidate.id) + 1)
    actions.addRoiScope({
      label: `Preview ROI ${String(candidateIndex).padStart(2, '0')}`,
      source: 'box',
      view: 'coordinate',
      components,
      clipBox,
      observerView: candidate.observerEvidence ? {
        horizontalDeg: candidate.observerEvidence.horizontalDeg, verticalDeg: candidate.observerEvidence.verticalDeg,
        direction: candidate.observerEvidence.direction,
      } : undefined,
      point: { x: candidate.center[0], y: candidate.center[1], z: candidate.center[2] },
    })
    const { existed: receiverAlreadyExists } = ensureCandidateReceiver(candidate, candidateIndex)
    if (precision) {
      if (!emitters.some((emitter) => emitter.enabled)) {
        if (sourceMode === 'body') {
          const generated = createLeakPreviewBodyPlaneEmitters(
            scene,
            sourceComponentIds,
            transformRules,
            Math.max(100_000, rayTraceConfig.ray_count),
            false,
          )
          const usedIds = emitters.map((emitter) => emitter.emitter_id)
          for (const plane of generated) {
            const emitterId = nextSpecId('emitter', usedIds)
            usedIds.push(emitterId)
            actions.upsertEmitter({
              ...plane,
              emitter_id: emitterId,
              power_mode: 'set_luminance',
              luminance_nit: 500,
              power_lumen: 1,
            })
          }
        } else {
          const emitterId = nextSpecId('emitter', emitters.map((emitter) => emitter.emitter_id))
          actions.upsertEmitter({
            ...createFaceEmitter(emitterId, sourceFaceIds),
            ray_count: Math.max(100_000, rayTraceConfig.ray_count),
          })
        }
      }
      setMessage('ROI, Receiver와 기본 Emitter를 준비했습니다. 방향과 크기를 확인한 뒤 정밀해석을 실행하세요.')
      onOpenPrecision()
      return
    }
    setMessage(receiverAlreadyExists
      ? '선택한 후보를 ROI List에 추가했습니다. 기존 Receiver를 유지합니다.'
      : '선택한 후보의 ROI와 Receiver를 생성했습니다.')
  }

  if (!scene) {
    return (
      <div className="rounded-lg border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
        먼저 Model Import에서 전체 세트 CAD를 불러오세요.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="sticky top-0 z-20 space-y-2 rounded-lg bg-background/95 pb-2 backdrop-blur-sm">
        <PreviewRunStatus busy={isRunning} validating={validatingCandidateId !== null} message={queryError ?? message}
          progress={job?.progress} terminal={terminalStatus} failed={Boolean(queryError) || activeJob?.status === 'failed' || message.includes('실패')}
          phase={isPreparing || isFinalizing ? job?.phase_detail : isRunning ? `${Math.round((job?.progress ?? 0)*100)}% · ${(job?.processed_rays ?? 0).toLocaleString()} / ${(job?.total_rays ?? 0).toLocaleString()} Rays` : undefined} />
        <div role="toolbar" aria-label="선택 Leak 작업" className="space-y-2 rounded-lg border border-orange-300 bg-background p-2">
          <div className="flex items-center gap-1 text-xs font-semibold">{selectedCandidate ? `${selectedCandidate.apertureId ? '선택 Leak' : '선택 경로'} · Candidate ${String(candidates.indexOf(selectedCandidate)+1).padStart(2,'0')} · ${selectedCandidate.label}` : 'Leak 선택 없음'}<HelpTooltip label="선택 Leak 작업 도움말">뷰어의 점 또는 후보 행을 선택한 뒤 공통 작업 버튼을 누르세요. 개구부 미확정 경로는 점을 표시하지 않고 경로만 검사합니다. 개구부가 확인된 경우에 Receiver·정밀해석을 생성할 수 있습니다. 묶음은 대표 경로·대표 시야각 한 곳을 검사하며 모든 구성원의 검증 통과를 뜻하지 않습니다. 경로 검증 중 회전 표시는 요청 대기 상태이며 서버 세부 진행률은 아닙니다.</HelpTooltip><HelpTooltip label="Preview 결과 저장 도움말">프로젝트 저장 시 Preview 결과·검출점·후보 목록·각도별 집계와 선택·표시 상태를 함께 저장합니다. 삭제한 후보도 그대로 유지합니다. 다시 열거나 Case를 전환하면 결과를 복원하며 실행 중 작업은 재개하지 않습니다. 광원·재질·형상 등 검사 조건이 달라지면 재실행이 필요합니다.</HelpTooltip></div>
          {candidates.length > 0 ? <div role="status" className="text-xs">개구부 경유 {candidates.filter(candidate => candidate.apertureId).length} · 개구부 미확정 {candidates.filter(candidate => !candidate.apertureId).length}</div> : null}
          {candidates.some(candidate => candidate.apertureId) ? <div role="status" className="text-xs text-amber-700">자동 출구 추정 · 최외곽 확정 아님</div> : null}
          {observerSummary?.aperture_summary?.reason === 'exit_estimation_cuda_failed' ? <div role="alert" className="text-xs text-destructive">자동 출구 추정 CUDA 실패 · CPU 대체 없이 미확정 유지</div> : null}
          {observerSummary?.aperture_summary?.reason === 'exit_estimation_intersection_failed' ? <div role="alert" className="text-xs text-destructive">자동 출구 추정 교차 검사 실패 · 미확정 유지</div> : null}
          {observerSummary && !observerSummary.aperture_summary ? <div role="status" className="text-xs text-amber-700">개구부 정보 없음 · 재해석 필요</div> : null}
          {['geometry_budget_exceeded','exit_estimation_budget_exceeded'].includes(observerSummary?.aperture_summary?.reason ?? '') ? <div role="status" className="text-xs text-amber-700">개구부 귀속 처리 예산 초과 · 미확정 경로 있음</div> : null}
          <div className="grid grid-cols-2 gap-1.5">
            <Button size="sm" variant="outline" disabled={!selectedCandidate?.observerEvidence || !selectedCandidate.apertureId || isRunning || validatingCandidateId !== null} onClick={() => { if (selectedCandidate) createViewReceiver(selectedCandidate) }}>시야각 Receiver 생성</Button>
            <Button size="sm" variant="outline" disabled={!selectedCandidate?.observerEvidence || !selectedCandidate.representativePath || isRunning || validatingCandidateId !== null} onClick={() => { if (selectedCandidate) void validateCandidatePath(selectedCandidate) }}>{validatingCandidateId ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : null}경로 검증</Button>
            <Button size="sm" variant="outline" disabled={!selectedCandidate?.observerEvidence || isRunning || validatingCandidateId !== null} onClick={() => { if (selectedCandidate) try { setComparisonRequest(candidateValidationRequest(selectedCandidate)) } catch (error) { setMessage(`광량 비교 실패: ${String(error)}`) } }}>광량 비교</Button>
            <Button size="sm" disabled={!selectedCandidate || !selectedCandidate.apertureId || isRunning || validatingCandidateId !== null} onClick={() => { if (selectedCandidate) createRoi(selectedCandidate, true) }}>정밀해석 준비</Button>
            <Button className="col-span-2" size="sm" variant="outline" disabled={!selectedCandidate || !selectedCandidate.apertureId || isRunning || validatingCandidateId !== null} onClick={() => { if (selectedCandidate) createRoi(selectedCandidate, false) }}>ROI·Receiver 생성</Button>
          </div>
        </div>
      </div>
      {comparisonRequest ? <SensorComparisonDialog key={`${scene?.metadata.scene_token}:${activeCadCaseId}`} request={comparisonRequest} onClose={() => setComparisonRequest(null)} /> : null}
      <CollapsibleSection title="광원 설정">
        <div className="flex items-center gap-1 text-sm font-semibold">Preview Light Source
          <HelpTooltip label="Preview 광원 도움말">
            기존 Emitter 사용은 Ray Tracing에 등록된 활성 광원을 선택해 Face·Body·DATUM, 출력, 방향과 Aim을 재사용합니다.
            Preview의 품질 설정에 맞춰 광선 수만 배분하며 원본 설정은 변경하지 않습니다.
            CAD Face를 다시 선택하면 기존 선택을 유지하여 추가·해제할 수 있습니다.
          </HelpTooltip>
        </div>
        <div className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-muted/25 p-1">
          {(['face', 'body'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              disabled={isRunning || pickingTarget === 'source'}
              className={cn(
                'rounded-md px-2 py-1.5 text-sm font-medium transition-colors',
                sourceMode === mode ? 'bg-primary text-primary-foreground' : 'hover:bg-muted',
              )}
              onClick={() => setSourceMode(mode)}
            >
              {mode === 'face' ? 'CAD Face' : 'Body'}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant={pickingTarget === 'source' ? 'default' : 'outline'}
            disabled={isRunning || sourceMode === 'emitters'}
            onClick={() => {
              if (pickingTarget === 'source') {
                void finishFaceSelection()
                return
              }
              finishIgnoreAreaSelection()
              finishBlockerAreaSelection()
              actions.setRoiBoxSelectionArmed(false)
              if (sourceMode === 'face') {
                actions.setFaceSelection(sourceFaceIds, [...new Set(sourceFaceIds.flatMap((faceId) => {
                  const componentId = scene.mesh.face_component_ids[faceId]
                  return componentId == null ? [] : [componentId]
                }))])
                actions.setEmitterSelectionTarget('surface')
              } else {
                actions.setSelectedComponentIds(sourceComponentIds)
              }
              actions.setEmitterFaceSelectionArmed(sourceMode === 'face')
              setPickingTarget('source')
              setMessage(`3D Viewer에서 광원 ${sourceMode === 'face' ? 'CAD Face' : 'Body'}를 선택한 뒤 선택 완료를 누르세요.`)
            }}
          >
            {sourceMode === 'face' ? <LocateFixed /> : <Cuboid />}
            {pickingTarget === 'source' ? '선택 완료' : sourceMode === 'emitters' ? 'CAD 선택' : `${sourceMode === 'face' ? 'CAD Face' : 'Body'} 선택`}
          </Button>
          <Button
            disabled={isRunning || availableEmitters.length === 0 || pickingTarget === 'source'}
            variant={sourceMode === 'emitters' ? 'default' : 'outline'}
            onClick={() => {
              setSourceEmitters(availableEmitters.map((emitter) => emitter.emitter_id))
              clearDetection()
              setMessage(`Ray Tracing Emitter ${availableEmitters.length}개를 사용합니다.`)
            }}
          >
            기존 Emitter 사용
          </Button>
        </div>
        <div className="rounded-md bg-muted/45 px-2.5 py-2 text-xs">
          등록된 광원 {sourceMode === 'emitters'
            ? <>Emitter <strong>{selectedEmitters.length}</strong>개</>
            : sourceMode === 'body'
            ? <><strong>Body {sourceComponentIds.length}</strong>개 · 가상 Plane <strong>{sourceComponentIds.length}</strong>개</>
            : <>CAD Face <strong>{sourceCadFaceCount}</strong>개</>}
        </div>
        {sourceMode === 'emitters' ? <CollapsibleSection title="등록 광원 목록" count={availableEmitters.length} defaultOpen={availableEmitters.length <= 6}><fieldset className="space-y-1" disabled={isRunning}>
          {availableEmitters.map((emitter) => <label key={emitter.emitter_id} className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={sourceEmitterIds.includes(emitter.emitter_id)}
              onChange={(event) => setSourceEmitters(event.currentTarget.checked
                ? [...sourceEmitterIds, emitter.emitter_id]
                : sourceEmitterIds.filter((id) => id !== emitter.emitter_id))} />
            {rayObjectDisplayName('emitter', emitter.emitter_id)}
          </label>)}
        </fieldset></CollapsibleSection> : null}
        {sourceMode === 'emitters' && sourceEmitterIds.length > selectedEmitters.length
          ? <div role="alert" className="text-xs text-amber-700 dark:text-amber-400">삭제·비활성화된 Emitter가 있습니다. 기존 Emitter 사용을 다시 눌러 선택하세요.</div> : null}
        {hasBsdf && detectionMode !== 'escape_directions' ? <div className="flex items-center gap-1 text-xs font-medium">BSDF · CPU / Source sampling
          <HelpTooltip label="Preview BSDF 계산 도움말">BSDF 재질을 유지하여 CPU와 Source sampling으로 계산합니다. Preview에만 적용되며 Ray Tracing 설정은 변경하지 않습니다. Receiver MIS보다 약한 빛샘 탐색에 더 많은 광선이 필요할 수 있습니다.</HelpTooltip>
        </div> : null}
      </CollapsibleSection>

      <CollapsibleSection title="검출 방향">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-sm font-semibold">Detection Direction
            <HelpTooltip label="Preview 검출 방향 도움말">유출 방향: Ray Tracing에서 선택한 CPU 또는 GPU를 사용하며, GPU 선택 시 교차 판정은 CUDA BVH, 방향·광량 집계는 CPU에서 수행합니다. 모델 전체의 경계 상자를 최종적으로 벗어나는 광선을 선택 축 기준 0~90°로 집계합니다. 반구 반경이나 화면 카메라에 의존하지 않습니다. 검출 각도와 집계는 경계 상자 출구를 기준으로 하며, 점은 마지막 유출 구간의 실제 출발 위치에 표시합니다. 이전 결과에 출발 위치가 없으면 기존 경계 위치를 사용합니다. 평면: 기존 유한 사각 Receiver 방식입니다. 두 방식 모두 광선 수와 최대 반사 횟수에 따라 미약한 빛샘을 놓칠 수 있습니다.</HelpTooltip>
          </span>
          <span className="text-xs text-muted-foreground">{detectionMode === 'escape_directions' ? '0–90°' : '1 View'}</span>
        </div>
        <select aria-label="Preview 검출 방식" className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
          value={detectionMode} disabled={isRunning} onChange={(event) => { setDetectionMode(event.currentTarget.value === 'planar' ? 'planar' : 'escape_directions'); if (event.currentTarget.value === 'planar') setObserver({ search_mode: 'preview' }) }}>
          <option value="escape_directions">유출 방향 · Source sampling</option>
          <option value="planar">평면 Receiver</option>
        </select>
        <div className="grid grid-cols-3 gap-1.5">
          {previewDirectionOptions.map((option) => {
            const selected = directions.includes(option.id)
            return (
              <Button
                key={option.id}
                size="sm"
                variant={selected ? 'default' : 'outline'}
                disabled={isRunning}
                onClick={() => toggleDirection(option.id)}
              >
                {option.label} <span className="text-[10px] opacity-70">{option.axis}</span>
              </Button>
            )
          })}
        </div>
      </CollapsibleSection>

      {detectionMode === 'escape_directions' ? <CollapsibleSection title="관찰 설정">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-sm font-semibold">시야각 역추적
            <HelpTooltip label="시야각 역추적 도움말">선택한 방향을 정면 0°로 놓은 평행 관찰 검사입니다. 후면 -Z에서는 좌우 +가 +X, 상하 +가 +Y 방향입니다. 입력 범위는 ±89.9°입니다. 주변 범위는 좌우·상하 각각 ±값이며 중심·끝점과 지정 간격을 검사합니다. 반사점마다 광원 연결과 다중 반사 경로를 탐색하고 실제 발광·반사·BSDF 방향 조건을 확인합니다. 역추적은 Ray Tracing에서 선택한 CPU/GPU를 사용합니다. GPU는 CAD 교차 판정을 배치 처리하며 발광·반사·BSDF 경로 검사는 CPU에서 수행합니다. 결과의 역추적 Compute 상태와 CUDA 성공 수를 확인하세요. 경로 탐색의 경로 수는 lm·nit가 아닙니다. 관찰 휘도 모드는 별도 확률 가중치로 셀 평균 nit를 추정합니다. 해상도와 셀 반복을 높이면 좁은 틈을 찾는 데 도움이 되지만 미검출은 빛샘 없음의 증명이 아닙니다. 미지정 재질은 Preview 기본 재질을 사용합니다. ROI에는 관찰 조건을 저장하며 일반 정밀해석 Receiver는 같은 방향의 모델 외부 관찰 평면에 생성되는 유한 면적 수광기입니다. 이 ROI가 활성화된 정밀해석은 외부 차폐·반사 경로를 유지하기 위해 전체 CAD를 사용합니다.</HelpTooltip>
          </span>
          <label className="flex items-center gap-1 text-xs"><input aria-label="시야각 역추적 사용" type="checkbox" checked={observer.enabled} disabled={isRunning} onChange={(event) => setObserver({ enabled: event.currentTarget.checked, ...(!event.currentTarget.checked ? { search_mode: 'preview' as const } : {}) })} />ON</label>
        </div>
        {observer.enabled ? <>
          <label className="block space-y-1 text-xs"><span className="flex items-center gap-1">검출 기준<HelpTooltip label="관찰 휘도 도움말">관찰 휘도는 평행 관찰 영상의 셀 평균 추정값(nit)입니다. 광원 출력, 반사율, BSDF와 샘플 확률을 반영합니다. 기본 필터 0.01 nit는 탐색용 설정이며 육안 가시성 표준이 아닙니다. 실제 광원·재질 보정과 투과 모델은 별도 검증이 필요합니다. 경로 탐색은 기존 방향 조건 검사이며 밝기가 아닙니다. 미지원 Aim·배광은 오류로 알리며 다른 광원으로 대체하지 않습니다.</HelpTooltip></span><select aria-label="역추적 검출 기준" className="h-8 w-full rounded border border-border bg-background px-2" disabled={isRunning} value={observer.measurement ?? 'paths'} onChange={(event) => setObserver({ measurement: event.currentTarget.value as 'paths' | 'radiance' })}><option value="radiance">관찰 휘도</option><option value="paths">경로 탐색</option></select></label>
          {observer.measurement === 'radiance' ? <label className="block space-y-1 text-xs"><span>최소 휘도 (nit)</span><NumberInput aria-label="최소 관찰 휘도 (nit)" className="h-8 w-full rounded border border-border bg-background px-2" value={observer.min_luminance_nit ?? .01} min={0} step={.01} disabled={isRunning} onValueChange={(value) => setObserver({ min_luminance_nit: Math.max(0, value) })} /></label> : null}
          <label className="flex items-center gap-1 text-xs"><input aria-label="각도 중간 탐색" type="checkbox" checked={observer.refine_angles ?? false} disabled={isRunning} onChange={(event) => setObserver({ refine_angles: event.currentTarget.checked })} />각도 중간 탐색<HelpTooltip label="각도 중간 탐색 도움말">검출이 0인 영역도 포함해 각도 간격을 절반으로 나눕니다. 방향 수와 계산량이 증가하며 더 좁은 각도의 미검출을 없애는 보장은 아닙니다.</HelpTooltip></label>
          <div className="block space-y-1 text-xs"><span className="flex items-center gap-1">각도 영역<HelpTooltip label="180도 전체 영역 도움말">선택한 도면 기준 방향을 중심으로 좌우 −90°~+90°, 상하 −90°~+90°의 반구를 검사합니다. 극점은 한 번만 검사합니다. 각도 간격 이하로 균등 분할하며 기본 간격은 15°입니다. 연속된 모든 각도를 검사하는 것은 아니므로 간격 사이의 좁은 빛샘은 놓칠 수 있습니다.</HelpTooltip></span><select aria-label="각도 영역" className="h-8 w-full rounded border border-border bg-background px-2" disabled={isRunning} value={observer.coverage ?? 'local'} onChange={(event) => setObserver({ coverage: event.currentTarget.value as 'local' | 'hemisphere', step_deg: event.currentTarget.value === 'hemisphere' ? 15 : 1 })}><option value="local">지정 각도 주변</option><option value="hemisphere">180° 전체 영역</option></select></div>
          <div className="grid grid-cols-2 gap-2">
            {([
              ['horizontal_deg', '좌우 시야각 (°)', -89.9, 89.9, .1],
              ['vertical_deg', '상하 시야각 (°)', -89.9, 89.9, .1],
              ['range_deg', '주변 범위 (±°)', 0, 10, .1],
              ['step_deg', '각도 간격 (°)', .1, 90, .1],
            ] as const).filter(([key]) => observer.coverage !== 'hemisphere' || key === 'step_deg').map(([key, label, min, max, step]) => <label key={key} className="space-y-1 text-xs">
              <span>{label}</span><NumberInput aria-label={label} className="h-8 w-full rounded border border-border bg-background px-2" value={observer[key]} min={min} max={max} step={step} disabled={isRunning}
                onValueChange={(value) => setObserver({ [key]: Math.max(min, Math.min(max, value)) })} />
            </label>)}
          </div>
          {observerPlan.fullDetail ? <label className="block space-y-1 text-xs"><span className="flex items-center gap-1">검사 간격 (mm)<HelpTooltip label="전체 정밀 검색 간격 도움말">관찰 평면의 가로·세로 검사 간격입니다. 모델 전체를 이 간격 이하의 격자로 검사합니다. 경로 탐색은 두 위치와 광원 투영 표본을, 관찰 휘도는 칸마다 8개의 계층화 표본을 사용합니다. 휘도 계산에는 투영 표본을 그대로 광량으로 합산하지 않습니다. 각도가 많아도 간격을 넓히지 않습니다. 작게 지정할수록 계산량이 증가하며 좁은 틈의 검출을 보장하지 않습니다.</HelpTooltip></span><NumberInput aria-label="전체 정밀 검사 간격 (mm)" className="h-8 w-full rounded border border-border bg-background px-2" value={observer.spacing_mm ?? 2} min={.1} max={100} disabled={isRunning} onValueChange={(value) => setObserver({ spacing_mm: Math.max(.1, Math.min(100, value)) })} /></label> : <div className="space-y-1">
            <span className="flex items-center gap-1 text-xs font-medium">검사 수준<HelpTooltip label="역추적 검사 수준 도움말">각도 수와 광원 수에 맞춰 해상도·셀 반복을 자동 배분합니다. 빠르게는 위치 탐색, 기본은 일반 확인, 세밀하게는 추가 탐색용입니다. 등급은 정확도 보장이 아니며 미검출은 빛샘 없음의 판정이 아닙니다. 각도 간격은 임의로 바꾸지 않습니다.</HelpTooltip></span>
            <div className="grid grid-cols-3 gap-1">{([['fast', '빠르게'], ['balanced', '기본'], ['fine', '세밀하게']] as const).map(([sampling, label]) => <Button key={sampling} size="sm" variant={(observer.sampling ?? 'manual') === sampling ? 'default' : 'outline'} aria-pressed={(observer.sampling ?? 'manual') === sampling} disabled={isRunning} onClick={() => setObserver({ sampling })}>{label}</Button>)}</div>
          </div>}
          {!observerPlan.error ? <div className="text-xs text-muted-foreground">{observerPlan.fullDetail ? '전체 정밀 검색' : observer.sampling && observer.sampling !== 'manual' ? '자동 배분' : '직접 설정'} · {observerPlan.views}방향 · 예상 표본 {observerPlan.plannedCount.toLocaleString()}개</div> : <div className="space-y-1"><div role="alert" className="text-xs text-amber-700 dark:text-amber-400">{observerPlan.error}</div>{(observerPlan.views > 121 || observer.coverage === 'hemisphere') ? <Button size="sm" variant="outline" disabled={isRunning} onClick={() => setObserver({ step_deg: observerPlan.suggestedStep })}>간격 자동 조정 · {observerPlan.suggestedStep}°</Button> : null}</div>}
          {!observerPlan.fullDetail ? <details className="rounded-md border border-border p-2">
            <summary className="cursor-pointer text-xs font-medium">고급 설정</summary>
            <div className="mt-2 space-y-2">
              <label className="flex items-center gap-1 text-xs"><input aria-label="역추적 직접 설정" type="checkbox" checked={(observer.sampling ?? 'manual') === 'manual'} disabled={isRunning} onChange={(event) => setObserver({ sampling: event.currentTarget.checked ? 'manual' : 'balanced', resolution: observerPlan.resolution, samples_per_cell: observerPlan.samples_per_cell })} />직접 설정<HelpTooltip label="역추적 샘플링 도움말">해상도/축은 모델 투영 영역의 가로·세로 격자 수입니다. 셀 반복은 각 칸에서 검사할 위치 수로, 경로 탐색의 첫 검사는 중심이고 추가 검사는 칸 안의 다른 위치입니다. 관찰 휘도는 칸을 나눈 계층화 무작위 표본으로 픽셀 평균을 추정합니다. 해상도를 두 배로 하면 격자 표본은 네 배, 반복을 두 배로 하면 두 배가 됩니다.</HelpTooltip></label>
              <div className="grid grid-cols-2 gap-2">{([['resolution', '해상도 / 축', 8, 256], ['samples_per_cell', '셀 반복', 1, 16]] as const).map(([key, label, min, max]) => <label key={key} className="space-y-1 text-xs"><span>{label}</span><NumberInput aria-label={label} className="h-8 w-full rounded border border-border bg-background px-2" value={observerPlan[key]} min={min} max={max} disabled={isRunning || (observer.sampling ?? 'manual') !== 'manual'} onValueChange={(value) => setObserver({ [key]: Math.round(Math.max(min, Math.min(max, value))) })} /></label>)}</div>
            </div>
          </details> : <div className="text-xs">정밀 CAD · 최대 반사 {rayTraceConfig.max_depth}회 · 실제 지정 재질</div>}
          <div className="flex gap-1"><Button size="sm" variant="outline" disabled={isRunning} onClick={() => setObserver({ coverage: 'local', horizontal_deg: 85, vertical_deg: 0, range_deg: 4, step_deg: 2 })}>좌우 극각</Button>
            <Button size="sm" variant="outline" disabled={isRunning} onClick={() => setObserver({ coverage: 'local', horizontal_deg: 0, vertical_deg: 85, range_deg: 4, step_deg: 2 })}>상하 극각</Button></div>
          <div className="text-xs text-muted-foreground">역추적 {rayTraceConfig.compute_backend === 'gpu_cuda' ? 'NVIDIA GPU 요청' : 'CPU'} · 직광 / 반사광</div>
        </> : null}
      </CollapsibleSection> : null}

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-semibold">{observerPlan.fullDetail ? '광원 추적 표본' : 'Preview Quality'}</span>
          <span className="text-xs text-muted-foreground">
            {observerPlan.fullDetail ? `${previewQualityLabel[quality].split(' · ')[0]} · ${rayTraceConfig.max_depth} Reflections` : previewQualityLabel[quality]}
          </span>
        </div>
        <label className="block space-y-1 text-xs"><span className="flex items-center gap-1">검색 모드<HelpTooltip label="Preview 검색 모드 도움말">전체 정밀 검색은 ROI를 적용하지 않고 전체 정밀 CAD와 지정 재질·BSDF로 관찰 경로를 찾습니다. 미지정 면은 흡수체로 처리합니다. 지정 검사 간격을 유지하며 경로 탐색은 두 위치, 관찰 휘도는 8개의 계층화 위치를 사용합니다. 각도 수에 따라 검사 간격을 넓히지 않습니다. Preview Quality의 광원 추적 표본은 별도로 계산합니다. 긴 계산은 중단할 수 있고 완료된 부분 결과를 확인할 수 있습니다. 경로 검출 수는 lm·nit가 아니며 광량 정밀해석과 구분합니다.</HelpTooltip></span><select aria-label="Preview 검색 모드" className="h-8 w-full rounded border border-border bg-background px-2" value={observer.search_mode ?? 'preview'} disabled={isRunning} onChange={(event) => { const full = event.currentTarget.value === 'full_detail'; setObserver({ search_mode: full ? 'full_detail' : 'preview', ...(full ? { enabled: true } : {}) }); if (full) setDetectionMode('escape_directions') }}><option value="preview">Preview</option><option value="full_detail">전체 정밀 검색</option></select></label>
        <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-muted/25 p-1">
          {(['fast', 'balanced', 'deep'] as LeakPreviewQuality[]).map((value) => (
            <button
              key={value}
              type="button"
              disabled={isRunning}
              className={cn(
                'rounded-md px-2 py-1.5 text-sm font-medium transition-colors',
                quality === value ? 'bg-primary text-primary-foreground' : 'hover:bg-muted',
              )}
              onClick={() => setQuality(value)}
            >
              {observerPlan.fullDetail ? previewQualityLabel[value].split(' · ')[0] : previewQualityName[value]}
            </button>
          ))}
        </div>
        {isRunning && jobId ? (
          <Button
            className="w-full"
            variant="destructive"
            disabled={stopMutation.isPending}
            onClick={() => void stopPreview()}
          >
            <StopCircle /> Preview Stop
          </Button>
        ) : (
          <Button className="w-full" disabled={isRunning || !hasPreviewSource || (detectionMode === 'escape_directions' && observer.enabled && observerPlan.error !== null)} onClick={() => void runPreview()}>
            <Play /> {canResumePreview ? '휘도 역추적 이어 실행' : observerPlan.fullDetail && observer.enabled ? '전체 정밀 검색 시작' : 'Run Leak Preview'}
          </Button>
        )}
        {canResumePreview && !isRunning ? <div className="flex items-center gap-2 text-xs"><span>중단 지점 보관</span><HelpTooltip label="Preview 이어 실행 도움말">같은 입력이면 광원 추적 결과와 관찰 휘도 역추적의 각도·셀·집계 전 Ray 상태를 유지해 이어갑니다. 변경된 입력으로 실행하거나 다른 해석 시작, 서버 종료, 작업 이력 정리 시 보관 상태는 해제됩니다. 보관 중에는 계산 메모리를 유지합니다. 경로 탐색 모드는 대상이 아닙니다.</HelpTooltip><Button size="sm" variant="outline" onClick={() => void runPreview(true)}>Preview 처음부터</Button></div> : null}
        {queryError ? <div className="space-y-1 text-xs text-destructive"><div>{queryError}</div><Button size="sm" variant="outline" onClick={() => void jobQuery.refetch()}>상태 다시 조회</Button></div> : null}
        <div className="flex items-center justify-between rounded-md border border-amber-300/70 bg-amber-50/60 px-2.5 py-2 dark:border-amber-700/60 dark:bg-amber-950/20">
          <span className="text-xs font-semibold">Leak Visualization</span>
          <Button
            size="sm"
            variant={visualizationVisible ? 'default' : 'outline'}
            disabled={previewPointCount === 0}
            onClick={() => setVisualizationVisible(!visualizationVisible)}
          >
            {visualizationVisible ? <Eye /> : <EyeOff />}
            {visualizationVisible ? 'Hide' : 'Show'}
          </Button>
        </div>
      </section>



      {previewResult && resultRestored ? <div role="status" className="text-xs text-muted-foreground">저장된 Preview 결과</div> : null}
      {previewResult ? <ComputeExecutionStatus configuredBackend={previewResult.config.compute_backend ?? 'cpu'}
        performance={(previewResult.metrics._performance_summary ?? {}) as Record<string, unknown>} /> : null}
      {observerSummary ? <section className="space-y-2 rounded-lg border border-border p-3">
        <div className="flex items-center gap-1 text-sm font-semibold">시야각 역추적<HelpTooltip label="역추적 결과 도움말">경로 탐색의 검출 수는 방향 조건을 만족한 경로 수입니다. 관찰 휘도는 모델과 샘플링에 따른 셀 평균 추정값이며 실측 보정 전입니다. 휘도 모드의 직광·반사광 수는 필터 후 표시 셀의 양수 기여 표본 수입니다. 새 결과는 직광이 포함된 표본과 반사만 있는 표본을 구분하고, 대표 경로는 직광이 확인되면 직광을 우선 보존합니다. 전체 휘도는 직광·반사 기여를 모두 합산합니다. 기존 유출 광량에 합산하지 않습니다. 같은 경로의 다른 탐색 표본이 포함될 수 있습니다. 위치는 처음 보이는 CAD 면 또는 광원 면입니다. 검사 간격보다 좁은 틈과 샘플링되지 않은 반사 경로는 놓칠 수 있습니다. 최대 반사 횟수에서 끝난 경로가 있으면 반사 횟수를 높여 재검사할 수 있습니다.</HelpTooltip></div>
        <ComputeExecutionStatus samplingScope="observer" configuredBackend={observerSummary.requested_backend ?? 'cpu'} performance={observerSummary.performance ?? { compute_execution_state: 'cpu' }} />
        <div className="text-xs">{observerSummary.complete ? '탐색 완료' : '중지 · 부분 결과'} · {observerSummary.attempted_count.toLocaleString()} / {observerSummary.planned_count.toLocaleString()} 경로 · 제외 {observerSummary.excluded_count.toLocaleString()}</div>
        <div className="text-xs">{observerSummary.path_classification_contract === 'direct_presence_priority_v1' ? '직광 포함' : '직광'} {observerSummary.direct_count.toLocaleString()} · {observerSummary.path_classification_contract === 'direct_presence_priority_v1' ? '반사만' : '반사광'} {observerSummary.reflected_count.toLocaleString()} · 반사 한도 종료 {observerSummary.depth_limited_count.toLocaleString()}</div>
        {observerSummary.path_validation_summary ? <div role={observerSummary.path_validation_summary.invalid ? 'alert' : 'status'} className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">대표 경로 검증 · 실패 {observerSummary.path_validation_summary.invalid} · 가상 광원 {observerSummary.path_validation_summary.virtual_emitter} · 외관 미확정<HelpTooltip label="대표 경로 검증 도움말">현재 정밀 형상과 Block으로 대표 경로의 차폐·반사면·발광 위치를 CPU에서 다시 검사합니다. 실패한 경로는 후보에서 제외하고, 가상 발광면의 공중 좌표는 Leak 점으로 표시하지 않습니다. 유효한 관찰 면과 실제 외관의 틈·홀 위치는 서로 다르며, 외관 개구부 좌표를 확정했다는 뜻은 아닙니다.</HelpTooltip></div> : null}
        {observerSummary.measure === 'luminance_cd_m2' && observerSummary.path_classification_contract !== 'direct_presence_priority_v1' ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">이전 경로 분류 · Preview 재실행 필요</div> : null}
        {observerSummary.measure === 'luminance_cd_m2' ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">평행 관찰 휘도 추정 · 실측 미보정 · 투과 미포함</div> : null}
        {observerSummary.peak_luminance_before_threshold_nit !== undefined ? <div className="flex items-center gap-1 text-xs">필터 전 최고 {observerSummary.peak_luminance_before_threshold_nit.toExponential(3)} nit · 휘도 미달 제외 {observerSummary.filtered_count ?? 0}표본<HelpTooltip label="휘도 미검출 진단 도움말">완료된 셀 중 Allowed 영역을 제외한 뒤의 평균 휘도 최댓값이며, 최소 휘도 필터를 적용하기 전 값입니다. 0은 이번 표본에서 양수 기여를 얻지 못했다는 뜻입니다. 경로 탐색은 광원 투영 표본도 사용하지만 관찰 휘도는 균일 관찰 면적을 확률 적분하므로 좁은 틈은 경로가 있어도 표본에서 놓칠 수 있습니다. 경로 수를 nit로 바꾸거나 발견 경로를 광량에 강제로 더하지 않습니다.</HelpTooltip></div> : null}
        {observerSummary.display_truncated ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">표시 한도 초과 · 밝은 셀 우선 표시</div> : null}
        {observerSummary.views.some((view) => (view.display_stride ?? 1) > 1) ? <div role="status" className="text-xs">검출점 묶음 표시 · 검사 간격 유지<HelpTooltip label="검출점 묶음 표시 도움말">메모리를 제한하기 위해 가까운 표시 셀을 묶었습니다. 지정 간격으로 전체 검사를 수행하며 검출 경로 수는 유지합니다. 표시 위치는 묶인 표본의 평균이고 개별 틈이 한 후보로 합쳐질 수 있습니다.</HelpTooltip></div> : null}
        {observerSummary.measure === 'luminance_cd_m2' ? <label className="flex items-center justify-end gap-1 text-xs">정렬<select aria-label="시야각 결과 정렬" className="h-7 rounded border border-border bg-background px-1" value={observerSort} onChange={(event) => setObserverSort(event.currentTarget.value as 'brightness' | 'angle')}><option value="brightness">휘도 높은 순</option><option value="angle">각도순</option></select></label> : null}
        <CollapsibleSection title="시야각 결과 목록" count={observerViews.length} defaultOpen={observerViews.length <= 8}><div className="max-h-48 overflow-auto"><table aria-label="시야각 역추적 결과" className="w-full text-right text-xs"><thead><tr><th>좌우 / 상하 (°)</th><th>직광</th><th>반사광</th>{observerSummary.measure === 'luminance_cd_m2' ? <th>최고 (nit)</th> : null}</tr></thead><tbody>{observerViews.map(({view,index}) => <tr key={index} className={cn(highlight?.kind === 'observer' && highlight.horizontal === view.horizontal_deg && highlight.vertical === view.vertical_deg && 'bg-orange-500/20')}><td><button type="button" className="w-full py-1 text-right" aria-pressed={highlight?.kind === 'observer' && highlight.horizontal === view.horizontal_deg && highlight.vertical === view.vertical_deg} onClick={() => setHighlight({ kind: 'observer', horizontal: view.horizontal_deg, vertical: view.vertical_deg })}>{view.horizontal_deg} / {view.vertical_deg}</button></td><td>{view.direct_count}</td><td>{view.reflected_count}</td>{observerSummary.measure === 'luminance_cd_m2' ? <td>{view.peak_luminance_nit?.toExponential(2)}</td> : null}</tr>)}</tbody></table></div></CollapsibleSection>
        {observerSummary.direct_count + observerSummary.reflected_count === 0 ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">{observerNoDetectionMessage(observerSummary)}</div> : null}
      </section> : null}
      {angularSummary ? <section className="space-y-2 rounded-lg border border-border p-3">
        <div className="text-sm font-semibold">{previewDirectionOptions.find((item) => item.id === angularSummary.direction)?.label} · 방향별 유출광</div>
        <div className="text-xs text-muted-foreground">선택 방향 {angularSummary.selected_count.toLocaleString()} rays · 제외 {angularSummary.excluded_count.toLocaleString()} rays</div>
        {angularSummary.cells.some((cell) => !cell.angle_bin_counts) ? <div role="status" className="text-xs text-amber-700">각도별 위치 정보 없음 · Preview 재실행 필요</div> : null}
        <table className="w-full text-right text-xs"><thead><tr><th className="text-left">각도</th><th>Rays</th><th>Flux (lm)</th></tr></thead>
          <tbody>{angularSummary.bins.map((bin, index) => <tr key={bin.min_deg} className={cn(highlight?.kind === 'escape' && highlight.bin === index && 'bg-orange-500/20')}><td className="py-1 text-left"><button type="button" className="w-full text-left" aria-pressed={highlight?.kind === 'escape' && highlight.bin === index} disabled={angularSummary.cells.some((cell) => !cell.angle_bin_counts)} onClick={() => setHighlight({ kind: 'escape', bin: index })}>{bin.min_deg}–{bin.max_deg}°</button></td><td>{bin.count.toLocaleString()}</td><td>{bin.flux_lumen.toExponential(3)}</td></tr>)}</tbody>
        </table>
      </section> : null}
      {candidates.length > 0 ? (
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ScanSearch className="size-4 text-orange-500" />
            <Button size="sm" variant="ghost" className="h-7 gap-1 px-1 text-sm font-semibold"
              aria-label={`Leak Candidates ${candidatesCollapsed ? '펼치기' : '접기'}`} aria-expanded={!candidatesCollapsed}
              aria-controls={candidateListId} onClick={() => setCandidatesCollapsed(!candidatesCollapsed)}>
              {candidatesCollapsed ? <Plus className="size-3" /> : <Minus className="size-3" />}Leak Candidates · {candidates.length}
            </Button>
            <HelpTooltip label="후보 도움말">Viewer의 Leak 점을 클릭하면 해당 후보를 강조하고 목록을 펼쳐 이동합니다. 출구를 수동 지정하지 않고, 검증 경로 주변의 서로 반대쪽 CAD 교점을 여러 거리에서 비교해 곡면과 열린 부품 사이 틈의 출구를 자동 추정합니다. 두 거리의 깊이가 일치하고 양쪽 지지가 있을 때만 표시합니다. 깊이 변동은 표본 간 변동이며 실제 위치 오차의 보증 범위가 아닙니다. 출구 추정은 세트 최외곽의 수학적 확정이나 실제 빛샘 검증이 아닙니다. 내부 반사점은 선택한 대표 경로로만 표시합니다. 같은 인식 개구부의 경로는 하나로 묶고, 자동 추정 위치는 인접한 국부 구간별로 묶습니다. 추정 그룹은 동일한 실제 틈이라는 확정이 아닙니다. 관찰자 쪽에서 가장 마지막에 인식된 개구부를 사용하며, 전체 외장 경계임을 보증하지 않습니다. 주변 지지가 부족한 곡면·열린 틈, 여러 외곽 깊이가 섞인 위치, 비정상 메시와 처리 예산 초과는 미확정으로 남기며 점을 만들지 않습니다. 이전 결과에 개구부 정보가 없으면 재해석이 필요합니다. CAD Hide·투명도·단면 표시는 Leak 점의 차폐를 해제하지 않습니다. 활성 CAD와 Block으로 실제 가려지는 점은 표시·클릭에서 제외하며, 해석 제외·삭제 부품은 차폐에서 제외합니다. 셀 평균보다 저장된 실제 경로의 표본 위치를 우선합니다. 선택 시 주황 화살표는 검출된 대표 Ray의 광원→관찰자 경로, 하늘색 화살표는 저장된 경로의 관찰 끝점에서 바깥으로 표시하는 방향 안내이며 광량이나 추가 교차 검사를 뜻하지 않습니다. 인접 위치와 유사한 광원·반사점 경로의 후보를 묶으며 대표 각도로 Receiver를 생성합니다. 경로 탐색의 대표 각도는 가장 밝은 각도가 아닙니다. 휘도 모드는 직광이 확인된 셀의 직광 경로를 우선 보존하며 총 휘도는 모든 유효 기여를 합산합니다. 후보의 추정 최고 휘도는 연속 각도의 최대값이나 가시 범위 경계는 아닙니다. 묶음의 검출 각도 목록에서 각 방향을 확인하세요. 경로 검출은 방향 조건을 만족하는 경로의 존재이며 lm·nit나 육안 가시성 판정이 아닙니다. BSDF의 미약한 산란 성분도 검출될 수 있습니다. 상대 Priority는 광원 추적 후보 간 상대값이며 절대 위험도나 측정값이 아닙니다. 저장된 경로가 없으면 두 종류의 화살표를 표시하지 않습니다. 목록에서 후보를 선택하면 해당 점을 강조합니다. 역추적 후보의 시야각 Receiver 생성은 검출 당시의 기준 축·좌우·상하 각도로 Receiver만 만들고 정밀해석 설정을 엽니다. 다른 수광면이 Ray를 먼저 받지 않도록 해당 Receiver만 ON으로 전환하며 기존 항목은 보존합니다. 처음에는 단일 각도이며 주변 범위·간격은 Receiver 편집에서 변경합니다. ROI·광원·재질·샘플링 설정은 변경하지 않습니다. 삭제하면 현재 결과 목록과 해당 강조점에서 제거합니다. 해석 원본과 각도별 집계, 이미 생성한 ROI·Receiver는 유지합니다. Preview를 다시 실행하면 새 결과에서 다시 검출될 수 있습니다.</HelpTooltip>
          </div>
          <div className="flex flex-wrap items-center gap-1 text-xs"><span>묶음 거리 (mm)</span><NumberInput aria-label="Leak 묶음 거리 (mm)" className="h-7 w-16 rounded border border-border px-1" value={groupDistance} min={1} max={100} onValueChange={value => setGroupDistance(Math.max(1,Math.min(100,value)))} /><HelpTooltip label="Leak 묶음 도움말">근접한 관찰 위치와 비슷한 내부 경로 모양을 묶습니다. 중간 점의 작은 차이와 일직선 위 추가 점을 허용하며 다른 반사 경로는 구분합니다. 표시 목록만 합치고 해석 원본과 점은 유지합니다. 거리를 늘리면 가까운 다른 틈이 합쳐질 수 있으므로 대표 경로를 확인하세요. 되돌리기는 마지막 수동 묶음 전 상태를 복원합니다. 후보를 삭제하면 되돌리기 기록을 해제합니다.</HelpTooltip>
            <Button size="sm" variant="outline" disabled={candidates.length < 2 || isRunning || validatingCandidateId !== null} onClick={() => {
              const state = leakPreviewStore.getState()
              if (!state.result) return
              const grouped = groupObserverCandidates(state.points, state.candidates, groupDistance)
              setGroupUndo(state)
              setDetection(state.result, grouped.points, grouped.candidates)
              const selected = grouped.candidates.find(candidate => candidate.id === state.selectedCandidateId || candidate.sourceCandidateIds?.includes(state.selectedCandidateId ?? ''))
              if (selected) selectCandidate(selected.id, 'viewer')
              setCandidatePage(0)
              setMessage(`경로 묶기 · ${state.candidates.length} → ${grouped.candidates.length}개`)
            }}>유사 경로 묶기</Button>
            <Button size="sm" variant="ghost" disabled={!groupUndo || isRunning || validatingCandidateId !== null} onClick={() => {
              if (groupUndo?.result && groupUndo.sceneToken === leakPreviewStore.getState().sceneToken) {
                setDetection(groupUndo.result, groupUndo.points, groupUndo.candidates)
                selectCandidate(groupUndo.selectedCandidateId, 'viewer')
                setMessage(`경로 묶음 되돌림 · ${groupUndo.candidates.length}개`)
              }
              setGroupUndo(null)
            }}>묶음 되돌리기</Button>
          </div>
          <div id={candidateListId} hidden={candidatesCollapsed} className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">{candidates.slice(safeCandidatePage * 100, (safeCandidatePage + 1) * 100).map((candidate, pageIndex) => {
            const index = safeCandidatePage * 100 + pageIndex
            const selected = candidate.id === selectedCandidateId
            return (
              <article
                key={candidate.id}
                ref={(node) => { if (node) candidateRows.current.set(candidate.id, node); else candidateRows.current.delete(candidate.id) }}
                aria-label={`Candidate ${String(index + 1).padStart(2, '0')}`}
                aria-current={selected ? 'true' : undefined}
                className={cn(
                  'rounded-lg border p-2.5 transition-colors',
                  selected ? 'border-orange-500 bg-orange-50 ring-2 ring-orange-500/50 dark:bg-orange-950/25' : 'border-border bg-background/40',
                )}
                onClick={() => selectCandidate(candidate.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <Button size="icon-xs" variant="ghost" aria-label={`Delete Candidate ${index + 1}`} onClick={(event) => { event.stopPropagation(); setGroupUndo(null); removeCandidate(candidate.id) }}><Trash2 /></Button>
                  <button type="button" aria-pressed={selected} className="min-w-0 flex-1 text-left text-sm font-semibold" onClick={event => { event.stopPropagation(); selectCandidate(candidate.id) }}>Candidate {String(index + 1).padStart(2, '0')} · {candidate.observerEvidence && (candidate.groupedCount ?? 1) > 1 ? `대표 ${candidate.observerEvidence.horizontalDeg}° / ${candidate.observerEvidence.verticalDeg}°` : candidate.label}{(candidate.groupedCount ?? 1) > 1 ? ` · ${candidate.groupedCount}개 묶음` : ''}</button>
                  <span className="text-xs text-muted-foreground">{candidate.apertureMethod ? `출구 추정 · 깊이 변동 ${candidate.apertureVariationMm?.toFixed(2)} mm` : candidate.apertureId ? '개구부 경유' : '개구부 미확정'}</span>
                  <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-xs font-semibold text-orange-700 dark:text-orange-300">
                    {candidate.peakLuminanceNit !== undefined ? `추정 ${candidate.peakLuminanceNit.toExponential(3)} nit` : candidate.observerEvidence ? '경로 검출 · 광량 미평가' : `상대 Priority · ${riskPriority(candidate.relativeStrength)}`}
                  </span>
                </div>
                <details className="mt-1 text-xs" onClick={event => event.stopPropagation()}><summary className="cursor-pointer">상세 정보</summary>
                <div className="mt-1 text-xs text-muted-foreground">
                  {candidate.observerEvidence ? '관찰 면 · 외관 위치 미확정' : 'Candidate Center'} ({candidate.center.map(coordinate).join(', ')}) mm
                </div>
                {candidate.observerEvidence ? <div className="mt-1 text-xs">직광 {candidate.observerAngles?.reduce((sum, angle) => sum + angle.directCount, 0) ?? candidate.observerEvidence.directCount} · 반사광 {candidate.observerAngles?.reduce((sum, angle) => sum + angle.reflectedCount, 0) ?? candidate.observerEvidence.reflectedCount}</div> : null}
                {candidate.peakDirectLuminanceNit !== undefined ? <div className="mt-1 text-xs">성분 최대 · 직광 {candidate.peakDirectLuminanceNit.toExponential(2)} nit · 반사 {candidate.peakReflectedLuminanceNit?.toExponential(2)} nit</div> : null}
                {candidate.observerEvidence && candidate.representativePath ? <div className="mt-1 flex items-center gap-1 text-xs">대표 경로 · {candidate.representativePath.length === 2 ? '직광' : `반사 ${candidate.representativePath.length-2}회`}<HelpTooltip label="직광 분류 도움말">직광은 현재 설정된 발광면에서 관찰자까지 반사 없이 도달한 경로입니다. 새 관찰 휘도 결과는 직광이 하나라도 확인된 표본·셀에서 직광 경로를 우선 보존합니다. 총 휘도에는 반사 성분도 포함되므로 대표 경로의 밝기와 같지 않습니다. 성분 최대는 후보 셀별 직광·반사 평균 휘도의 각각 최댓값이며 서로 다른 셀일 수 있으므로 둘을 더해 후보 최고 휘도로 해석하지 마세요. 확산판 등가 광원의 직광·반사 분류는 실물 LED 기준 분류와 다를 수 있습니다. 직광 0은 표본에서 찾지 못했다는 뜻이며 직광이 없다는 증거가 아닙니다. 이전 저장 결과는 재실행해야 새 분류를 적용합니다.</HelpTooltip></div> : null}
                {candidate.observerEvidence && (candidate.groupedCount ?? 1) > 1 && !candidate.observerAngles ? <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">각도별 묶음 정보 없음 · Preview 재실행 필요</div> : null}
                {candidate.observerAngles && candidate.observerAngles.length > 1 ? <details className="mt-1 text-xs"><summary className="cursor-pointer">검출 각도 · {candidate.observerAngles.length}방향</summary><div className="mt-1 max-h-32 overflow-auto">{candidate.observerAngles.map((angle) => <div key={`${angle.horizontalDeg}:${angle.verticalDeg}`}>좌우 {angle.horizontalDeg}° · 상하 {angle.verticalDeg}° · 직광 {angle.directCount} · 반사광 {angle.reflectedCount}</div>)}</div></details> : null}
                {candidate.sampledPathCount > 0 ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    Sampled paths {candidate.sampledPathCount} · Reflections {' '}
                    {candidate.minReflectionCount === candidate.maxReflectionCount
                      ? candidate.minReflectionCount
                      : `${candidate.minReflectionCount}–${candidate.maxReflectionCount}`}
                    {' '}· 기준축 각도 {candidate.meanExitAngleDeg?.toFixed(1)}°
                    {candidate.grazingPathCount > 0 ? ` · Grazing ${candidate.grazingPathCount}` : ''}
                  </div>
                ) : null}

                </details>
              </article>
            )
          })}{candidatePageCount > 1 ? <div className="flex items-center justify-between gap-2"><Button size="sm" variant="outline" aria-label="이전 후보" disabled={safeCandidatePage === 0} onClick={() => setCandidatePage(safeCandidatePage - 1)}>이전</Button><span className="text-xs">{safeCandidatePage * 100 + 1}–{Math.min(candidates.length, (safeCandidatePage + 1) * 100)} / {candidates.length}</span><Button size="sm" variant="outline" aria-label="다음 후보" disabled={safeCandidatePage === candidatePageCount - 1} onClick={() => setCandidatePage(safeCandidatePage + 1)}>다음</Button></div> : null}</div>
        </section>
      ) : null}
    </div>
  )
}
