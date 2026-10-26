import {
  Ban,
  CheckCircle2,
  Circle,
  CircleAlert,
  Layers3,
  LoaderCircle,
  Play,
  Square,
} from 'lucide-react'

import { AppDialog } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  useWorkspaceStore,
  workspaceSelectors,
  type CadCase,
} from '@/stores'

import {
  type BatchRayTraceCaseState,
  type BatchRayTraceCaseStatus,
} from './batch-ray-tracing'
import type { BatchRayTracingManager } from './batch-ray-tracing-manager'

const caseStatusLabels: Record<BatchRayTraceCaseStatus, string> = {
  pending: '대기',
  loading_scene: 'Scene 준비',
  queued: '대기열',
  preparing: '계산 준비',
  running: '해석 중',
  finalizing: '결과 정리',
  completed: '완료',
  failed: '실패',
  cancelled: '중단',
}

function caseStatusIcon(status: BatchRayTraceCaseStatus) {
  if (status === 'completed') {
    return <CheckCircle2 className="size-4 text-emerald-600" aria-hidden="true" />
  }
  if (status === 'failed') {
    return <CircleAlert className="size-4 text-destructive" aria-hidden="true" />
  }
  if (status === 'cancelled') {
    return <Ban className="size-4 text-muted-foreground" aria-hidden="true" />
  }
  if (status === 'pending') {
    return <Circle className="size-4 text-muted-foreground" aria-hidden="true" />
  }
  return <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden="true" />
}

function BatchCaseRow({
  cadCase,
  selected,
  caseState,
  disabled,
  onSelectedChange,
}: {
  cadCase: CadCase
  selected: boolean
  caseState?: BatchRayTraceCaseState
  disabled: boolean
  onSelectedChange(selected: boolean): void
}) {
  const name = cadCase.name || `CASE ${String(cadCase.order).padStart(2, '0')}`
  const status = caseState?.status
  const progress = Math.max(0, Math.min(1, caseState?.progress ?? 0))

  return (
    <label
      className={`block rounded-lg border p-3 transition-colors ${
        selected
          ? 'border-primary/35 bg-primary/5'
          : 'border-border bg-background/35'
      } ${disabled ? '' : 'cursor-pointer hover:border-primary/45'}`}
    >
      <span className="flex min-w-0 items-start gap-3">
        <input
          type="checkbox"
          className="mt-0.5 shrink-0"
          checked={selected}
          disabled={disabled}
          aria-label={`${name} 선택`}
          onChange={(event) => onSelectedChange(event.currentTarget.checked)}
        />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center justify-between gap-2">
            <span className="min-w-0 truncate text-sm font-semibold">
              {name}
            </span>
            {status ? (
              <span className="flex shrink-0 items-center gap-1 text-xs font-medium">
                {caseStatusIcon(status)}
                {caseStatusLabels[status]}
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {cadCase.cad.displayName}
          </span>
          {caseState ? (
            <>
              <span className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span className="min-w-0 truncate">{caseState.phaseDetail}</span>
                <span className="shrink-0">{Math.round(progress * 100)}%</span>
              </span>
              <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-muted">
                <span
                  className={`block h-full rounded-full transition-[width] ${
                    status === 'failed'
                      ? 'bg-destructive'
                      : status === 'completed'
                        ? 'bg-emerald-500'
                        : 'bg-primary'
                  }`}
                  style={{ width: `${progress * 100}%` }}
                />
              </span>
              {caseState.geometryCacheHit === true ? (
                <Badge variant="outline" className="mt-2 border-emerald-500/40 text-emerald-700 dark:text-emerald-300">
                  Geometry reused
                </Badge>
              ) : null}
              {caseState.error ? (
                <span className="mt-2 block rounded-md border border-destructive/25 bg-destructive/8 px-2 py-1.5 text-xs leading-4 text-destructive">
                  {caseState.error}
                </span>
              ) : null}
            </>
          ) : null}
        </span>
      </span>
    </label>
  )
}

export function BatchRayTracingDialog({
  manager,
  singleRayTraceRunning = false,
}: {
  manager: BatchRayTracingManager
  singleRayTraceRunning?: boolean
}) {
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const orderedCases = [...cadCases].sort((left, right) => left.order - right.order)
  const availableCaseIds = orderedCases.map((item) => item.caseId)
  const selectedCaseIds = manager.selectedCaseIds.filter((caseId) =>
    availableCaseIds.includes(caseId),
  )
  const selectedSet = new Set(selectedCaseIds)
  const caseStates = new Map(
    manager.state.cases.map((item) => [item.caseId, item]),
  )
  const allSelected =
    orderedCases.length > 0 && selectedCaseIds.length === orderedCases.length
  const completedCount = manager.state.cases.filter(
    (item) => item.status === 'completed',
  ).length
  const failedCount = manager.state.cases.filter(
    (item) => item.status === 'failed',
  ).length
  const canStart =
    !manager.isRunning &&
    !singleRayTraceRunning &&
    selectedCaseIds.length >= 2

  return (
    <AppDialog
      open={manager.open}
      onOpenChange={manager.setOpen}
      title="Batch Ray Tracing"
      help="선택한 Case를 위에서 아래 순서로 하나씩 해석합니다. 한 Case가 실패해도 다음 Case를 계속 실행하며, 완료된 결과는 각 Case에 보관됩니다. 앱 종료 후에도 남기려면 Batch 완료 후 Save Workspace를 눌러 .bitsam 파일로 저장하세요. 동일한 CAD 구성은 준비된 Scene과 Geometry를 가능한 범위에서 재사용합니다. GPU Case는 엄격한 CUDA 준비 검사를 통과하지 못하면 CPU로 대체하지 않고 실패로 기록합니다."
      size="lg"
      contentClassName="max-h-[min(52rem,calc(100vh-2rem))]"
      footer={
        <>
          <Button variant="outline" onClick={() => manager.setOpen(false)}>
            Close
          </Button>
          {manager.isRunning ? (
            <Button
              variant="destructive"
              disabled={manager.cancelPending}
              onClick={() => void manager.cancel()}
            >
              {manager.cancelPending ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Square />
              )}
              Cancel Batch
            </Button>
          ) : (
            <Button
              disabled={!canStart}
              title={
                singleRayTraceRunning
                  ? '현재 단일 Ray Tracing이 끝난 뒤 실행할 수 있습니다.'
                  : selectedCaseIds.length < 2
                    ? 'Case를 2개 이상 선택해 주세요.'
                    : undefined
              }
              onClick={() => void manager.run()}
            >
              <Play />
              Run {selectedCaseIds.length} Cases
            </Button>
          )}
        </>
      }
    >
      <div className="flex min-h-0 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/25 px-3 py-2">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={allSelected}
              disabled={manager.isRunning || orderedCases.length === 0}
              aria-label="전체 Case 선택"
              onChange={(event) =>
                manager.selectCases(
                  event.currentTarget.checked ? availableCaseIds : [],
                )
              }
            />
            전체 선택
          </label>
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Layers3 className="size-3.5" aria-hidden="true" />
            {selectedCaseIds.length} / {orderedCases.length} Cases
          </span>
        </div>

        {manager.state.status !== 'idle' ? (
          <div
            className="rounded-lg border border-primary/25 bg-primary/5 p-3"
            role="status"
          >
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="font-semibold">
                {manager.state.status === 'running'
                  ? 'Batch 실행 중'
                  : manager.state.status === 'completed'
                    ? 'Batch 완료'
                    : manager.state.status === 'completed_with_errors'
                      ? 'Batch 완료 · 오류 있음'
                      : 'Batch 중단'}
              </span>
              <span>{Math.round(manager.state.progress * 100)}%</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${manager.state.progress * 100}%` }}
              />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{completedCount} completed</span>
              {failedCount > 0 ? (
                <span className="text-destructive">{failedCount} failed</span>
              ) : null}
              {completedCount > 0 ? (
                <Badge variant="outline" className="border-emerald-500/40 text-emerald-700 dark:text-emerald-300">
                  Results stored in Cases
                </Badge>
              ) : null}
            </div>
          </div>
        ) : null}

        {singleRayTraceRunning ? (
          <p className="rounded-lg border border-amber-400/45 bg-amber-50/75 px-3 py-2 text-xs font-medium text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
            단일 Ray Tracing이 실행 중입니다. 완료하거나 중단한 뒤 Batch를 시작해 주세요.
          </p>
        ) : null}

        {selectedCaseIds.length < 2 && !manager.isRunning ? (
          <p className="text-xs font-medium text-muted-foreground">
            Batch 실행에는 Case 2개 이상이 필요합니다.
          </p>
        ) : null}

        {manager.error ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/8 p-2 text-xs leading-4 text-destructive">
            {manager.error}
          </p>
        ) : null}

        <div className="max-h-[min(32rem,55vh)] space-y-2 overflow-y-auto pr-1">
          {orderedCases.map((cadCase) => (
            <BatchCaseRow
              key={cadCase.caseId}
              cadCase={cadCase}
              selected={selectedSet.has(cadCase.caseId)}
              caseState={caseStates.get(cadCase.caseId)}
              disabled={manager.isRunning}
              onSelectedChange={(selected) =>
                manager.setCaseSelected(cadCase.caseId, selected)
              }
            />
          ))}
        </div>
      </div>
    </AppDialog>
  )
}
