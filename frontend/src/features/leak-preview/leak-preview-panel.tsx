import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { BoxSelect, Cuboid, Eye, EyeOff, Lightbulb, LocateFixed, Pencil, Play, Plus, ScanSearch, Square, StopCircle, Trash2, X } from 'lucide-react'

import {
  apiClient,
  apiQueryKeys,
  type ScenePayload,
  useRayTraceJobQuery,
  useStartRayTraceMutation,
  useStopRayTraceMutation,
} from '@/api'
import { ComputeExecutionStatus } from '@/features/results/compute-execution-status'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { createFaceEmitter, nextSpecId, rayObjectDisplayName } from '@/features/raytracing'
import {
  groupRoiFacesByComponent,
} from '@/features/roi'
import { cn } from '@/lib/utils'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'

import {
  buildLeakPreviewRequest,
  escapeDirectionSummary,
  observerProbeSummary,
  createLeakPreviewBlockerFromFaces,
  createLeakPreviewBodyPlaneEmitters,
  createCandidateReceiver,
  detectLeakPreviewCandidates,
  resolveLeakPreviewRoiFaces,
  type LeakPreviewCandidate,
  type LeakPreviewDirection,
  type LeakPreviewQuality,
} from './leak-preview-model'
import { useLeakPreviewStore } from './leak-preview-store'

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
  const queryClient = useQueryClient()
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
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
  const ensureScene = useLeakPreviewStore((state) => state.ensureScene)
  const rebindSceneToken = useLeakPreviewStore((state) => state.rebindSceneToken)
  const detectionMode = useLeakPreviewStore((state) => state.detectionMode)
  const observer = useLeakPreviewStore((state) => state.observer)
  const setObserver = useLeakPreviewStore((state) => state.setObserver)
  const setDetectionMode = useLeakPreviewStore((state) => state.setDetectionMode)
  const previewResult = useLeakPreviewStore((state) => state.result)
  const observerSummary = previewResult ? observerProbeSummary(previewResult) : null
  const angularSummary = previewResult ? escapeDirectionSummary(previewResult) : null
  const quality = useLeakPreviewStore((state) => state.quality)
  const directions = useLeakPreviewStore((state) => state.directions)
  const jobId = useLeakPreviewStore((state) => state.jobId)
  const candidates = useLeakPreviewStore((state) => state.candidates)
  const previewPointCount = useLeakPreviewStore((state) => state.points.length)
  const visualizationVisible = useLeakPreviewStore((state) => state.visualizationVisible)
  const ignoreAreasVisible = useLeakPreviewStore((state) => state.ignoreAreasVisible)
  const blockersVisible = useLeakPreviewStore((state) => state.blockersVisible)
  const selectedCandidateId = useLeakPreviewStore((state) => state.selectedCandidateId)
  const ignoreAreaSelectionArmed = useLeakPreviewStore((state) => state.ignoreAreaSelectionArmed)
  const activeIgnoreAreaId = useLeakPreviewStore((state) => state.activeIgnoreAreaId)
  const ignoreAreas = useLeakPreviewStore((state) => state.ignoreAreas)
  const blockers = useLeakPreviewStore((state) => state.blockers)
  const blockerAreaSelectionId = useLeakPreviewStore((state) => state.blockerAreaSelectionId)
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
  const setIgnoreAreasVisible = useLeakPreviewStore((state) => state.setIgnoreAreasVisible)
  const setBlockersVisible = useLeakPreviewStore((state) => state.setBlockersVisible)
  const selectCandidate = useLeakPreviewStore((state) => state.selectCandidate)
  const beginIgnoreAreaSelection = useLeakPreviewStore((state) => state.beginIgnoreAreaSelection)
  const finishIgnoreAreaSelection = useLeakPreviewStore((state) => state.finishIgnoreAreaSelection)
  const removeCandidate = useLeakPreviewStore((state) => state.removeCandidate)
  const setIgnoreAreaVisible = useLeakPreviewStore((state) => state.setIgnoreAreaVisible)
  const setIgnoreAreaEnabled = useLeakPreviewStore((state) => state.setIgnoreAreaEnabled)
  const removeIgnoreArea = useLeakPreviewStore((state) => state.removeIgnoreArea)
  const addBlocker = useLeakPreviewStore((state) => state.addBlocker)
  const updateBlocker = useLeakPreviewStore((state) => state.updateBlocker)
  const removeBlocker = useLeakPreviewStore((state) => state.removeBlocker)
  const beginBlockerAreaSelection = useLeakPreviewStore((state) => state.beginBlockerAreaSelection)
  const finishBlockerAreaSelection = useLeakPreviewStore((state) => state.finishBlockerAreaSelection)
  const startMutation = useStartRayTraceMutation()
  const stopMutation = useStopRayTraceMutation()
  const jobQuery = useRayTraceJobQuery(jobId)
  const job = jobQuery.data
  const handledRunRef = useRef<string | null>(null)
  const [message, setMessage] = useState('광원으로 사용할 CAD Face를 선택하세요.')
  const [pickingTarget, setPickingTarget] = useState<'source' | 'blocker' | null>(null)
  const [editingBlockerId, setEditingBlockerId] = useState<string | null>(null)
  const [blockerCreationPending, setBlockerCreationPending] = useState(false)

  useEffect(() => {
    if (scene) ensureScene(scene.metadata.scene_token)
  }, [ensureScene, scene])

  const isRunning = startMutation.isPending || job?.status === 'queued' || job?.status === 'running'
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
  const analysisOnly = <T extends { visible?: boolean }>(items: T[]) => items.map(({ visible: _visible, ...item }) => item)
  const inputSignatureForSceneToken = (sceneToken: string | null) => JSON.stringify({
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
    directions,
    materialAssignments,
    bsdfAssets: bsdfAssets.map((asset) => ({ id: asset.id, transport: asset.transport })),
    transformRules,
    excludedComponentIds,
    deletedComponentIds,
    blockers: analysisOnly(blockers),
    allowedAreas: analysisOnly(ignoreAreas),
  })
  const previewInputSignature = inputSignatureForSceneToken(
    scene?.metadata.scene_token ?? null,
  )

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
    if (!scene || !jobId || job?.job_id !== jobId || job.status !== 'completed' || !job.result) return
    if (runSignature && runSignature !== previewInputSignature) return
    if (detectionMode === 'escape_directions' && !escapeDirectionSummary(job.result)) {
      clearDetection()
      setMessage('방향별 유출 집계 결과가 없습니다. 서버를 최신 버전으로 업데이트한 뒤 다시 실행하세요.')
      return
    }
    if (detectionMode === 'escape_directions' && observer.enabled && !observerProbeSummary(job.result)) {
      clearDetection()
      setMessage('역추적 결과가 없습니다. 서버와 화면을 함께 업데이트한 뒤 다시 실행하세요.')
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
      job.phase === 'stopped'
        ? `Preview를 중지했습니다. 중지 시점까지 빛샘 후보 ${detection.candidates.length}개가 검출되었습니다.`
        : detection.candidates.length > 0
        ? `빛샘 후보 ${detection.candidates.length}개를 찾았습니다.`
        : '현재 탐색 조건에서 미검출 · 빛샘 없음 판정이 아닙니다.',
    )
  }, [clearDetection, detectionMode, observer.enabled, ignoreAreas, job, jobId, previewInputSignature, quality, runSignature, scene, setDetection, transformRules])

  useEffect(() => {
    if (job?.status !== 'cancelled') return
    setMessage('Preview를 중지했습니다.')
  }, [job])

  useEffect(() => {
    if (!runSignature || runSignature === previewInputSignature) return
    clearDetection()
    setMessage('Preview 재실행 필요')
  }, [clearDetection, previewInputSignature, runSignature])

  useEffect(() => {
    if (job?.status !== 'failed') return
    setMessage(`Preview 실행 실패: ${job.error}`)
  }, [job])

  const finishFaceSelection = async (target: 'source' | 'blocker') => {
    if (target === 'blocker' && selectedFaceIds.length === 0) {
      setMessage('3D Viewer에서 CAD Face를 하나 이상 선택하세요.')
      return
    }
    if (target === 'blocker') {
      if (!scene) return
      setBlockerCreationPending(true)
      setMessage('선택한 CAD Surface에서 Blocker를 생성하고 있습니다.')
      // Paint the pending state before processing a potentially dense face.
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
      try {
        const blocker = createLeakPreviewBlockerFromFaces(scene, selectedFaceIds, blockers.length + 1)
        if (!blocker) {
          setMessage('하나의 평평한 CAD Surface를 다시 선택하세요.')
          return
        }
        addBlocker(blocker)
        setEditingBlockerId(blocker.id)
        beginBlockerAreaSelection(blocker.id)
        setPickingTarget(null)
        actions.setSelectedFaceIds([])
        actions.setEmitterFaceSelectionArmed(false)
        actions.setRoiBoxSelectionArmed(true)
        clearDetection()
        setMessage(`${blocker.label} 기준 Face 위에서 Blocker 영역을 드래그하세요.`)
      } finally {
        setBlockerCreationPending(false)
      }
      return
    }
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

  const runPreview = async () => {
    if (!scene || !hasPreviewSource) return
    clearDetection()
    handledRunRef.current = null
    setMessage('전체 세트의 외부 유출광을 탐색하고 있습니다.')
    try {
      const start = (requestScene: ScenePayload) => startMutation.mutateAsync({
        request: buildLeakPreviewRequest({
          scene: requestScene,
          sourceFaceIds,
          sourceComponentIds: sourceMode === 'body' ? sourceComponentIds : [],
          quality,
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
        }),
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

  const createRoi = (candidate: LeakPreviewCandidate, precision: boolean) => {
    if (!scene) return
    setMessage('ROI 후보에 포함되는 CAD Face를 찾고 있습니다.')
    const faceIds = resolveLeakPreviewRoiFaces(
      scene,
      candidate.clipBox,
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
      clipBox: candidate.clipBox,
      observerView: candidate.observerEvidence ? {
        horizontalDeg: candidate.observerEvidence.horizontalDeg, verticalDeg: candidate.observerEvidence.verticalDeg,
        direction: candidate.observerEvidence.direction,
      } : undefined,
      point: { x: candidate.center[0], y: candidate.center[1], z: candidate.center[2] },
    })
    const receiverAlreadyExists = receivers.some((receiver) =>
      receiver.reference_mode === 'leak_preview_candidate' &&
      (!candidate.observerEvidence || Math.abs(receiver.normal.reduce((sum, value, axis) => sum + value * candidate.normal[axis], 0) - 1) < 1e-6) &&
      Math.hypot(
        (candidate.observerEvidence ? receiver.center[0] : receiver.base_center?.[0] ?? receiver.center[0]) - (candidate.observerEvidence?.receiverCenter[0] ?? candidate.center[0]),
        (candidate.observerEvidence ? receiver.center[1] : receiver.base_center?.[1] ?? receiver.center[1]) - (candidate.observerEvidence?.receiverCenter[1] ?? candidate.center[1]),
        (candidate.observerEvidence ? receiver.center[2] : receiver.base_center?.[2] ?? receiver.center[2]) - (candidate.observerEvidence?.receiverCenter[2] ?? candidate.center[2]),
      ) < 0.1,
    )
    if (!receiverAlreadyExists) {
      const receiverId = nextSpecId(
        'receiver',
        receivers.map((receiver) => receiver.receiver_id),
      )
      actions.upsertReceiver({
        ...createCandidateReceiver(candidate, candidateIndex),
        receiver_id: receiverId,
        display_name: `Preview Receiver ${candidateIndex}`,
      })
    }
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
      <div className="rounded-lg border border-sky-200 bg-gradient-to-br from-sky-50 to-blue-50 p-3 dark:border-sky-800 dark:from-sky-950/45 dark:to-blue-950/35">
        <div className="flex items-center gap-2 text-sm font-semibold text-sky-900 dark:text-sky-100">
          <Lightbulb className="size-4 text-amber-500" />
          Whole Set Lighting Preview
        </div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          빛샘 Risk 영역 탐색 · ROI/Receiver 자동 생성
        </p>
      </div>

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
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
                void finishFaceSelection('source')
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
        {sourceMode === 'emitters' ? <fieldset className="space-y-1" disabled={isRunning}>
          {availableEmitters.map((emitter) => <label key={emitter.emitter_id} className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={sourceEmitterIds.includes(emitter.emitter_id)}
              onChange={(event) => setSourceEmitters(event.currentTarget.checked
                ? [...sourceEmitterIds, emitter.emitter_id]
                : sourceEmitterIds.filter((id) => id !== emitter.emitter_id))} />
            {rayObjectDisplayName('emitter', emitter.emitter_id)}
          </label>)}
        </fieldset> : null}
        {sourceMode === 'emitters' && sourceEmitterIds.length > selectedEmitters.length
          ? <div role="alert" className="text-xs text-amber-700 dark:text-amber-400">삭제·비활성화된 Emitter가 있습니다. 기존 Emitter 사용을 다시 눌러 선택하세요.</div> : null}
        {hasBsdf && detectionMode !== 'escape_directions' ? <div className="flex items-center gap-1 text-xs font-medium">BSDF · CPU / Source sampling
          <HelpTooltip label="Preview BSDF 계산 도움말">BSDF 재질을 유지하여 CPU와 Source sampling으로 계산합니다. Preview에만 적용되며 Ray Tracing 설정은 변경하지 않습니다. Receiver MIS보다 약한 빛샘 탐색에 더 많은 광선이 필요할 수 있습니다.</HelpTooltip>
        </div> : null}
      </section>

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-sm font-semibold">Detection Direction
            <HelpTooltip label="Preview 검출 방향 도움말">유출 방향: Ray Tracing에서 선택한 CPU 또는 GPU를 사용하며, GPU 선택 시 교차 판정은 CUDA BVH, 방향·광량 집계는 CPU에서 수행합니다. 모델 전체의 경계 상자를 최종적으로 벗어나는 광선을 선택 축 기준 0~90°로 집계합니다. 반구 반경이나 화면 카메라에 의존하지 않습니다. 위치는 경계 상자 출구 기준이며 실제 홀 표면의 정확한 좌표와 다를 수 있습니다. 평면: 기존 유한 사각 Receiver 방식입니다. 두 방식 모두 광선 수와 최대 반사 횟수에 따라 미약한 빛샘을 놓칠 수 있습니다.</HelpTooltip>
          </span>
          <span className="text-xs text-muted-foreground">{detectionMode === 'escape_directions' ? '0–90°' : '1 View'}</span>
        </div>
        <select aria-label="Preview 검출 방식" className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
          value={detectionMode} disabled={isRunning} onChange={(event) => setDetectionMode(event.currentTarget.value === 'planar' ? 'planar' : 'escape_directions')}>
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
      </section>

      {detectionMode === 'escape_directions' ? <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-sm font-semibold">시야각 역추적
            <HelpTooltip label="시야각 역추적 도움말">선택한 방향을 정면 0°로 놓은 평행 관찰 검사입니다. 후면 -Z에서는 좌우 +가 +X, 상하 +가 +Y 방향입니다. 입력 범위는 ±89.9°입니다. 주변 범위는 좌우·상하 각각 ±값이며 중심·끝점과 지정 간격을 검사합니다. 반사점마다 광원 연결과 다중 반사 경로를 탐색하고 실제 발광·반사·BSDF 방향 조건을 확인합니다. 역추적은 Ray Tracing에서 선택한 CPU/GPU를 사용합니다. GPU는 CAD 교차 판정을 배치 처리하며 발광·반사·BSDF 경로 검사는 CPU에서 수행합니다. 결과의 역추적 Compute 상태와 CUDA 성공 수를 확인하세요. 경로 수는 lm·nit가 아닙니다. 해상도와 셀 반복을 높이면 좁은 틈을 찾는 데 도움이 되지만 미검출은 빛샘 없음의 증명이 아닙니다. 미지정 재질은 Preview 기본 재질을 사용합니다. ROI에는 관찰 조건을 저장하며 일반 정밀해석 Receiver는 같은 방향의 모델 외부 관찰 평면에 생성되는 유한 면적 수광기입니다. 이 ROI가 활성화된 정밀해석은 외부 차폐·반사 경로를 유지하기 위해 전체 CAD를 사용합니다.</HelpTooltip>
          </span>
          <label className="flex items-center gap-1 text-xs"><input aria-label="시야각 역추적 사용" type="checkbox" checked={observer.enabled} disabled={isRunning} onChange={(event) => setObserver({ enabled: event.currentTarget.checked })} />ON</label>
        </div>
        {observer.enabled ? <>
          <div className="grid grid-cols-2 gap-2">
            {([
              ['horizontal_deg', '좌우 시야각 (°)', -89.9, 89.9, .1],
              ['vertical_deg', '상하 시야각 (°)', -89.9, 89.9, .1],
              ['range_deg', '주변 범위 (±°)', 0, 10, .1],
              ['step_deg', '각도 간격 (°)', .1, 10, .1],
              ['resolution', '해상도 / 축', 8, 256, 1],
              ['samples_per_cell', '셀 반복', 1, 16, 1],
            ] as const).map(([key, label, min, max, step]) => <label key={key} className="space-y-1 text-xs">
              <span>{label}</span><NumberInput aria-label={label} className="h-8 w-full rounded border border-border bg-background px-2" value={observer[key]} min={min} max={max} step={step} disabled={isRunning}
                onValueChange={(value) => setObserver({ [key]: step === 1 ? Math.round(Math.max(min, Math.min(max, value))) : Math.max(min, Math.min(max, value)) })} />
            </label>)}
          </div>
          <div className="flex gap-1"><Button size="sm" variant="outline" disabled={isRunning} onClick={() => setObserver({ horizontal_deg: 85, vertical_deg: 0, range_deg: 4, step_deg: 2 })}>좌우 극각</Button>
            <Button size="sm" variant="outline" disabled={isRunning} onClick={() => setObserver({ horizontal_deg: 0, vertical_deg: 85, range_deg: 4, step_deg: 2 })}>상하 극각</Button></div>
          <div className="text-xs text-muted-foreground">역추적 {rayTraceConfig.compute_backend === 'gpu_cuda' ? 'NVIDIA GPU 요청' : 'CPU'} · 직광 / 반사광</div>
        </> : null}
      </section> : null}

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-semibold">Preview Quality</span>
          <span className="text-xs text-muted-foreground">
            {previewQualityLabel[quality]}
          </span>
        </div>
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
              {previewQualityName[value]}
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
          <Button className="w-full" disabled={!hasPreviewSource} onClick={() => void runPreview()}>
            <Play /> Run Leak Preview
          </Button>
        )}
        {isRunning ? (
          <div className="space-y-1">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-sky-500 transition-[width]" style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} />
            </div>
            <div className="text-right text-xs text-muted-foreground">
              {isPreparing
                ? '경량 CAD · BVH 준비 중'
                : isFinalizing
                  ? job?.phase_detail ?? '결과 집계 중'
                : `${job?.geometry_cache_hit ? 'BVH 재사용 · ' : ''}${Math.round((job?.progress ?? 0) * 100)}% · ${(job?.processed_rays ?? 0).toLocaleString()} / ${(job?.total_rays ?? 0).toLocaleString()} Rays`}
            </div>
          </div>
        ) : null}
        <p role="status" className="text-xs leading-5 text-muted-foreground">{message}</p>
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

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-sm font-semibold">
            <Cuboid className="size-4 text-slate-500" /> Preview Blockers
            <HelpTooltip label="Block 적용 및 표시 도움말">체크박스는 해석 적용 ON/OFF, 눈 아이콘은 화면 표시입니다. 숨겨도 ON인 Block은 해석에 적용됩니다.</HelpTooltip>
          </span>
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={blockers.length === 0}
              onClick={() => setBlockersVisible(!blockersVisible)}
            >
              {blockersVisible ? <Eye /> : <EyeOff />}
              {blockersVisible ? 'Hide All' : 'Show All'}
            </Button>
            {pickingTarget !== 'blocker' ? (
              <Button
                size="sm"
                variant="outline"
                disabled={isRunning}
                onClick={() => {
                  finishIgnoreAreaSelection()
                  finishBlockerAreaSelection()
                  actions.setRoiBoxSelectionArmed(false)
                  actions.setSelectedFaceIds([])
                  actions.setEmitterFaceSelectionArmed(true)
                  setPickingTarget('blocker')
                  setMessage('3D Viewer에서 Blocker 기준 CAD Surface를 선택하세요.')
                }}
              >
                <Cuboid /> Add Blocker
              </Button>
            ) : null}
          </div>
        </div>
        {pickingTarget === 'blocker' ? (
          <div className="rounded-md border border-sky-300 bg-sky-50/70 p-2 dark:border-sky-700 dark:bg-sky-950/25">
            <div className="mb-2 text-xs font-medium">
              {selectedFaceIds.length > 0 ? 'CAD Surface 선택됨' : 'CAD Surface를 선택하세요'}
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <Button
                size="sm"
                disabled={selectedFaceIds.length === 0 || blockerCreationPending}
                onClick={() => void finishFaceSelection('blocker')}
              >
                <Cuboid /> {blockerCreationPending ? '생성 중' : 'Blocker 생성'}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={blockerCreationPending}
                onClick={() => {
                  actions.setSelectedFaceIds([])
                  actions.setEmitterFaceSelectionArmed(false)
                  finishBlockerAreaSelection()
                  actions.setRoiBoxSelectionArmed(false)
                  setPickingTarget(null)
                  setMessage('Blocker 추가를 취소했습니다.')
                }}
              >
                <X /> 취소
              </Button>
            </div>
          </div>
        ) : null}
        {blockers.map((blocker) => {
          const editing = editingBlockerId === blocker.id
          const fieldClass = 'mt-1 h-8 w-full rounded-md border border-border bg-background/70 px-2 text-xs outline-none focus:border-primary/60'
          return (
            <div key={blocker.id} className="rounded-md border border-border p-2">
              <div className="flex items-center gap-1">
                <input
                  aria-label={`${blocker.label} name`}
                  className="h-8 min-w-0 flex-1 rounded-md bg-transparent px-1 text-xs font-semibold outline-none focus:bg-muted/50"
                  value={blocker.label}
                  onChange={(event) => updateBlocker(blocker.id, { label: event.currentTarget.value })}
                />
                <label className="flex items-center gap-1 text-xs"><input type="checkbox" aria-label={`${blocker.label} 적용`} checked={blocker.enabled} disabled={isRunning} onChange={(event) => updateBlocker(blocker.id, { enabled: event.currentTarget.checked })} />{blocker.enabled ? 'ON' : 'OFF'}</label>
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} ${blocker.visible !== false ? 'Hide' : 'Show'}`} onClick={() => updateBlocker(blocker.id, { visible: blocker.visible === false })}>
                  {blocker.visible !== false ? <Eye /> : <EyeOff />}
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} Edit`} onClick={() => setEditingBlockerId(editing ? null : blocker.id)}>
                  <Pencil />
                </Button>
                <Button
                  size="icon-xs"
                  variant={blockerAreaSelectionId === blocker.id ? 'default' : 'ghost'}
                  aria-label={`${blocker.label} Draw area`}
                  title="Draw area on CAD Face"
                  onClick={() => {
                    finishIgnoreAreaSelection()
                    actions.setEmitterFaceSelectionArmed(false)
                    actions.setSelectedFaceIds([])
                    beginBlockerAreaSelection(blocker.id)
                    actions.setRoiBoxSelectionArmed(true)
                    setEditingBlockerId(blocker.id)
                    setMessage(`${blocker.label} 기준 Face 위에서 Blocker 영역을 드래그하세요.`)
                  }}
                >
                  <BoxSelect />
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} Delete`} onClick={() => {
                  if (blockerAreaSelectionId === blocker.id) actions.setRoiBoxSelectionArmed(false)
                  removeBlocker(blocker.id)
                }}>
                  <Trash2 />
                </Button>
              </div>
              {editing ? (
                <div className="mt-2 space-y-2">
                  <div className="grid grid-cols-3 gap-1.5">
                    {(['x', 'y', 'z'] as const).map((axis, axisIndex) => (
                      <label key={axis} className="text-[11px] font-medium text-muted-foreground">
                        {axis.toUpperCase()} (mm)
                        <NumberInput value={blocker.baseCenter[axisIndex]} decimals={1} className={fieldClass} onValueChange={(value) => {
                          const baseCenter = [...blocker.baseCenter] as [number, number, number]
                          baseCenter[axisIndex] = value
                          updateBlocker(blocker.id, { baseCenter })
                        }} />
                      </label>
                    ))}
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    {([
                      ['Width', 'widthMm', blocker.widthMm],
                      ['Height', 'heightMm', blocker.heightMm],
                      ['Offset', 'offsetMm', blocker.offsetMm],
                      ['Depth H', 'depthMm', blocker.depthMm],
                    ] as const).map(([label, key, value]) => (
                      <label key={key} className="text-[11px] font-medium text-muted-foreground">
                        {label} (mm)
                        <NumberInput value={value} min={key === 'offsetMm' ? undefined : 0.1} decimals={1} className={fieldClass} onValueChange={(next) => updateBlocker(blocker.id, { [key]: next })} />
                      </label>
                    ))}
                  </div>
                  <Button size="sm" variant={blocker.reverse ? 'default' : 'outline'} className="w-full" onClick={() => updateBlocker(blocker.id, { reverse: !blocker.reverse })}>
                    Reverse
                  </Button>
                </div>
              ) : null}
            </div>
          )
        })}
      </section>

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-sm font-semibold">Allowed Area
            <HelpTooltip label="Allowed Area 적용 도움말">체크박스 ON인 영역을 Preview 추적과 후보 표시에서 제외합니다. 눈 아이콘은 표시만 바꾸며 숨겨도 해석에 적용됩니다. 영역을 변경하면 Preview를 다시 실행해야 합니다. 이미 생성된 ROI·Receiver는 자동 삭제되지 않습니다. Ray Tracing에는 Run Options의 Allowed Area도 ON이어야 합니다.</HelpTooltip>
          </span>
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={ignoreAreas.length === 0}
              onClick={() => setIgnoreAreasVisible(!ignoreAreasVisible)}
            >
              {ignoreAreasVisible ? <Eye /> : <EyeOff />}
              {ignoreAreasVisible ? 'Hide All' : 'Show All'}
            </Button>
            <Button
              size="sm"
              variant={ignoreAreaSelectionArmed ? 'default' : 'outline'}
              disabled={!scene || isRunning}
              onClick={() => {
                if (ignoreAreaSelectionArmed) {
                  finishIgnoreAreaSelection()
                  actions.setRoiBoxSelectionArmed(false)
                  return
                }
                actions.setEmitterFaceSelectionArmed(false)
                actions.setSelectedFaceIds([])
                setPickingTarget(null)
                finishBlockerAreaSelection()
                beginIgnoreAreaSelection()
                actions.setRoiBoxSelectionArmed(true)
              }}
            >
              <BoxSelect /> {ignoreAreaSelectionArmed ? '선택 완료' : 'Add Area'}
            </Button>
          </div>
        </div>
        {ignoreAreas.map((area) => (
          <div key={area.id} className="flex items-center gap-1 rounded-md border border-border px-2 py-1.5">
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" aria-label={`${area.label} 적용`} checked={area.enabled} disabled={isRunning} onChange={(event) => setIgnoreAreaEnabled(area.id, event.currentTarget.checked)} /></label>
            <span className="min-w-0 flex-1 truncate text-xs font-medium">
              {area.label} · {area.regions.length}개 영역 · {area.enabled ? 'ON' : 'OFF'}
            </span>
            <Button
              size="icon-xs"
              variant={activeIgnoreAreaId === area.id ? 'default' : 'ghost'}
              aria-label={`${area.label} 영역 추가`}
              disabled={isRunning}
              onClick={() => {
                actions.setEmitterFaceSelectionArmed(false)
                actions.setSelectedFaceIds([])
                setPickingTarget(null)
                finishBlockerAreaSelection()
                beginIgnoreAreaSelection(area.id)
                actions.setRoiBoxSelectionArmed(true)
              }}
            >
              <Plus />
            </Button>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label={`${area.label} ${area.visible !== false ? 'Hide' : 'Show'}`}
              onClick={() => setIgnoreAreaVisible(area.id, area.visible === false)}
            >
              {area.visible !== false ? <Eye /> : <EyeOff />}
            </Button>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label={`${area.label} Delete`}
              onClick={() => removeIgnoreArea(area.id)}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </section>

      {previewResult ? <ComputeExecutionStatus configuredBackend={previewResult.config.compute_backend ?? 'cpu'}
        performance={(previewResult.metrics._performance_summary ?? {}) as Record<string, unknown>} /> : null}
      {observerSummary ? <section className="space-y-2 rounded-lg border border-border p-3">
        <div className="flex items-center gap-1 text-sm font-semibold">시야각 역추적<HelpTooltip label="역추적 결과 도움말">검출 수는 발광·반사 방향 조건을 만족한 관찰 경로 수입니다. 기존 유출 광량에 합산하지 않습니다. 같은 경로의 다른 탐색 표본이 포함될 수 있습니다. 위치는 처음 보이는 CAD 면 또는 광원 면입니다. 검사 간격보다 좁은 틈과 샘플링되지 않은 반사 경로는 놓칠 수 있습니다. 최대 반사 횟수에서 끝난 경로가 있으면 반사 횟수를 높여 재검사할 수 있습니다.</HelpTooltip></div>
        <ComputeExecutionStatus configuredBackend={observerSummary.requested_backend ?? 'cpu'} performance={observerSummary.performance ?? { compute_execution_state: 'cpu' }} />
        <div className="text-xs">{observerSummary.complete ? '탐색 완료' : '중지 · 부분 결과'} · {observerSummary.attempted_count.toLocaleString()} / {observerSummary.planned_count.toLocaleString()} 경로 · 제외 {observerSummary.excluded_count.toLocaleString()}</div>
        <div className="text-xs">직광 {observerSummary.direct_count.toLocaleString()} · 반사광 {observerSummary.reflected_count.toLocaleString()} · 반사 한도 종료 {observerSummary.depth_limited_count.toLocaleString()}</div>
        <div className="max-h-48 overflow-auto"><table className="w-full text-right text-xs"><thead><tr><th>좌우 / 상하 (°)</th><th>직광</th><th>반사광</th></tr></thead><tbody>{observerSummary.views.map((view, i) => <tr key={i}><td>{view.horizontal_deg} / {view.vertical_deg}</td><td>{view.direct_count}</td><td>{view.reflected_count}</td></tr>)}</tbody></table></div>
        {observerSummary.direct_count + observerSummary.reflected_count === 0 ? <div role="status" className="text-xs text-amber-700 dark:text-amber-400">현재 조건에서 미검출 · 빛샘 없음 판정이 아닙니다.</div> : null}
      </section> : null}
      {angularSummary ? <section className="space-y-2 rounded-lg border border-border p-3">
        <div className="text-sm font-semibold">{previewDirectionOptions.find((item) => item.id === angularSummary.direction)?.label} · 방향별 유출광</div>
        <div className="text-xs text-muted-foreground">선택 방향 {angularSummary.selected_count.toLocaleString()} rays · 제외 {angularSummary.excluded_count.toLocaleString()} rays</div>
        <table className="w-full text-right text-xs"><thead><tr><th className="text-left">각도</th><th>Rays</th><th>Flux (lm)</th></tr></thead>
          <tbody>{angularSummary.bins.map((bin) => <tr key={bin.min_deg}><td className="py-1 text-left">{bin.min_deg}–{bin.max_deg}°</td><td>{bin.count.toLocaleString()}</td><td>{bin.flux_lumen.toExponential(3)}</td></tr>)}</tbody>
        </table>
      </section> : null}
      {candidates.length > 0 ? (
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ScanSearch className="size-4 text-orange-500" /> Leak Candidates
            <HelpTooltip label="후보 삭제 도움말">삭제하면 현재 결과 목록과 해당 강조점에서 제거합니다. 해석 원본과 각도별 집계, 이미 생성한 ROI·Receiver는 유지합니다. Preview를 다시 실행하면 새 결과에서 다시 검출될 수 있습니다.</HelpTooltip>
          </div>
          {candidates.map((candidate, index) => {
            const selected = candidate.id === selectedCandidateId
            return (
              <article
                key={candidate.id}
                className={cn(
                  'rounded-lg border p-2.5 transition-colors',
                  selected ? 'border-orange-300 bg-orange-50 dark:border-orange-700 dark:bg-orange-950/25' : 'border-border bg-background/40',
                )}
                onClick={() => selectCandidate(candidate.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <Button size="icon-xs" variant="ghost" aria-label={`Delete Candidate ${index + 1}`} onClick={(event) => { event.stopPropagation(); removeCandidate(candidate.id) }}><Trash2 /></Button>
                  <strong className="text-sm">Candidate {String(index + 1).padStart(2, '0')} · {candidate.label}</strong>
                  <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-xs font-semibold text-orange-700 dark:text-orange-300">
                    {candidate.observerEvidence ? '경로' : 'Risk'} Priority · {riskPriority(candidate.relativeStrength)}
                  </span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Candidate Center ({candidate.center.map(coordinate).join(', ')}) mm
                </div>
                {candidate.observerEvidence ? <div className="mt-1 text-xs">직광 {candidate.observerEvidence.directCount} · 반사광 {candidate.observerEvidence.reflectedCount}</div> : null}
                {candidate.sampledPathCount > 0 ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    Sampled paths {candidate.sampledPathCount} · Reflections {' '}
                    {candidate.minReflectionCount === candidate.maxReflectionCount
                      ? candidate.minReflectionCount
                      : `${candidate.minReflectionCount}–${candidate.maxReflectionCount}`}
                    {' '}· Normal angle {candidate.meanExitAngleDeg?.toFixed(1)}°
                    {candidate.grazingPathCount > 0 ? ` · Grazing ${candidate.grazingPathCount}` : ''}
                  </div>
                ) : null}
                <div className="mt-2 grid grid-cols-2 gap-1.5">
                  <Button size="sm" variant="outline" onClick={(event) => { event.stopPropagation(); createRoi(candidate, false) }}>
                    <Square /> ROI·Receiver 생성
                  </Button>
                  <Button size="sm" onClick={(event) => { event.stopPropagation(); createRoi(candidate, true) }}>
                    정밀해석 준비
                  </Button>
                </div>
              </article>
            )
          })}
        </section>
      ) : null}
    </div>
  )
}
