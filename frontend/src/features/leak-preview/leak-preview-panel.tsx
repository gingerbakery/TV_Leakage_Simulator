import { useEffect, useMemo, useRef, useState } from 'react'
import { BoxSelect, Cuboid, Eye, EyeOff, Lightbulb, LocateFixed, Pencil, Play, ScanSearch, Square, StopCircle, Trash2 } from 'lucide-react'

import type { ScenePayload } from '@/api'
import {
  useRayTraceJobQuery,
  useStartRayTraceMutation,
  useStopRayTraceMutation,
} from '@/api'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { createFaceEmitter, nextSpecId } from '@/features/raytracing'
import {
  groupRoiFacesByComponent,
} from '@/features/roi'
import { cn } from '@/lib/utils'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'

import {
  buildLeakPreviewRequest,
  createLeakPreviewBlockerFromFaces,
  createCandidateReceiver,
  detectLeakPreviewCandidates,
  resolveLeakPreviewRoiFaces,
  type LeakPreviewCandidate,
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
  return new Set(
    faceIds.map((faceId) => sourceIds[faceId] ?? faceId),
  ).size
}

const previewQualityLabel: Record<LeakPreviewQuality, string> = {
  fast: '100,000 Rays · 3 Reflections',
  balanced: '500,000 Rays · 5 Reflections',
  deep: '1,000,000 Rays · 8 Reflections',
}

const previewQualityName: Record<LeakPreviewQuality, string> = {
  fast: 'Fast',
  balanced: 'Balanced',
  deep: 'Deep Scan',
}

export function LeakPreviewPanel({ scene, onOpenPrecision }: LeakPreviewPanelProps) {
  const selectedFaceIds = useWorkspaceStore(workspaceSelectors.selectedFaceIds)
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
  const ensureScene = useLeakPreviewStore((state) => state.ensureScene)
  const quality = useLeakPreviewStore((state) => state.quality)
  const jobId = useLeakPreviewStore((state) => state.jobId)
  const candidates = useLeakPreviewStore((state) => state.candidates)
  const selectedCandidateId = useLeakPreviewStore((state) => state.selectedCandidateId)
  const ignoreAreaSelectionArmed = useLeakPreviewStore((state) => state.ignoreAreaSelectionArmed)
  const ignoreAreas = useLeakPreviewStore((state) => state.ignoreAreas)
  const blockers = useLeakPreviewStore((state) => state.blockers)
  const runSignature = useLeakPreviewStore((state) => state.runSignature)
  const setSourceFaceIds = useLeakPreviewStore((state) => state.setSourceFaceIds)
  const setQuality = useLeakPreviewStore((state) => state.setQuality)
  const setJobId = useLeakPreviewStore((state) => state.setJobId)
  const setRunSignature = useLeakPreviewStore((state) => state.setRunSignature)
  const setDetection = useLeakPreviewStore((state) => state.setDetection)
  const clearDetection = useLeakPreviewStore((state) => state.clearDetection)
  const selectCandidate = useLeakPreviewStore((state) => state.selectCandidate)
  const setIgnoreAreaSelectionArmed = useLeakPreviewStore((state) => state.setIgnoreAreaSelectionArmed)
  const setIgnoreAreaEnabled = useLeakPreviewStore((state) => state.setIgnoreAreaEnabled)
  const removeIgnoreArea = useLeakPreviewStore((state) => state.removeIgnoreArea)
  const addBlocker = useLeakPreviewStore((state) => state.addBlocker)
  const updateBlocker = useLeakPreviewStore((state) => state.updateBlocker)
  const removeBlocker = useLeakPreviewStore((state) => state.removeBlocker)
  const startMutation = useStartRayTraceMutation()
  const stopMutation = useStopRayTraceMutation()
  const jobQuery = useRayTraceJobQuery(jobId)
  const job = jobQuery.data
  const handledRunRef = useRef<string | null>(null)
  const [message, setMessage] = useState('광원으로 사용할 CAD Face를 선택하세요.')
  const [pickingTarget, setPickingTarget] = useState<'source' | 'blocker' | null>(null)
  const [editingBlockerId, setEditingBlockerId] = useState<string | null>(null)

  useEffect(() => {
    if (scene) ensureScene(scene.metadata.scene_token)
  }, [ensureScene, scene])

  const isRunning = startMutation.isPending || job?.status === 'queued' || job?.status === 'running'
  const isPreparing = job?.status === 'running' && job.phase === 'preparing'
  const existingEmitterFaces = emitters
    .filter((emitter) => emitter.enabled && emitter.emitter_type === 'face')
    .flatMap((emitter) => emitter.face_indices)
  const sourceCadFaceCount = cadFaceCount(scene, sourceFaceIds)
  const previewInputSignature = useMemo(() => JSON.stringify({
    sceneToken: scene?.metadata.scene_token ?? null,
    sourceFaceIds,
    quality,
    materialAssignments,
    transformRules,
    excludedComponentIds,
    deletedComponentIds,
    blockers,
  }), [
    blockers,
    deletedComponentIds,
    excludedComponentIds,
    materialAssignments,
    quality,
    scene?.metadata.scene_token,
    sourceFaceIds,
    transformRules,
  ])

  useEffect(() => {
    return () => actions.setEmitterFaceSelectionArmed(false)
  }, [actions])

  useEffect(() => {
    return () => {
      setIgnoreAreaSelectionArmed(false)
      actions.setRoiBoxSelectionArmed(false)
    }
  }, [actions, setIgnoreAreaSelectionArmed])

  useEffect(() => {
    if (!scene || job?.status !== 'completed' || !job.result) return
    const detectionKey = `${job.job_id}:${JSON.stringify(ignoreAreas)}`
    if (handledRunRef.current === detectionKey) return
    handledRunRef.current = detectionKey
    const detection = detectLeakPreviewCandidates(scene, job.result, {
      quality,
      ignoreAreas,
      transformRules,
    })
    setDetection(job.result, detection.points, detection.candidates)
    setMessage(
      detection.candidates.length > 0
        ? `빛샘 후보 ${detection.candidates.length}개를 찾았습니다.`
        : '외부 유출광이 검출되지 않았습니다. Balanced 모드로 다시 확인해 보세요.',
    )
  }, [ignoreAreas, job, quality, scene, setDetection, transformRules])

  useEffect(() => {
    if (!runSignature || runSignature === previewInputSignature) return
    clearDetection()
    setMessage('Preview 재실행 필요')
  }, [clearDetection, previewInputSignature, runSignature])

  useEffect(() => {
    if (job?.status !== 'failed') return
    setMessage(`Preview 실행 실패: ${job.error}`)
  }, [job])

  const finishFaceSelection = (target: 'source' | 'blocker') => {
    if (selectedFaceIds.length === 0) {
      setMessage('3D Viewer에서 CAD Face를 하나 이상 선택하세요.')
      return
    }
    if (target === 'blocker') {
      if (!scene) return
      const blocker = createLeakPreviewBlockerFromFaces(scene, selectedFaceIds, blockers.length + 1)
      if (!blocker) {
        setMessage('Preview Blocker는 하나의 평평한 CAD Surface를 선택해야 합니다.')
        return
      }
      addBlocker(blocker)
      setEditingBlockerId(blocker.id)
      setPickingTarget(null)
      actions.setSelectedFaceIds([])
      actions.setEmitterFaceSelectionArmed(false)
      clearDetection()
      setMessage('Preview Blocker를 생성했습니다. 위치와 Depth를 조정하세요.')
      return
    }
    setSourceFaceIds(selectedFaceIds)
    setPickingTarget(null)
    actions.setEmitterFaceSelectionArmed(false)
    clearDetection()
    setRunSignature(previewInputSignature)
    setMessage(`광원 Face ${selectedFaceIds.length}개가 등록되었습니다.`)
  }

  const runPreview = async () => {
    if (!scene || sourceFaceIds.length === 0) return
    clearDetection()
    handledRunRef.current = null
    setMessage('전체 세트의 외부 유출광을 탐색하고 있습니다.')
    try {
      const started = await startMutation.mutateAsync({
        request: buildLeakPreviewRequest({
          scene,
          sourceFaceIds,
          quality,
          computeBackend: rayTraceConfig.compute_backend,
          materialAssignments,
          transformRules,
          excludedComponentIds,
          deletedComponentIds,
          blockers,
        }),
      })
      setJobId(started.job_id)
    } catch (error) {
      setRunSignature(null)
      setMessage(`Preview 실행 실패: ${error instanceof Error ? error.message : String(error)}`)
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
      point: { x: candidate.center[0], y: candidate.center[1], z: candidate.center[2] },
    })
    if (precision) {
      const receiverId = nextSpecId(
        'receiver',
        receivers.map((receiver) => receiver.receiver_id),
      )
      // Use the regular workspace contract so the user can immediately review
      // and refine the automatically placed precision Receiver.
      actions.upsertReceiver({
        ...createCandidateReceiver(candidate, candidateIndex),
        receiver_id: receiverId,
        display_name: `Preview Receiver ${candidateIndex}`,
      })
      if (!emitters.some((emitter) => emitter.enabled)) {
        const emitterId = nextSpecId('emitter', emitters.map((emitter) => emitter.emitter_id))
        actions.upsertEmitter({
          ...createFaceEmitter(emitterId, sourceFaceIds),
          ray_count: Math.max(100_000, rayTraceConfig.ray_count),
        })
      }
      setMessage('ROI, Receiver와 기본 Emitter를 준비했습니다. 방향과 크기를 확인한 뒤 정밀해석을 실행하세요.')
      onOpenPrecision()
      return
    }
    setMessage('선택한 후보를 ROI List에 추가했습니다.')
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
        <div className="text-sm font-semibold">Preview Light Source</div>
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant={pickingTarget === 'source' ? 'default' : 'outline'}
            onClick={() => {
              if (pickingTarget === 'source') {
                finishFaceSelection('source')
                return
              }
              actions.setSelectedFaceIds([])
              actions.setEmitterFaceSelectionArmed(true)
              setPickingTarget('source')
              setMessage('3D Viewer에서 광원 Face를 선택한 뒤 선택 완료를 누르세요.')
            }}
          >
            <LocateFixed />
            {pickingTarget === 'source' ? '선택 완료' : 'CAD Face 선택'}
          </Button>
          <Button
            variant="outline"
            disabled={existingEmitterFaces.length === 0}
            onClick={() => {
              setSourceFaceIds(existingEmitterFaces)
              clearDetection()
              setMessage(`기존 Emitter Face ${existingEmitterFaces.length}개를 사용합니다.`)
            }}
          >
            기존 Emitter 사용
          </Button>
        </div>
        <div className="rounded-md bg-muted/45 px-2.5 py-2 text-xs">
          등록된 광원 CAD Face <strong>{sourceCadFaceCount}</strong>개
        </div>
      </section>

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
            onClick={() => stopMutation.mutate({ jobId })}
          >
            <StopCircle /> Preview Stop
          </Button>
        ) : (
          <Button className="w-full" disabled={sourceFaceIds.length === 0} onClick={() => void runPreview()}>
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
                : `${Math.round((job?.progress ?? 0) * 100)}% · ${(job?.processed_rays ?? 0).toLocaleString()} / ${(job?.total_rays ?? 0).toLocaleString()} Rays`}
            </div>
          </div>
        ) : null}
        <p role="status" className="text-xs leading-5 text-muted-foreground">{message}</p>
      </section>

      <section className="space-y-2 rounded-lg border border-border bg-background/45 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-sm font-semibold">
            <Cuboid className="size-4 text-slate-500" /> Preview Blockers
          </span>
          <Button
            size="sm"
            variant={pickingTarget === 'blocker' ? 'default' : 'outline'}
            disabled={isRunning}
            onClick={() => {
              if (pickingTarget === 'blocker') {
                finishFaceSelection('blocker')
                return
              }
              actions.setSelectedFaceIds([])
              actions.setEmitterFaceSelectionArmed(true)
              setPickingTarget('blocker')
              setMessage('Blocker 기준으로 사용할 평평한 CAD Surface를 선택하세요.')
            }}
          >
            <Cuboid /> {pickingTarget === 'blocker' ? '선택 완료' : 'Add Blocker'}
          </Button>
        </div>
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
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} ${blocker.enabled ? 'Hide' : 'Show'}`} onClick={() => updateBlocker(blocker.id, { enabled: !blocker.enabled })}>
                  {blocker.enabled ? <Eye /> : <EyeOff />}
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} Edit`} onClick={() => setEditingBlockerId(editing ? null : blocker.id)}>
                  <Pencil />
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label={`${blocker.label} Delete`} onClick={() => removeBlocker(blocker.id)}>
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
          <span className="text-sm font-semibold">Allowed Area</span>
          <Button
            size="sm"
            variant={ignoreAreaSelectionArmed ? 'default' : 'outline'}
            disabled={!scene || isRunning}
            onClick={() => {
              const next = !ignoreAreaSelectionArmed
              setIgnoreAreaSelectionArmed(next)
              actions.setRoiBoxSelectionArmed(next)
            }}
          >
            <BoxSelect /> {ignoreAreaSelectionArmed ? 'Cancel' : 'Add'}
          </Button>
        </div>
        {ignoreAreas.map((area) => (
          <div key={area.id} className="flex items-center gap-1 rounded-md border border-border px-2 py-1.5">
            <span className="min-w-0 flex-1 truncate text-xs font-medium">{area.label}</span>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label={`${area.label} ${area.enabled ? 'Hide' : 'Show'}`}
              onClick={() => setIgnoreAreaEnabled(area.id, !area.enabled)}
            >
              {area.enabled ? <Eye /> : <EyeOff />}
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

      {candidates.length > 0 ? (
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ScanSearch className="size-4 text-orange-500" /> Leak Candidates
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
                  <strong className="text-sm">Candidate {String(index + 1).padStart(2, '0')} · {candidate.label}</strong>
                  <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-xs font-semibold text-orange-700 dark:text-orange-300">
                    Risk Priority · {riskPriority(candidate.relativeStrength)}
                  </span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Candidate Center ({candidate.center.map(coordinate).join(', ')}) mm
                </div>
                <div className="mt-2 grid grid-cols-2 gap-1.5">
                  <Button size="sm" variant="outline" onClick={(event) => { event.stopPropagation(); createRoi(candidate, false) }}>
                    <Square /> ROI 생성
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
